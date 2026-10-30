# Halden Bazaar: e-commerce agent platform

A small, database-backed test store with a real AI shopping assistant, built as a realistic target for
AEGIS (the repository root). Everything in the store is synthetic: products, customers, orders,
policies. Checkout uses a clearly labelled local payment fixture; no money moves.

It has two separate experiences in one app:

- **Storefront** (customers): catalogue with search and filters, product details, comparison, cart, checkout,
  order history and status, cancellation and return eligibility and requests, support requests, store policies,
  and the **shopping assistant** chat.
- **Agent Workbench** (staff): every agent run as it actually happened. For a selected run it shows
  A customer request, B model activity, C tool-call proposals, D SENTINEL decisions, E tool execution,
  F AEGIS evaluation, G the full execution trace and H the live execution state, streamed over a WebSocket and
  rebuilt from persisted events after a refresh or reconnect. It also starts conversations, runs AEGIS suites in
  protected or baseline mode, compares runs, handles approvals, shows the policy behind a decision, and checks
  the model configuration.

## How it fits together

```
Browser (5174) ──> shop backend (8100) ──> model: Ollama local or ollama.com (backend only, key never in the browser)
                        │
                        │ every tool call: POST /api/v1/sentinel/evaluate  (service token, bound to agent shop-assistant
                        ▼                                                    and tool namespace "shop")
               AEGIS (8000) ── decides ALLOW / BLOCK / REQUIRE_APPROVAL, records it, streams it
                        │
                        │ security tests: POST /api/internal/agent-runs (internal token) ─> same agent, sandbox copy
                        ▼
               shop backend runs the scenario against a fresh sandbox database and returns side-effect evidence
```

- The shop backend is the enforcement point. `shop/agent/pep.py` is the only code path that runs agent tools.
  It looks up the record owner in the shop database, asks SENTINEL, and executes only when SENTINEL says so.
  If SENTINEL is unreachable, the call is denied (fail closed).
- Live conversations are always enforced. Baseline (unenforced) mode exists only for AEGIS test runs, and those
  always run against a per-scenario sandbox copy of the fixtures.
- The model never decides who owns an order: ownership comes from the database and the session customer.

## Run it on macOS

Prerequisites: the root project set up (`make setup` and `make init` in the repository root), Python 3.10+, Node 20+.

```bash
cd ~/Desktop/nex/aegis-ai              # repository root
make shop-setup                        # shop Python deps into .venv, npm ci, ecommerce-agent-platform/.env
make shop-init                         # data/shop.db, migrations, synthetic seed data
make link-ecommerce                    # SENTINEL service token + internal token into both .env files (not printed)
```

Start three things, each in its own Terminal tab:

```bash
make dev                               # AEGIS API :8000 and UI :5173
make shop-dev                          # shop API :8100 and storefront/Workbench :5174
ollama serve                           # only for a local model (the Ollama app does this for you)
```

Open:

- Storefront: http://127.0.0.1:5174 , sign in as `asha@shopper.test` (customer CUST-2001)
- Agent Workbench: http://127.0.0.1:5174/workbench , sign in as `staff@halden.test`
- AEGIS: http://127.0.0.1:5173

The test-account password is `SHOP_DEMO_PASSWORD` from `ecommerce-agent-platform/.env`, or, if that was empty
during `make shop-init`, the one written to `ecommerce-agent-platform/data/first-run-credentials.txt`.
Reset it with `cd ecommerce-agent-platform/backend && ../../.venv/bin/python -m shop.cli reset-password asha@shopper.test`.

## Choose the model

The assistant needs a model that supports tool calling.

Local (free, runs on your Mac; `qwen3:8b` fits a 16 GB MacBook Air):

```bash
ollama pull qwen3:8b
.venv/bin/python ecommerce-agent-platform/scripts/configure_model.py local qwen3:8b
```

Ollama cloud (runs on ollama.com, billed to your Ollama plan):

```bash
.venv/bin/python ecommerce-agent-platform/scripts/configure_model.py cloud gemma4:31b   # prompts for the key, hidden
```

Create or rotate keys at https://ollama.com/settings/keys. The key is stored only in the git-ignored
`ecommerce-agent-platform/.env` (mode 0600) and sent only from the backend as `Authorization: Bearer`.
Then restart `make shop-dev` and verify:

```bash
make -C ecommerce-agent-platform check-model   # ONE request to the model; prints whether it proposed a tool call
```

The Workbench page *Model and integration* shows the same status and a *Check tool calling* button. The list of
models your key can use is shown there (from `/api/tags`). If a model does not propose tool calls reliably, pick
another one; runs will show honestly that no tool was called.

## Use it

- **Normal conversation:** storefront, Assistant. Ask "Find wireless headphones under ₹3,000" or
  "Can I cancel order ORD-7001?". Cancellations and returns become approvals; staff approve them in the Workbench,
  then press *Execute approved action*, which re-submits the exact call through SENTINEL.
- **Watch it live:** keep the Workbench open on Runs while chatting; states and decisions appear as they happen.
- **Security tests:** Workbench, Security tests. Choose *Protected* or *Baseline benchmark*, run a suite or single
  scenarios (*Run* replays a saved scenario). Each result links to its Workbench trace and to AEGIS.
- **Compare baseline and protected:** run the same suite in both modes, then use *Compare* on the same page, or an
  investigation in AEGIS.
- **Inspect a block:** in a run, the SENTINEL decision links to the policy (version, parameters, scope).
- **Reports and incidents:** AEGIS UI, Reports and Investigations, for the test runs started here.

## Tools the agent has

`search_products`, `get_product_details`, `compare_products`, `check_inventory`, `get_cart`, `add_to_cart`,
`remove_from_cart`, `get_order_status`, `get_order_details`, `check_cancellation_eligibility`,
`request_order_cancellation`, `check_return_eligibility`, `request_return`, `create_support_request`,
`get_store_policy`, `get_customer_profile`, and two staff-only tools (`admin_update_price`, `admin_issue_refund`)
that are offered to the model by default so SENTINEL's denial can be observed
(`SHOP_EXPOSE_STAFF_TOOLS_TO_AGENT=false` hides them). All are registered with SENTINEL as `shop.*` with strict
JSON schemas.

## Environment variables

See `.env.example` (every variable, with defaults). Secrets: `OLLAMA_API_KEY`, `SHOP_OPENAI_API_KEY`,
`AEGIS_API_TOKEN`, `SHOP_INTERNAL_TOKEN`. None of them is returned by any API or logged.

## Database

SQLite at `data/shop.db`, managed by Alembic (`backend/migrations/versions/0001_store_and_agent_records.py`,
19 tables: customers, users, auth_sessions, products, inventory, carts, cart_items, orders, order_items,
cancellation_requests, return_requests, support_requests, refunds, store_policies, conversations, chat_messages,
agent_runs, agent_events, pending_actions). AEGIS keeps its own database; the two talk only over the APIs above.
Security tests create sandbox copies under `data/sandboxes/<correlation id>/store.db`.

## Tests

```bash
make shop-test
```

Runs 16 end-to-end tests that start the real AEGIS backend, the real shop backend and an
**Ollama-protocol test double** (`tests/doubles/ollama_double.py`) in separate processes, linked with real tokens.
The double is a scripted stand-in for a model, used only by tests, so results are deterministic; it deliberately
obeys injected instructions to prove SENTINEL stops them. Plus 2 UI tests (vitest). See `../docs/TESTING.md`.
