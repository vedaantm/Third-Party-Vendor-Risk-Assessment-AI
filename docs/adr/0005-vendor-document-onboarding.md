# ADR 0005: Vendor document onboarding before Phase 1

- **Status:** accepted
- **Date:** 2026-11-23
- **Phase:** 5, items 1–3 (built ahead of Phase 1)

## Context

Clients want vendors to send their SOC 2 and ISO 27001 reports through
VendorIQ, have a security questionnaire pre-filled from them, and see the
verified facts in the evaluation. In the plan this is Phase 5, and it leans on
pieces from earlier phases that don't exist yet:

- Keycloak and the `vendor_contact` role (Phase 1), which would limit an
  outside user to one vendor relationship;
- `org_id` from the verified token and row-level security (Phase 1);
- MinIO for files (Phase 4);
- ClamAV for uploads (Phase 5) and Presidio redaction (Phase 3).

Waiting for all of that would delay the feature by several phases. Building it
now without a plan for those pieces would leave tables and routes that Phase 1
has to rewrite.

## Decision

- **Invite links stand in for the `vendor_contact` role.** A client creates a
  vendor engagement and gets a link holding a random token (`secrets`, at least
  256 bits). Only the token's SHA-256 hash is stored
  (`vendor_engagements.invite_token_hash`, unique). The link expires after
  `expires_in_days` (1 to 90, chosen by the client per invite; more than 30
  needs `acknowledge_long_expiry: true`), or after `INVITE_TTL_HOURS`
  (default 7 days) when none is given. It opens exactly one engagement. A wrong or expired
  token gets 404, the same as an engagement that doesn't exist.
- **Every new table is tenant-owned now.** `vendor_engagements`,
  `vendor_documents` and `questionnaire_answers` have `org_id NOT NULL` and an
  index on `(org_id, created_at)`, as Phase 1 requires. Until Phase 1, the
  server fills `org_id` from one constant, `DEFAULT_ORG_ID`. It is never read
  from a request body. Phase 1 changes only where the value comes from (the
  token instead of the setting); the schema stays the same.
- **UUID keys.** The new tables use `Uuid` columns, native `uuid` on Postgres,
  so Phase 1's row-level security policy
  (`org_id = current_setting('app.org_id')::uuid`) works without a type change.
- **Files go to local disk** under `DOCUMENT_STORAGE_DIR` until MinIO arrives
  (Phase 4). `vendor_documents.storage_key` is a path relative to that
  directory, so the move to MinIO keeps the key and changes the backend.
  Uploads are capped at `MAX_UPLOAD_BYTES`, and the SHA-256 of each file is
  stored.
- **No malware scan yet.** ClamAV comes with the rest of Phase 5. Until then
  the server stores files under keys it generates (never the uploaded file
  name) and never serves them back to the browser as active content. Only
  SOC 2 and ISO 27001 PDFs are parsed; other files are stored as they are.
- **Extracted text goes to Groq**, the LLM the agents already use, to read the
  report into structured facts (`vendor_documents.extracted`) and an evidence
  tier.
- **Synthetic reports only.** Tests and evals use generated SOC 2 and ISO 27001
  fixtures. Real reports should not be uploaded until Phase 3 redaction exists.
- **Migration `0002_vendor_onboarding`** adds the three tables and touches no
  existing table. Its downgrade drops only those tables.

## Consequences

- Until Phase 1 the client side has no login, as today, and the vendor side is
  protected only by the invite token. Anyone holding a live link can upload to
  that one engagement, and nothing else.
- All data belongs to one org. Phase 1 must backfill `org_id` on these tables
  if it introduces a different org id for the existing data.
- Uploaded files are on the backend's disk, so they don't survive a container
  rebuild without a volume, and can't be shared between replicas. That is
  acceptable for a single local stack and goes away with MinIO.
- Answers are unique per `(engagement_id, question_id)`, so a re-run of
  autofill updates its own `suggested` rows and leaves the client's `accepted`
  and `edited` ones alone.
- `questionnaire_answers` also has a `created_at` column. The answer rows need
  it for the `(org_id, created_at)` index that every tenant table has.
