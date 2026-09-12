import json
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

        try:
            response = await self.llm.ainvoke(messages)
            result = self._parse_json_response(response.content)
            result["reasoning"] = reasoning
            return result

        except Exception as e:
            error_str = str(e)

            # CRITICAL: Re-raise rate limit errors so the retry wrapper
            # in routes.py can see them and retry the whole agent.
            # Previously these were silently swallowed here, returning
            # score: 0.0, and routes.py never knew to retry.
            if "429" in error_str or "rate_limit_exceeded" in error_str:
                raise

            # All other errors (JSON parse, timeout, etc.) return
            # a graceful fallback so one bad agent doesn't kill the evaluation
            print(f"  [WARNING] Agent Step 2 failed: {error_str[:200]}")
            return {
                "score": 0.0,
                "explanation": f"Agent execution failed: {error_str[:200]}",
                "evidence": [],
                "confidence": 0.0,
                "red_flags": [f"Agent error — check logs"],
                "reasoning": reasoning,
            }

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
            "ofac_clear", "news_red_flags",
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

        if not raw or not raw.strip():
            print("  [WARNING] Empty Step 2 response")
            return {
                "score": 0.0, "explanation": "Empty model response",
                "evidence": [], "confidence": 0.0,
                "red_flags": ["Step 2 empty — context overflow likely"],
            }

        # Try full JSON parse first
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start != -1 and end > start:
            try:
                result = json.loads(cleaned[start:end])
                return result
            except json.JSONDecodeError:
                pass

        # JSON was truncated — salvage the score at minimum
        # The model produced the right answer, just got cut off before closing }
        score_match      = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', raw)
        confidence_match = re.search(r'"confidence"\s*:\s*(\d+(?:\.\d+)?)', raw)
        explanation_match = re.search(r'"explanation"\s*:\s*"([^"]{10,})"', raw)

        if score_match:
            score = float(score_match.group(1))
            print(f"  [INFO] Partial JSON — salvaged score: {score}")
            return {
                "score":       score,
                "explanation": explanation_match.group(1)[:300] if explanation_match else "Truncated response — score salvaged",
                "evidence":    [],
                "confidence":  float(confidence_match.group(1)) if confidence_match else 0.75,
                "red_flags":   [],
            }

        # Nothing salvageable
        print(f"  [WARNING] Could not parse Step 2 output: {raw[:200]}")
        return {
            "score": 0.0, "explanation": f"Parse failed: {raw[:150]}",
            "evidence": [], "confidence": 0.0,
            "red_flags": ["JSON parse error"],
        }