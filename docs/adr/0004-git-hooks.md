# ADR 0004: Plain git hooks instead of the pre-commit framework

- **Status:** accepted
- **Date:** 2026-11-17
- **Phase:** 0, item 10

## Context

The plan asks for pre-commit hooks running ruff, mypy, gitleaks and the
`commit-msg` attribution check. The usual tool is the `pre-commit` framework.
Three things count against it here:

- It installs each hook into its own environment, with versions pinned in
  `.pre-commit-config.yaml`. ruff and mypy would then drift from `uv.lock`,
  and mypy would run without the project's dependencies, so it would report
  different errors from CI.
- The lint check is a ratchet against `backend/lint-baseline.json`
  (`scripts/lint_baseline.py`). It is not plain ruff or mypy, so the
  framework's stock hooks don't fit.
- It refuses to install while `core.hooksPath` is set, and the repo already
  uses `.githooks/` for `commit-msg`.

## Decision

- `.githooks/pre-commit` is a bash script:
  - it runs the lint ratchet (`uv run --locked`, the same versions as CI)
    when backend Python or its lint config is staged. That takes about 2 s;
  - it runs gitleaks over the staged changes, using a local binary or else
    the Docker image CI pins by digest. That takes about 1 s.
- Without gitleaks or Docker, the hook warns and lets the commit through,
  since CI scans the full history anyway. A hook that can't run tends to get
  bypassed with `--no-verify`.
- `make hooks` sets `core.hooksPath .githooks`; `make install` runs it.
- `tests/test_git_hooks.py` runs both hooks in a throwaway repo, with stand-ins
  for uv, gitleaks and docker. It also checks that the hook and CI pin the
  same gitleaks image.
- The plan's history check now greps identities and trailers, not whole
  messages. A commit may name the CLAUDE.md file without crediting anyone.

## Consequences

- Lint reads the working tree, not the index. With a partly staged file it
  checks unstaged edits too, so it can block a commit whose staged content is
  clean. Stash the rest first or fix the finding.
- Frontend lint isn't in the hook. The frontend has no ESLint yet, and its
  type check runs in `make check` and CI.
- Hooks are a convenience; CI and branch protection (ADR 0003) are the gate.
