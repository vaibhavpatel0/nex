# AEGIS AI + SENTINEL

An intelligent security testing, evaluation and runtime protection framework for autonomous AI agents.

- **AEGIS AI** runs security scenarios against an agent (prompt injection, indirect injection, unauthorized tool use,
  file access, data disclosure, API failures, malformed model output, permission escalation), evaluates what the agent
  actually did from stored records, and produces findings, metrics, regression comparisons and reports.
- **SENTINEL** is the enforcement layer between agents and their tools. Every supported tool call passes through a
  deterministic policy engine *before* execution and is allowed, blocked, or held for human approval.

Both share one event stream, one policy set and one trace store. Everything runs locally; the built-in quick suite uses
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
`AEGIS_ADMIN_PASSWORD` in `.env`). On an empty install the Overview page offers **Run the quick suite**.

Run all tests: `make test` (backend pytest + frontend type check and vitest).

## What to try first

1. **Overview, Run the quick suite.** Eight scenarios against *Ops assistant v1 (naive)*. Open the run: the naive
   agent follows instructions hidden in an email and a document; SENTINEL blocks each hijacked call before it runs.
2. **Run security tests** with the same suite against *Ops assistant v2 (guarded)*.
3. **Open investigation** on the v2 run and pick the v1 run as baseline: scenario-by-scenario comparison, metric
   deltas and warnings about sample size.
4. **Generate report**: an evidence-based HTML report (print to PDF from the browser).
5. **Runtime protection**: run an agent live, watch the trace build, submit single tool calls, approve or deny
   held actions, and try re-using an approval with changed arguments.
6. **Policies**: disable or tune a policy (versioned, with a required change note) and re-run a scenario.

## E-commerce agent platform

`ecommerce-agent-platform/` is an independently runnable test store (Halden Bazaar) with a real Ollama-powered
shopping assistant whose every tool call goes through SENTINEL, plus an Agent Workbench that shows each run's model
calls, decisions and tool results live. AEGIS tests the same agent in sandbox copies of the store, in protected or
baseline mode.

```bash
make shop-setup && make shop-init && make link-ecommerce
make dev          # tab 1: AEGIS on :8000 / :5173
make shop-dev     # tab 2: shop API :8100, storefront and Workbench :5174
```

Details: [ecommerce-agent-platform/README.md](ecommerce-agent-platform/README.md) ·
[integration](docs/ECOMMERCE_INTEGRATION.md) · [audit](docs/AUDIT.md).

## Connect your own agent by URL

**Agent connections** in the sidebar: paste an agent's URL (A2A Agent Card, MCP server, OpenAPI document or AEGIS agent manifest), prove you control its domain (DNS TXT or HTTPS file), authorize scoped access, then test it, monitor it and, after routing its tool calls through SENTINEL, protect it. Protection is shown only when SENTINEL has actually recorded decisions for the agent. Try it locally with `make connect-demo`. Details: [docs/AGENT_CONNECTIONS.md](docs/AGENT_CONNECTIONS.md).

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
docs/                 architecture, threat model, policies, API, install, user guide, testing, limitations,
                      audit, e-commerce integration
ecommerce-agent-platform/
  backend/shop/       store API, agent loop, enforcement point (agent/pep.py), sandbox, migrations
  frontend/src/       storefront (store/) and Agent Workbench (workbench/)
  tests/              end-to-end tests and the Ollama-protocol test double
scripts/link_ecommerce.py  creates and writes the integration tokens
```

## Status

Verified in development (30 Oct 2026): 108 backend tests, 4 frontend tests, 16 e-commerce end-to-end tests and 2
e-commerce UI tests pass. Earlier (29 Oct 2026): the Playwright flow passes on a
fresh install with no console errors, in light and dark themes and at 390 px width. See `docs/TESTING.md` for the
exact list and `docs/LIMITATIONS.md` for what is not implemented or not verified (live Ollama model, Docker image,
PostgreSQL, in-app garak runs).

Documentation: [Installation](docs/INSTALLATION.md) · [User guide](docs/USER_GUIDE.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Threat model](docs/THREAT_MODEL.md) ·
[Security policies](docs/SECURITY_POLICIES.md) · [API](docs/API_REFERENCE.md) · [Testing](docs/TESTING.md) ·
[Limitations](docs/LIMITATIONS.md)
