# backend/app/services/rag_service.py

import os
import sys
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH = os.environ.get("CHROMA_PATH", "./chroma_db")

# The production vector store, relative to this file (backend/chroma_db).
REAL_CHROMA_DIR = Path(__file__).resolve().parents[2] / "chroma_db"


def _guard_chroma_path(path: str, under_pytest: bool) -> None:
    """
    Refuse to open the real backend/chroma_db while running under pytest.
    Importing this module seeds (writes to) the store, and backend/.env sets
    CHROMA_PATH to the real directory with override=True via
    app.db.database — so a test that imports the app could otherwise write
    to production data. Tests must point CHROMA_PATH at a throwaway directory
    (tests/conftest.py does).
    """
    if not under_pytest:
        return
    resolved = Path(path).resolve()
    real = REAL_CHROMA_DIR.resolve()
    if resolved == real or real in resolved.parents:
        raise RuntimeError(
            f"Refusing to open the real ChromaDB at {resolved} under pytest. "
            f"Set CHROMA_PATH to a temporary directory for tests."
        )


_guard_chroma_path(CHROMA_PATH, under_pytest="pytest" in sys.modules)

_embedding_fn = ONNXMiniLM_L6_V2()
_client = chromadb.PersistentClient(path=CHROMA_PATH)

# ── Three separate collections ────────────────────────────
# 1. Industry benchmarks — pre-populated, used from day one
# 2. Compliance requirements — pre-populated per industry
# 3. Past evaluations — grows over time

_benchmarks = _client.get_or_create_collection(
    name="industry_benchmarks",
    embedding_function=_embedding_fn,
    metadata={"hnsw:space": "cosine"}
)

_compliance_kb = _client.get_or_create_collection(
    name="compliance_requirements",
    embedding_function=_embedding_fn,
    metadata={"hnsw:space": "cosine"}
)

_evaluations = _client.get_or_create_collection(
    name="vendor_evaluations",
    embedding_function=_embedding_fn,
    metadata={"hnsw:space": "cosine"}
)


# ── Pre-populate benchmarks on first run ──────────────────

def _seed_benchmarks():
    """
    Seeds ChromaDB with industry benchmark data and compliance
    requirements. These give agents useful context from the very first
    evaluation.

    Upserts on every startup (ids are stable, so this is idempotent) — the
    old "skip if already seeded" check meant edits to these texts never
    reached an existing chroma_db volume.

    These texts must stay consistent with INDUSTRY_CERT_MAP and the
    compliance agent prompt: regulations (GDPR, CCPA, HIPAA, SOX) are never
    described as certifications, and no text gives platform scores for named
    vendors (that anchors the agents on a remembered number).
    """
    print("  → RAG: Seeding industry benchmarks...")

    benchmark_docs = [
        {
            "id": "bench_it_services",
            "text": """IT Services & Software industry benchmarks:
Financial: Typical current ratio 1.8-2.5.
Debt-to-equity ratio acceptable up to 1.5. Net margin healthy above 12%.
Revenue growth healthy above 8% YoY. EBITDA margin typically 18-25%.
Interest coverage ratio should exceed 5x for investment grade.
Red flags: D/E above 3, negative EBITDA, revenue decline two consecutive years.""",
            "metadata": {"industry": "IT Services", "type": "financial_benchmark"}
        },
        {
            "id": "bench_manufacturing",
            "text": """Manufacturing industry benchmarks:
Financial: Current ratio 1.4-2.0.
Debt-to-equity acceptable up to 2.0 due to capital intensity.
Net margin healthy above 7%. Revenue growth healthy above 5% YoY.
Gross margin typically 25-40%. Interest coverage should exceed 3x.
Red flags: D/E above 4, current ratio below 1.0, negative gross margin.
Supply chain risk elevated — operational resilience critical.""",
            "metadata": {"industry": "Manufacturing", "type": "financial_benchmark"}
        },
        {
            "id": "bench_financial_services",
            "text": """Financial Services industry benchmarks:
Higher regulatory scrutiny than most sectors.
Required certifications: SOC1 and SOC2 Type II. ISO27001 strongly preferred.
PCI-DSS required for any payment processing vendor.
Revenue concentration above 30% in one client is high risk.
Red flags: Any SEC/FINRA action, missing SOC2 Type II, credit rating below BBB.""",
            "metadata": {"industry": "Financial Services", "type": "financial_benchmark"}
        },
        {
            "id": "bench_healthcare",
            "text": """Healthcare industry benchmarks:
HIPAA is a regulation, not a certification — there is no official HIPAA
certification, so its absence from certifications_found is expected.
Vendors handling PHI must comply with HIPAA and sign a Business Associate
Agreement (BAA) before contract.
FDA 21 CFR Part 11 applies to software that manages regulated electronic records.
Required certification: SOC2 Type II. ISO27001 strongly preferred.
Operational resilience is critical given patient safety implications.
Red flags: Any FDA warning letter, HIPAA violation history.""",
            "metadata": {"industry": "Healthcare", "type": "financial_benchmark"}
        },
        {
            "id": "bench_logistics",
            "text": """Logistics & Supply Chain industry benchmarks:
Financial: Capital intensive sector.
On-time delivery rate should exceed 95% for top-tier vendors.
ISO 9001 and ISO 14001 commonly held. ISO 45001 for safety-critical.
Operational resilience critical — BCP and disaster recovery required.
Geographic coverage and last-mile capability are key differentiators.
Red flags: Delivery rate below 90%, no BCP documentation, key-person dependency.""",
            "metadata": {"industry": "Logistics", "type": "financial_benchmark"}
        },
    ]

    compliance_docs = [
        {
            "id": "comp_universal",
            "text": """Universal compliance requirements (all industries):
OFAC sanctions check is mandatory — any confirmed match = immediate reject.
Required certifications depend on the vendor's industry; there is no single
certification required of every vendor.
GDPR and CCPA are regulations, not certifications: vendors handling personal
data must comply with them, and they are never "missing certifications".
Anti-bribery policy (FCPA compliance) required for international vendors.
UBO declaration required under FinCEN Beneficial Ownership Rule.
Business registration verified via state Secretary of State database.
Credit rating below BB- is a financial red flag across all sectors.""",
            "metadata": {"industry": "all", "type": "compliance_requirement"}
        },
        {
            "id": "comp_it_services",
            "text": """IT Services compliance requirements:
SOC2 Type II: mandatory. Issued by registered CPA firm (verify via PCAOB).
ISO/IEC 27001: strongly preferred, often contractually required (not mandatory).
ISO 27701: preferred if vendor processes personal data at scale.
CSA STAR: cloud-specific certification, important for SaaS vendors.
Penetration testing reports: request last 12-month report.
Data processing agreement (DPA) must be signed before contract.
Incident response plan: required, test annually.""",
            "metadata": {"industry": "IT Services", "type": "compliance_requirement"}
        },
        {
            "id": "comp_manufacturing",
            "text": """Manufacturing compliance requirements:
ISO 9001: quality management, widely held in sector.
ISO 14001: environmental management, increasingly required.
ISO 45001: occupational health and safety.
Export control: ITAR/EAR compliance if defense-related components.
Modern slavery statement required if vendor has UK/California operations.
Product liability insurance minimum $5M recommended.
Supply chain audit rights clause in contract recommended.""",
            "metadata": {"industry": "Manufacturing", "type": "compliance_requirement"}
        },
    ]

    _benchmarks.upsert(
        ids=[d["id"] for d in benchmark_docs],
        documents=[d["text"] for d in benchmark_docs],
        metadatas=[d["metadata"] for d in benchmark_docs],
    )

    _compliance_kb.upsert(
        ids=[d["id"] for d in compliance_docs],
        documents=[d["text"] for d in compliance_docs],
        metadatas=[d["metadata"] for d in compliance_docs],
    )

    print(f"  ✅ RAG: Seeded {len(benchmark_docs)} benchmarks, "
          f"{len(compliance_docs)} compliance requirements")


# Run seeding at import time
_seed_benchmarks()


# ── Public functions ──────────────────────────────────────

PAST_EVAL_LIMIT = 2
PAST_EVAL_FETCH = 10


def _exclude_vendor(docs: list, metas: list, vendor_name: str, limit: int) -> tuple[list, list]:
    """Drop past evaluations of the vendor being evaluated, keep up to `limit`."""
    key = vendor_name.lower().strip()
    kept = [
        (doc, meta) for doc, meta in zip(docs, metas)
        if str((meta or {}).get("vendor_name", "")).lower().strip() != key
    ][:limit]
    return [d for d, _ in kept], [m for _, m in kept]


def retrieve_context(
    vendor_name: str,
    industry: str,
    domain: str,
    n_results: int = 2
) -> str:
    """
    Retrieves relevant context for a specific agent domain.
    Combines:
    - Industry benchmarks (always useful from day one)
    - Compliance requirements (always useful from day one)
    - Past similar evaluations (useful after a few runs)

    Returns a formatted string injected into the agent's prompt.
    """
    context_parts = []

    # 1. Industry benchmarks
    try:
        bench_query = f"{industry} {domain} financial benchmarks scores averages"
        bench_results = _benchmarks.query(
            query_texts=[bench_query],
            n_results=1,
            where={"type": "financial_benchmark"}
        )
        docs = bench_results.get("documents", [[]])[0]
        if docs:
            context_parts.append(
                f"INDUSTRY BENCHMARKS ({industry}):\n{docs[0]}"
            )
    except Exception as e:
        print(f"  ⚠ RAG benchmark retrieval failed: {e}")

    # 2. Compliance requirements (especially useful for compliance agent)
    if domain in ("compliance", "financial", "strategic"):
        try:
            comp_results = _compliance_kb.query(
                query_texts=[f"{industry} compliance requirements certifications"],
                n_results=1,
            )
            docs = comp_results.get("documents", [[]])[0]
            if docs:
                context_parts.append(
                    f"COMPLIANCE REQUIREMENTS ({industry}):\n{docs[0]}"
                )
        except Exception as e:
            print(f"  ⚠ RAG compliance retrieval failed: {e}")

    # 3. Past evaluations of OTHER vendors (only if we have some)
    #    The query used to include the vendor's own name, so its own previous
    #    evaluation was the top hit and each run anchored on the last score.
    #    Same-vendor results are now dropped; the query is industry/domain only.
    total_evals = _evaluations.count()
    if total_evals > 0:
        try:
            past_results = _evaluations.query(
                query_texts=[f"{industry} {domain} risk evaluation"],
                # Over-fetch so 2 remain after dropping same-vendor rows
                n_results=min(PAST_EVAL_FETCH, total_evals),
            )
            docs = past_results.get("documents", [[]])[0]
            metas = past_results.get("metadatas", [[]])[0]
            docs, metas = _exclude_vendor(docs, metas, vendor_name, PAST_EVAL_LIMIT)
            if docs:
                past_lines = ["PAST EVALUATIONS (benchmarks):"]
                for doc, meta in zip(docs, metas):
                    past_lines.append(
                        f"- {meta.get('vendor_name','?')}: "
                        f"{meta.get('recommendation','?')} | "
                        f"Score: {meta.get('composite_score',0):.0f}/100"
                    )
                context_parts.append("\n".join(past_lines))
        except Exception as e:
            print(f"  ⚠ RAG past evaluation retrieval failed: {e}")

    if not context_parts:
        return ""

    return (
        "\n\n" + "=" * 50 + "\n"
        "KNOWLEDGE BASE CONTEXT (use as benchmarks):\n" +
        "=" * 50 + "\n" +
        "\n\n".join(context_parts) +
        "\n" + "=" * 50
    )


def store_evaluation(evaluation_id: str, result: dict) -> None:
    """Saves a completed evaluation into ChromaDB."""
    vendor_name    = result.get("vendor_name", "unknown")
    recommendation = result.get("recommendation", "UNKNOWN")
    composite      = result.get("composite_score", 0)
    red_flags      = result.get("red_flags", [])

    document = f"""
Vendor: {vendor_name}
Recommendation: {recommendation}
Composite Score: {composite}/100
Risk Score: {result.get("risk_score", 0)}/100
Fit Score: {result.get("fit_score", 0)}/100
Confidence: {result.get("confidence", 0):.0%}
Agent Scores: Financial={result.get("agent_scores", {}).get("financial", "N/A")},
Compliance={result.get("agent_scores", {}).get("compliance", "N/A")},
Operational={result.get("agent_scores", {}).get("operational", "N/A")},
Reputation={result.get("agent_scores", {}).get("reputation", "N/A")},
Strategic={result.get("agent_scores", {}).get("strategic", "N/A")}
Red Flags: {", ".join(red_flags) if red_flags else "None"}
Summary: {result.get("final_explanation", "")[:400]}
""".strip()

    metadata = {
        "vendor_name":     vendor_name,
        "recommendation":  recommendation,
        "composite_score": float(composite),
        "risk_score":      float(result.get("risk_score", 0)),
        "fit_score":       float(result.get("fit_score", 0)),
        "red_flag_count":  len(red_flags),
    }

    try:
        _evaluations.upsert(
            ids=[evaluation_id],
            documents=[document],
            metadatas=[metadata],
        )
        print(f"  ✅ RAG: stored evaluation for {vendor_name}")
    except Exception as e:
        print(f"  ⚠ RAG: failed to store: {e}")


def get_collection_stats() -> dict:
    try:
        return {
            "vendor_evaluations":      _evaluations.count(),
            "industry_benchmarks":     _benchmarks.count(),
            "compliance_requirements": _compliance_kb.count(),
            "chroma_path":             CHROMA_PATH,
            "status":                  "ok",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}