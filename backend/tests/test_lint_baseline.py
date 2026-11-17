"""The lint ratchet: findings may go down, never up."""

from scripts import lint_baseline as lb

BASE = {
    "ruff": {"app/a.py": {"F401": 2}},
    "mypy": {"app/b.py": {"arg-type": 1}},
    "format": ["app/old.py"],
}


def test_same_findings_pass():
    assert lb.regressions(BASE, BASE) == []


def test_fewer_findings_pass_and_are_reported_as_improvements():
    current = {"ruff": {"app/a.py": {"F401": 1}}, "mypy": {}, "format": []}
    assert lb.regressions(BASE, current) == []
    # one F401, one arg-type, one formatted file
    assert lb.improvements(BASE, current) == 3


def test_more_of_an_allowed_rule_fails():
    current = {**BASE, "ruff": {"app/a.py": {"F401": 3}}}
    assert lb.regressions(BASE, current) == [
        "ruff: app/a.py: F401 x3 (baseline allows 2)"
    ]


def test_new_rule_in_a_known_file_fails():
    current = {**BASE, "mypy": {"app/b.py": {"arg-type": 1, "index": 1}}}
    assert lb.regressions(BASE, current) == [
        "mypy: app/b.py: index x1 (baseline allows 0)"
    ]


def test_findings_in_a_new_file_fail():
    current = {**BASE, "ruff": {**BASE["ruff"], "app/new.py": {"E501": 1}}}
    assert lb.regressions(BASE, current) == [
        "ruff: app/new.py: E501 x1 (baseline allows 0)"
    ]


def test_unformatted_new_file_fails_but_known_one_does_not():
    current = {**BASE, "format": ["app/new.py", "app/old.py"]}
    assert lb.regressions(BASE, current) == [
        "format: app/new.py is not ruff-formatted (run: ruff format app/new.py)"
    ]


def test_ruff_report_is_counted_per_file_and_rule():
    report = [
        {"filename": "app/a.py", "code": "F401"},
        {"filename": "app/a.py", "code": "F401"},
        {"filename": "app/a.py", "code": None},
    ]
    assert lb.ruff_counts(report) == {"app/a.py": {"F401": 2, "syntax-error": 1}}


def test_absolute_paths_become_backend_relative():
    path = str(lb.BACKEND / "app" / "x.py")
    assert lb.unformatted_files([{"filename": path}, {"filename": path}]) == [
        "app/x.py"
    ]


def test_mypy_output_is_counted_per_file_and_code():
    output = "\n".join(
        [
            "app/b.py:10: error: Incompatible types  [arg-type]",
            "app/b.py:12:5: error: Value of type None is not indexable  [index]",
            "app/b.py:14: note: See https://mypy.readthedocs.io",
            "app\\c.py:3: error: Missing return  [return]",
        ]
    )
    assert lb.mypy_counts(output) == {
        "app/b.py": {"arg-type": 1, "index": 1},
        "app/c.py": {"return": 1},
    }


def test_the_committed_baseline_is_valid():
    import json

    data = json.loads(lb.BASELINE.read_text(encoding="utf-8"))
    assert set(data) == {"ruff", "mypy", "format"}
    assert all(
        isinstance(n, int) and n > 0
        for tool in ("ruff", "mypy")
        for rules in data[tool].values()
        for n in rules.values()
    )
