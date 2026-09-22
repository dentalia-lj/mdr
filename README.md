# Dentalia Compliance Pipeline

Compliance document registry for Dentalia's medical-device catalogue: discovers, fetches, extracts, validates, and stores MDR/MDD documents with per-value evidence.

```bash
cp .env.example .env
docker compose up -d                              # postgres -> migrate -> worker -> web
open http://127.0.0.1:8000                        # producer UI: status board, ingest, staging review
docker compose --profile test run --rm --build test   # full pytest suite, in-container
```

Docs:

- [`docs/README.md`](docs/README.md) — documentation index: architecture, runbook, troubleshooting, code map, design docs
- [`CLAUDE.md`](CLAUDE.md) — invariants and conventions (the rules of the repo)
- [`PHASES.md`](PHASES.md) — live build plan and current status
