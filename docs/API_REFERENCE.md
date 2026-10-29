# API reference

Base path `/api/v1`. Interactive OpenAPI docs: `http://127.0.0.1:8000/api/docs` (schema at `/api/openapi.json`).
Authenticate with `POST /auth/login`, then send `Authorization: Bearer <token>`. List endpoints accept
`limit` (1 to 500) and `offset` and return `{items, total, limit, offset}`. Errors return `{detail}`; validation errors
return field locations without echoing submitted values. Long operations return `202` with an ID to poll.

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/health` | public | status, database, version, time |
| POST | `/auth/login` | public | `{username, password}` gives `{token, expires_at, user}`; throttled after 5 failures |
| POST | `/auth/logout` | authenticated | revokes the token |
| GET | `/auth/me` | authenticated | user, role, permissions |
| GET, POST | `/users` | users:manage | list / create (`{username, password>=12, role}`) |
| PATCH | `/users/{id}` | users:manage | `{role?, disabled?}`; cannot demote or disable yourself |
| GET | `/agents`, `/agents/{id}` | read | includes connection status, last run, last event, revisions |
| POST | `/agents` | agents:write | register (simulated, local_model, hosted_model) |
| PATCH | `/agents/{id}` | agents:write | every change creates a new revision |
| GET | `/tools`, `/permission-profiles` | read | tool schemas, grants |
| GET | `/policies`, `/policies/{id}` | read | includes precedence, rule types, policy-set hash, versions |
| POST | `/policies` | policies:write | custom policy `CUS-###` |
| PATCH | `/policies/{id}` | policies:write | `{enabled?, priority?, params?, applies_to?, description?, risk?, change_note}`; locked rules protected |
| GET | `/test-suites`, `/scenarios`, `/scenarios/{key}` | read | `?category=&suite=` |
| POST | `/scenarios` | policies:write | custom scenario `CUS-...`, validated |
| PATCH | `/scenarios/{key}` | tests:run | `{enabled?, regression?}` |
| POST | `/test-runs` | tests:run | `{agent_id, suite_key or scenario_keys, label}`, returns 202 `{run_id}` |
| POST | `/test-runs/{id}/replay` | tests:run | `{agent_id?}` replays the same scenario list |
| GET | `/test-runs`, `/test-runs/{id}` | read | progress, results, metrics (with formulas), findings, snapshots |
| GET | `/test-runs/compare?baseline=&candidate=` | read | regression comparison with warnings |
| GET | `/findings` | read | `?run_id=&status=` |
| GET, POST | `/investigations` | read / investigations:write | `{run_id, baseline_run_id?, title?}` |
| GET, PATCH | `/investigations/{id}` | read / investigations:write | `{status?, notes?, baseline_run_id?}` |
| GET, POST | `/reports` | read / reports:generate | `{investigation_id}` or `{run_id}`; 409 if data fails validation |
| GET | `/reports/{id}`, `/reports/{id}/download` | read | HTML; download served with a restrictive CSP |
| GET | `/analytics?agent_id=` | read | aggregate metrics, formulas, per-category and per-run data, coverage |
| GET | `/overview` | read | dashboard data, `empty` flag |
| GET | `/runtime/events` | read | `?since_seq=&event_type=policy.*&severity=&agent_id=&run_id=&correlation_id=&start=&end=&order=` |
| GET | `/runtime/decisions` | read | `?decision=&agent_id=&tool_name=` |
| POST | `/runtime/sessions` | runtime:invoke | `{agent_id, task, session_subject?}`, returns 202 `{correlation_id}` |
| POST | `/runtime/invoke` | runtime:invoke | `{agent_id, tool, args, session_subject?, correlation_id?, approval_id?}`, returns the structured decision |
| POST | `/runtime/sandbox/reset` | data:delete | restore runtime fixtures |
| GET | `/runtime/sandbox/outbox` | read | simulated sent mail (bodies redacted) |
| GET | `/approvals` | read | `?status=pending|approved|consumed|denied|expired` |
| POST | `/approvals/{id}/decide` | approvals:decide | `{approve, note}`; 409 if not pending |
| POST | `/approvals/{id}/execute` | approvals:decide | re-submits the original action through the gateway |
| GET | `/traces`, `/traces/{correlation_id}` | read | filters: agent, run, kind, status, q, has_block, start, end |
| GET | `/integrations` | read | Ollama, hosted, garak (no secrets) |
| POST | `/integrations/{key}/check` | tests:run | live reachability check |
| GET | `/settings/data` | read | counts and retention |
| POST | `/settings/data/purge` | data:delete | `{older_than_days, confirm: "DELETE"}` |

## Decision record (`/runtime/invoke`, `/approvals/{id}/execute`)
```json
{"decision_id": "dec_...", "tool_call_id": "call_...", "agent_id": "agt_...", "tool_name": "files.read",
 "decision": "BLOCK", "execution_status": "blocked", "matched_policy": "SEN-010",
 "policy_ids_evaluated": ["SEN-001", "SEN-002", "SEN-003", "SEN-010", "..."],
 "reason": "Path matches a denied location", "risk": "high", "approval_id": null, "error_class": null,
 "timestamp": "2026-10-29T14:02:11.120Z", "correlation_id": "cor_...", "result_summary": null}
```
Argument values are never echoed in reasons; stored arguments are redacted.

## WebSocket `/api/v1/ws`
1. Client sends `{"type":"auth","token":"...","since_seq":N}` within 10 s (token is not in the URL).
2. Server sends `{"type":"ready","latest_seq":M}`, then every persisted event with `seq > N` (`"replayed": true`, at
   most 500; more gives `replay_truncated`), then live `{"type":"event","event":{...}}`.
3. `ping` every 20 s; `overflow` then close code 4408 for slow clients; close 4401 when unauthorised.
Event types: `agent.request`, `model.response`, `tool.requested`, `policy.decision`, `tool.executed`, `tool.blocked`,
`tool.failed`, `approval.requested`, `approval.decided`, `agent.output`, `agent.error`, `run.queued`, `run.started`,
`scenario.started`, `scenario.completed`, `run.completed`, `run.failed`, `policy.changed`, `integration.checked`,
`system.*`.
