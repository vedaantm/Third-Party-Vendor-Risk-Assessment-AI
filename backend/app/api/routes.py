# backend/app/api/routes.py
"""
API Routes — Async Job Architecture
=====================================

THE BIG CHANGE FROM THE OLD VERSION:
    Before: POST /evaluate waited 2 minutes, then returned the result.
            If the connection dropped at minute 1:58, you got nothing.
            
    After:  POST /evaluate returns a job_id in < 100ms (202 Accepted).
            The pipeline runs as a background task.
            GET /jobs/{job_id} returns real-time status as the pipeline progresses.
            Result persists in PostgreSQL — survives server restarts.

THE ASYNC JOB PATTERN:
    1. POST /evaluate
       → Creates a Job record in PostgreSQL (status: pending)
       → Starts _run_evaluation_pipeline() as background task
       → Returns { job_id, poll_url } immediately with HTTP 202

    2. Background task runs (_run_evaluation_pipeline)
       → Updates Job.status = "running"
       → Updates Job.progress + Job.current_step at each stage
       → Saves result when done (Job.status = "complete")
       → Saves error if it fails (Job.status = "failed")

    3. GET /jobs/{job_id} (called every 3 seconds from frontend)
       → Returns { status, progress, current_step }
       → When status == "complete", also returns { result }

REDIS INTEGRATION:
    Cert cache reads now go to Redis first (< 1ms) before Supabase (80ms).
    Write goes to both Redis and Supabase simultaneously.
    If Redis is down, falls back to Supabase automatically.
"""

import asyncio
import math
import os
import re
import uuid

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, update

from app.db.database import get_db, AsyncSessionLocal
from app.db.models import Evaluation, CertCache, Job
from app.agents.financial_agent   import run_financial_agent
from app.agents.compliance_agent  import run_compliance_agent
from app.agents.operational_agent import run_operational_agent
from app.agents.reputation_agent  import run_reputation_agent
from app.agents.strategic_agent   import run_strategic_agent
from app.agents.meta_agent        import run_meta_agent
from app.services.scraper         import scrape_vendor_financials, scrape_vendor_compliance
from app.services.rag_service     import store_evaluation, retrieve_context
from app.services.compliance_discovery import discover_certifications

# Redis cache layer — gracefully falls back to Supabase if Redis is down
from app.services.cache import (
    get_cert_cache_redis,
    set_cert_cache_redis,
)

router = APIRouter()


# ─── Request Models ───────────────────────────────────────────────────────────

class ClientProfile(BaseModel):
    company_name:   str
    industry:       str
    contract_value: float
    contract_years: int
    risk_appetite:  str
    priorities:     list[str]
    dealbreakers:   list[str]
    nice_to_have:   list[str]


AGENT_WEIGHT_KEYS = ("financial", "compliance", "operational", "reputation", "strategic")
WEIGHT_SUM_TOLERANCE = 1e-3   # allows float rounding, e.g. 0.30+0.25+0.20+0.10+0.15


class EvaluateRequest(BaseModel):
    vendor_name:    str
    client_profile: ClientProfile
    weights: dict[str, float] = {
        "financial":   0.30,
        "compliance":  0.25,
        "operational": 0.20,
        "reputation":  0.10,
        "strategic":   0.15,
    }

    @field_validator("weights")
    @classmethod
    def _validate_weights(cls, weights: dict[str, float]) -> dict[str, float]:
        """
        Weights must name exactly the five agents, each finite and >= 0, and
        sum to 1. Previously any dict was accepted: negative weights distorted
        the composite, totals != 1 put scores from different requests on
        different scales, and a misspelt key silently fell back to its default.
        """
        unknown = sorted(set(weights) - set(AGENT_WEIGHT_KEYS))
        missing = [k for k in AGENT_WEIGHT_KEYS if k not in weights]
        if unknown or missing:
            raise ValueError(
                f"weights must have exactly these keys: {list(AGENT_WEIGHT_KEYS)} "
                f"(unknown: {unknown}, missing: {missing})"
            )
        bad = {k: v for k, v in weights.items() if not math.isfinite(v) or v < 0}
        if bad:
            raise ValueError(f"weights must be finite and >= 0, got {bad}")
        total = sum(weights.values())
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"weights must sum to 1.0, got {total:.4f}")
        return weights


# ─── Cache Helpers (Three-Tier: Redis → Supabase → Pipeline) ─────────────────

CERT_CACHE_TTL_DAYS = 7

def _parse_cert_cache_valid_after(raw: str | None) -> datetime | None:
    """
    CERT_CACHE_VALID_AFTER (env, ISO date or datetime, UTC if no offset):
    Supabase cert_cache rows cached before this instant are treated as
    expired. Set it to the deploy date whenever cert discovery logic changes
    in a way that invalidates cached results. Unset → no cutoff.

    An unparseable value fails at startup rather than being silently ignored.
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError as e:
        raise ValueError(
            f"CERT_CACHE_VALID_AFTER={raw!r} is not an ISO date/datetime "
            f"(expected e.g. 2026-10-20 or 2026-10-20T09:00:00+00:00)"
        ) from e
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# DEPLOY NOTE — purge the cert cache when deploying a change to cert
# discovery or registry name matching (e.g. the stricter matching in
# app/services/name_matching.py, which removed false FedRAMP / CSA STAR /
# EDGAR matches). Cached results from the old logic are otherwise served
# for up to CERT_CACHE_TTL_DAYS:
#   Supabase:  DELETE FROM cert_cache;
#   Redis:     bump the cert key version in app/services/cache.py (now v2),
#              or delete keys matching cert:*
# If a purge isn't possible, set CERT_CACHE_VALID_AFTER to the deploy date.
CERT_CACHE_VALID_AFTER = _parse_cert_cache_valid_after(
    os.environ.get("CERT_CACHE_VALID_AFTER")
)


async def _read_cert_cache(vendor_name: str, db: AsyncSession) -> dict | None:
    """
    Three-tier cert cache read:
        Tier 1: Redis       (< 1ms, in-memory)
        Tier 2: Supabase    (80ms, PostgreSQL)
        Tier 3: None        (must run the discovery pipeline)

    BEFORE: every read went to Supabase = 80ms network round trip
    AFTER:  Redis hit = < 1ms (60-100x faster)
    """
    # Tier 1: Try Redis first (fast path)
    redis_result = await get_cert_cache_redis(vendor_name)
    if redis_result is not None:
        return redis_result

    # Tier 2: Try Supabase (persistent fallback)
    key = vendor_name.lower().strip()
    result = await db.execute(
        select(CertCache).where(CertCache.vendor_name == key)
    )
    row = result.scalar_one_or_none()

    if not row:
        return None   # Cache miss — must run pipeline

    cached_at = row.cached_at.replace(tzinfo=timezone.utc)
    if CERT_CACHE_VALID_AFTER and cached_at < CERT_CACHE_VALID_AFTER:
        print(f"  → Cert cache for '{vendor_name}' predates CERT_CACHE_VALID_AFTER — re-running")
        return None

    age = datetime.now(timezone.utc) - cached_at
    if age > timedelta(days=CERT_CACHE_TTL_DAYS):
        print(f"  → Cert cache expired for '{vendor_name}' ({age.days} days old) — re-running")
        return None

    print(f"  → Supabase cert cache HIT for '{vendor_name}' (cached {age.days}d ago)")

    # Found in Supabase — warm the Redis cache for next time
    cert_data = {
        "certifications_found":            row.certifications_found or [],
        "certification_evidence_tiers":    row.certification_evidence_tiers or {},
        "regulatory_frameworks_disclosed": row.regulatory_frameworks_disclosed or [],
        "evidence":                        row.evidence or [],
        "sources_checked":                 row.sources_checked or [],
        "edgar_cik":                       row.edgar_cik,
    }
    await set_cert_cache_redis(vendor_name, cert_data)   # Warm Redis for next read
    return cert_data


async def _write_cert_cache(vendor_name: str, cert_result: dict, db: AsyncSession) -> None:
    """
    Write cert result to both Redis (fast) and Supabase (persistent).
    Both caches are always in sync.
    """
    # Write to Redis first (fast)
    await set_cert_cache_redis(vendor_name, cert_result)

    # Write to Supabase (persistent backup)
    key = vendor_name.lower().strip()
    existing = await db.execute(
        select(CertCache).where(CertCache.vendor_name == key)
    )
    row = existing.scalar_one_or_none()

    if row:
        row.certifications_found            = cert_result.get("certifications_found", [])
        row.certification_evidence_tiers    = cert_result.get("certification_evidence_tiers", {})
        row.regulatory_frameworks_disclosed = cert_result.get("regulatory_frameworks_disclosed", [])
        row.evidence                        = cert_result.get("evidence", [])
        row.sources_checked                 = cert_result.get("sources_checked", [])
        row.edgar_cik                       = cert_result.get("edgar_cik")
        row.cached_at                       = datetime.now(timezone.utc)
    else:
        db.add(CertCache(
            vendor_name                     = key,
            certifications_found            = cert_result.get("certifications_found", []),
            certification_evidence_tiers    = cert_result.get("certification_evidence_tiers", {}),
            regulatory_frameworks_disclosed = cert_result.get("regulatory_frameworks_disclosed", []),
            evidence                        = cert_result.get("evidence", []),
            sources_checked                 = cert_result.get("sources_checked", []),
            edgar_cik                       = cert_result.get("edgar_cik"),
        ))

    await db.flush()
    print(f"  → Cert cache written (Redis + Supabase) for '{vendor_name}'")


# ─── Job Status Helpers ───────────────────────────────────────────────────────

async def _update_job(
    job_id:       str,
    db:           AsyncSession,
    status:       str | None = None,
    current_step: str | None = None,
    progress:     int | None = None,
    result:       dict | None = None,
    error:        str | None = None,
) -> None:
    """
    Update a job's status in PostgreSQL.
    Called at each meaningful step of the pipeline so the frontend
    always has current state to display.

    Example calls throughout the pipeline:
        await _update_job(job_id, db, current_step="Scraping financial data", progress=5)
        await _update_job(job_id, db, current_step="Running compliance agent", progress=52)
        await _update_job(job_id, db, status="complete", progress=100, result=response_data)
    """
    updates = {"updated_at": datetime.now(timezone.utc)}
    if status       is not None: updates["status"]       = status
    if current_step is not None: updates["current_step"] = current_step
    if progress     is not None: updates["progress"]     = progress
    if result       is not None: updates["result"]       = result
    if error        is not None: updates["error"]        = error

    await db.execute(
        update(Job).where(Job.job_id == job_id).values(**updates)
    )
    await db.commit()


# ─── Agent Retry Wrapper ──────────────────────────────────────────────────────

async def _run_agent_with_retry(fn, state: dict, name: str, max_retries: int = 3):
    """
    Run a specialist agent with automatic retry on rate limit.

    Groq sometimes returns 429 with an exact wait time in the error message:
    "Rate limit reached... Please try again in 1.35s"
    
    We parse that exact wait time instead of using a fixed sleep.
    This is more efficient: if we need to wait 1.35s, we wait 1.35s.
    Not 10s. Not 60s. Exactly what Groq says.
    """
    for attempt in range(max_retries + 1):
        try:
            return await fn(state)
        except Exception as e:
            error_str     = str(e)
            is_rate_limit = "429" in error_str or "rate_limit_exceeded" in error_str

            if is_rate_limit and attempt < max_retries:
                # Parse the exact wait time from Groq's error message
                wait_match = re.search(r"try again in (\d+(?:\.\d+)?)s", error_str)
                if wait_match:
                    wait = float(wait_match.group(1)) + 1.5   # Add 1.5s buffer
                else:
                    wait = (2 ** attempt) * 3   # Fallback: 3, 6, 12 seconds

                wait = min(wait, 60)   # Never wait more than 60s per retry
                print(f"  → [{name}] rate limit, waiting {wait:.1f}s "
                      f"(attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait)
            else:
                raise


# ─── The Evaluation Pipeline (Background Task) ────────────────────────────────

async def _run_evaluation_pipeline(
    job_id:        str,
    vendor_name:   str,
    client_profile: dict,
    weights:       dict,
    evaluation_id: str,
) -> None:
    """
    The full evaluation pipeline — now runs as a background task.

    KEY DIFFERENCES FROM THE OLD SYNCHRONOUS VERSION:
        1. Runs independently of the HTTP request that created it
        2. Updates Job status at each step → real progress on frontend
        3. Catches ALL exceptions → failures are recorded, never silently lost
        4. Has its own database session (background tasks need their own)
        5. Uses the three-tier cert cache (Redis → Supabase → Pipeline)

    This function is called by POST /evaluate and runs in the background.
    The HTTP response (202) was already sent before this starts.
    """
    # Background tasks need their own database session.
    # The request's session has already been closed by the time this runs.
    async with AsyncSessionLocal() as db:
        try:
            # ── Mark job as running ───────────────────────────────────────────
            await _update_job(job_id, db, status="running",
                              current_step="Starting evaluation", progress=2)

            # ── Step 1: Financial data scraping ──────────────────────────────
            await _update_job(job_id, db,
                              current_step="Scraping financial data (yfinance, Wikipedia, news)",
                              progress=5)

            financial_data = scrape_vendor_financials(vendor_name)
            industry       = financial_data.get("industry", client_profile["industry"])

            await _update_job(job_id, db,
                              current_step="Checking OFAC sanctions and compliance signals",
                              progress=10)

            compliance_data = scrape_vendor_compliance(vendor_name, industry=industry)

            # ── Step 2: Cert discovery (Redis → Supabase → Pipeline) ─────────
            await _update_job(job_id, db,
                              current_step="Checking certification registry cache",
                              progress=15)

            cert_discovery = await _read_cert_cache(vendor_name, db)

            if cert_discovery is None:
                await _update_job(job_id, db,
                                  current_step="Discovering certifications: EDGAR → FedRAMP → CSA STAR",
                                  progress=20)

                cert_discovery = await discover_certifications(vendor_name)

                # Only cache if pipeline found meaningful results
                # CSA_STAR alone = rate-limited run = don't cache thin results
                meaningful = [
                    c for c in cert_discovery.get("certifications_found", [])
                    if c != "CSA_STAR"
                ]
                if meaningful:
                    await _write_cert_cache(vendor_name, cert_discovery, db)
                else:
                    print("  → Skipping cache write — results too thin (rate limit likely hit)")

            # Merge cert discovery results into compliance_data
            compliance_data["certifications_found"]            = cert_discovery.get("certifications_found", [])
            compliance_data["certification_evidence_tiers"]    = cert_discovery.get("certification_evidence_tiers", {})
            compliance_data["regulatory_frameworks_disclosed"] = cert_discovery.get("regulatory_frameworks_disclosed", [])
            compliance_data["cert_evidence"]                   = cert_discovery.get("evidence", [])
            compliance_data["cert_sources_checked"]            = cert_discovery.get("sources_checked", [])
            compliance_data["edgar_cik"]                       = cert_discovery.get("edgar_cik")

            # ── Step 3: Build agent state ─────────────────────────────────────
            await _update_job(job_id, db,
                              current_step="Preparing agent context", progress=28)

            state = {
                "vendor_id":          f"v-{uuid.uuid4().hex[:6]}",
                "vendor_name":        vendor_name,
                "company_id":         client_profile["company_name"].lower().replace(" ", "-"),
                "evaluation_id":      evaluation_id,
                "weight_financial":   weights.get("financial",   0.30),
                "weight_compliance":  weights.get("compliance",  0.25),
                "weight_operational": weights.get("operational", 0.20),
                "weight_reputation":  weights.get("reputation",  0.10),
                "weight_strategic":   weights.get("strategic",   0.15),
                "financial_data":     financial_data,
                "compliance_data":    compliance_data,
                "operational_data": {
                    "vendor_name":        vendor_name,
                    "industry":           industry,
                    "years_in_operation": financial_data.get("years_in_operation"),
                    "company_size": (
                        "large" if (financial_data.get("ebitda") or 0) > 1_000_000_000
                        else "mid" if (financial_data.get("ebitda") or 0) > 100_000_000
                        else "small"
                    ),
                    "ebitda":         financial_data.get("ebitda"),
                    "revenue_growth": financial_data.get("revenue_growth"),
                },
                "reputation_data": {
                    "vendor_name":        vendor_name,
                    "industry":           industry,
                    "years_in_operation": financial_data.get("years_in_operation"),
                    "certifications":     compliance_data.get("certifications_found", []),
                    "news_red_flags":     compliance_data.get("news_red_flags", []),
                    "ofac_status":        compliance_data.get("ofac_status"),
                    "ofac_clear":         compliance_data.get("ofac_clear"),
                    "revenue_growth":     financial_data.get("revenue_growth"),
                },
                "strategic_data": {
                    "vendor_name":        vendor_name,
                    "industry":           industry,
                    "years_in_operation": financial_data.get("years_in_operation"),
                    "revenue_growth":     financial_data.get("revenue_growth"),
                    "gross_margin":       financial_data.get("gross_margin"),
                    "ebitda":             financial_data.get("ebitda"),
                    "certifications":     compliance_data.get("certifications_found", []),
                    "client_profile":     client_profile,
                },
                "pipeline_status":   "running",
                "conflict_detected": False,
                "data_complete":     True,
                "missing_fields":    [],
            }

            # ── Step 4: Run specialist agents ─────────────────────────────────
            # Brief cooldown before agents to let cert extraction window clear
            print("  → Cooling down before agents (15s)...")
            await asyncio.sleep(15)

            agent_map = {
                "financial":   run_financial_agent,
                "compliance":  run_compliance_agent,
                "operational": run_operational_agent,
                "reputation":  run_reputation_agent,
                "strategic":   run_strategic_agent,
            }
            output_key_map = {
                "financial":   "financial_output",
                "compliance":  "compliance_output",
                "operational": "operational_output",
                "reputation":  "reputation_output",
                "strategic":   "strategic_output",
            }
            # Progress checkpoints for each agent
            agent_progress = {
                "financial":   40,
                "compliance":  54,
                "operational": 66,
                "reputation":  78,
                "strategic":   88,
            }

            agent_results = {}
            for name, fn in agent_map.items():
                try:
                    await asyncio.sleep(12)   # Spacing between agent calls

                    # Update progress so frontend shows which agent is running
                    await _update_job(job_id, db,
                                      current_step=f"Running {name} analysis agent...",
                                      progress=agent_progress[name])

                    # Inject RAG context for this agent's domain
                    rag_context = retrieve_context(
                        vendor_name=vendor_name,
                        industry=industry,
                        domain=name,
                    )
                    if rag_context:
                        state[f"{name}_data"]["rag_context"] = rag_context
                        print(f"  ✅ RAG: injected {name} context")

                    result = await _run_agent_with_retry(fn, state, name)
                    state[output_key_map[name]] = result
                    agent_results[name]         = result

                except Exception as e:
                    print(f"[WARNING] {name} agent failed: {e}")
                    state[output_key_map[name]] = None
                    agent_results[name]         = None

            # Ensure compliance output has cert tiers for meta-agent.
            # Sanctions status is NOT taken from here by the meta-agent — it
            # reads compliance_data directly, so it survives a failed
            # compliance agent. This overwrite just keeps the stored agent
            # output consistent with the deterministic screen.
            if state.get("compliance_output"):
                state["compliance_output"]["certification_evidence_tiers"] = \
                    compliance_data.get("certification_evidence_tiers", {})
                state["compliance_output"]["sanctions_clear"] = \
                    compliance_data.get("ofac_clear")
                if agent_results.get("compliance"):
                    agent_results["compliance"]["certification_evidence_tiers"] = \
                        compliance_data.get("certification_evidence_tiers", {})

            # ── Step 5: Meta-agent synthesis ──────────────────────────────────
            await _update_job(job_id, db,
                              current_step="Synthesising recommendation (meta-agent)", progress=92)

            meta_result = await run_meta_agent(state)

            # ── Step 6: Persist evaluation to Supabase ────────────────────────
            await _update_job(job_id, db,
                              current_step="Saving evaluation results", progress=96)

            def safe_score(agent_name):
                r = agent_results.get(agent_name)
                return r.get("score") if r else None

            eval_row = Evaluation(
                evaluation_id=     evaluation_id,
                vendor_name=       vendor_name,
                company_name=      client_profile["company_name"],
                financial_score=   safe_score("financial"),
                compliance_score=  safe_score("compliance"),
                operational_score= safe_score("operational"),
                reputation_score=  safe_score("reputation"),
                strategic_score=   safe_score("strategic"),
                composite_score=   meta_result.get("composite_score"),
                risk_score=        meta_result.get("risk_score"),
                fit_score=         meta_result.get("fit_score"),
                confidence=        meta_result.get("aggregate_confidence"),
                recommendation=    meta_result.get("final_recommendation"),
                final_explanation= meta_result.get("final_explanation"),
                red_flags=         meta_result.get("all_red_flags", []),
                weights=           weights,
                agent_outputs=     agent_results,
            )
            db.add(eval_row)

            # Build the response payload
            response_data = {
                "evaluation_id":     evaluation_id,
                "vendor_name":       vendor_name,
                "recommendation":    meta_result.get("final_recommendation"),
                "composite_score":   meta_result.get("composite_score"),
                "risk_score":        meta_result.get("risk_score"),
                "fit_score":         meta_result.get("fit_score"),
                "confidence":        meta_result.get("aggregate_confidence"),
                "dealbreaker_status": meta_result.get("dealbreaker_status"),
                "ofac_status":        meta_result.get("ofac_status"),
                "ofac_matches":       compliance_data.get("ofac_matches", []),
                "unavailable_agents": meta_result.get("unavailable_agents", []),
                "agent_scores": {
                    name: (agent_results[name].get("score")
                           if agent_results[name] else None)
                    for name in agent_map
                },
                "red_flags":                       meta_result.get("all_red_flags", []),
                "final_explanation":               meta_result.get("final_explanation"),
                "certifications_found":            compliance_data.get("certifications_found", []),
                "certification_evidence_tiers":    compliance_data.get("certification_evidence_tiers", {}),
                "regulatory_frameworks_disclosed": compliance_data.get("regulatory_frameworks_disclosed", []),
                "cert_evidence":                   compliance_data.get("cert_evidence", []),
                "edgar_cik":                       compliance_data.get("edgar_cik"),
                "industry":                        industry,
            }

            # Store in ChromaDB for RAG cross-vendor context
            store_evaluation(evaluation_id, response_data)

            # ── Mark job complete ─────────────────────────────────────────────
            await _update_job(job_id, db,
                              status="complete",
                              current_step="Done",
                              progress=100,
                              result=response_data)

            await db.commit()
            print(f"  ✅ Job {job_id} complete: {meta_result.get('final_recommendation')} "
                  f"({meta_result.get('composite_score')}/100)")

        except Exception as e:
            # Record failure — never silently lose work
            error_msg = f"{type(e).__name__}: {str(e)}"
            print(f"[ERROR] Job {job_id} failed: {error_msg}")

            try:
                await _update_job(job_id, db,
                                  status="failed",
                                  current_step="Pipeline failed",
                                  error=error_msg)
                await db.commit()
            except Exception as inner_e:
                print(f"[ERROR] Could not update job failure status: {inner_e}")


# ─── POST /evaluate — Returns 202 Immediately ────────────────────────────────

@router.post("/evaluate")
async def evaluate_vendor(
    request:          EvaluateRequest,
    background_tasks: BackgroundTasks,
    db:               AsyncSession = Depends(get_db),
):
    """
    Submit a vendor evaluation request.

    RETURNS IMMEDIATELY with job_id (202 Accepted).
    The 2-minute pipeline runs in the background.
    Poll GET /jobs/{job_id} for status and result.

    WHY 202 AND NOT 200?
        HTTP 200 means "I finished the work."
        HTTP 202 means "I accepted your request and am working on it."
        The work is not done when this endpoint responds — hence 202.
        This is the correct semantics, not pedantry.
    """
    vendor_name    = request.vendor_name
    client_profile = request.client_profile.model_dump()
    weights        = request.weights

    # Generate IDs
    job_id        = f"job-{uuid.uuid4().hex[:12]}"
    evaluation_id = f"eval-{uuid.uuid4().hex[:8]}"

    # Create the job record immediately
    # This persists in PostgreSQL before the background task starts.
    # If the server restarts after this point but before completion,
    # the job record survives and shows status "pending" or "running".
    new_job = Job(
        job_id       = job_id,
        vendor_name  = vendor_name,
        status       = "pending",
        current_step = "Queued — starting shortly",
        progress     = 0,
    )
    db.add(new_job)
    await db.commit()

    # Queue the pipeline as a background task
    # This returns immediately — the pipeline starts after the HTTP response is sent
    background_tasks.add_task(
        _run_evaluation_pipeline,
        job_id        = job_id,
        vendor_name   = vendor_name,
        client_profile = client_profile,
        weights       = weights,
        evaluation_id = evaluation_id,
    )

    # Return 202 — the job ID is how the client tracks progress
    return JSONResponse(
        status_code=202,
        content={
            "job_id":        job_id,
            "evaluation_id": evaluation_id,
            "vendor_name":   vendor_name,
            "status":        "pending",
            "message":       "Evaluation started. Poll /api/v1/jobs/{job_id} for progress.",
            "poll_url":      f"/api/v1/jobs/{job_id}",
        }
    )


# ─── GET /jobs/{job_id} — Poll for Status ────────────────────────────────────

@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    db:     AsyncSession = Depends(get_db),
):
    """
    Get the current status of an evaluation job.

    Poll this every 3 seconds from the frontend.
    When status == "complete", the full evaluation result is in response.result.
    When status == "failed", the error message is in response.error.

    Frontend polling pattern (JavaScript):
        const poll = async (jobId) => {
            while (true) {
                const res  = await fetch(`/api/v1/jobs/${jobId}`)
                const job  = await res.json()
                updateUI(job.current_step, job.progress)
                if (job.status === 'complete') { showResult(job.result); break }
                if (job.status === 'failed')   { showError(job.error);   break }
                await sleep(3000)  // wait 3 seconds, then poll again
            }
        }
    """
    result = await db.execute(
        select(Job).where(Job.job_id == job_id)
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.status == "complete":
        return job.to_complete_dict()

    if job.status == "failed":
        return {
            **job.to_status_dict(),
            "error": job.error,
        }

    # pending or running — return status without result
    return job.to_status_dict()


# ─── GET /companies/search ────────────────────────────────────────────────────

@router.get("/companies/search")
async def search_companies(q: str = ""):
    """
    Search SEC-registered companies by name or ticker.
    Powers the autocomplete dropdown in the frontend.
    Returns top 10 matches with name, ticker, and CIK.
    """
    if len(q.strip()) < 2:
        return {"companies": []}

    from app.services.edgar_pipeline import _load_ticker_cik_map
    ticker_map = _load_ticker_cik_map()

    q_lower = q.lower().strip()
    matches = []
    for ticker, data in ticker_map.items():
        name = data.get("title", "")
        if q_lower in name.lower() or q_lower in ticker.lower():
            matches.append({
                "name":   name,
                "ticker": ticker,
                "cik":    str(data["cik"]).zfill(10),
            })

    matches.sort(key=lambda x: (
        0 if x["name"].lower().startswith(q_lower) else 1,
        x["name"]
    ))
    return {"companies": matches[:10]}


# ─── GET /evaluations ─────────────────────────────────────────────────────────

MAX_LIST_LIMIT = 100   # list endpoints return full rows; keep responses bounded


@router.get("/evaluations")
async def list_evaluations(
    limit: int = Query(20, ge=1, le=MAX_LIST_LIMIT),
    db:    AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Evaluation)
        .order_by(desc(Evaluation.created_at))
        .limit(limit)
    )
    rows = result.scalars().all()
    return {"evaluations": [row.to_dict() for row in rows]}


# ─── GET /evaluations/{evaluation_id}/report ─────────────────────────────────
# IMPORTANT: this route must come BEFORE /{evaluation_id}
# Otherwise FastAPI matches "report" as an evaluation_id parameter

@router.get("/evaluations/{evaluation_id}/report")
async def get_evaluation_report(
    evaluation_id: str,
    db:            AsyncSession = Depends(get_db),
):
    """Full structured report with per-agent reasoning and evidence."""
    result = await db.execute(
        select(Evaluation).where(Evaluation.evaluation_id == evaluation_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    agent_outputs = row.agent_outputs or {}
    agent_breakdown = {}
    for agent_name in ["financial", "compliance", "operational", "reputation", "strategic"]:
        output = agent_outputs.get(agent_name)
        if not output:
            agent_breakdown[agent_name] = {"status": "not run"}
            continue
        agent_breakdown[agent_name] = {
            "score":       output.get("score"),
            "confidence":  output.get("confidence"),
            "explanation": output.get("explanation"),
            "reasoning":   output.get("reasoning"),
            "evidence":    output.get("evidence", []),
            "red_flags":   output.get("red_flags", []),
        }

    return {
        "evaluation_id":    row.evaluation_id,
        "vendor_name":      row.vendor_name,
        "evaluated_for":    row.company_name,
        "created_at":       row.created_at.isoformat(),
        "recommendation":   row.recommendation,
        "scores": {
            "composite":  row.composite_score,
            "risk":       row.risk_score,
            "fit":        row.fit_score,
            "confidence": row.confidence,
        },
        "agent_breakdown":  agent_breakdown,
        "red_flags":        row.red_flags,
        "full_explanation": row.final_explanation,
        "weights_used":     row.weights,
    }


# ─── GET /evaluations/{evaluation_id} ────────────────────────────────────────

@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(evaluation_id: str, db: AsyncSession = Depends(get_db)):
    """Returns one evaluation summary by ID."""
    result = await db.execute(
        select(Evaluation).where(Evaluation.evaluation_id == evaluation_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return row.to_dict()


# ─── GET /jobs ────────────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=MAX_LIST_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    """List recent jobs — useful for debugging and the history dashboard."""
    result = await db.execute(
        select(Job).order_by(desc(Job.created_at)).limit(limit)
    )
    jobs = result.scalars().all()
    return {"jobs": [j.to_status_dict() for j in jobs]}


# ─── GET /rag/stats ───────────────────────────────────────────────────────────

@router.get("/rag/stats")
async def rag_stats():
    """Shows what's stored in ChromaDB collections."""
    from app.services.rag_service import get_collection_stats
    return get_collection_stats()


# ─── GET /health (enhanced) ───────────────────────────────────────────────────

@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    """
    Enhanced health check including Redis and database status.
    Called by monitoring systems and the CI/CD pipeline.
    """
    from app.services.cache import redis_health

    redis_status = await redis_health()

    # Quick DB check
    try:
        await db.execute(select(Job).limit(1))
        db_status = "healthy"
    except Exception as e:
        db_status = f"error: {e}"

    return {
        "status":   "ok",
        "database": db_status,
        "redis":    redis_status,
        "version":  "2.0.0",   # Bumped for async job architecture
    }
