import asyncio
from app.agents.meta_agent import run_meta_agent

GOOD_OUTPUT = {
    "score": 80, "explanation": "fine", "evidence": [],
    "confidence": 0.97, "red_flags": [],
}


def make_state(strategic_extra=None, compliance_extra=None):
    return {
        "vendor_name": "TestCo",
        "financial_output":   dict(GOOD_OUTPUT),
        "compliance_output":  {**GOOD_OUTPUT, "sanctions_clear": True, **(compliance_extra or {})},
        "operational_output": dict(GOOD_OUTPUT),
        "reputation_output":  dict(GOOD_OUTPUT),
        "strategic_output":   {**GOOD_OUTPUT, **(strategic_extra or {})},
    }


async def main():
    print("=== Case 1: the original bug — SOC2 dealbreaker, no tier data ===")
    state = make_state(strategic_extra={"dealbreakers_triggered": ["Must hold SOC2 certification"]})
    result = await run_meta_agent(state)
    print(f"  recommendation: {result['final_recommendation']}  (expected REJECT)")
    print(f"  dealbreaker_status: {result['dealbreaker_status']}  (expected hard_reject)")
    print(f"  decision_reason: {result['decision_reason']}")
    assert result["final_recommendation"] == "REJECT"
    assert result["dealbreaker_status"] == "hard_reject"
    print("  PASS\n")

    print("=== Case 2: clean vendor, no dealbreakers — unaffected behavior ===")
    state = make_state()
    result = await run_meta_agent(state)
    print(f"  recommendation: {result['final_recommendation']}  (expected APPROVE)")
    print(f"  dealbreaker_status: {result['dealbreaker_status']}  (expected clear)")
    assert result["final_recommendation"] == "APPROVE"
    assert result["dealbreaker_status"] == "clear"
    print("  PASS\n")

    print("=== Case 3: future state — dealbreaker verified via registry tier ===")
    state = make_state(
        strategic_extra={"dealbreakers_triggered": ["Must hold ISO27001 certification"]},
        compliance_extra={"certification_evidence_tiers": {"ISO27001": "VERIFIED_REGISTRY"}},
    )
    result = await run_meta_agent(state)
    print(f"  recommendation: {result['final_recommendation']}  (expected APPROVE)")
    print(f"  dealbreaker_status: {result['dealbreaker_status']}  (expected clear)")
    assert result["final_recommendation"] == "APPROVE"
    assert result["dealbreaker_status"] == "clear"
    print("  PASS\n")

    print("=== Case 4: future state — self-reported only, no corroboration ===")
    state = make_state(
        strategic_extra={"dealbreakers_triggered": ["Must hold SOC2 certification"]},
        compliance_extra={"certification_evidence_tiers": {"SOC2": "SELF_REPORTED_UNVERIFIABLE"}},
    )
    result = await run_meta_agent(state)
    print(f"  recommendation: {result['final_recommendation']}  (expected REVIEW)")
    print(f"  dealbreaker_status: {result['dealbreaker_status']}  (expected needs_review)")
    assert result["final_recommendation"] == "REVIEW"
    assert result["dealbreaker_status"] == "needs_review"
    print("  PASS\n")

    print("=== Case 5: OFAC still takes priority over dealbreaker logic ===")
    state = make_state(
        strategic_extra={"dealbreakers_triggered": ["Must hold ISO27001 certification"]},
        compliance_extra={
            "sanctions_clear": False,
            "certification_evidence_tiers": {"ISO27001": "VERIFIED_REGISTRY"},
        },
    )
    result = await run_meta_agent(state)
    print(f"  recommendation: {result['final_recommendation']}  (expected REJECT)")
    print(f"  decision_reason: {result['decision_reason']}")
    assert result["final_recommendation"] == "REJECT"
    assert "OFAC" in result["decision_reason"]
    print("  PASS\n")

    print("All cases passed.")


asyncio.run(main())