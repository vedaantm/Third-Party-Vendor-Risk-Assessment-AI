"""
Lint ratchet: ruff, ruff format and mypy may not get worse than the baseline.

The code predates linting, so it has findings (many in protected files that
are not changed casually). Rather than fix them all at once or switch the
checks off, lint-baseline.json records today's findings per file and rule,
and the check fails on anything beyond that:

  - a new ruff or mypy finding (file + rule count above the baseline)
  - a new file that is not ruff-formatted

Fixing findings never fails the check. Run `make lint-baseline` afterwards
to lower the recorded counts, so the slack cannot be reused.

    python scripts/lint_baseline.py           # check (CI, make lint)
    python scripts/lint_baseline.py --update  # rewrite the baseline
"""

import json
import pathlib
import re
import subprocess
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
BASELINE = BACKEND / "lint-baseline.json"

Counts = dict[str, dict[str, int]]  # file -> rule -> count

_MYPY_LINE = re.compile(
    r"^(?P<file>[^:]+):\d+(?::\d+)?: error: .*\[(?P<code>[a-z0-9-]+)\]\s*$"
)


def _rel(path: str) -> str:
    p = pathlib.Path(path)
    if p.is_absolute():
        return p.resolve().relative_to(BACKEND).as_posix()
    # Tools on Windows print backslashes; keep keys identical on every OS
    return path.replace("\\", "/")


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", check=False
    )


def _add(counts: Counts, file: str, rule: str) -> None:
    by_rule = counts.setdefault(file, {})
    by_rule[rule] = by_rule.get(rule, 0) + 1


def ruff_counts(report: list[dict]) -> Counts:
    counts: Counts = {}
    for item in report:
        _add(counts, _rel(item["filename"]), item["code"] or "syntax-error")
    return counts


def unformatted_files(report: list[dict]) -> list[str]:
    return sorted({_rel(item["filename"]) for item in report})


def mypy_counts(output: str) -> Counts:
    counts: Counts = {}
    for line in output.splitlines():
        m = _MYPY_LINE.match(line.strip())
        if m:
            _add(counts, _rel(m["file"]), m["code"])
    return counts


def collect() -> dict:
    ruff = _run(
        sys.executable,
        "-m",
        "ruff",
        "check",
        ".",
        "--output-format",
        "json",
        "--exit-zero",
    )
    fmt = _run(
        sys.executable,
        "-m",
        "ruff",
        "format",
        "--check",
        ".",
        "--output-format",
        "json",
    )
    mypy = _run(sys.executable, "-m", "mypy", "--no-pretty", "--no-error-summary")
    if (
        ruff.returncode != 0
        or mypy.returncode not in (0, 1)
        or fmt.returncode not in (0, 1)
    ):
        sys.exit(f"lint tool failed to run:\n{ruff.stderr}{fmt.stderr}{mypy.stderr}")
    return {
        "ruff": ruff_counts(json.loads(ruff.stdout or "[]")),
        "format": unformatted_files(json.loads(fmt.stdout or "[]")),
        "mypy": mypy_counts(mypy.stdout),
    }


def regressions(baseline: dict, current: dict) -> list[str]:
    """Everything in current that the baseline does not allow."""
    problems = []
    for tool in ("ruff", "mypy"):
        allowed = baseline.get(tool, {})
        for file, by_rule in sorted(current.get(tool, {}).items()):
            for rule, n in sorted(by_rule.items()):
                limit = allowed.get(file, {}).get(rule, 0)
                if n > limit:
                    problems.append(
                        f"{tool}: {file}: {rule} x{n} (baseline allows {limit})"
                    )
    allowed_unformatted = set(baseline.get("format", []))
    for file in current.get("format", []):
        if file not in allowed_unformatted:
            problems.append(
                f"format: {file} is not ruff-formatted (run: ruff format {file})"
            )
    return problems


def improvements(baseline: dict, current: dict) -> int:
    """How many baseline findings are gone — the baseline can be lowered by this much."""
    gone = 0
    for tool in ("ruff", "mypy"):
        for file, by_rule in baseline.get(tool, {}).items():
            for rule, limit in by_rule.items():
                gone += max(0, limit - current.get(tool, {}).get(file, {}).get(rule, 0))
    gone += len(set(baseline.get("format", [])) - set(current.get("format", [])))
    return gone


def main(argv: list[str]) -> int:
    current = collect()
    if "--update" in argv:
        BASELINE.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        total = sum(
            n for t in ("ruff", "mypy") for r in current[t].values() for n in r.values()
        )
        print(
            f"lint baseline written: {total} findings, {len(current['format'])} unformatted files"
        )
        return 0

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    problems = regressions(baseline, current)
    for p in problems:
        print(p)
    gone = improvements(baseline, current)
    if gone:
        print(
            f"{gone} baseline finding(s) fixed - run `make lint-baseline` to lock that in"
        )
    if problems:
        print(
            f"\nlint: {len(problems)} new finding(s) above the baseline in {BASELINE.name}"
        )
        return 1
    print("lint: no new findings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
