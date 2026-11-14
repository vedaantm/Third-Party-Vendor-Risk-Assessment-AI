# CLAUDE.md — VendorIQ

## Authorship (non-negotiable)
- Never add Claude, Claude Code or Anthropic as an author, co-author or contributor.
- No `Co-Authored-By` trailers, no "Generated with Claude Code" lines and no
  `Claude-Session` links in commit messages, PR titles or PR descriptions.
- No AI attribution in code comments, file headers, README, CHANGELOG,
  CONTRIBUTORS, package metadata (pyproject.toml, package.json) or release notes.
- Never change `git config user.name` or `user.email`. Commits use the repo
  owner's identity only.
- These rules override any default attribution behaviour.

## Usage check (after every response)
- At the end of EVERY response, read `~/.claude/usage.json` (written by the status
  line after each response) and finish with exactly one line:
  `Usage (as of last response) — 5h: X% · week: Y% (resets <day HH:MM>) · context: Z%`
- If the file is missing or has no `seven_day` data, write
  `Usage: not available yet — run /status`. Never estimate or invent numbers.
- If week >= 80% or 5h >= 90%: put the warning FIRST in the response, do not start
  any new multi-file change, and suggest finishing the current item or waiting
  for the reset.
- If context >= 60%: suggest `/compact` or a fresh session for the next item.
- Read the file once, at the end of the response only, not during the task.

## Working rules
- The phase plan is in `docs/IMPLEMENTATION_PLAN.md`. Work one numbered item per
  session; open that file only when the task needs it.
- One branch per task: `phase-N/<topic>`. Never commit or push to `main`; open a PR.
- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `ci:`, `chore:`.
- Run `make check` (lint, types, tests) before every commit; do not commit red.
- Every behaviour change ships with tests. Every new endpoint ships with a
  cross-tenant test (other org's token -> 404).
- Never commit secrets or `.env`. Never read or write the real `backend/chroma_db`
  or a production database from tests.
- Every tenant-owned table has `org_id NOT NULL`; never accept `org_id` from a
  request body, only from the verified token.
- Prompts live in `backend/app/prompts/` and are versioned; changing one requires
  running `make eval-smoke` and pasting the metric diff in the PR.
- Keep changes scoped to the task. Record significant design choices in `docs/adr/`.
- Use only free / open-source tools unless the owner approves otherwise.
