"""
Specialist agents: each produces valid structured output, unusable output
raises instead of scoring 0, and prompts carry the right data.

The LLM is replaced by FakeLLM via app.agents.base_agent.get_llm, so each
agent's real code path (reasoning pass → scoring pass → parse/validate) runs
without calling Groq. Live tests call Groq for real.
"""

import asyncio
import os
from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage

from app.agents import base_agent
from app.agents.base_agent import AgentOutputError, parse_agent_output
from app.agents.compliance_agent import (
    COMPLIANCE_SYSTEM_PROMPT, ComplianceAgent, get_required_certs, run_compliance_agent,
)
from app.agents.financial_agent import FINANCIAL_SYSTEM_PROMPT, FinancialAgent, run_financial_agent
from app.agents.operational_agent import OPERATIONAL_SYSTEM_PROMPT, run_operational_agent
from app.agents.reputation_agent import REPUTATION_SYSTEM_PROMPT, run_reputation_agent
from app.agents.strategic_agent import STRATEGIC_SYSTEM_PROMPT, run_strategic_agent

REASONING = "Liquidity is strong [Yahoo Finance / yfinance]. Verdict: low risk."
VALID_JSON = (
    '{"score": 78, "explanation": "Healthy", "evidence": ["current ratio 1.8"], '
    '"confidence": 0.88, "red_flags": []}'
)


class FakeLLM:
    """Answers the reasoning pass (no SystemMessage) and the scoring pass."""

    def __init__(self, scoring_reply=VALID_JSON, scoring_error=None):
        self.scoring_reply, self.scoring_error = scoring_reply, scoring_error
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if isinstance(messages[0], SystemMessage):
            if self.scoring_error:
                raise self.scoring_error
            return SimpleNamespace(content=self.scoring_reply)
        return SimpleNamespace(content=REASONING)

    @property
    def scoring_messages(self):
        return next(m for m in self.calls if isinstance(m[0], SystemMessage))


@pytest.fixture
def fake_llm(monkeypatch):
    """Every LLM the agents construct is this one FakeLLM instance."""
    llm = FakeLLM()
    monkeypatch.setattr(base_agent, "get_llm", lambda **kwargs: llm)
    return llm


CLIENT_PROFILE = {
    "company_name": "Acme Manufacturing Corp", "industry": "Manufacturing",
    "contract_value": 5_000_000, "contract_years": 3, "risk_appetite": "medium",
    "priorities": ["cloud infrastructure", "24/7 enterprise support"],
    "dealbreakers": ["no SOC2 certification", "active OFAC sanctions"],
    "nice_to_have": ["ISO27001 certified"],
}


def make_state():
    return {
        "vendor_name": "TestCo",
        "financial_data": {
            "net_margin": 13.9, "current_ratio": 1.8, "debt_equity_ratio": 0.1,
            "industry": "Software", "_sources": {"net_margin": "Yahoo Finance / yfinance"},
        },
        "compliance_data": {
            "industry": "Software",
            "certifications_found": ["SOC2_Type2", "ISO27001"],
            "certification_evidence_tiers": {"SOC2_Type2": "VERIFIED_REGISTRY",
                                             "ISO27001": "CORROBORATED"},
            "regulatory_frameworks_disclosed": ["GDPR"],
            "ofac_status": "clear", "ofac_clear": True,
            "news_red_flags": [],
            "recent_news": [{"source": "Reuters", "title": "TestCo wins NASA contract",
                             "snippet": "Multi-year deal", "date": ""}],
        },
        "operational_data": {"vendor_name": "TestCo", "industry": "Software",
                             "company_size": "large", "ebitda": 2_000_000_000},
        "reputation_data": {"vendor_name": "TestCo", "industry": "Software",
                            "certifications": ["SOC2_Type2"], "news_red_flags": []},
        "strategic_data": {"vendor_name": "TestCo", "industry": "Software",
                           "certifications": ["SOC2_Type2"],
                           "client_profile": CLIENT_PROFILE},
    }


AGENT_CASES = [
    ("financial",   run_financial_agent,   FINANCIAL_SYSTEM_PROMPT),
    ("compliance",  run_compliance_agent,  COMPLIANCE_SYSTEM_PROMPT),
    ("operational", run_operational_agent, OPERATIONAL_SYSTEM_PROMPT),
    ("reputation",  run_reputation_agent,  REPUTATION_SYSTEM_PROMPT),
    ("strategic",   run_strategic_agent,   STRATEGIC_SYSTEM_PROMPT),
]
AGENT_IDS = [c[0] for c in AGENT_CASES]


# ── Each agent produces valid structured output ───────────

@pytest.mark.parametrize("name, run_agent, system_prompt", AGENT_CASES, ids=AGENT_IDS)
def test_agent_returns_validated_structured_output(fake_llm, name, run_agent, system_prompt):
    result = asyncio.run(run_agent(make_state()))
    assert result["score"] == 78.0 and isinstance(result["score"], float)
    assert result["confidence"] == 0.88
    assert result["evidence"] == ["current ratio 1.8"]
    assert result["red_flags"] == []
    assert result["explanation"] == "Healthy"
    assert result["reasoning"] == REASONING


@pytest.mark.parametrize("name, run_agent, system_prompt", AGENT_CASES, ids=AGENT_IDS)
def test_agent_makes_reasoning_then_scoring_call(fake_llm, name, run_agent, system_prompt):
    asyncio.run(run_agent(make_state()))
    assert len(fake_llm.calls) == 2
    reasoning_msgs, scoring_msgs = fake_llm.calls
    assert "TestCo" in reasoning_msgs[0].content
    assert f"{name.upper()} ANALYSIS" in reasoning_msgs[0].content
    assert scoring_msgs[0].content == system_prompt
    assert "TestCo" in scoring_msgs[1].content


def test_reasoning_prompt_cites_sources_and_hides_private_keys(fake_llm):
    asyncio.run(run_financial_agent(make_state()))
    prompt = fake_llm.calls[0][0].content
    assert "net_margin: Yahoo Finance / yfinance" in prompt
    assert '"_sources"' not in prompt


@pytest.mark.parametrize("name, run_agent, system_prompt", AGENT_CASES, ids=AGENT_IDS)
@pytest.mark.parametrize("bad_reply", [
    "", "I cannot evaluate this vendor.", '{"score": 120, "confidence": 0.9}',
    '{"score": 70, "confidence": 70}',
])
def test_agent_raises_on_unusable_output(fake_llm, name, run_agent, system_prompt, bad_reply):
    fake_llm.scoring_reply = bad_reply
    with pytest.raises(AgentOutputError):
        asyncio.run(run_agent(make_state()))


@pytest.mark.parametrize("name, run_agent, system_prompt", AGENT_CASES, ids=AGENT_IDS)
def test_agent_propagates_rate_limit_errors_for_retry(fake_llm, name, run_agent, system_prompt):
    fake_llm.scoring_error = RuntimeError("Error code: 429 - rate_limit_exceeded, try again in 1.2s")
    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(run_agent(make_state()))


def test_agent_uses_two_llms_with_separate_token_budgets(monkeypatch):
    budgets = []
    monkeypatch.setattr(base_agent, "get_llm",
                        lambda **kw: budgets.append(kw["max_tokens"]) or FakeLLM())
    FinancialAgent()
    assert budgets == [1200, 1500]


# ── Output parsing and validation ─────────────────────────

def test_parses_json_inside_code_fence_with_preamble():
    raw = 'Here you go:\n```json\n{"score": 55.5, "confidence": 0.6}\n```'
    result = parse_agent_output(raw)
    assert result["score"] == 55.5
    assert result["evidence"] == [] and result["red_flags"] == []


def test_string_red_flag_is_wrapped_in_list():
    result = parse_agent_output('{"score": 50, "confidence": 0.5, "red_flags": "D/E above 3"}')
    assert result["red_flags"] == ["D/E above 3"]


def test_extra_fields_are_preserved():
    result = parse_agent_output(
        '{"score": 50, "confidence": 0.5, "dealbreakers_triggered": ["x"], "news_sentiment": "MIXED"}'
    )
    assert result["dealbreakers_triggered"] == ["x"]
    assert result["news_sentiment"] == "MIXED"


@pytest.mark.parametrize("raw", ["", "   ", "I cannot score this vendor.", "[1, 2, 3]"])
def test_unusable_output_raises_instead_of_scoring_zero(raw):
    with pytest.raises(AgentOutputError):
        parse_agent_output(raw)


@pytest.mark.parametrize("raw", [
    '{"score": 150, "confidence": 0.9}',
    '{"score": -5, "confidence": 0.9}',
    '{"score": 80, "confidence": 85}',
    '{"score": true, "confidence": 0.9}',
    '{"score": "80", "confidence": 0.9}',
    '{"score": NaN, "confidence": 0.9}',
    '{"score": 80}',
    '{"confidence": 0.9}',
    '{"score": 80, "confidence": 0.9, "red_flags": {"a": 1}}',
])
def test_invalid_fields_raise(raw):
    with pytest.raises(AgentOutputError):
        parse_agent_output(raw)


@pytest.mark.parametrize("raw", ['{"score": 0, "confidence": 0}', '{"score": 100, "confidence": 1}'])
def test_boundary_values_are_valid(raw):
    parse_agent_output(raw)


def test_truncated_json_with_score_and_confidence_is_salvaged_and_flagged():
    raw = '{"score": 68, "confidence": 0.7, "explanation": "Healthy margins and low leverage", "red_flags": ["Neg'
    result = parse_agent_output(raw)
    assert (result["score"], result["confidence"], result["truncated"]) == (68.0, 0.7, True)
    assert result["explanation"] == "Healthy margins and low leverage"
    assert any("truncated" in f for f in result["red_flags"])


def test_truncated_json_without_confidence_raises():
    with pytest.raises(AgentOutputError):
        parse_agent_output('{"score": 68, "explanation": "Healthy margins and')


def test_truncated_json_with_out_of_range_score_raises():
    with pytest.raises(AgentOutputError):
        parse_agent_output('{"score": 680, "confidence": 0.7, "explanation": "Healthy margins and')


# ── Compliance agent ──────────────────────────────────────

@pytest.mark.parametrize("industry, expected", [
    ("Software", ["SOC2_Type2"]),
    ("Software - Application", ["SOC2_Type2"]),
    ("IT Services", ["SOC2_Type2"]),
    ("Cybersecurity", ["SOC2_Type2", "ISO27001"]),
    ("Financial Services", ["SOC1", "SOC2_Type2"]),
    ("Healthcare", ["SOC2_Type2"]),
    ("Manufacturing", ["ISO9001", "ISO14001"]),
    ("Underwater Basket Weaving", ["ISO9001"]),
    ("", ["ISO9001"]),
    (None, ["ISO9001"]),
])
def test_required_certs_by_industry(industry, expected):
    assert get_required_certs(industry) == expected


def test_required_cert_map_never_lists_regulations():
    from app.agents.compliance_agent import INDUSTRY_CERT_MAP
    regulations = {"GDPR", "CCPA", "SOX", "FCPA", "HIPAA"}
    for required in INDUSTRY_CERT_MAP.values():
        assert not regulations & set(required)


def compliance_prompt(**data):
    base = {"industry": "Software", "ofac_status": "clear"}
    return ComplianceAgent()._build_user_prompt("TestCo", {**base, **data}, "Verdict: fine")


def test_compliance_prompt_satisfied_and_missing_split():
    prompt = compliance_prompt(
        industry="Financial Services",
        certifications_found=["SOC2_Type2"],
        certification_evidence_tiers={"SOC2_Type2": "VERIFIED_REGISTRY"},
    )
    assert "Satisfied: ['SOC2_Type2']" in prompt
    assert "Missing:   ['SOC1']" in prompt
    assert "  - SOC2_Type2 [VERIFIED_REGISTRY]" in prompt


def test_compliance_prompt_never_lists_regulations_as_missing():
    prompt = compliance_prompt(certifications_found=[], regulatory_frameworks_disclosed=["GDPR", "CCPA"])
    missing_line = next(l for l in prompt.splitlines() if l.strip().startswith("Missing:"))
    assert "GDPR" not in missing_line and "CCPA" not in missing_line
    assert "['GDPR', 'CCPA']" in prompt   # shown under regulatory frameworks


def test_compliance_prompt_cert_without_tier_defaults_to_corroborated():
    assert "  - ISO27001 [CORROBORATED]" in compliance_prompt(certifications_found=["ISO27001"])


def test_compliance_prompt_uses_verdict_line_from_reasoning():
    prompt = ComplianceAgent()._build_user_prompt(
        "TestCo", {"industry": "Software"}, "Line one.\n**Verdict:** acceptable risk\nTrailing"
    )
    assert "Your analysis conclusion: Verdict: acceptable risk" in prompt


@pytest.mark.parametrize("status, expected_text, expected_json", [
    ("clear", "CLEAR", '"sanctions_clear": true'),
    ("match", "MATCH", '"sanctions_clear": false'),
    ("possible_match", "POSSIBLE MATCH", '"sanctions_clear": null'),
    ("unavailable", "NOT COMPLETED", '"sanctions_clear": null'),
    (None, "NOT COMPLETED", '"sanctions_clear": null'),
    ("something-new", "NOT COMPLETED", '"sanctions_clear": null'),
])
def test_compliance_prompt_shows_ofac_status(status, expected_text, expected_json):
    prompt = compliance_prompt(ofac_status=status)
    assert f"OFAC screening: {expected_text}" in prompt
    assert f'{expected_json}, "certifications_valid"' in prompt


def test_compliance_prompt_missing_ofac_status_is_not_clear():
    prompt = ComplianceAgent()._build_user_prompt("TestCo", {"industry": "Software"}, "Verdict: fine")
    assert "OFAC screening: NOT COMPLETED" in prompt


# ── Strategic and reputation agents ───────────────────────

def test_strategic_prompt_lists_client_priorities_dealbreakers_and_contract(fake_llm):
    asyncio.run(run_strategic_agent(make_state()))
    prompt = fake_llm.scoring_messages[1].content
    for item in CLIENT_PROFILE["priorities"] + CLIENT_PROFILE["dealbreakers"] + CLIENT_PROFILE["nice_to_have"]:
        assert f"- {item}" in prompt
    assert "CLIENT: Acme Manufacturing Corp" in prompt
    assert "$5,000,000 over 3 years" in prompt


def test_reputation_agent_pulls_news_from_compliance_data(fake_llm):
    state = make_state()
    asyncio.run(run_reputation_agent(state))
    prompt = fake_llm.scoring_messages[1].content
    assert "- [Reuters] TestCo wins NASA contract | Multi-year deal" in prompt
    assert state["reputation_data"]["recent_news"] == state["compliance_data"]["recent_news"]


def test_reputation_prompt_without_news_says_so(fake_llm):
    state = make_state()
    state["compliance_data"]["recent_news"] = []
    asyncio.run(run_reputation_agent(state))
    assert "No news articles found." in fake_llm.scoring_messages[1].content


def test_financial_prompt_includes_only_key_numeric_fields(fake_llm):
    state = make_state()
    state["financial_data"]["some_internal_blob"] = "x" * 50
    asyncio.run(run_financial_agent(state))
    prompt = fake_llm.scoring_messages[1].content
    assert '"net_margin": 13.9' in prompt
    assert "some_internal_blob" not in prompt


# ── Live: real Groq calls ─────────────────────────────────

@pytest.mark.live
@pytest.mark.parametrize("name, run_agent, system_prompt", AGENT_CASES, ids=AGENT_IDS)
def test_live_agent_output_passes_validation(name, run_agent, system_prompt):
    if os.environ.get("GROQ_API_KEY", "").startswith("test-key"):
        pytest.skip("GROQ_API_KEY not configured")
    result = asyncio.run(run_agent(make_state()))
    assert 0 <= result["score"] <= 100
    assert 0 <= result["confidence"] <= 1
    assert isinstance(result["red_flags"], list)
