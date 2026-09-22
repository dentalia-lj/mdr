# One image, many worker-side services (worker replicas, scheduler) per
# CLAUDE.md. DEVIATION (this session, user-directed — see PHASES.md gap note):
# the producer UI is now a physically separate, slim image (Dockerfile.web)
# with none of this image's heavy deps — see that file.
# Base: Playwright's Python image so the browser-fallback fetch tier (S1.4) has
# a working Chromium + system deps without extra setup. Noble = Ubuntu 24.04,
# ships Python 3.12 (satisfies requires-python >=3.12). Tag verified to exist on
# mcr.microsoft.com (manifest inspect); newest available at scaffold time.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble AS app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package. pyproject.toml + app/ are the build inputs (hatchling
# builds the wheel from app/), so both must be present before `pip install .`.
# `.[extract,resolve,ingest,discover,fetch,email]` pulls in pymupdf/pdfplumber
# (S0.2), rapidfuzz (S1.2 RESOLVE), pandas/openpyxl (S1.1 INGEST), httpx (S1.3
# DISCOVER SearchAdapter + S1.4 FETCH transport) and playwright (S1.4 FETCH
# bot-wall tier; already in the base image). `email` (S2.4 email.poll) adds no
# runtime dep — imaplib/email are stdlib and the classifier reuses `extract`'s
# pymupdf — but is listed so the stage surface is explicit. The worker is the
# only process that needs these; the standalone web container (Dockerfile.web)
# deliberately does not install them (it only enqueues jobs, never fetches or
# parses files).
# `-c constraints.txt` pins every package, direct and transitive, to what the
# running images carried on 2026-09-18 -- see that file's header.
COPY pyproject.toml constraints.txt ./
COPY app/ ./app/
RUN pip install -c constraints.txt ".[extract,resolve,ingest,discover,fetch,email]"

# Migrations are a runtime asset (applied by app/db.py), not part of the wheel.
# Copied after install so churn here doesn't bust the dependency layer.
COPY migrations/ ./migrations/

# Playbooks stay in THIS image after slice 4 (2026-08-27), and only this one.
# The runtime no longer reads them -- `app/playbooks.py` and
# `app/extract/t0_layout.py` read `manufacturer.body` once `set_source` is
# wired -- but `dentalia manufacturers seed` imports these files INTO that
# table, and an import you can only run from a git checkout is not an import
# anyone will run. `Dockerfile.web` deliberately has no equivalent COPY and
# compose no longer mounts the directory into `web`: the UI must have exactly
# one store, or an edit lands in whichever copy the reader happened to pick.
# No wheel packaging needed either --
# `python -m app.workers.runner` (and every `python -c`/pytest invocation this
# image runs) executes from /app with the cwd ahead of site-packages on
# sys.path, so `app.playbooks` always resolves to this copied source tree and
# PLAYBOOKS_DIR (repo-root-relative) always lands on /app/playbooks, whether
# or not the installed wheel also contains a playbooks/ directory.
COPY playbooks/ ./playbooks/

# Default service = queue worker. app/workers/runner.py is written in a later
# S0.1 step; this CMD is only evaluated at container start, so the image BUILDS
# regardless of whether the module exists yet. Compose overrides `command:` for
# the scheduler service.
CMD ["python", "-m", "app.workers.runner"]

# --- test stage -------------------------------------------------------------
# In-container pytest at the exact runtime (3.12) the services use — the host
# venv is 3.11 (followups [s0.1]). Run via the compose `test` profile:
#   docker compose --profile test run --rm --build test
# NOTE: compose build configs must keep `target: app` for the service images
# now that a later stage exists (an untargeted build resolves to `test`).
FROM app AS test

RUN pip install -c constraints.txt ".[dev,web,ingest]"

# Self-contained for CI; the compose service additionally bind-mounts the repo
# over /app so local runs pick up uncommitted changes without a rebuild
# (`python -m pytest` puts the cwd first on sys.path, ahead of site-packages).
COPY web/ ./web/
COPY tests/ ./tests/

CMD ["python", "-m", "pytest", "-q"]
