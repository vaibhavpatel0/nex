# AEGIS AI + SENTINEL

An intelligent security testing, evaluation and runtime protection framework for autonomous AI agents.

- **AEGIS AI** runs security scenarios against an agent (prompt injection, indirect injection, unauthorized tool use,
  file access, data disclosure, API failures, malformed model output, permission escalation), evaluates what the agent
  actually did from stored records, and produces findings, metrics, regression comparisons and reports.
- **SENTINEL** is the enforcement layer between agents and their tools. Every supported tool call passes through a
  deterministic policy engine *before* execution and is allowed, blocked, or held for human approval.

Both share one event stream, one policy set and one trace store. Everything runs locally; the built-in demo uses
simulated agents and sandboxed fixtures, so no API key or model is needed.

## Quick start (macOS)

Prerequisites: Python 3.10 or newer, Node.js 20 or newer, `make` (Xcode command line tools).

```bash
brew install python@3.12 node        # if `python3 --version` is older than 3.10
cd aegis-ai                          # this folder
make setup                           # .venv + pip install + npm ci + .env from .env.example
make init                            # database, built-in policies/agents/scenarios, first admin
make dev                             # API on :8000 and UI on :5173
```

Open http://127.0.0.1:5173 and sign in as `admin` with the password `make init` printed (or the one you set in
`AEGIS_ADMIN_PASSWORD` in `.env`). On an empty install the Overview page offers **Run the quick demo**.

Run all tests: `make test` (backend pytest + frontend type check and vitest).

## What to try first

1. **Overview, Run the quick demo.** Eight scenarios against *Ops assistant v1 (naive)*. Open the run: the naive
   agent follows instructions hidden in an email and a document; SENTINEL blocks each hijacked call before it runs.
2. **Run security tests** with the same suite against *Ops assistant v2 (guarded)*.
3. **Open investigation** on the v2 run and pick the v1 run as baseline: scenario-by-scenario comparison, metric
   deltas and warnings about sample size.
4. **Generate report**: an evidence-based HTML report (print to PDF from the browser).
5. **Runtime protection**: run an agent live, watch the trace build, submit single tool calls, approve or deny
   held actions, and try re-using an approval with changed arguments.
6. **Policies**: disable or tune a policy (versioned, with a required change note) and re-run a scenario.

## Repository layout

```
backend/app/
  security/      authentication, authorization, policy_engine, tool_gateway, input_validation, audit, redaction
  sentinel/      approval, enforcement, rate_limits
  aegis/         scenario_registry, scenario_runner, evaluation_engine, trace_collector,
                 regression_runner, report_generator, metrics, scenarios/builtin.json
  adapters/tools protected tool implementations + sandbox fixtures (only the gateway can execute them)
  agents/        simulated (naive/guarded), llm_agent (tool-calling loop), factory
  integrations/  ollama, providers (OpenAI-compatible), garak (status only)
  api/ websocket/ middleware/ models/ database/ seed/ services/ fixtures/
backend/migrations/   Alembic
frontend/src/         React + TypeScript + Vite UI (features/*, components/, services/live.ts)
tests/                unit, integration, security, end_to_end (pytest)
scripts/e2e_browser.js  Playwright click-through of the whole product
docs/                 architecture, threat model, policies, API, install, user guide, testing, limitations
```

## Status

Verified in development (29 Oct 2026): 95 backend tests and 4 frontend tests pass; the Playwright flow passes on a
fresh install with no console errors, in light and dark themes and at 390 px width. See `docs/TESTING.md` for the
exact list and `docs/LIMITATIONS.md` for what is not implemented or not verified (live Ollama model, Docker image,
PostgreSQL, in-app garak runs).

Documentation: [Installation](docs/INSTALLATION.md) · [User guide](docs/USER_GUIDE.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Threat model](docs/THREAT_MODEL.md) ·
[Security policies](docs/SECURITY_POLICIES.md) · [API](docs/API_REFERENCE.md) · [Testing](docs/TESTING.md) ·
[Limitations](docs/LIMITATIONS.md)
