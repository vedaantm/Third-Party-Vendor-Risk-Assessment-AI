import json
import math
import re
from abc import ABC, abstractmethod
from langchain_core.messages import SystemMessage, HumanMessage
from app.config_watsonx import get_llm, PRIMARY_MODEL
from app.orchestrator.state import VendorRiskState


class BaseAgent(ABC):

    def __init__(self, model_id: str = PRIMARY_MODEL):
        # Two separate LLM instances with different token budgets.
        #
        # WHY TWO INSTANCES?
        # Step 1 (reasoning) needs 1,200 tokens for a thorough
        # 6-8 sentence chain-of-thought analysis with source citations.
        #
        # Step 2 (scoring) only needs ~600 tokens for the JSON output,
        # but it MUST be complete — a truncated JSON silently returns
        # score: 0.0 via the fallback, corrupting the entire evaluation.
        #
        # Using separate budgets means reasoning can be verbose without
        # ever stealing tokens from the JSON output step.
        self.reasoning_llm = get_llm(
            model_id=model_id, temperature=0.0, max_tokens=1200
        )
        self.scoring_llm = get_llm(
            model_id=model_id, temperature=0.0, max_tokens=1500
        )
        # Keep self.llm pointing to scoring for backward compatibility
        # with any subclass that calls self.llm directly
        self.llm = self.scoring_llm

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        pass

    @property
    @abstractmethod
    def domain_data_key(self) -> str:
        pass

    async def run(self, state: VendorRiskState) -> dict:
        domain_data = state.get(self.domain_data_key, {})
        vendor_name = state.get("vendor_name", "Unknown Vendor")

        # Step 1 — Chain of thought reasoning pass
        print(f"\n  → Step 1: Reasoning through {vendor_name}'s data...")
        reasoning = await self._get_reasoning(vendor_name, domain_data)

        print("\n── Chain-of-Thought Reasoning ──────────────────────────")
        print(reasoning)
        print("────────────────────────────────────────────────────────\n")

        # Step 2 — Scoring pass informed by the reasoning
        print(f"  → Step 2: Producing final score based on reasoning...")
        user_prompt = self._build_user_prompt(vendor_name, domain_data, reasoning)

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_prompt)
        ]

        # Errors propagate — including AgentOutputError for unusable output.
        # Previously any failure returned score 0.0 / confidence 0.0, which
        # the meta-agent counted as a real score at full weight, dragging the
        # composite down. Now the caller (routes.py) records the agent as
        # unavailable (None), its weight is redistributed, and the meta-agent
        # routes the evaluation to human review. Rate-limit errors also
        # propagate here, so the retry wrapper in routes.py can retry them.
        response = await self.llm.ainvoke(messages)
        result = self._parse_json_response(response.content)
        result["reasoning"] = reasoning
        return result

    async def _get_reasoning(self, vendor_name: str, domain_data: dict) -> str:
        # Extract sources if present
        sources = domain_data.get("_sources", {})
        sources_text = ""
        if sources:
            sources_text = "\n\nDATA SOURCES (cite these in your reasoning):\n"
            sources_text += "\n".join(
                f"  - {field}: {source}"
                for field, source in sources.items()
                if not field.startswith("_")
            )

        # Also include certification sources if present
        cert_sources = domain_data.get("certifications_sources", {})
        if cert_sources:
            sources_text += "\n\nCERTIFICATION SOURCES:\n"
            sources_text += "\n".join(
                f"  - {cert}: verified at {url}"
                for cert, url in cert_sources.items()
            )

        reasoning_prompt = f"""
    You are a senior analyst evaluating a vendor for enterprise procurement.

    Vendor: {vendor_name}
    Domain: {self.domain_data_key.replace('_data', '').upper()} ANALYSIS

    Data provided:
    {json.dumps(
        {k: v for k, v in domain_data.items()
        if not k.startswith('_') and k != 'certifications_sources'},
        indent=2
    )}
    {sources_text}

    Analyze this vendor strictly from your domain perspective.
    In 6-8 sentences cover the most important signals in your data.

    IMPORTANT: For every data point you mention, cite its source in brackets.
    Example: "The current ratio of 1.4 [Yahoo Finance / yfinance] indicates..."
    Example: "ISO27001 certification [verified at trust.microsoft.com] confirms..."

    Be direct. Cite actual numbers with their sources.
    End with a one-line verdict for the CPO.
    """
        messages = [HumanMessage(content=reasoning_prompt)]
        # Use reasoning_llm (1,200 tokens) — reasoning is verbose
        response = await self.reasoning_llm.ainvoke(messages)
        return response.content
    
    def _build_user_prompt(
        self,
        vendor_name: str,
        domain_data: dict,
        reasoning: str
    ) -> str:
        # Extract verdict line
        lines = reasoning.split("\n")
        verdict = ""
        for line in reversed(lines):
            line = line.strip().replace("**", "")
            if line and ("verdict" in line.lower() or "recommend" in line.lower()):
                verdict = line[:400]
                break
        if not verdict:
            verdict = lines[-1].strip()[:400] if lines else "Analysis complete."

        # Include key numeric fields — essential for financial agent to score correctly
        # Keep this list short to avoid context overflow
        key_fields = [
            # Financial
            "net_margin", "gross_margin", "revenue_growth", "debt_equity_ratio",
            "current_ratio", "credit_rating", "years_in_operation", "interest_coverage",
            # Operational
            "company_size", "ebitda", "industry",
            # Compliance
            "certifications_found", "certification_evidence_tiers",
            "ofac_status", "news_red_flags",
        ]
        key_data = {
            k: v for k, v in domain_data.items()
            if k in key_fields and v is not None
        }

        data_str = json.dumps(key_data, indent=2) if key_data else "No structured data available."

        return (
            f"Vendor: {vendor_name}\n"
            f"Your conclusion: {verdict}\n\n"
            f"Key data:\n{data_str}\n\n"
            f"Output ONLY this JSON (no other text):\n"
            f'{{"score": <0-100>, "explanation": "<one sentence>", '
            f'"evidence": [], "confidence": <0.0-1.0>, "red_flags": []}}'
        )

    def _parse_json_response(self, raw: str) -> dict:
        print(f"\n[STEP2 RAW]:\n{raw}\n[END STEP2]")
        return parse_agent_output(raw)


class AgentOutputError(Exception):
    """The model's scoring output was missing, unparseable, or out of range."""


def _as_list(value, field: str) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return value
    raise AgentOutputError(f"'{field}' must be a list, got {type(value).__name__}")


def _as_number(value, field: str, low: float, high: float) -> float:
    # bool is a subclass of int — reject it explicitly
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentOutputError(f"'{field}' must be a number, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or not (low <= value <= high):
        raise AgentOutputError(f"'{field}' must be in [{low}, {high}], got {value}")
    return value


def validate_agent_output(result) -> dict:
    """
    Enforce the contract the meta-agent relies on. Out-of-range values are
    rejected rather than clamped — a confidence of 85 or a score of 850 means
    the model misread the scale, and guessing the intent would hide that.
    """
    if not isinstance(result, dict):
        raise AgentOutputError(f"expected a JSON object, got {type(result).__name__}")
    if "score" not in result or "confidence" not in result:
        raise AgentOutputError("output is missing 'score' or 'confidence'")

    result["score"]       = _as_number(result["score"], "score", 0.0, 100.0)
    result["confidence"]  = _as_number(result["confidence"], "confidence", 0.0, 1.0)
    result["evidence"]    = _as_list(result.get("evidence"), "evidence")
    result["red_flags"]   = _as_list(result.get("red_flags"), "red_flags")
    result["explanation"] = str(result.get("explanation") or "")
    return result


def parse_agent_output(raw: str) -> dict:
    """
    Parse and validate the Step 2 scoring JSON. Raises AgentOutputError when
    nothing trustworthy can be recovered — never invents a score.
    """
    if not raw or not raw.strip():
        raise AgentOutputError("empty model response (context overflow likely)")

    # Try full JSON parse first
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    start = cleaned.find("{")
    end   = cleaned.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return validate_agent_output(json.loads(cleaned[start:end]))
        except json.JSONDecodeError:
            pass   # fall through to truncation salvage

    # JSON was truncated — salvage only if BOTH score and confidence made it
    # out. (The old fallback invented confidence = 0.75 when it was missing.)
    # Anything after the cut-off — including red flags — is lost, so the
    # salvaged result says so explicitly rather than looking clean.
    score_match       = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', raw)
    confidence_match  = re.search(r'"confidence"\s*:\s*(\d+(?:\.\d+)?)', raw)
    explanation_match = re.search(r'"explanation"\s*:\s*"([^"]{10,})"', raw)

    if score_match and confidence_match:
        print(f"  [INFO] Partial JSON — salvaged score: {score_match.group(1)}")
        return validate_agent_output({
            "score":       float(score_match.group(1)),
            "confidence":  float(confidence_match.group(1)),
            "explanation": explanation_match.group(1)[:300] if explanation_match
                           else "Truncated response — score salvaged",
            "evidence":    [],
            "red_flags":   ["Agent output was truncated — red flags may be incomplete"],
            "truncated":   True,
        })

    raise AgentOutputError(f"could not parse scoring output: {raw[:200]!r}")