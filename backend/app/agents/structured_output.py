# backend/app/agents/structured_output.py

"""
The specialist agents' scoring output, requested as structured output.

The scoring call passes AgentOutput to the model as a JSON schema
(Groq response_format=json_schema), so the API returns a JSON object of
that shape instead of free text we have to search for braces in.
validate_agent_output still runs on the result as a second check — the
meta-agent relies on its exact rules (no clamping, lists normalised).

Two ways the schema path can come up short, both handed to the old text
parser (parse_agent_output), which keeps its truncation salvage until the
Phase 2 evals show it can go:

  - Groq rejects output that is not valid JSON for the schema — usually
    because max_tokens cut it off — with 400 json_validate_failed and the
    partial text in 'failed_generation'.
  - The JSON arrives but does not fit the model (e.g. score 850); the raw
    text goes to the parser, whose validation raises AgentOutputError.

AGENT_STRUCTURED_OUTPUT=false switches back to the plain text call, so the
two modes can be compared in evals.
"""

from collections.abc import Callable

from pydantic import BaseModel, Field

from app.log import get_logger
from app.settings import settings

log = get_logger(__name__)


class AgentOutput(BaseModel):
    """What every specialist agent's scoring pass must return."""

    score: float = Field(ge=0, le=100, description="0-100, higher means lower risk")
    explanation: str = Field(description="One sentence")
    evidence: list[str] = Field(description="Facts the score rests on, with sources")
    confidence: float = Field(ge=0, le=1, description="0.0-1.0")
    red_flags: list[str] = Field(description="Concerns a buyer must see; empty if none")


def _failed_generation(error: Exception) -> str | None:
    """The partial output Groq attaches to a json_validate_failed 400, if this is one."""
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return None
    detail = body.get("error", body)
    if not isinstance(detail, dict) or detail.get("code") != "json_validate_failed":
        return None
    return detail.get("failed_generation") or ""


async def run_scoring_call(llm, messages: list, parse: Callable[[str], dict],
                           agent: str = "") -> dict:
    """
    Make the scoring call and return a validated output dict.

    parse is the text parser (BaseAgent._parse_json_response): it validates
    and raises AgentOutputError when nothing trustworthy can be recovered.
    Rate-limit and other API errors propagate, for the caller's retry.
    """
    if not settings.agent_structured_output:
        response = await llm.ainvoke(messages)
        return parse(response.content)

    # Imported here: base_agent imports this module
    from app.agents.base_agent import validate_agent_output

    structured = llm.with_structured_output(AgentOutput, method="json_schema", include_raw=True)
    try:
        out = await structured.ainvoke(messages)
    except Exception as e:
        partial = _failed_generation(e)
        if partial is None:
            raise
        log.warning("agent.schema_rejected", agent=agent, reason="json_validate_failed",
                    partial_chars=len(partial))
        return parse(partial)

    if out.get("parsed") is not None:
        return validate_agent_output(out["parsed"].model_dump())

    log.warning("agent.schema_mismatch", agent=agent, error=str(out.get("parsing_error"))[:300])
    return parse(out["raw"].content)
