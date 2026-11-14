# backend/scripts

Manual scripts for poking at live services. They are **not tests**: they call
Groq, SEC EDGAR, Yahoo Finance, FedRAMP and DuckDuckGo for real, use your
`backend/.env`, and spend Groq tokens. The offline test suite is in
`backend/tests/` (`make test`).

Run from `backend/` as modules, in the locked environment, so `app.*` imports resolve:

```bash
cd backend
uv run python -m scripts.run_all_agents          # every agent + meta-agent for one vendor
uv run python -m scripts.run_financial_agent     # financial agent only
uv run python -m scripts.run_cert_pipeline       # web cert discovery for one company
uv run python -m scripts.run_compliance_stress   # compliance agent across several vendors
uv run python -m scripts.run_registry_connectors # FedRAMP + CSA STAR lookups
uv run python -m scripts.check_groq_usage        # print Groq rate-limit headers
```

They're named `run_*` rather than `test_*` so pytest never collects them.
