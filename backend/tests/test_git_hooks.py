"""The repo's git hooks (.githooks), run against a throwaway repository.

uv, gitleaks and docker are replaced by stand-ins on PATH that record their
arguments and exit with a chosen status, so these tests check the hook's
wiring (what runs, and when a commit is blocked), not the tools themselves.
"""

import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
HOOKS = REPO / ".githooks"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or "system32" in BASH.lower(),  # WSL's bash cannot see this PATH
    reason="needs bash (Git Bash on Windows)",
)

STUB = """#!/usr/bin/env bash
echo "{name} $*" >> "$STUB_LOG"
exit "${{{var}:-0}}"
"""


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    (work / "backend" / "app").mkdir(parents=True)
    (work / "docs").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("uv", "gitleaks", "docker"):
        stub = bin_dir / name
        stub.write_text(
            STUB.format(name=name, var=f"STUB_{name.upper()}_EXIT"),
            encoding="utf-8",
            newline="\n",
        )
        stub.chmod(0o755)
    log = tmp_path / "calls.log"
    log.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
    }
    subprocess.run(["git", "init", "-q", str(work)], check=True)

    def stage(path: str, text: str = "x = 1\n") -> None:
        (work / path).write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", path], cwd=work, check=True)

    def run(hook: str, *args: str, **stub_exits: int):
        extra = {f"STUB_{k.upper()}_EXIT": str(v) for k, v in stub_exits.items()}
        return subprocess.run(
            [BASH, str(HOOKS / hook), *args],
            cwd=work,
            env={**env, **extra},
            capture_output=True,
            text=True,
            check=False,
        )

    def calls() -> list[str]:
        return log.read_text(encoding="utf-8").splitlines()

    def remove(name: str) -> None:
        (bin_dir / name).unlink()

    return type(
        "Repo",
        (),
        {
            "stage": staticmethod(stage),
            "run": staticmethod(run),
            "calls": staticmethod(calls),
            "remove": staticmethod(remove),
            "path": work,
        },
    )


def test_staged_backend_python_runs_lint_and_gitleaks(repo):
    repo.stage("backend/app/x.py")
    result = repo.run("pre-commit")
    assert result.returncode == 0, result.stderr
    assert repo.calls() == [
        "uv run --locked python scripts/lint_baseline.py",
        "gitleaks git --staged --redact --no-banner .",
    ]


def test_lint_config_change_also_runs_lint(repo):
    repo.stage("backend/lint-baseline.json", "{}\n")
    assert repo.run("pre-commit").returncode == 0
    assert repo.calls()[0].startswith("uv run")


def test_non_python_change_skips_lint_but_scans_for_secrets(repo):
    repo.stage("docs/notes.md", "# notes\n")
    assert repo.run("pre-commit").returncode == 0
    assert repo.calls() == ["gitleaks git --staged --redact --no-banner ."]


def test_lint_failure_blocks_the_commit(repo):
    repo.stage("backend/app/x.py")
    result = repo.run("pre-commit", uv=1)
    assert result.returncode == 1
    assert "commit blocked" in result.stderr


def test_secret_found_blocks_the_commit(repo):
    repo.stage("docs/notes.md")
    assert repo.run("pre-commit", gitleaks=1).returncode == 1


def test_without_gitleaks_docker_runs_the_pinned_image(repo):
    repo.remove("gitleaks")
    repo.stage("docs/notes.md")
    assert repo.run("pre-commit").returncode == 0
    run = repo.calls()[-1]
    assert run.startswith("docker run --rm")
    assert "zricethezav/gitleaks@sha256:" in run
    assert run.endswith("git --staged --redact --no-banner /repo")


def test_without_gitleaks_or_docker_it_warns_and_allows(repo):
    repo.remove("gitleaks")
    repo.stage("docs/notes.md")
    result = repo.run("pre-commit", docker=1)  # `docker info` fails: not running
    assert result.returncode == 0
    assert "gitleaks skipped" in result.stderr


def test_hook_and_ci_pin_the_same_gitleaks_image():
    digest = re.compile(r"zricethezav/gitleaks@sha256:[0-9a-f]{64}")
    hook = digest.findall((HOOKS / "pre-commit").read_text(encoding="utf-8"))
    ci = digest.findall(
        (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    assert len(hook) == 1
    assert set(ci) == set(hook)


@pytest.mark.parametrize(
    ("message", "allowed"),
    [
        ("feat: add x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n", False),
        ("feat: add x\n\nGenerated with [Claude Code](https://claude.com)\n", False),
        ("fix: y\n\nClaude-Session: https://claude.ai/code/abc\n", False),
        ("docs: follow the rules in CLAUDE.md\n", True),
        ("feat: add x\n\nCo-Authored-By: Jane Doe <jane@example.com>\n", True),
    ],
)
def test_commit_msg_rejects_only_ai_attribution(repo, tmp_path, message, allowed):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(message, encoding="utf-8")
    assert (repo.run("commit-msg", str(msg)).returncode == 0) is allowed
