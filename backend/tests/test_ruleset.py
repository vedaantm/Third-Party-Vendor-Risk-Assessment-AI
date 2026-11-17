"""The committed branch ruleset must require exactly the jobs CI defines.

A required check that no CI job reports blocks every PR; a CI job missing
from the ruleset can fail without blocking the merge.
"""

import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
RULESET = REPO / ".github" / "rulesets" / "main.json"


def _ci_job_names() -> set[str]:
    # Job display names are the only 4-space-indented `name:` keys in ci.yml
    text = CI.read_text(encoding="utf-8")
    return set(re.findall(r"^    name: (.+?)\s*$", text, flags=re.MULTILINE))


def _ruleset() -> dict:
    return json.loads(RULESET.read_text(encoding="utf-8"))


def _rule(kind: str) -> dict:
    return next(r for r in _ruleset()["rules"] if r["type"] == kind)


def test_required_checks_match_ci_jobs():
    required = {
        c["context"]
        for c in _rule("required_status_checks")["parameters"]["required_status_checks"]
    }
    assert required == _ci_job_names()
    assert len(required) == 5


def test_main_requires_prs_linear_history_and_no_bypass():
    ruleset = _ruleset()
    assert ruleset["enforcement"] == "active"
    assert ruleset["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert ruleset["bypass_actors"] == []
    kinds = {r["type"] for r in ruleset["rules"]}
    assert {
        "pull_request",
        "required_linear_history",
        "non_fast_forward",
        "deletion",
    } <= kinds
    assert "merge" not in _rule("pull_request")["parameters"]["allowed_merge_methods"]
    assert _rule("required_status_checks")["parameters"][
        "strict_required_status_checks_policy"
    ]
