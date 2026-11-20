"""
Knowledge-base context for agent prompts (app/services/rag_service.py).

Most tests swap the three Chroma collections for fakes, so they check the
retrieval rules (which collections each domain sees, self-exclusion of the
vendor's own past evaluation, failure isolation) without embedding anything.
One round-trip test uses a throwaway collection in the test Chroma directory
that conftest.py sets up; nothing here opens backend/chroma_db.
"""

import uuid

import pytest

from app.services import rag_service


class FakeCollection:
    def __init__(self, docs=None, metas=None, count=0, error=None):
        self.docs = docs or []
        self.metas = metas if metas is not None else [{} for _ in self.docs]
        self._count = count
        self.error = error
        self.queries = []
        self.upserts = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if self.error:
            raise self.error
        return {"documents": [self.docs], "metadatas": [self.metas]}

    def count(self):
        if self.error:
            raise self.error
        return self._count

    def upsert(self, **kwargs):
        if self.error:
            raise self.error
        self.upserts.append(kwargs)


@pytest.fixture
def collections(monkeypatch):
    def install(benchmarks=None, compliance=None, evaluations=None):
        fakes = {
            "benchmarks": benchmarks or FakeCollection(),
            "compliance": compliance or FakeCollection(),
            "evaluations": evaluations or FakeCollection(),
        }
        monkeypatch.setattr(rag_service, "_benchmarks", fakes["benchmarks"])
        monkeypatch.setattr(rag_service, "_compliance_kb", fakes["compliance"])
        monkeypatch.setattr(rag_service, "_evaluations", fakes["evaluations"])
        return fakes

    return install


def past(*vendors):
    """A FakeCollection of past evaluations for the given vendor names."""
    metas = [
        {"vendor_name": v, "recommendation": "APPROVE", "composite_score": 80.0 + i}
        for i, v in enumerate(vendors)
    ]
    return FakeCollection(
        docs=[f"doc {v}" for v in vendors], metas=metas, count=len(vendors)
    )


# ── _exclude_vendor ───────────────────────────────────────


def test_exclude_vendor_drops_own_evaluations_case_and_space_insensitive():
    docs = ["a", "b", "c", "d"]
    metas = [
        {"vendor_name": "Okta"},
        {"vendor_name": "  OKTA "},
        {"vendor_name": "Acme"},
        {"vendor_name": "Beta"},
    ]
    assert rag_service._exclude_vendor(docs, metas, " okta", limit=5) == (
        ["c", "d"],
        [{"vendor_name": "Acme"}, {"vendor_name": "Beta"}],
    )


def test_exclude_vendor_keeps_rank_order_and_applies_limit_after_exclusion():
    docs = ["okta", "a", "b", "c"]
    metas = [{"vendor_name": n} for n in ("Okta", "A", "B", "C")]
    kept_docs, _ = rag_service._exclude_vendor(docs, metas, "Okta", limit=2)
    assert kept_docs == ["a", "b"]


def test_exclude_vendor_tolerates_missing_metadata():
    docs, metas = rag_service._exclude_vendor(["x", "y"], [None, {}], "Okta", limit=2)
    assert docs == ["x", "y"] and metas == [None, {}]


# ── retrieve_context ──────────────────────────────────────


def test_benchmark_query_is_filtered_to_financial_benchmarks(collections):
    fakes = collections(benchmarks=FakeCollection(docs=["Software D/E median 0.8"]))
    context = rag_service.retrieve_context("Okta", "Software", "financial")
    (query,) = fakes["benchmarks"].queries
    assert query["where"] == {"type": "financial_benchmark"}
    assert query["n_results"] == 1
    assert (
        "Software" in query["query_texts"][0] and "financial" in query["query_texts"][0]
    )
    assert "INDUSTRY BENCHMARKS (Software):\nSoftware D/E median 0.8" in context


@pytest.mark.parametrize(
    "domain, sees_compliance",
    [
        ("compliance", True),
        ("financial", True),
        ("strategic", True),
        ("operational", False),
        ("reputation", False),
    ],
)
def test_compliance_requirements_only_for_relevant_domains(
    collections, domain, sees_compliance
):
    fakes = collections(compliance=FakeCollection(docs=["Software needs SOC2_Type2"]))
    context = rag_service.retrieve_context("Okta", "Software", domain)
    assert bool(fakes["compliance"].queries) is sees_compliance
    assert ("COMPLIANCE REQUIREMENTS (Software)" in context) is sees_compliance


def test_past_evaluations_skipped_when_store_is_empty(collections):
    fakes = collections(benchmarks=FakeCollection(docs=["bench"]))
    context = rag_service.retrieve_context("Okta", "Software", "financial")
    assert fakes["evaluations"].queries == []
    assert "PAST EVALUATIONS" not in context


def test_past_evaluations_never_anchor_on_the_vendors_own_score(collections):
    fakes = collections(evaluations=past("Okta", "Acme", "okta", "Beta", "Gamma"))
    context = rag_service.retrieve_context("Okta", "Software", "financial")

    (query,) = fakes["evaluations"].queries
    assert (
        "okta" not in query["query_texts"][0].lower()
    )  # query is industry/domain only
    assert query["n_results"] == 5  # over-fetch, capped by store size
    assert "- Okta:" not in context and "- okta:" not in context
    assert (
        "PAST EVALUATIONS (benchmarks):\n- Acme: APPROVE | Score: 81/100\n- Beta: APPROVE | Score: 83/100"
        in context
    )
    assert "Gamma" not in context  # PAST_EVAL_LIMIT


def test_past_evaluation_fetch_is_capped(collections):
    evaluations = past(*[f"V{i}" for i in range(3)])
    evaluations._count = 500
    fakes = collections(evaluations=evaluations)
    rag_service.retrieve_context("Okta", "Software", "financial")
    assert fakes["evaluations"].queries[0]["n_results"] == rag_service.PAST_EVAL_FETCH


def test_only_own_past_evaluations_adds_no_section(collections):
    collections(
        benchmarks=FakeCollection(docs=["bench"]), evaluations=past("Okta", "OKTA")
    )
    assert "PAST EVALUATIONS" not in rag_service.retrieve_context(
        "Okta", "Software", "financial"
    )


def test_past_evaluation_with_missing_metadata_uses_placeholders(collections):
    collections(evaluations=FakeCollection(docs=["d"], metas=[{}], count=1))
    assert "- ?: ? | Score: 0/100" in rag_service.retrieve_context(
        "Okta", "Software", "financial"
    )


def test_context_is_wrapped_in_a_labelled_block(collections):
    collections(
        benchmarks=FakeCollection(docs=["bench"]),
        compliance=FakeCollection(docs=["reqs"]),
    )
    context = rag_service.retrieve_context("Okta", "Software", "compliance")
    rule = "=" * 50
    assert context.startswith(
        f"\n\n{rule}\nKNOWLEDGE BASE CONTEXT (use as benchmarks):\n{rule}\n"
    )
    assert context.endswith(f"\n{rule}")
    assert context.index("INDUSTRY BENCHMARKS") < context.index(
        "COMPLIANCE REQUIREMENTS"
    )


def test_no_context_returns_empty_string(collections):
    collections()
    assert rag_service.retrieve_context("Okta", "Software", "compliance") == ""


def test_one_failing_collection_does_not_drop_the_others(collections):
    collections(
        benchmarks=FakeCollection(error=RuntimeError("index corrupt")),
        compliance=FakeCollection(docs=["reqs"]),
        evaluations=past("Acme"),
    )
    context = rag_service.retrieve_context("Okta", "Software", "compliance")
    assert "INDUSTRY BENCHMARKS" not in context
    assert "COMPLIANCE REQUIREMENTS (Software):\nreqs" in context
    assert "- Acme: APPROVE" in context


def test_failing_past_evaluation_query_keeps_benchmarks(collections):
    evaluations = past("Acme")
    evaluations.query = lambda **kw: (_ for _ in ()).throw(RuntimeError("query failed"))
    collections(benchmarks=FakeCollection(docs=["bench"]), evaluations=evaluations)
    context = rag_service.retrieve_context("Okta", "Software", "operational")
    assert "INDUSTRY BENCHMARKS (Software):\nbench" in context
    assert "PAST EVALUATIONS" not in context


# ── store_evaluation ──────────────────────────────────────

RESULT = {
    "vendor_name": "Okta",
    "recommendation": "REVIEW",
    "composite_score": 72,
    "risk_score": 30,
    "fit_score": 75,
    "confidence": 0.85,
    "agent_scores": {
        "financial": 70,
        "compliance": 80,
        "operational": 65,
        "reputation": 72,
        "strategic": 74,
    },
    "red_flags": ["Data breach mentioned in news", "Low current ratio"],
    "final_explanation": "x" * 1000,
}


def test_store_evaluation_writes_document_and_typed_metadata(collections):
    fakes = collections()
    rag_service.store_evaluation("eval-1", RESULT)
    (upsert,) = fakes["evaluations"].upserts
    assert upsert["ids"] == ["eval-1"]
    assert upsert["metadatas"] == [
        {
            "vendor_name": "Okta",
            "recommendation": "REVIEW",
            "composite_score": 72.0,
            "risk_score": 30.0,
            "fit_score": 75.0,
            "red_flag_count": 2,
        }
    ]
    assert all(
        isinstance(upsert["metadatas"][0][k], float)
        for k in ("composite_score", "risk_score", "fit_score")
    )
    (doc,) = upsert["documents"]
    assert doc.startswith(
        "Vendor: Okta\nRecommendation: REVIEW\nComposite Score: 72/100"
    )
    assert "Confidence: 85%" in doc
    assert "Financial=70" in doc and "Strategic=74" in doc
    assert "Red Flags: Data breach mentioned in news, Low current ratio" in doc
    assert doc.endswith("Summary: " + "x" * 400)  # explanation truncated


def test_store_evaluation_defaults_for_missing_fields(collections):
    fakes = collections()
    rag_service.store_evaluation("eval-2", {})
    (upsert,) = fakes["evaluations"].upserts
    meta = upsert["metadatas"][0]
    assert meta["vendor_name"] == "unknown" and meta["recommendation"] == "UNKNOWN"
    assert meta["composite_score"] == 0.0 and meta["red_flag_count"] == 0
    doc = upsert["documents"][0]
    assert "Red Flags: None" in doc and "Financial=N/A" in doc


def test_store_evaluation_failure_is_swallowed(collections):
    collections(evaluations=FakeCollection(error=RuntimeError("disk full")))
    rag_service.store_evaluation("eval-3", RESULT)  # must not raise


# ── get_collection_stats ──────────────────────────────────


def test_collection_stats_ok(collections):
    collections(
        benchmarks=FakeCollection(count=5),
        compliance=FakeCollection(count=3),
        evaluations=FakeCollection(count=7),
    )
    stats = rag_service.get_collection_stats()
    assert stats["status"] == "ok"
    assert (
        stats["vendor_evaluations"],
        stats["industry_benchmarks"],
        stats["compliance_requirements"],
    ) == (7, 5, 3)
    assert stats["chroma_path"] == rag_service.CHROMA_PATH


def test_collection_stats_reports_error(collections):
    collections(evaluations=FakeCollection(error=RuntimeError("locked")))
    assert rag_service.get_collection_stats()["status"] != "ok"


# ── Round trip through a real (throwaway) Chroma collection ─


@pytest.fixture
def real_evaluations(monkeypatch):
    name = f"test_evaluations_{uuid.uuid4().hex[:12]}"
    collection = rag_service._client.get_or_create_collection(
        name=name,
        embedding_function=rag_service._embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )
    monkeypatch.setattr(rag_service, "_evaluations", collection)
    yield collection
    rag_service._client.delete_collection(name)


def test_stored_evaluations_are_retrieved_for_other_vendors_only(real_evaluations):
    assert "test-chroma" in rag_service.CHROMA_PATH  # never backend/chroma_db
    rag_service.store_evaluation(
        "rt-okta", {**RESULT, "vendor_name": "Okta", "composite_score": 40}
    )
    rag_service.store_evaluation(
        "rt-acme", {**RESULT, "vendor_name": "Acme Cloud", "composite_score": 88}
    )
    rag_service.store_evaluation(
        "rt-okta", {**RESULT, "vendor_name": "Okta", "composite_score": 45}
    )  # upsert
    assert real_evaluations.count() == 2

    context = rag_service.retrieve_context("Okta", "Software", "financial")
    assert "- Acme Cloud: REVIEW | Score: 88/100" in context
    assert "- Okta:" not in context
