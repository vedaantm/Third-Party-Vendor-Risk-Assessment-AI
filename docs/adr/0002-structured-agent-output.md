# ADR 0002: Request the agents' scores as structured output

- **Status:** accepted
- **Date:** 2026-11-14
- **Phase:** 0, item 7

## Context

Each specialist agent makes two LLM calls: a free-text reasoning pass, then a
scoring pass whose reply must be JSON (`score`, `confidence`, `explanation`,
`evidence`, `red_flags`). The scoring reply was plain text. `parse_agent_output`
looked for the outermost braces and, when the JSON was cut off, pulled `score`
and `confidence` out with regular expressions. Every format quirk the model
produced became a parser case.

Groq supports `response_format = json_schema` for `openai/gpt-oss-120b`, and
LangChain exposes it as `with_structured_output(..., method="json_schema")`.

A live check showed the two outcomes:

- normal replies come back as JSON matching the schema;
- a reply cut off by `max_tokens` is not returned as partial text. Groq
  answers `400 json_validate_failed` and puts the partial output in
  `failed_generation`.

## Decision

- `AgentOutput` (Pydantic, `app/agents/structured_output.py`) defines the
  scoring output: all five fields required, `score` 0–100, `confidence` 0–1.
- `run_scoring_call` asks for that schema with `include_raw=True`. Parsed
  output still goes through `validate_agent_output`, the contract the
  meta-agent relies on.
- Fallbacks go to the old text parser, which keeps its truncation salvage:
  - on `json_validate_failed`, it parses `failed_generation`;
  - on JSON that misses the model (for example `score: 850`), it parses the
    raw text, whose validation raises `AgentOutputError`.
  Rate-limit and other API errors propagate as before, for the retry in
  `routes.py`.
- `AGENT_STRUCTURED_OUTPUT` (default `true`) switches back to the plain-text
  call, so Phase 2 evals can compare the two modes.
- The prompts are unchanged. The scoring prompt still describes the JSON it
  wants, which also serves the text mode.

## Consequences

- The model is held to the schema by the API, so malformed JSON is rare. A
  live run of all five agents produced schema-valid output with no fallback.
- Truncated output behaves as before: salvaged when both `score` and
  `confidence` survive, otherwise the agent is marked unavailable.
- The regex salvage stays until Phase 2 evals show that removing it causes no
  regression. `make eval-smoke` does not exist yet, so this change has no
  metric diff. It was checked with the unit tests and the live agent tests.
- Moving to another provider means checking that it supports JSON-schema
  output. If it doesn't, set `AGENT_STRUCTURED_OUTPUT=false`.
