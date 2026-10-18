import re

from app.orchestrator.state import VendorRiskState

TRACKED_CERTS = [
    "SOC1", "SOC2", "SOC3", "ISO27001", "ISO27701", "ISO9001", "ISO14001",
    "ISO45001", "ISO13485", "ISO22301", "ISO20000", "ISO42001", "GDPR",
    "CCPA", "HIPAA", "PCI_DSS", "FedRAMP", "NIST_CSF", "SOX", "FCPA", "CSA_STAR",
]


def _normalize(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


# ── Decision thresholds ───────────────────────────────────
APPROVE_MIN_COMPOSITE  = 70
REJECT_BELOW_COMPOSITE = 45
# Auto-approval requires every agent to report this confidence on average —
# matches the golden dataset's label criteria ("confidence > 95%").
APPROVE_MIN_CONFIDENCE = 0.95
# A low composite only auto-rejects if the agents are at least this sure of
# it; below this, a low score may just reflect thin data → human review.
REJECT_MIN_CONFIDENCE  = 0.50

TIER_RANK = {
    "VERIFIED_REGISTRY": 3,
    "CORROBORATED": 2,
    "SELF_REPORTED_UNVERIFIABLE": 1,
    "CONTRADICTED": 0,
}


def _find_cert_keyword_in_text(text: str) -> str | None:
    norm_text = _normalize(text)
    for cert in TRACKED_CERTS:
        if _normalize(cert) in norm_text:
            return cert
    return None


def _tier_for_cert(cert: str | None, tiers: dict) -> str | None:
    """
    Dealbreaker text names a cert family ("SOC2"), but tiers are keyed by the
    specific cert ("SOC2_Type2"). Return the strongest tier among all keys in
    that family. Previously tiers.get("SOC2") always missed, so a SOC2
    dealbreaker was hard-rejected even with a verified SOC2_Type2.
    """
    if not cert:
        return None
    family = _normalize(cert)
    candidates = [
        tier for key, tier in tiers.items()
        if _normalize(key).startswith(family)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda t: TIER_RANK.get(t, -1))


def _resolve_ofac_status(state: dict, compliance_output: dict | None) -> str:
    """
    The deterministic OFAC screen lives in compliance_data and is the source
    of truth — it must not depend on the compliance LLM agent succeeding.
    Falls back to the agent's sanctions_clear only for states built without
    compliance_data (older callers / scripts).
    """
    status = (state.get("compliance_data") or {}).get("ofac_status")
    if status:
        return status
    if compliance_output is not None:
        sanctions_clear = compliance_output.get("sanctions_clear")
        if sanctions_clear is True:
            return "clear"
        if sanctions_clear is False:
            return "match"
    return "unavailable"


def _evaluate_dealbreakers(strategic_output: dict | None, compliance_output: dict | None) -> dict:
    """
    Resolves strategic_output['dealbreakers_triggered'] — previously ignored
    entirely by the recommendation logic — into hard_reject / needs_review /
    clear.

    Forward-compatible with the evidence-tier system (registry connectors +
    self-attestation, planned next): if compliance_output carries a
    `certification_evidence_tiers` dict mapping a tracked cert keyword to
    VERIFIED_REGISTRY / CORROBORATED / SELF_REPORTED_UNVERIFIABLE /
    CONTRADICTED, a triggered dealbreaker matching a tracked cert is resolved
    against that tier instead of being an automatic reject. Until that data
    exists upstream, every cert-matching dealbreaker falls through to the
    same safe default as a non-cert dealbreaker: hard reject. That default
    is intentional — never auto-approve or silently downgrade a dealbreaker
    without positive evidence to justify it.
    """
    if not strategic_output:
        return {"status": "clear", "reason": None}

    triggered = strategic_output.get("dealbreakers_triggered") or []
    if isinstance(triggered, str):
        # LLM returned a bare string — don't iterate it character by character
        triggered = [triggered]
    triggered = [str(d) for d in triggered if str(d).strip()]
    if not triggered:
        return {"status": "clear", "reason": None}

    tiers = (compliance_output or {}).get("certification_evidence_tiers") or {}

    unresolved, needs_review, cleared = [], [], []

    for dealbreaker in triggered:
        cert = _find_cert_keyword_in_text(dealbreaker)
        tier = _tier_for_cert(cert, tiers)

        if tier in ("VERIFIED_REGISTRY", "CORROBORATED"):
            cleared.append(dealbreaker)
        elif tier == "SELF_REPORTED_UNVERIFIABLE":
            needs_review.append(dealbreaker)
        else:
            unresolved.append(dealbreaker)

    if unresolved:
        return {
            "status": "hard_reject",
            "reason": (
                "Client-specified dealbreaker(s) triggered with no verifying "
                f"evidence: {', '.join(unresolved)}."
            ),
        }
    if needs_review:
        return {
            "status": "needs_review",
            "reason": (
                "Dealbreaker(s) triggered but only self-reported, with no "
                f"independent corroboration available: {', '.join(needs_review)}. "
                "Expected for evidence types (e.g. SOC reports) with no public "
                "registry — route to human reviewer rather than auto-reject "
                "or auto-approve."
            ),
        }
    return {
        "status": "clear",
        "reason": (
            f"Dealbreaker(s) initially flagged but confirmed satisfied on "
            f"verification: {', '.join(cleared)}."
        ),
    }


async def run_meta_agent(state: VendorRiskState) -> dict:
    """
    Aggregates all agent scores into a final decision.
    Applies company-defined weights.
    Produces composite score, risk score, fit score,
    recommendation, and CPO-ready explanation.
    """

    weights = {
        "financial":   state.get("weight_financial",   0.30),
        "compliance":  state.get("weight_compliance",  0.25),
        "operational": state.get("weight_operational", 0.20),
        "reputation":  state.get("weight_reputation",  0.10),
        "strategic":   state.get("weight_strategic",   0.15),
    }

    outputs = {
        "financial":   state.get("financial_output"),
        "compliance":  state.get("compliance_output"),
        "operational": state.get("operational_output"),
        "reputation":  state.get("reputation_output"),
        "strategic":   state.get("strategic_output"),
    }

    # ── Redistribute weights for missing agents ───────────
    available       = {k: v for k, v in outputs.items() if v is not None}
    missing         = {k: v for k, v in outputs.items() if v is None}
    missing_weight  = sum(weights[k] for k in missing)

    total_avail = sum(weights[k] for k in available)
    if missing_weight > 0 and total_avail > 0:
        for k in available:
            weights[k] += weights[k] / total_avail * missing_weight

    # ── Composite score ───────────────────────────────────
    composite = sum(
        outputs[k]["score"] * weights[k]
        for k in available
    )

    # ── Risk score (Financial 55% + Compliance 45%) ───────
    fin_score  = outputs["financial"]["score"]  if outputs.get("financial")  else 50
    comp_score = outputs["compliance"]["score"] if outputs.get("compliance") else 50
    risk_score = (fin_score * 0.55) + (comp_score * 0.45)

    # ── Fit score (Strategic 50% + Operational 35% + Reputation 15%) ──
    strat_score = outputs["strategic"]["score"]   if outputs.get("strategic")   else 50
    ops_score   = outputs["operational"]["score"] if outputs.get("operational") else 50
    rep_score   = outputs["reputation"]["score"]  if outputs.get("reputation")  else 50
    fit_score   = (strat_score * 0.50) + (ops_score * 0.35) + (rep_score * 0.15)

    # ── Confidence aggregation ────────────────────────────
    confidences = [outputs[k]["confidence"] for k in available]
    avg_conf    = sum(confidences) / len(confidences) if confidences else 0

    # Apply penalty if any agent is low confidence
    if any(c < 0.5 for c in confidences):
        avg_conf *= 0.85

    # ── OFAC status (deterministic screen, independent of the LLM agent) ──
    ofac_status  = _resolve_ofac_status(state, outputs.get("compliance"))
    ofac_matches = (state.get("compliance_data") or {}).get("ofac_matches") or []
    ofac_detail  = f" SDN entries: {'; '.join(ofac_matches)}." if ofac_matches else ""

    # ── Dealbreaker override (previously ignored — see _evaluate_dealbreakers) ──
    dealbreaker_result = _evaluate_dealbreakers(
        outputs.get("strategic"), outputs.get("compliance")
    )

    unavailable_agents = sorted(missing)

    # ── Recommendation logic ──────────────────────────────
    # Order matters: hard rejects first, then anything that blocks an
    # automated decision, then score-based outcomes. The confidence gate only
    # guards APPROVE — it previously ran before the score checks, so a vendor
    # scoring 20/100 at 80% confidence came out as REVIEW instead of REJECT.
    if ofac_status == "match":
        recommendation = "REJECT"
        decision_reason = (
            "OFAC sanctions match detected. "
            "Vendor must be rejected immediately — no exceptions." + ofac_detail
        )
    elif dealbreaker_result["status"] == "hard_reject":
        recommendation = "REJECT"
        decision_reason = dealbreaker_result["reason"]
    elif ofac_status == "possible_match":
        recommendation = "REVIEW"
        decision_reason = (
            "Possible OFAC sanctions match — the vendor name partially matches "
            "SDN list entries. A compliance officer must confirm or clear the "
            "match before any decision." + ofac_detail
        )
    elif ofac_status != "clear":
        recommendation = "REVIEW"
        decision_reason = (
            "OFAC sanctions screening could not be completed (SDN list "
            "unavailable). Screen manually before proceeding."
        )
    elif dealbreaker_result["status"] == "needs_review":
        recommendation = "REVIEW"
        decision_reason = dealbreaker_result["reason"]
    elif unavailable_agents:
        # Missing agents are excluded from the confidence average, so without
        # this gate a partial evaluation could auto-approve or auto-reject.
        recommendation = "REVIEW"
        decision_reason = (
            f"{len(unavailable_agents)} of {len(outputs)} analysis agents did not "
            f"produce a usable result ({', '.join(unavailable_agents)}). "
            f"The evaluation is incomplete — route to a human reviewer."
        )
    elif composite < REJECT_BELOW_COMPOSITE and avg_conf >= REJECT_MIN_CONFIDENCE:
        recommendation = "REJECT"
        decision_reason = (
            f"Composite score of {composite:.1f} is below the minimum "
            f"acceptable threshold. Do not proceed with this vendor."
        )
    elif composite < REJECT_BELOW_COMPOSITE:
        recommendation = "REVIEW"
        decision_reason = (
            f"Composite score of {composite:.1f} is below the rejection "
            f"threshold, but confidence ({avg_conf:.0%}) is too low to "
            f"auto-reject — the low score may reflect missing data."
        )
    elif avg_conf < APPROVE_MIN_CONFIDENCE:
        recommendation = "REVIEW"
        decision_reason = (
            f"Confidence ({avg_conf:.0%}) is below the "
            f"{APPROVE_MIN_CONFIDENCE:.0%} auto-approval threshold. "
            f"Route to human reviewer before proceeding."
        )
    elif composite >= APPROVE_MIN_COMPOSITE:
        recommendation = "APPROVE"
        decision_reason = (
            f"Composite score of {composite:.1f} with {avg_conf:.0%} "
            f"confidence meets approval criteria."
        )
    else:
        recommendation = "REVIEW"
        decision_reason = (
            f"Composite score of {composite:.1f} falls in the review band. "
            f"Proceed with caution — additional due diligence recommended."
        )

    # ── Collect all red flags across agents ───────────────
    all_red_flags = []
    for k in available:
        flags = outputs[k].get("red_flags", [])
        for f in flags:
            all_red_flags.append(f"[{k.upper()}] {f}")

    # ── Build CPO explanation ─────────────────────────────
    score_lines = "\n".join([
        f"  • {k.capitalize()}: {outputs[k]['score']:.1f}/100"
        for k in available
    ])

    explanation = f"""
VENDOR EVALUATION SUMMARY — {state.get('vendor_name', 'Unknown')}

RECOMMENDATION: {recommendation}
{decision_reason}

SCORES:
{score_lines}

  Composite Score : {composite:.1f}/100
  Risk Score      : {risk_score:.1f}/100  (lower = more risk)
  Strategic Fit   : {fit_score:.1f}/100
  Confidence      : {avg_conf:.0%}

  OFAC Screening  : {ofac_status}
  Agents Missing  : {', '.join(unavailable_agents) if unavailable_agents else 'none'}

RED FLAGS ({len(all_red_flags)} total):
{chr(10).join(f'  ⚠ {f}' for f in all_red_flags) if all_red_flags else '  None detected'}

WHAT YOU SHOULD DO:
{_get_action_items(recommendation, composite, avg_conf, all_red_flags, ofac_status, unavailable_agents)}
"""

    return {
        "composite_score":     round(composite, 2),
        "risk_score":          round(risk_score, 2),
        "fit_score":           round(fit_score, 2),
        "aggregate_confidence": round(avg_conf, 4),
        "final_recommendation": recommendation,
        "decision_reason":     decision_reason,
        "dealbreaker_status":  dealbreaker_result["status"],
        "ofac_status":         ofac_status,
        "unavailable_agents":  unavailable_agents,
        "all_red_flags":       all_red_flags,
        "final_explanation":   explanation.strip(),
        "pipeline_status":     "complete"
    }


def _get_action_items(
    recommendation: str,
    composite: float,
    confidence: float,
    red_flags: list,
    ofac_status: str = "clear",
    unavailable_agents: list | None = None,
) -> str:
    actions = []

    if recommendation == "REJECT":
        return (
            "1. Do not proceed with this vendor.\n"
            "2. Document rejection reason in audit trail.\n"
            "3. Notify procurement team to source alternatives immediately."
        )

    if recommendation == "APPROVE":
        return (
            "1. Proceed with contract negotiation.\n"
            "2. Schedule quarterly performance reviews.\n"
            "3. Set up automated monitoring alerts for score changes."
        )

    # REVIEW — build specific actions based on what's actually wrong
    actions.append(
        "1. Escalate to senior procurement officer for manual review."
    )

    # Confidence-specific actions
    if confidence < 0.75:
        actions.append(
            "2. Do NOT approve until confidence exceeds 75% — "
            "request missing data from vendor."
        )
    elif confidence < 0.95:
        actions.append(
            "2. Confidence is acceptable but below auto-approval — "
            "one senior sign-off required."
        )

    # Flag-specific actions
    action_num = 3
    flag_text  = " ".join(red_flags).lower()

    if ofac_status != "clear":
        actions.append(
            f"{action_num}. Run a manual OFAC/sanctions screen and record the "
            "disposition before any further step."
        )
        action_num += 1

    if unavailable_agents:
        actions.append(
            f"{action_num}. Re-run the evaluation or manually assess the missing "
            f"areas: {', '.join(unavailable_agents)}."
        )
        action_num += 1

    # Word boundaries — a bare substring check matched "sec" in "security"
    if re.search(r"\b(ftc|sec)\b", flag_text):
        actions.append(
            f"{action_num}. Investigate FTC/SEC regulatory action — "
            "request legal team review before proceeding."
        )
        action_num += 1

    if "concentration" in flag_text:
        actions.append(
            f"{action_num}. Negotiate revenue concentration clause "
            "into contract to protect against single-customer dependency."
        )
        action_num += 1

    if "breach" in flag_text:
        actions.append(
            f"{action_num}. Request full incident report on data breach "
            "and confirm remediation steps taken."
        )
        action_num += 1

    if "certif" in flag_text or "missing" in flag_text:
        actions.append(
            f"{action_num}. Require vendor to provide missing "
            "certifications before contract signing."
        )
        action_num += 1

    if "operational" in flag_text and "stub" in flag_text:
        actions.append(
            f"{action_num}. Request vendor's operational SLA history "
            "and business continuity plan directly."
        )
        action_num += 1

    return "\n".join(actions)