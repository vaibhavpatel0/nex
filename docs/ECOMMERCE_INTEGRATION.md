# E-commerce agent integration

How `ecommerce-agent-platform/` (Halden Bazaar) connects to AEGIS AI + SENTINEL. Setup and day-to-day use are in
`ecommerce-agent-platform/README.md`.

## Roles

| Component | Role |
|---|---|
| Shop backend (`shop`, port 8100) | Store logic, the agent loop, and the **policy enforcement point** (`shop/agent/pep.py`). Owns the store database. |
| SENTINEL (`/api/v1/sentinel/*`, port 8000) | **Policy decision point** shared with in-process agents (`app/sentinel/decision_point.py`). Validates arguments against the registered schema, evaluates policies, stores calls, decisions, approvals, events. |
| AEGIS | Drives the same agent through `RemoteHTTPAgent` for scenarios, evaluates results from SENTINEL's records plus the sandbox evidence the shop returns. |

## Credentials

- **Service token** (`aegis_svc_...`, in the shop `.env` as `AEGIS_API_TOKEN`). Stored in AEGIS as a SHA-256 hash.
  Bound to agent `shop-assistant` and tool namespace `shop`. Works only on `/api/v1/sentinel/*`; refused on every
  other API route and on the AEGIS WebSocket. Cannot hold `users:manage`, `data:delete` or `policies:write`.
- **Internal token** (`SHOP_INTERNAL_TOKEN` in the shop `.env`, `AEGIS_ECOMMERCE_TOKEN` in the root `.env`). AEGIS
  presents it to `/api/internal/*` on the shop to start scenario runs. Compared in constant time.
- **Model key** (`OLLAMA_API_KEY`, shop `.env` only). Used only by the shop backend for `https://ollama.com`.

`make link-ecommerce` creates the first two and writes them without printing them.

## Tool-call protocol

1. On start, the shop registers its tools: `PUT /sentinel/tools` with strict JSON schemas
   (`additionalProperties: false`), operation, sensitivity and namespace. Names cannot collide with built-in tools.
2. Each run opens a session: `POST /sentinel/sessions` with the correlation ID and session customer. For AEGIS
   scenarios the session joins the test trace and returns `mode` and `enforce`.
3. For every proposed call the shop looks up the record owner in its own database and calls
   `POST /sentinel/evaluate` with tool, raw arguments, resource attributes and session customer.
   SENTINEL returns `decision`, `execute`, `enforced`, matched policy and version, reason, risk, approval ID.
4. The shop executes only when `execute` is true. If SENTINEL cannot be reached, the call is denied locally.
   Unenforced execution is refused outside a sandbox even if a response claims otherwise.
5. The result goes back with `POST /sentinel/tool-calls/{id}/result`. SENTINEL refuses a result for a call it did
   not allow. It scans the result for embedded instructions; a hit flags the call `suspected_injection`, and policy
   SEN-024 then holds later write or egress calls in that session for approval.
6. Model requests, responses and errors are forwarded as session events; `complete` closes live sessions.

Approvals are bound to agent, tool and the canonical hash of the arguments. Staff approve in the Workbench or in
AEGIS, then *Execute approved action* in the Workbench re-submits the stored arguments with the approval ID; a
changed argument does not match and is not executed.

## Policies added for the store

| Key | Rule | Effect | Purpose |
|---|---|---|---|
| SEN-013 | `customer_scope` on shop record tools | BLOCK | Orders, carts, returns and profile must belong to the session customer (owner from the shop database). |
| SEN-024 | `tainted_session` (flags `suspected_injection`; write and egress) | REQUIRE_APPROVAL | After injected content is read, later side effects need a human. |
| SEN-032 | `approval_required` on cancellation and return requests | REQUIRE_APPROVAL | Staff review every request. |
| SEN-044 | `rate_limit` 2 support requests per 300 s per session | BLOCK | Stops ticket spam. |
| SEN-041 | agent-wide limit raised to 300 calls per minute | BLOCK | Room for test suites. |

Staff tools (`shop.admin_update_price`, `shop.admin_issue_refund`) are not in the agent's permission profile, so
the default-deny rule blocks them.

## Scenarios (agent `shop-assistant`, suites `ecommerce`, `ecommerce-quick`, `ecommerce-gateway`)

ECOM-001 product search within a budget; ECOM-002 own order lookup; ECOM-003 another customer's order;
ECOM-004 injection in a product description; ECOM-005 injected refund in a policy document; ECOM-006 another
customer's contact details; ECOM-007 removing another customer's cart item; ECOM-008 cancelling a shipped order;
ECOM-009 price change request; ECOM-010 malformed tool call; ECOM-011 unbounded stock polling; ECOM-012 model
timeout; ECOM-013 inventory service unavailable; ECOM-014 stock changes between check and add; ECOM-015 regression,
customer-supplied price. GW-001 to GW-006 are gateway probes: one tool request submitted straight to the
enforcement point without a model, so the policy layer is tested deterministically.

Every scenario runs against a fresh sandbox copy of the fixtures. The shop returns row-level side effects
(including rows of other customers), invariants (cart prices match the catalogue) and the tools actually invoked;
AEGIS checks them against the scenario's expectations (`side_effects`, `tools_not_invoked`, `invariants_hold`,
`expected_agent_error`).

## Events

Shop events (`agent_events`, persisted before publishing) carry seq, event ID, correlation ID, type, state, tool,
decision, execution status, error class and a redacted payload. Types: `run.started`, `model.request`,
`model.response`, `model.error`, `model.limit`, `tool.proposed`, `policy.checking`, `policy.decision`,
`tool.result`, `tool.not_executed`, `sentinel.unavailable`, `run.completed`, `run.failed`. States: RUNNING,
WAITING_FOR_MODEL, TOOL_REQUESTED, POLICY_CHECKING, BLOCKED, WAITING_FOR_APPROVAL, TOOL_EXECUTING, COMPLETED,
FAILED. The same correlation ID keys the AEGIS trace, tool calls, decisions and scenario result.

WebSocket `/api/ws`: first frame `{"type":"auth","token":"...","since_seq":N}`; omit `since_seq` for live events
only, give a number to replay persisted events after it. Staff receive all runs; customers only their own.

## Database changes

- AEGIS migration `0002_remote_tools_service_tokens`: table `service_tokens`; `tool_definitions` gains `provider`,
  `revision`, `updated_at`; `agents.agent_type` allows `remote_agent`; `test_runs.mode` (`protected` or
  `baseline`); `tool_calls.enforced`. Apply with `make init` (runs migrations; existing data is kept).
- Shop migration `0001_store_and_agent_records` (separate database `ecommerce-agent-platform/data/shop.db`).
