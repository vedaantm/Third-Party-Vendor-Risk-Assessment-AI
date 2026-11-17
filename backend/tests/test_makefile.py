"""Makefile rules that a fresh clone depends on."""

import os
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MAKE = shutil.which("make")

pytestmark = pytest.mark.skipif(MAKE is None, reason="needs GNU make")

# Under `make check` the parent make's flags leak in (e.g. "Entering directory")
ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS", "MAKEOVERRIDES")
}


@pytest.fixture
def clone(tmp_path):
    """A directory with the real Makefile and an example env file."""
    shutil.copy(REPO / "Makefile", tmp_path / "Makefile")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env.example").write_text(
        "GROQ_API_KEY=\n", encoding="utf-8"
    )
    return tmp_path


def _make(clone, target):
    return subprocess.run(
        [MAKE, "-s", target],
        cwd=clone,
        env=ENV,
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_env_is_created_from_the_example(clone):
    result = _make(clone, "backend/.env")
    assert result.returncode == 0, result.stderr
    assert (clone / "backend" / ".env").read_text(encoding="utf-8") == (
        "GROQ_API_KEY=\n"
    )
    assert "GROQ_API_KEY" in result.stdout


def test_existing_env_is_never_overwritten(clone):
    env = clone / "backend" / ".env"
    env.write_text("GROQ_API_KEY=real-key\n", encoding="utf-8")
    assert _make(clone, "backend/.env").returncode == 0
    assert env.read_text(encoding="utf-8") == "GROQ_API_KEY=real-key\n"


def test_up_creates_the_env_before_starting_compose(clone):
    plan = subprocess.run(
        [MAKE, "-n", "up"],
        cwd=clone,
        env=ENV,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert plan[0] == "cp backend/.env.example backend/.env"
    assert plan[-1] == "docker compose up --build"
