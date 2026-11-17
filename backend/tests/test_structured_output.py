"""
Agents' scoring call as structured output (AgentOutput JSON schema),
with the text parser as the fallback for rejected or mismatched output.
"""

import asyncio

import groq
import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from test_agents import AGENT_CASES, AGENT_IDS, VALID_JSON, FakeLLM, make_state

from app.agents import structured_output
from app.agents.base_agent import AgentOutputError, parse_agent_output
from app.agents.structured_output import AgentOutput, run_scoring_call
from app.settings import settings

MESSAGES = [SystemMessage(content="score it"), HumanMessage(content="Vendor: TestCo")]


def groq_400(code, failed_generation=None):
    """The error Groq raises when output fails the JSON schema."""
    error = {"message": "Failed to validate JSON.", "type": "invalid_request_error", "code": code}
    if failed_generation is not None:
        error["failed_generation"] = failed_generation
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com"))
    return groq.BadRequestError("Error code: 400", response=response, body={"error": error})


def score(llm, parse=parse_agent_output):
    return asyncio.run(run_scoring_call(llm, MESSAGES, parse=parse, agent="TestAgent"))


@pytest.fixture
def fake_llm(monkeypatch):
    from app.agents import base_agent
    llm = FakeLLM()
    monkeypatch.setattr(base_agent, "get_llm", lambda **kwargs: llm)
    return llm


# ── The schema ───────────────────────────────────────────────────────────────

def test_schema_requires_every_field_and_bounds_the_numbers():
    schema = AgentOutput.model_json_schema()
    assert set(schema["required"]) == {"score", "explanation", "evidence", "confidence", "red_flags"}
    props = schema["properties"]
    assert (props["score"]["minimum"], props["score"]["maximum"]) == (0, 100)
    assert (props["confidence"]["minimum"], props["confidence"]["maximum"]) == (0, 1)
    assert props["evidence"]["items"]["type"] == "string"


# ── Through the real agents ──────────────────────────────────────────────────

@pytest.mark.parametrize("name, run_agent, system_prompt", AGENT_CASES, ids=AGENT_IDS)
def test_agents_request_the_agent_output_schema(fake_llm, name, run_agent, system_prompt):
    result = asyncio.run(run_agent(make_state()))
    assert fake_llm.structured_requests == [(AgentOutput, "json_schema", True)]
    assert result["score"] == 78.0 and result["reasoning"]


@pytest.mark.parametrize("name, run_agent, system_prompt", AGENT_CASES, ids=AGENT_IDS)
def test_text_mode_makes_the_plain_call(fake_llm, monkeypatch, name, run_agent, system_prompt):
    monkeypatch.setattr(settings, "agent_structured_output", False)
    result = asyncio.run(run_agent(make_state()))
    assert fake_llm.structured_requests == []
    assert result["score"] == 78.0 and result["confidence"] == 0.88


# ── run_scoring_call ─────────────────────────────────────────────────────────

def test_parsed_output_is_validated_again():
    """validate_agent_output's rules still apply to schema-parsed output."""
    result = score(FakeLLM(scoring_reply=VALID_JSON))
    assert result == {"score": 78.0, "explanation": "Healthy", "evidence": ["current ratio 1.8"],
                      "confidence": 0.88, "red_flags": []}


def test_parsed_output_does_not_go_through_the_text_parser():
    def parse(raw):
        raise AssertionError("text parser should not run")
    assert score(FakeLLM(), parse=parse)["score"] == 78.0


@pytest.mark.parametrize("reply", [
    '{"score": 850, "explanation": "x", "evidence": [], "confidence": 0.9, "red_flags": []}',
    '{"score": 70, "explanation": "x", "evidence": [], "confidence": 70, "red_flags": []}',
    '{"score": 70}',
    "I cannot evaluate this vendor.",
])
def test_output_that_misses_the_schema_is_rejected(reply):
    with pytest.raises(AgentOutputError):
        score(FakeLLM(scoring_reply=reply))


def test_fenced_json_that_misses_the_schema_parser_is_recovered_by_the_text_parser():
    result = score(FakeLLM(scoring_reply=f"```json\n{VALID_JSON}\n```"))
    assert result["score"] == 78.0


def test_truncated_output_rejected_by_groq_is_salvaged():
    partial = '{"score": 64, "confidence": 0.8, "explanation": "Thin margins and rising debt", "evid'
    llm = FakeLLM(scoring_error=groq_400("json_validate_failed", partial))
    result = score(llm)
    assert (result["score"], result["confidence"], result["truncated"]) == (64.0, 0.8, True)
    assert result["red_flags"] == ["Agent output was truncated — red flags may be incomplete"]


@pytest.mark.parametrize("failed_generation", ["", None, '{"score": 64'])
def test_rejected_output_without_score_and_confidence_raises(failed_generation):
    llm = FakeLLM(scoring_error=groq_400("json_validate_failed", failed_generation))
    with pytest.raises(AgentOutputError):
        score(llm)


def test_other_bad_requests_propagate():
    llm = FakeLLM(scoring_error=groq_400("model_decommissioned"))
    with pytest.raises(groq.BadRequestError):
        score(llm)


def test_rate_limits_propagate_for_retry():
    llm = FakeLLM(scoring_error=RuntimeError("Error code: 429 - rate_limit_exceeded"))
    with pytest.raises(RuntimeError, match="429"):
        score(llm)


@pytest.mark.parametrize("body, expected", [
    ({"error": {"code": "json_validate_failed", "failed_generation": "{"}}, "{"),
    ({"code": "json_validate_failed", "failed_generation": "{"}, "{"),
    ({"error": {"code": "json_validate_failed"}}, ""),
    ({"error": {"code": "other"}}, None),
    ("not a dict", None),
    (None, None),
])
def test_failed_generation_extraction(body, expected):
    error = Exception("x")
    error.body = body
    assert structured_output._failed_generation(error) == expected
