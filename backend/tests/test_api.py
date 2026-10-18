"""
API endpoints and the background evaluation pipeline.

The app runs in-process through httpx's ASGI transport (no server, no
lifespan). get_db and the pipeline's AsyncSessionLocal point at a temporary
SQLite database. In endpoint tests the background pipeline is replaced by a
recorder; pipeline tests call the real function with every external
dependency (scrapers, cert discovery, agents, RAG, sleeps) faked.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

import main_api
from app.api import routes
from app.db.database import get_db
from app.db.models import CertCache, Evaluation, Job
from conftest import install_fake_redis, make_sessionmaker

app = main_api.app
REAL_PIPELINE = routes._run_evaluation_pipeline

CLIENT_PROFILE = {
    "company_name": "Acme Manufacturing Corp", "industry": "Manufacturing",
    "contract_value": 5_000_000, "contract_years": 3, "risk_appetite": "medium",
    "priorities": ["cloud infrastructure"], "dealbreakers": ["active OFAC sanctions"],
    "nice_to_have": ["ISO27001 certified"],
}
EVALUATE_BODY = {"vendor_name": "Okta", "client_profile": CLIENT_PROFILE}


# ── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def Session(db_url, monkeypatch):
    engine, Session = asyncio.run(make_sessionmaker(db_url))

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(routes, "AsyncSessionLocal", Session)
    yield Session
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def pipeline_calls(monkeypatch):
    """Endpoint tests must never run the real pipeline in the background."""
    calls = []

    async def record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(routes, "_run_evaluation_pipeline", record)
    return calls


def call(method, url, **kwargs):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, url, **kwargs)
    return asyncio.run(go())


def add_rows(Session, *rows):
    async def go():
        async with Session() as s:
            s.add_all(rows)
            await s.commit()
    asyncio.run(go())


def fetch(Session, model, **where):
    async def go():
        async with Session() as s:
            stmt = select(model).filter_by(**where)
            return (await s.execute(stmt)).scalars().all()
    return asyncio.run(go())


def ts(minutes_ago):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


# ── Root and health ───────────────────────────────────────

def test_root():
    body = call("GET", "/").json()
    assert body["status"] == "running" and body["docs"] == "/docs"


def test_basic_health():
    assert call("GET", "/health").json() == {"status": "ok"}


def test_full_health_with_redis_down(Session):
    body = call("GET", "/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "healthy"
    assert body["redis"]["status"] == "unavailable"


def test_full_health_with_redis_up(Session, monkeypatch):
    import fakeredis

    class FakeRedisWithInfo(fakeredis.FakeAsyncRedis):
        async def info(self, section=None):
            return {"redis_version": "7.2.4"}

    async def go():
        install_fake_redis(monkeypatch, FakeRedisWithInfo(decode_responses=True))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return (await c.get("/api/v1/health")).json()
    assert asyncio.run(go())["redis"]["status"] == "healthy"


def test_full_health_reports_database_error():
    class BrokenSession:
        async def execute(self, *args, **kwargs):
            raise ConnectionError("db unreachable")

    async def broken_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        body = call("GET", "/api/v1/health").json()
    finally:
        app.dependency_overrides.clear()
    assert body["database"].startswith("error:") and "db unreachable" in body["database"]


# ── POST /evaluate ────────────────────────────────────────

def test_evaluate_returns_202_creates_pending_job_and_queues_pipeline(Session, pipeline_calls):
    resp = call("POST", "/api/v1/evaluate", json=EVALUATE_BODY)
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"].startswith("job-") and body["evaluation_id"].startswith("eval-")
    assert body["status"] == "pending"
    assert body["poll_url"] == f"/api/v1/jobs/{body['job_id']}"

    [job] = fetch(Session, Job, job_id=body["job_id"])
    assert (job.status, job.progress, job.vendor_name) == ("pending", 0, "Okta")

    [queued] = pipeline_calls
    assert queued["job_id"] == body["job_id"]
    assert queued["evaluation_id"] == body["evaluation_id"]
    assert queued["client_profile"] == CLIENT_PROFILE
    assert queued["weights"]["financial"] == 0.30          # defaults applied


def test_evaluate_passes_custom_weights(Session, pipeline_calls):
    weights = {"financial": 0.2, "compliance": 0.4, "operational": 0.2,
               "reputation": 0.1, "strategic": 0.1}
    call("POST", "/api/v1/evaluate", json={**EVALUATE_BODY, "weights": weights})
    assert pipeline_calls[0]["weights"] == weights


@pytest.mark.parametrize("body", [
    {"vendor_name": "Okta"},                                                   # no client_profile
    {"client_profile": CLIENT_PROFILE},                                        # no vendor_name
    {**EVALUATE_BODY, "client_profile": {**CLIENT_PROFILE, "contract_value": "lots"}},
    {**EVALUATE_BODY, "client_profile": {k: v for k, v in CLIENT_PROFILE.items() if k != "priorities"}},
])
def test_evaluate_rejects_invalid_body(Session, pipeline_calls, body):
    assert call("POST", "/api/v1/evaluate", json=body).status_code == 422
    assert pipeline_calls == []


VALID_WEIGHTS = {"financial": 0.30, "compliance": 0.25, "operational": 0.20,
                 "reputation": 0.10, "strategic": 0.15}


def post_weights(weights):
    return call("POST", "/api/v1/evaluate", json={**EVALUATE_BODY, "weights": weights})


def test_evaluate_accepts_weights_with_float_rounding(Session, pipeline_calls):
    # 0.30 + 0.25 + 0.20 + 0.10 + 0.15 == 1.0000000000000002 in floating point
    assert post_weights(VALID_WEIGHTS).status_code == 202
    assert post_weights({**VALID_WEIGHTS, "financial": 0.3004, "strategic": 0.15}).status_code == 202


def test_evaluate_accepts_zero_weight_for_an_agent(Session):
    weights = {"financial": 0.5, "compliance": 0.5, "operational": 0.0,
               "reputation": 0.0, "strategic": 0.0}
    assert post_weights(weights).status_code == 202


def test_evaluate_rejects_negative_weights(Session, pipeline_calls):
    weights = {"financial": -1.0, "compliance": 1.0, "operational": 0.5,
               "reputation": 0.25, "strategic": 0.25}
    resp = post_weights(weights)
    assert resp.status_code == 422
    assert "finite and >= 0" in resp.text
    assert pipeline_calls == []


@pytest.mark.parametrize("scale", [0.9, 5.0, 0.0])
def test_evaluate_rejects_weights_not_summing_to_one(Session, scale):
    weights = {k: scale for k in VALID_WEIGHTS}
    resp = post_weights(weights)
    assert resp.status_code == 422
    assert "must sum to 1.0" in resp.text


def test_evaluate_rejects_unknown_weight_keys(Session):
    weights = {"finance": 0.3, "compliance": 0.25, "operational": 0.2,
               "reputation": 0.1, "strategic": 0.15}
    resp = post_weights(weights)
    assert resp.status_code == 422
    assert "finance" in resp.text and "financial" in resp.text


def test_evaluate_rejects_missing_weight_keys(Session):
    weights = {"financial": 0.5, "compliance": 0.5}
    resp = post_weights(weights)
    assert resp.status_code == 422
    assert "missing" in resp.text


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_evaluate_rejects_non_finite_weights_with_clean_422(Session, pipeline_calls, bad):
    # Not valid JSON, but Python's parser accepts it. The error body echoes
    # the input, so it must stay serialisable (was a 500 before the handler).
    import json as _json
    body = ('{"vendor_name": "Okta", "client_profile": %s, "weights": {"financial": %s, '
            '"compliance": 0.25, "operational": 0.2, "reputation": 0.1, "strategic": 0.15}}')
    resp = call("POST", "/api/v1/evaluate", content=body % (_json.dumps(CLIENT_PROFILE), bad),
                headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert "finite and >= 0" in resp.text
    assert pipeline_calls == []


def validation_errors(**body):
    """A real RequestValidationError built from the request model."""
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError
    try:
        routes.EvaluateRequest.model_validate(body)
    except ValidationError as e:
        return RequestValidationError(e.errors())
    raise AssertionError("body unexpectedly valid")


FINITE_INVALID_BODIES = [
    {"vendor_name": "Okta"},                                                  # missing field
    {**EVALUATE_BODY, "client_profile": {**CLIENT_PROFILE, "contract_years": "x"}},  # bad type
    {**EVALUATE_BODY, "weights": {"finance": 1.0}},                           # weights validator
    {**EVALUATE_BODY, "weights": {k: -0.2 for k in VALID_WEIGHTS}},
]


@pytest.mark.parametrize("body", FINITE_INVALID_BODIES)
def test_422_handler_is_byte_identical_to_fastapi_default_for_finite_input(body):
    from fastapi.exception_handlers import request_validation_exception_handler as default
    exc = validation_errors(**body)
    ours = asyncio.run(main_api.validation_exception_handler(None, exc))
    theirs = asyncio.run(default(None, exc))
    assert ours.status_code == theirs.status_code == 422
    assert ours.body == theirs.body


def test_422_handler_only_differs_for_non_finite_numbers():
    from fastapi.exception_handlers import request_validation_exception_handler as default
    exc = validation_errors(**{**EVALUATE_BODY, "weights": {**VALID_WEIGHTS, "financial": float("nan")}})
    with pytest.raises(ValueError, match="not JSON compliant"):
        asyncio.run(default(None, exc))          # FastAPI's default can't render it
    ours = asyncio.run(main_api.validation_exception_handler(None, exc))
    assert ours.status_code == 422 and b'"nan"' in ours.body


def test_json_safe_leaves_everything_but_non_finite_floats_alone():
    value = {"a": [1, 2.5, "NaN", None, True, {"b": -0.0}], "c": "inf"}
    assert main_api._json_safe(value) == value
    assert main_api._json_safe({"x": [float("inf"), float("-inf"), float("nan")]}) == \
        {"x": ["inf", "-inf", "nan"]}


def test_only_request_validation_errors_use_the_custom_handler():
    from fastapi.exceptions import RequestValidationError
    custom = {exc for exc, handler in app.exception_handlers.items()
              if handler is main_api.validation_exception_handler}
    assert custom == {RequestValidationError}


def test_standard_validation_errors_keep_fastapi_shape(Session):
    detail = call("POST", "/api/v1/evaluate", json={"vendor_name": "Okta"}).json()["detail"]
    assert detail[0]["loc"] == ["body", "client_profile"]
    assert detail[0]["type"] == "missing"


# ── GET /jobs/{id} and /jobs ──────────────────────────────

def test_job_status_pending_has_no_result(Session):
    add_rows(Session, Job(job_id="job-1", vendor_name="Okta", status="running",
                          current_step="Running financial analysis agent...", progress=40))
    body = call("GET", "/api/v1/jobs/job-1").json()
    assert (body["status"], body["progress"]) == ("running", 40)
    assert "result" not in body and "error" not in body


def test_job_status_complete_includes_result(Session):
    add_rows(Session, Job(job_id="job-2", vendor_name="Okta", status="complete",
                          progress=100, result={"recommendation": "REVIEW"}))
    assert call("GET", "/api/v1/jobs/job-2").json()["result"] == {"recommendation": "REVIEW"}


def test_job_status_failed_includes_error(Session):
    add_rows(Session, Job(job_id="job-3", vendor_name="Okta", status="failed",
                          error="RuntimeError: boom"))
    body = call("GET", "/api/v1/jobs/job-3").json()
    assert body["error"] == "RuntimeError: boom" and "result" not in body


def test_job_status_unknown_is_404(Session):
    assert call("GET", "/api/v1/jobs/job-nope").status_code == 404


def test_list_jobs_newest_first_with_limit(Session):
    add_rows(Session, *[Job(job_id=f"job-{i}", vendor_name=f"V{i}", created_at=ts(10 - i))
                        for i in range(5)])
    jobs = call("GET", "/api/v1/jobs", params={"limit": 3}).json()["jobs"]
    assert [j["job_id"] for j in jobs] == ["job-4", "job-3", "job-2"]
    assert all("result" not in j for j in jobs)


@pytest.mark.parametrize("path", ["/api/v1/jobs", "/api/v1/evaluations"])
@pytest.mark.parametrize("limit, status", [(1_000_000, 422), (101, 422), (0, 422),
                                           (-5, 422), (100, 200), (1, 200)])
def test_list_endpoints_bound_limit(Session, path, limit, status):
    assert call("GET", path, params={"limit": limit}).status_code == status


# ── /evaluations ──────────────────────────────────────────

def evaluation(eval_id, minutes_ago, **extra):
    return Evaluation(
        evaluation_id=eval_id, vendor_name="Okta", company_name="Acme",
        financial_score=80, compliance_score=70, composite_score=75, risk_score=74,
        fit_score=72, confidence=0.9, recommendation="REVIEW", red_flags=["[X] y"],
        final_explanation="summary", weights={"financial": 0.3},
        created_at=ts(minutes_ago), **extra,
    )


def test_list_evaluations_newest_first_with_limit(Session):
    add_rows(Session, evaluation("eval-old", 30), evaluation("eval-new", 1), evaluation("eval-mid", 10))
    evals = call("GET", "/api/v1/evaluations", params={"limit": 2}).json()["evaluations"]
    assert [e["evaluation_id"] for e in evals] == ["eval-new", "eval-mid"]
    assert evals[0]["scores"]["composite"] == 75


def test_get_evaluation(Session):
    add_rows(Session, evaluation("eval-1", 1))
    body = call("GET", "/api/v1/evaluations/eval-1").json()
    assert body["recommendation"] == "REVIEW" and body["red_flags"] == ["[X] y"]


def test_get_evaluation_404(Session):
    assert call("GET", "/api/v1/evaluations/eval-missing").status_code == 404


def test_report_includes_agent_breakdown_and_not_run(Session):
    outputs = {"financial": {"score": 80, "confidence": 0.9, "explanation": "ok",
                             "reasoning": "r", "evidence": ["e"], "red_flags": []},
               "compliance": None}
    add_rows(Session, evaluation("eval-r", 1, agent_outputs=outputs))
    body = call("GET", "/api/v1/evaluations/eval-r/report").json()
    assert body["evaluated_for"] == "Acme"
    assert body["agent_breakdown"]["financial"]["reasoning"] == "r"
    for name in ("compliance", "operational", "reputation", "strategic"):
        assert body["agent_breakdown"][name] == {"status": "not run"}
    assert body["weights_used"] == {"financial": 0.3}


def test_report_404(Session):
    assert call("GET", "/api/v1/evaluations/eval-missing/report").status_code == 404


# ── /companies/search ─────────────────────────────────────

@pytest.fixture
def ticker_map(monkeypatch):
    from app.services import edgar_pipeline
    data = {"OKTA": {"cik": 1660134, "title": "Okta, Inc."},
            "BKNG": {"cik": 1075531, "title": "Booking Holdings Inc."}}
    data.update({f"OK{i}": {"cik": i, "title": f"Brokta Ventures {i}"} for i in range(15)})
    monkeypatch.setattr(edgar_pipeline, "_load_ticker_cik_map", lambda: data)


@pytest.mark.parametrize("q", ["", "o", " o "])
def test_search_short_query_returns_nothing(ticker_map, q):
    assert call("GET", "/api/v1/companies/search", params={"q": q}).json() == {"companies": []}


def test_search_ranks_prefix_matches_first_caps_at_10_and_pads_cik(ticker_map):
    companies = call("GET", "/api/v1/companies/search", params={"q": "okta"}).json()["companies"]
    assert len(companies) == 10
    assert companies[0] == {"name": "Okta, Inc.", "ticker": "OKTA", "cik": "0001660134"}


def test_search_matches_ticker(ticker_map):
    companies = call("GET", "/api/v1/companies/search", params={"q": "bkng"}).json()["companies"]
    assert [c["ticker"] for c in companies] == ["BKNG"]


# ── /rag/stats ────────────────────────────────────────────

def test_rag_stats_uses_test_chroma_and_reports_counts():
    body = call("GET", "/api/v1/rag/stats").json()
    assert body["status"] == "ok"
    assert "vendorrisk-test-chroma" in body["chroma_path"]   # never the real chroma_db
    assert body["industry_benchmarks"] == 5 and body["compliance_requirements"] == 3


# The guard is tested by calling it directly with the real path — no test
# ever opens backend/chroma_db, even to prove that it can't.

def test_chroma_guard_refuses_real_store_under_pytest():
    from app.services.rag_service import REAL_CHROMA_DIR, _guard_chroma_path
    for path in (str(REAL_CHROMA_DIR), str(REAL_CHROMA_DIR / "sub"), f"{REAL_CHROMA_DIR}/../chroma_db"):
        with pytest.raises(RuntimeError, match="Refusing to open the real ChromaDB"):
            _guard_chroma_path(path, under_pytest=True)


def test_chroma_guard_allows_other_paths_and_production(tmp_path):
    from app.services.rag_service import REAL_CHROMA_DIR, _guard_chroma_path
    _guard_chroma_path(str(tmp_path / "chroma"), under_pytest=True)
    _guard_chroma_path(str(REAL_CHROMA_DIR.parent / "chroma_db_backup"), under_pytest=True)
    _guard_chroma_path(str(REAL_CHROMA_DIR), under_pytest=False)


# ── Stuck jobs after restart ──────────────────────────────

@pytest.mark.xfail(strict=True, reason=(
    "Gap (main_api.py lifespan / routes.py job architecture): pipelines run "
    "as in-process BackgroundTasks, so a restart mid-evaluation leaves the "
    "job 'running' forever and clients poll indefinitely. Desired: startup "
    "marks stale pending/running jobs as failed."
))
def test_stale_running_jobs_are_failed_on_startup(Session, db_url, monkeypatch):
    add_rows(Session, Job(job_id="job-stuck", vendor_name="Okta", status="running",
                          progress=54, created_at=ts(120), updated_at=ts(120)))

    async def restart():
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
        monkeypatch.setattr(main_api, "engine", create_async_engine(db_url, poolclass=NullPool))
        async with main_api.lifespan(app):
            pass
    asyncio.run(restart())
    [job] = fetch(Session, Job, job_id="job-stuck")
    assert job.status == "failed"


# ══════════════════════════════════════════════════════════
# Background pipeline (_run_evaluation_pipeline)
# ══════════════════════════════════════════════════════════

def agent_output(score=85.0, confidence=0.97, **extra):
    return {"score": score, "confidence": confidence, "explanation": "ok",
            "evidence": [], "red_flags": [], "reasoning": "r", **extra}


DISCOVERED = {
    "certifications_found": ["SOC2_Type2", "FedRAMP"],
    "certification_evidence_tiers": {"SOC2_Type2": "VERIFIED_REGISTRY", "FedRAMP": "VERIFIED_REGISTRY"},
    "regulatory_frameworks_disclosed": ["GDPR"], "evidence": ["10-K"],
    "sources_checked": ["EDGAR"], "edgar_cik": "0001660134",
}


@pytest.fixture
def pipeline(Session, monkeypatch):
    """Fakes every external dependency of the real pipeline."""
    fx = SimpleNamespace(
        Session=Session, sleeps=[], stored=[], discover_calls=0, agent_states={},
        financial={"industry": "Software", "ebitda": 2e9, "revenue_growth": 12.0,
                   "years_in_operation": 15, "gross_margin": 75.0},
        compliance={"ofac_status": "clear", "ofac_clear": True, "ofac_matches": [],
                    "news_red_flags": [], "recent_news": []},
        discovered=dict(DISCOVERED),
        agents={name: agent_output() for name in
                ("financial", "compliance", "operational", "reputation", "strategic")},
        rag_context="",
    )

    async def sleep(seconds):
        fx.sleeps.append(seconds)

    async def discover(name):
        fx.discover_calls += 1
        return fx.discovered

    def make_agent(name):
        async def agent(state):
            fx.agent_states[name] = {k: v for k, v in state.items()}
            result = fx.agents[name]
            if isinstance(result, Exception):
                raise result
            return dict(result)
        return agent

    monkeypatch.setattr(routes, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(routes, "scrape_vendor_financials", lambda name: dict(fx.financial))
    monkeypatch.setattr(routes, "scrape_vendor_compliance",
                        lambda name, industry=None: dict(fx.compliance, industry=industry))
    monkeypatch.setattr(routes, "discover_certifications", discover)
    monkeypatch.setattr(routes, "retrieve_context", lambda **kw: fx.rag_context)
    monkeypatch.setattr(routes, "store_evaluation", lambda eid, data: fx.stored.append((eid, data)))
    for name in fx.agents:
        monkeypatch.setattr(routes, f"run_{name}_agent", make_agent(name))

    def run(job_id="job-p", vendor="Okta", weights=None):
        add_rows(Session, Job(job_id=job_id, vendor_name=vendor, status="pending"))
        asyncio.run(REAL_PIPELINE(
            job_id=job_id, vendor_name=vendor, client_profile=dict(CLIENT_PROFILE),
            weights=weights or {}, evaluation_id=f"eval-{job_id}",
        ))
        [job] = fetch(Session, Job, job_id=job_id)
        return job

    fx.run = run
    return fx


def test_pipeline_happy_path(pipeline):
    job = pipeline.run()
    assert (job.status, job.progress, job.current_step) == ("complete", 100, "Done")
    result = job.result
    assert result["recommendation"] == "APPROVE"
    assert result["agent_scores"] == {name: 85.0 for name in pipeline.agents}
    assert result["certifications_found"] == ["SOC2_Type2", "FedRAMP"]
    assert result["ofac_status"] == "clear" and result["unavailable_agents"] == []
    assert result["industry"] == "Software"

    [row] = fetch(pipeline.Session, Evaluation, evaluation_id="eval-job-p")
    assert (row.recommendation, row.financial_score) == ("APPROVE", 85.0)
    assert pipeline.stored[0][0] == "eval-job-p"


def test_pipeline_result_is_visible_through_jobs_endpoint(pipeline):
    pipeline.run(job_id="job-api")
    body = call("GET", "/api/v1/jobs/job-api").json()
    assert body["status"] == "complete" and body["result"]["recommendation"] == "APPROVE"


def test_pipeline_failed_agent_is_recorded_as_unavailable(pipeline):
    pipeline.agents["operational"] = RuntimeError("model returned garbage")
    job = pipeline.run()
    assert job.status == "complete"
    assert job.result["agent_scores"]["operational"] is None
    assert job.result["unavailable_agents"] == ["operational"]
    assert job.result["recommendation"] == "REVIEW"


def test_pipeline_ofac_match_rejects_even_if_compliance_agent_fails(pipeline):
    pipeline.compliance.update(ofac_status="match", ofac_clear=False,
                               ofac_matches=["OKTA BAD [SDGT] (SDN #1)"])
    pipeline.agents["compliance"] = RuntimeError("compliance agent down")
    job = pipeline.run()
    assert job.result["recommendation"] == "REJECT"
    assert job.result["ofac_matches"] == ["OKTA BAD [SDGT] (SDN #1)"]


def test_pipeline_unknown_ofac_status_reaches_agents_as_none(pipeline):
    pipeline.compliance.update(ofac_status="unavailable", ofac_clear=None)
    job = pipeline.run()
    assert job.result["recommendation"] == "REVIEW"
    rep = pipeline.agent_states["reputation"]["reputation_data"]
    assert rep["ofac_status"] == "unavailable" and rep["ofac_clear"] is None


def test_pipeline_exception_marks_job_failed(pipeline, monkeypatch):
    def boom(name):
        raise RuntimeError("yfinance exploded")
    monkeypatch.setattr(routes, "scrape_vendor_financials", boom)
    job = pipeline.run()
    assert job.status == "failed"
    assert job.error == "RuntimeError: yfinance exploded"
    assert job.current_step == "Pipeline failed"


def test_pipeline_injects_rag_context_per_agent(pipeline):
    pipeline.rag_context = "KNOWLEDGE BASE CONTEXT"
    pipeline.run()
    assert pipeline.agent_states["financial"]["financial_data"]["rag_context"] == "KNOWLEDGE BASE CONTEXT"
    assert pipeline.agent_states["strategic"]["strategic_data"]["rag_context"] == "KNOWLEDGE BASE CONTEXT"


def test_pipeline_cert_cache_hit_skips_discovery(pipeline):
    add_rows(pipeline.Session, CertCache(vendor_name="okta", cached_at=ts(60), **DISCOVERED))
    pipeline.run()
    assert pipeline.discover_calls == 0


def test_pipeline_caches_meaningful_discovery(pipeline):
    pipeline.run()
    assert pipeline.discover_calls == 1
    assert fetch(pipeline.Session, CertCache, vendor_name="okta")


def test_pipeline_does_not_cache_csa_star_only_results(pipeline):
    pipeline.discovered = {**DISCOVERED, "certifications_found": ["CSA_STAR"],
                           "certification_evidence_tiers": {"CSA_STAR": "CORROBORATED"}}
    pipeline.run()
    assert fetch(pipeline.Session, CertCache, vendor_name="okta") == []


def test_pipeline_merges_cert_tiers_into_compliance_output(pipeline):
    job = pipeline.run()
    [row] = fetch(pipeline.Session, Evaluation, evaluation_id="eval-job-p")
    assert row.agent_outputs["compliance"]["certification_evidence_tiers"] == \
        DISCOVERED["certification_evidence_tiers"]


def test_pipeline_applies_custom_weights(pipeline):
    pipeline.agents["financial"] = agent_output(score=0.0)
    weights = {"financial": 1.0, "compliance": 0.0, "operational": 0.0,
               "reputation": 0.0, "strategic": 0.0}
    assert pipeline.run(weights=weights).result["composite_score"] == 0.0


# ── _run_agent_with_retry ─────────────────────────────────

@pytest.fixture
def sleeps(monkeypatch):
    recorded = []

    async def sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(routes, "asyncio", SimpleNamespace(sleep=sleep))
    return recorded


def flaky(*errors, result="ok"):
    remaining = list(errors)

    async def fn(state):
        if remaining:
            raise remaining.pop(0)
        return result
    return fn


def test_retry_waits_exactly_what_groq_says_plus_buffer(sleeps):
    fn = flaky(RuntimeError("429 rate_limit_exceeded. Please try again in 1.35s"))
    assert asyncio.run(routes._run_agent_with_retry(fn, {}, "financial")) == "ok"
    assert sleeps == [pytest.approx(2.85)]


def test_retry_falls_back_to_exponential_backoff(sleeps):
    fn = flaky(*[RuntimeError("429")] * 3)
    asyncio.run(routes._run_agent_with_retry(fn, {}, "financial"))
    assert sleeps == [3, 6, 12]


def test_retry_wait_is_capped_at_60s(sleeps):
    fn = flaky(RuntimeError("429 try again in 600s"))
    asyncio.run(routes._run_agent_with_retry(fn, {}, "financial"))
    assert sleeps == [60]


def test_retry_gives_up_after_max_retries(sleeps):
    fn = flaky(*[RuntimeError("429")] * 4)
    with pytest.raises(RuntimeError):
        asyncio.run(routes._run_agent_with_retry(fn, {}, "financial"))
    assert len(sleeps) == 3


def test_non_rate_limit_error_is_not_retried(sleeps):
    with pytest.raises(ValueError):
        asyncio.run(routes._run_agent_with_retry(flaky(ValueError("bad output")), {}, "financial"))
    assert sleeps == []


# ── CORS ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    (None, ["http://localhost:5173"]),
    ("", ["http://localhost:5173"]),
    ("   ", ["http://localhost:5173"]),
    ("https://vendoriq.example.com", ["https://vendoriq.example.com"]),
    (" http://localhost:5173/ , https://app.example.com ,", ["http://localhost:5173", "https://app.example.com"]),
])
def test_cors_origins_parsing(raw, expected):
    assert main_api._cors_origins(raw) == expected


def preflight(origin):
    return call("OPTIONS", "/api/v1/evaluations", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET"})


def test_cors_allows_default_frontend_origin():
    resp = preflight("http://localhost:5173")
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_other_origins():
    resp = preflight("https://evil.example.com")
    assert "access-control-allow-origin" not in resp.headers


# ── truncated_agents in the job result ────────────────────

def test_pipeline_result_lists_truncated_agents(pipeline):
    pipeline.agents["financial"] = agent_output(truncated=True,
        red_flags=["Agent output was truncated — red flags may be incomplete"])
    job = pipeline.run()
    assert job.result["truncated_agents"] == ["financial"]
    assert job.result["recommendation"] == "REVIEW"


def test_pipeline_result_has_empty_truncated_agents_when_none(pipeline):
    assert pipeline.run().result["truncated_agents"] == []
