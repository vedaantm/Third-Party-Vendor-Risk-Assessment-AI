"""
Meta-agent decision logic: APPROVE / REVIEW / REJECT, OFAC, dealbreakers,
missing and truncated agent outputs, score formulas and the CPO explanation.
Pure Python — no LLM or network.
"""

import asyncio
from itertools import combinations

import pytest

from app.agents.base_agent import parse_agent_output
from app.agents.meta_agent import run_meta_agent, _get_action_items

AGENTS = ["financial", "compliance", "operational", "reputation", "strategic"]


def output(score=80.0, confidence=0.97, **extra):
    return {
        "score": score, "explanation": "fine", "evidence": [],
        "confidence": confidence, "red_flags": [], **extra,
    }


def make_state(ofac_status="clear", ofac_matches=None, **outputs):
    """All five agents healthy and OFAC clear unless overridden."""
    state = {
        "vendor_name": "TestCo",
        "compliance_data": {
            "ofac_status": ofac_status,
            "ofac_matches": ofac_matches or [],
        },
    }
    for name in AGENTS:
        state[f"{name}_output"] = outputs.get(name, output())
    return state


def run(state):
    return asyncio.run(run_meta_agent(state))


# ── Baseline ──────────────────────────────────────────────

def test_clean_vendor_is_approved():
    result = run(make_state())
    assert result["final_recommendation"] == "APPROVE"
    assert result["unavailable_agents"] == []
    assert result["ofac_status"] == "clear"


# ── Failed agents (bug: failure used to count as a real 0 score) ──

def test_failed_agent_weight_is_redistributed_not_scored_as_zero():
    state = make_state(financial=None)
    state.update({
        "weight_financial": 0.30, "weight_compliance": 0.25,
        "weight_operational": 0.20, "weight_reputation": 0.10,
        "weight_strategic": 0.15,
    })
    result = run(state)
    # Remaining agents all score 80 → redistributed composite is exactly 80.
    # Scoring the failed agent as 0 would have produced 56.
    assert result["composite_score"] == pytest.approx(80.0)
    assert result["unavailable_agents"] == ["financial"]


def test_failed_agent_blocks_auto_approval():
    result = run(make_state(operational=None))
    assert result["final_recommendation"] == "REVIEW"
    assert "operational" in result["decision_reason"]


def test_failed_agent_blocks_auto_reject_on_partial_data():
    low = output(score=20.0, confidence=0.9)
    state = make_state(
        financial=None, compliance=low, operational=low,
        reputation=low, strategic=low,
    )
    assert run(state)["final_recommendation"] == "REVIEW"


def test_all_agents_failed_goes_to_review():
    state = make_state(**{name: None for name in AGENTS})
    result = run(state)
    assert result["final_recommendation"] == "REVIEW"
    assert result["unavailable_agents"] == sorted(AGENTS)


def test_zero_weights_on_remaining_agents_do_not_crash():
    state = make_state(financial=None)
    state.update({f"weight_{name}": 0.0 for name in AGENTS})
    state["weight_financial"] = 1.0
    assert run(state)["final_recommendation"] == "REVIEW"


# ── Decision ordering (bug: confidence gate pre-empted REJECT) ──

def test_low_score_with_reasonable_confidence_is_rejected():
    low = output(score=20.0, confidence=0.80)
    result = run(make_state(**{name: low for name in AGENTS}))
    assert result["final_recommendation"] == "REJECT"


def test_low_score_with_low_confidence_goes_to_review():
    low = output(score=20.0, confidence=0.30)
    result = run(make_state(**{name: low for name in AGENTS}))
    assert result["final_recommendation"] == "REVIEW"
    assert "too low to auto-reject" in result["decision_reason"]


def test_high_score_below_confidence_threshold_goes_to_review():
    good = output(score=90.0, confidence=0.90)
    result = run(make_state(**{name: good for name in AGENTS}))
    assert result["final_recommendation"] == "REVIEW"
    assert "auto-approval threshold" in result["decision_reason"]


def test_mid_band_score_goes_to_review():
    mid = output(score=60.0, confidence=0.97)
    result = run(make_state(**{name: mid for name in AGENTS}))
    assert result["final_recommendation"] == "REVIEW"
    assert "review band" in result["decision_reason"]


# ── OFAC (bug: unknown → clear; status lost if compliance agent failed) ──

def test_ofac_match_rejects_even_when_compliance_agent_failed():
    result = run(make_state(ofac_status="match", compliance=None,
                            ofac_matches=["BAD CO [SDGT] (SDN #1)"]))
    assert result["final_recommendation"] == "REJECT"
    assert "OFAC" in result["decision_reason"]
    assert "BAD CO" in result["decision_reason"]


def test_ofac_possible_match_goes_to_review():
    result = run(make_state(ofac_status="possible_match"))
    assert result["final_recommendation"] == "REVIEW"
    assert "Possible OFAC" in result["decision_reason"]


def test_ofac_unavailable_is_never_treated_as_clear():
    result = run(make_state(ofac_status="unavailable"))
    assert result["final_recommendation"] == "REVIEW"
    assert "could not be completed" in result["decision_reason"]


def test_ofac_match_outranks_dealbreaker_clearance():
    state = make_state(
        ofac_status="match",
        strategic=output(dealbreakers_triggered=["Must hold ISO27001 certification"]),
        compliance=output(certification_evidence_tiers={"ISO27001": "VERIFIED_REGISTRY"}),
    )
    assert run(state)["final_recommendation"] == "REJECT"


def test_legacy_state_without_compliance_data_uses_sanctions_clear():
    state = make_state(compliance=output(sanctions_clear=False))
    del state["compliance_data"]
    assert run(state)["final_recommendation"] == "REJECT"

    state = make_state(compliance=output(sanctions_clear=True))
    del state["compliance_data"]
    assert run(state)["final_recommendation"] == "APPROVE"


def test_legacy_state_with_no_sanctions_information_is_not_clear():
    state = make_state(compliance=output())   # no sanctions_clear key
    del state["compliance_data"]
    assert run(state)["final_recommendation"] == "REVIEW"


# ── Dealbreakers (bug: "SOC2" never matched the "SOC2_Type2" tier key) ──

def dealbreaker_state(dealbreakers, tiers):
    return make_state(
        strategic=output(dealbreakers_triggered=dealbreakers),
        compliance=output(certification_evidence_tiers=tiers),
    )


def test_soc2_dealbreaker_cleared_by_verified_soc2_type2():
    result = run(dealbreaker_state(
        ["Must hold SOC2 certification"], {"SOC2_Type2": "VERIFIED_REGISTRY"}))
    assert result["dealbreaker_status"] == "clear"
    assert result["final_recommendation"] == "APPROVE"


def test_soc2_dealbreaker_uses_strongest_tier_in_family():
    result = run(dealbreaker_state(
        ["no SOC 2 Type II report"],
        {"SOC2_Type1": "SELF_REPORTED_UNVERIFIABLE", "SOC2_Type2": "CORROBORATED"}))
    assert result["dealbreaker_status"] == "clear"


def test_dealbreaker_without_evidence_is_hard_reject():
    result = run(dealbreaker_state(["Must hold SOC2 certification"], {}))
    assert result["dealbreaker_status"] == "hard_reject"
    assert result["final_recommendation"] == "REJECT"


def test_self_reported_dealbreaker_needs_review():
    result = run(dealbreaker_state(
        ["Must hold SOC2 certification"], {"SOC2": "SELF_REPORTED_UNVERIFIABLE"}))
    assert result["dealbreaker_status"] == "needs_review"
    assert result["final_recommendation"] == "REVIEW"


def test_non_cert_dealbreaker_is_hard_reject():
    result = run(dealbreaker_state(["criminal investigation"], {}))
    assert result["dealbreaker_status"] == "hard_reject"


def test_dealbreakers_as_bare_string_are_not_split_into_characters():
    result = run(dealbreaker_state(
        "Must hold ISO27001 certification", {"ISO27001": "VERIFIED_REGISTRY"}))
    assert result["dealbreaker_status"] == "clear"


# ── Action items ──────────────────────────────────────────

def test_security_red_flag_does_not_trigger_sec_action():
    text = _get_action_items("REVIEW", 60, 0.9, ["[COMPLIANCE] weak security posture"])
    assert "FTC/SEC" not in text


def test_sec_red_flag_triggers_sec_action():
    text = _get_action_items("REVIEW", 60, 0.9, ["[REPUTATION] SEC investigation opened"])
    assert "FTC/SEC" in text


def test_ofac_and_missing_agent_actions_listed_for_review():
    text = _get_action_items("REVIEW", 60, 0.9, [], "possible_match", ["financial"])
    assert "OFAC" in text
    assert "financial" in text


# ── How many unavailable agents force human review? ───────
# Answer: one. Any missing agent blocks both auto-approve and auto-reject.

@pytest.mark.parametrize("missing_count", [1, 2, 3, 4, 5])
def test_any_number_of_unavailable_agents_forces_review(missing_count):
    for missing in combinations(AGENTS, missing_count):
        result = run(make_state(**{name: None for name in missing}))
        assert result["final_recommendation"] == "REVIEW", missing
        assert result["unavailable_agents"] == sorted(missing)


def test_zero_unavailable_agents_allows_automated_decision():
    assert run(make_state())["final_recommendation"] == "APPROVE"


@pytest.mark.parametrize("missing_agent", AGENTS)
def test_single_unavailable_agent_blocks_approval_of_otherwise_perfect_vendor(missing_agent):
    perfect = output(score=100.0, confidence=1.0)
    state = make_state(**{name: perfect for name in AGENTS})
    state[f"{missing_agent}_output"] = None
    result = run(state)
    assert result["final_recommendation"] == "REVIEW"
    assert missing_agent in result["decision_reason"]


def test_hard_rejects_still_apply_when_agents_are_unavailable():
    # OFAC match and dealbreaker rejection outrank the missing-agent gate
    assert run(make_state(ofac_status="match", financial=None))["final_recommendation"] == "REJECT"
    state = make_state(
        financial=None,
        strategic=output(dealbreakers_triggered=["criminal investigation"]),
    )
    assert run(state)["final_recommendation"] == "REJECT"


# ── Truncated agent output ────────────────────────────────
# A truncated Step 2 reply is salvaged by parse_agent_output only when both
# score and confidence survived. The meta-agent counts it at full weight and
# surfaces the truncation red flag, but because red flags after the cut-off
# may be lost it forces REVIEW: never auto-approve, never auto-reject on
# score. Only an OFAC match or a hard dealbreaker can still reject.

TRUNCATED_RAW = (
    '{"score": 90, "confidence": 0.97, '
    '"explanation": "Strong liquidity and low leverage", "red_flags": ["D/E'
)


def truncated_output():
    return parse_agent_output(TRUNCATED_RAW)


def test_truncated_output_is_counted_at_full_weight():
    state = make_state(financial=truncated_output())
    result = run(state)
    assert result["unavailable_agents"] == []
    # 90 * 0.30 + 80 * 0.70 with default weights
    assert result["composite_score"] == pytest.approx(83.0)


def test_truncated_output_red_flag_is_surfaced_in_result_and_explanation():
    result = run(make_state(financial=truncated_output()))
    flag = "[FINANCIAL] Agent output was truncated — red flags may be incomplete"
    assert flag in result["all_red_flags"]
    assert "truncated" in result["final_explanation"]


@pytest.mark.parametrize("agent", AGENTS)
def test_truncated_output_forces_review_never_auto_approves(agent):
    # The cut-off reply lost a red flag about D/E; all scores are high and
    # confident, so without the gate this vendor would be auto-approved.
    perfect = output(score=100.0, confidence=1.0)
    state = make_state(**{name: perfect for name in AGENTS})
    state[f"{agent}_output"] = truncated_output()
    result = run(state)
    assert result["final_recommendation"] == "REVIEW"
    assert result["truncated_agents"] == [agent]
    assert "truncated" in result["decision_reason"]


def test_truncated_output_with_low_scores_reviews_instead_of_rejecting():
    low = output(score=20.0, confidence=0.9)
    truncated_low = parse_agent_output('{"score": 20, "confidence": 0.9, "explanation": "Weak liquidity ratios')
    state = make_state(financial=truncated_low, compliance=low, operational=low,
                       reputation=low, strategic=low)
    assert run(state)["final_recommendation"] == "REVIEW"


def test_truncated_output_does_not_override_hard_rejects():
    assert run(make_state(ofac_status="match", financial=truncated_output()))["final_recommendation"] == "REJECT"
    state = make_state(
        financial=truncated_output(),
        strategic=output(dealbreakers_triggered=["criminal investigation"]),
    )
    assert run(state)["final_recommendation"] == "REJECT"


def test_multiple_truncated_agents_are_all_listed():
    state = make_state(financial=truncated_output(), reputation=truncated_output())
    result = run(state)
    assert result["truncated_agents"] == ["financial", "reputation"]
    assert "Agents Truncated: financial, reputation" in result["final_explanation"]


def test_untruncated_outputs_report_no_truncation():
    result = run(make_state())
    assert result["truncated_agents"] == []
    assert "Agents Truncated: none" in result["final_explanation"]


def test_truncated_agents_get_an_action_item():
    text = _get_action_items("REVIEW", 80, 0.97, [], "clear", [], ["financial"])
    assert "truncated analysis (financial)" in text


def test_truncated_output_without_confidence_becomes_unavailable_agent():
    # parse_agent_output raises; routes.py records the agent as None
    from app.agents.base_agent import AgentOutputError
    with pytest.raises(AgentOutputError):
        parse_agent_output('{"score": 90, "explanation": "Strong liquid')
    result = run(make_state(financial=None))
    assert result["final_recommendation"] == "REVIEW"


# ── Score formulas ────────────────────────────────────────

def test_risk_and_fit_score_formulas():
    state = make_state(
        financial=output(score=60), compliance=output(score=80),
        strategic=output(score=50), operational=output(score=70),
        reputation=output(score=90),
    )
    result = run(state)
    assert result["risk_score"] == pytest.approx(60 * 0.55 + 80 * 0.45)
    assert result["fit_score"] == pytest.approx(50 * 0.50 + 70 * 0.35 + 90 * 0.15)


def test_risk_and_fit_use_neutral_50_for_missing_agents():
    result = run(make_state(financial=None, strategic=None))
    assert result["risk_score"] == pytest.approx(50 * 0.55 + 80 * 0.45)
    assert result["fit_score"] == pytest.approx(50 * 0.50 + 80 * 0.35 + 80 * 0.15)


def test_custom_weights_are_applied():
    state = make_state(financial=output(score=100), compliance=output(score=0))
    state.update({
        "weight_financial": 0.5, "weight_compliance": 0.5,
        "weight_operational": 0.0, "weight_reputation": 0.0, "weight_strategic": 0.0,
    })
    assert run(state)["composite_score"] == pytest.approx(50.0)


def test_low_confidence_agent_applies_085_penalty():
    state = make_state(financial=output(confidence=0.4))
    expected = (0.4 + 0.97 * 4) / 5 * 0.85
    assert run(state)["aggregate_confidence"] == pytest.approx(expected, abs=1e-4)


def test_no_penalty_when_all_confidences_at_least_half():
    state = make_state(financial=output(confidence=0.5))
    expected = (0.5 + 0.97 * 4) / 5
    assert run(state)["aggregate_confidence"] == pytest.approx(expected, abs=1e-4)


# ── Explanation and action items ──────────────────────────

def test_explanation_contains_scores_flags_and_ofac_line():
    state = make_state(reputation=output(red_flags=["Data breach reported [Reuters]"]))
    text = run(state)["final_explanation"]
    assert "VENDOR EVALUATION SUMMARY — TestCo" in text
    assert "Composite Score" in text and "Financial: 80.0/100" in text
    assert "[REPUTATION] Data breach reported [Reuters]" in text
    assert "OFAC Screening  : clear" in text
    assert "Agents Missing  : none" in text


def test_reject_action_items():
    text = _get_action_items("REJECT", 20, 0.9, [])
    assert text.startswith("1. Do not proceed")


def test_approve_action_items():
    text = _get_action_items("APPROVE", 90, 0.97, [])
    assert "Proceed with contract negotiation" in text


@pytest.mark.parametrize("flag, expected", [
    ("data breach in 2024", "incident report"),
    ("revenue concentration above 50%", "concentration clause"),
    ("missing certification SOC2", "missing"),
])
def test_review_action_items_follow_red_flags(flag, expected):
    assert expected in _get_action_items("REVIEW", 60, 0.9, [flag])


def test_review_with_low_confidence_requests_more_data():
    assert "75%" in _get_action_items("REVIEW", 60, 0.6, [])
