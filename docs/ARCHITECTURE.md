# Architecture

## Execution flow

```
User task or AEGIS scenario
  -> agent adapter (simulated | Ollama | OpenAI-compatible)       receives only a ToolClient
  -> SENTINEL gateway (security/tool_gateway.py)
       1 correlation id         2 argument validation (strict schema, size limit)
       3 agent identity + permission profile                       4 resource attributes (metadata only)
       5 deterministic policy evaluation (fail closed)             6 approval binding check
       7 decision persisted     8 ALLOW: execute protected tool with timeout
                                   BLOCK: tool never invoked
                                   REQUIRE_APPROVAL: approval request, tool not invoked
       9 result / classified failure persisted
  -> events persisted (runtime_events, seq) then pushed over WebSocket
  -> AEGIS evaluation (aegis/evaluation_engine.py) from stored tool calls, decisions and trace
  -> metrics, findings, investigations, reports, dashboard
```

## Components

| Area | Module | Responsibility |
|---|---|---|
| Gateway | `security/tool_gateway.py` | the only holder of the execution capability; runs the pipeline above |
| Policy engine | `security/policy_engine.py` | pure function: inputs to decision; 16 rule types; documented precedence |
| Validation | `security/input_validation.py` | pydantic strict schemas; sanitized errors; canonical argument hash |
| Redaction | `security/redaction.py` | field fingerprints + credential/PII/marker patterns before persistence |
| Approvals | `sentinel/approval.py`, `sentinel/enforcement.py` | binding to agent+tool+args hash, expiry, single use |
| Limits | `sentinel/rate_limits.py` | session and agent call counts from persisted tool calls |
| Tools | `adapters/tools/*` | 12 protected tools over a per-run sandbox (documents, SQLite store, inbox, outbox, inventory API with faults, fixture web pages) |
| Agents | `agents/*` | simulated naive/guarded grammar agents; generic LLM tool loop |
| Providers | `integrations/*` | Ollama native API; OpenAI-compatible Chat Completions; garak status |
| Executor | `services/agent_executor.py` | shared by tests and runtime sessions: trace record, events, timeouts |
| AEGIS | `aegis/*` | registry, runner (bounded concurrency, sandbox per scenario), evaluator, metrics, regression, reports |
| Events | `websocket/event_bus.py`, `websocket/routes.py` | persist-then-publish bus; WS auth in first frame; replay by seq; de-duplication |
| API | `api/*` | versioned REST under `/api/v1`, RBAC on every route, OpenAPI at `/api/docs` |
| UI | `frontend/src` | React + TS; LiveClient reconnects with exponential backoff and resumes from last seq |

## Data model

23 tables (see `models/entities.py`, migration `0001`): users, api_sessions, tool_definitions, permission_profiles,
agents, agent_versions, policies, policy_versions, test_suites, test_suite_scenarios, scenarios, test_runs,
scenario_results, evaluation_metrics, findings, investigations, reports, traces, runtime_events, tool_calls,
policy_decisions, approval_requests, integration_configs. Foreign keys, check constraints on enumerations,
indexes on correlation, run, agent and time columns. All timestamps are UTC (`UTCDateTime` type); the UI renders local time.

Integration configurations store no secrets: keys are read from environment variables at call time.

## Reproducibility

A test run snapshots the agent configuration (including the permission profile grants), the policy set (hash plus
per-policy version), the platform version and the exact scenario list. Each result records the scenario version.
Regression comparisons warn when any of these differ.

## Concurrency and recovery

- SQLite in WAL mode with a 5 s busy timeout. Event inserts are serialized by a process lock so `seq` order matches publish order.
- Runs execute as asyncio tasks behind a semaphore (`AEGIS_MAX_CONCURRENT_RUNS`). Runs found `queued`/`running` at
  startup are marked `failed` with an explanation.
- Endpoints commit their own transaction before publishing events (avoids SQLite self-deadlock).
- WebSocket: subscribe first, then replay from the database, then stream live; events already replayed are skipped.
  Slow clients that overflow a 500-item queue are disconnected and recover by reconnecting.

## Switching to PostgreSQL

Set `AEGIS_DATABASE_URL`. The application uses SQLAlchemy Core/ORM only and Alembic migrations with batch mode.
SQLite-specific code is limited to connection pragmas in `database/base.py`. Not tested on PostgreSQL yet.

## Remote agents (e-commerce platform)

Agents that run in another service use SENTINEL as a remote decision point (`api/sentinel_routes.py` on top of
`sentinel/decision_point.py`, the same code the in-process gateway uses). The remote service is the enforcement
point and must execute only on `execute: true`. AEGIS drives remote agents through `agents/remote_http.py`.
Test runs carry a `mode`: `protected`, or `baseline`, where decisions are recorded with `enforced=false` but not
enforced, allowed only while a baseline test run is running. See `docs/ECOMMERCE_INTEGRATION.md`.
