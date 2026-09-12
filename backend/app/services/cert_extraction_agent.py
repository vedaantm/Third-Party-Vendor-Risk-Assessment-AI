# backend/app/services/cert_extraction_agent.py

"""
Certification Extraction Agent — batched + retry version.

Key changes from original:
- Pages are processed in batches of 2 (not all at once) to stay within
  the per-minute token budget.
- Each batch call retries up to 3 times on rate limit, waiting the exact
  duration Groq specifies in the error message.
- Per-page text capped at 2,000 chars (down from 4,000) — cert claims
  are always near the top of a compliance page.
- Results from all batches are merged and deduplicated.
- Daily limit (hours-long wait) is detected and handled gracefully
  rather than blocking the entire evaluation.
"""

import asyncio
import json
import re

from app.config_watsonx import get_llm
from langchain_core.messages import SystemMessage, HumanMessage

BATCH_SIZE = 2
MAX_CHARS_PER_PAGE = 2000
MAX_RETRY_WAIT_S = 90

SYSTEM_PROMPT = """You are a compliance and security certification analyst.
Read raw text scraped from a company's website or SEC filing and identify
which industry-standard certifications the company actually holds.

CRITICAL DISTINCTION — CERTIFICATIONS vs REGULATIONS:

CERTIFICATIONS are voluntary standards a company obtains by passing an
independent audit or assessment. Examples: ISO 27001, SOC 2, FedRAMP.

REGULATIONS are laws that companies are legally required to comply with.
Examples: GDPR, CCPA, SOX, FCPA, HIPAA.

RULES:
1. NEVER report GDPR, CCPA, SOX, or FCPA in certifications_found.
   These are laws, not certifications. Report them in
   regulatory_frameworks_disclosed instead.

2. HIPAA: only report in certifications_found if the company explicitly
   states it has obtained a third-party HIPAA compliance attestation —
   not merely that it "complies with HIPAA" as a regulatory obligation.

3. Only report certifications where the company is describing ITS OWN
   credential — not a competitor's, not a training topic, not a standard
   it helps customers achieve, and NOT a regulatory framework it helps
   clients comply with.

4. Real-world phrasing variants to recognize:
   - "SOC Type I and Type II"      → SOC1 AND SOC2_Type2
   - "ISO/IEC 27001:2022"          → ISO27001
   - "SSAE 18 / ISAE 3402"         → typically paired with SOC1 or SOC2
   - "PIMS certified to ISO 27701" → ISO27701
   - "CSA STAR Level 2"            → CSA_STAR
   - "ISO/IEC 27017"               → ISO27017
   - "ISO/IEC 27018"               → ISO27018

CERTIFICATIONS to check for:
SOC1, SOC2_Type1, SOC2_Type2, SOC3,
ISO27001, ISO27701, ISO27017, ISO27018,
ISO9001, ISO14001, ISO45001, ISO13485,
ISO22301, ISO20000, ISO42001,
PCI_DSS, FedRAMP, NIST_CSF, CSA_STAR

REGULATIONS (report in regulatory_frameworks_disclosed only):
GDPR, CCPA, SOX, FCPA, HIPAA

CRITICAL GROUNDING RULES:
1. Red flags must cite an explicit data point. Never generate a finding
   from an absence of data or a general concern.
2. NEVER confuse benchmark thresholds with vendor values.
3. If a data point is not provided, say "not provided" — do not infer.
4. Every sentence must be traceable to a specific source in brackets.

Respond ONLY with valid JSON, no preamble:
{
  "certifications_found": ["ISO27001", "SOC2_Type2"],
  "evidence": [
    "ISO27001 — page states 'ISO/IEC 27001:2022 certified' [source: <url>]"
  ],
  "regulatory_frameworks_disclosed": ["GDPR"],
  "uncertain": [],
  "explicitly_not_mentioned": []
}
"""

REGULATIONS = {"GDPR", "CCPA", "SOX", "FCPA"}


def _parse_wait_seconds(error_str: str) -> float:
    hours   = re.search(r"(\d+)h", error_str)
    minutes = re.search(r"(\d+)m(?!s)", error_str)
    seconds = re.search(r"(\d+(?:\.\d+)?)s(?!\w)", error_str)
    millis  = re.search(r"(\d+(?:\.\d+)?)ms", error_str)
    total = 0.0
    if hours:   total += float(hours.group(1)) * 3600
    if minutes: total += float(minutes.group(1)) * 60
    if seconds: total += float(seconds.group(1))
    if millis and not seconds: total += float(millis.group(1)) / 1000
    return total if total > 0 else 10.0


def _empty_result() -> dict:
    return {
        "certifications_found": [],
        "evidence": [],
        "regulatory_frameworks_disclosed": [],
        "uncertain": [],
        "explicitly_not_mentioned": [],
    }


def _parse_llm_output(raw: str) -> dict:
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return _empty_result()


def _sanitize(result: dict) -> dict:
    leaked = [c for c in result.get("certifications_found", []) if c in REGULATIONS]
    if leaked:
        result["certifications_found"] = [
            c for c in result["certifications_found"] if c not in REGULATIONS
        ]
        existing = set(result.get("regulatory_frameworks_disclosed", []))
        result["regulatory_frameworks_disclosed"] = list(existing | set(leaked))
    return result


async def _extract_batch(batch: list[dict], attempt_label: str = "") -> dict:
    # llm = get_llm(model_id="llama-3.1-8b-instant", temperature=0.0)
    llm = get_llm(model_id="openai/gpt-oss-20b", temperature=0.0)
    combined = "\n\n".join(
        f"--- SOURCE: {p['url']} ---\n{p['text'][:MAX_CHARS_PER_PAGE]}"
        for p in batch
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Extract certifications:\n\n{combined}"),
    ]

    for attempt in range(4):
        try:
            response = await llm.ainvoke(messages)
            result = _parse_llm_output(response.content.strip())
            return _sanitize(result)
        except Exception as e:
            error_str = str(e)
            if "429" not in error_str and "rate_limit" not in error_str:
                raise
            wait = _parse_wait_seconds(error_str)
            if wait >= MAX_RETRY_WAIT_S:
                print(
                    f"  → Daily token limit reached (Groq says wait {wait:.0f}s). "
                    f"Returning empty — run again after limit resets."
                )
                return _empty_result()
            wait = max(wait, 3.0) + 1
            print(
                f"  → Rate limit hit{attempt_label}, "
                f"waiting {wait:.0f}s (attempt {attempt + 1}/3)..."
            )
            await asyncio.sleep(wait)

    print("  → All retries exhausted — returning empty for this batch")
    return _empty_result()


def _merge(results: list[dict]) -> dict:
    merged_certs, merged_evidence, merged_regulatory, merged_uncertain = [], [], [], []
    seen_certs = set()
    for r in results:
        for c in r.get("certifications_found", []):
            if c not in seen_certs:
                seen_certs.add(c)
                merged_certs.append(c)
        merged_evidence.extend(r.get("evidence", []))
        for reg in r.get("regulatory_frameworks_disclosed", []):
            if reg not in merged_regulatory:
                merged_regulatory.append(reg)
        merged_uncertain.extend(r.get("uncertain", []))
    return {
        "certifications_found":            merged_certs,
        "evidence":                        merged_evidence,
        "regulatory_frameworks_disclosed": merged_regulatory,
        "uncertain":                       merged_uncertain,
        "explicitly_not_mentioned":        [],
    }


async def extract_certifications(pages: list[dict]) -> dict:
    if not pages:
        return _empty_result()
    batch_results = []
    total_batches = -(-len(pages) // BATCH_SIZE)  # ceiling division
    for i in range(0, len(pages), BATCH_SIZE):
        batch = pages[i : i + BATCH_SIZE]
        label = f" (batch {i // BATCH_SIZE + 1}/{total_batches})"
        print(f"  → Extracting certs from pages {i+1}–{min(i+BATCH_SIZE, len(pages))}")
        result = await _extract_batch(batch, attempt_label=label)
        batch_results.append(result)
        if i + BATCH_SIZE < len(pages):
            await asyncio.sleep(5)
    return _merge(batch_results)