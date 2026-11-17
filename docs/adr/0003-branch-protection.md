# ADR 0003: Protect `main` with a ruleset kept in the repo

- **Status:** accepted
- **Date:** 2026-11-17
- **Phase:** 0, item 9

## Context

CI (item 8) only helps if a red run blocks the merge. GitHub enforces that
with branch protection, which is configured in the web UI and invisible to
code review. GitHub rulesets can be exported and imported as JSON.

## Decision

- `.github/rulesets/main.json` is the source of truth. Import it under
  **Settings → Rules → Rulesets → New ruleset → Import a ruleset**. After a
  change to the file, delete the old ruleset and import it again.
- Rules on the default branch:
  - changes arrive through a pull request; no direct pushes, force-pushes or
    deletion;
  - all five CI jobs must pass, on a branch that is up to date with `main`;
  - review threads must be resolved;
  - linear history, so PRs merge by **rebase** or **squash**, not a merge
    commit.
- Required approvals: 0. There is one maintainer, and GitHub does not let an
  author approve their own PR. Raise it when a second maintainer joins.
- No bypass actors, the owner included.
- Default merge method: **rebase and merge**, which keeps each conventional
  commit. Squash is allowed for PRs whose commits are noise; the squashed
  message must then be written as a conventional commit.
- A required check matches the job's `name:` in `ci.yml`. Renaming a job
  means updating this file in the same PR, or every PR is blocked waiting
  for a check that no longer exists.

## Consequences

- Dependabot PRs also need green checks and must be rebased when `main`
  moves (`@dependabot rebase`).
- Merge commits stop appearing on `main`; history before this ADR keeps them.
- Rulesets are enforced on public repositories, and on private ones only on a
  paid plan.
