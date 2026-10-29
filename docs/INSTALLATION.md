# Installation (macOS)

## Prerequisites

| Tool | Version | Check | Install |
|---|---|---|---|
| Python | 3.10+ (3.12 recommended) | `python3 --version` | `brew install python@3.12` |
| Node.js | 20+ (22 used in development) | `node --version` | `brew install node` |
| make, git | any | `make --version` | `xcode-select --install` |

macOS ships an older `python3` with the Xcode tools on some versions. If `make setup` stops with
"Python 3.10+ required", install Homebrew Python and run `make setup PY=python3.12`.

## Local development

```bash
make setup     # creates .venv, installs backend/requirements.txt, runs npm ci, copies .env.example to .env
make init      # alembic upgrade head + seed + first admin
make dev       # backend :8000 (reload) + frontend :5173 (proxies /api and the WebSocket to :8000)
```

Separate terminals instead of `make dev`: `make dev-backend` and `make dev-frontend`.

Single process (built UI served by FastAPI on :8000): `make serve`.

### First admin

`make init` creates `admin` (or `AEGIS_ADMIN_USERNAME`). If `AEGIS_ADMIN_PASSWORD` is empty, a random password is
generated and printed once. Re-running `init` never changes an existing admin. Add more users in Settings, or
`cd backend && ../.venv/bin/python -m app.cli create-user NAME ROLE` (roles: admin, operator, viewer).

### Database and migrations

- SQLite file at `data/aegis.db` (WAL mode). `make init` applies Alembic migrations then seeds.
- New schema change: edit `backend/app/models/entities.py`, then
  `cd backend && ../.venv/bin/alembic revision --autogenerate -m "describe change"` and review the file.
- Fresh empty install: `make reset-demo` (deletes `data/`).
- PostgreSQL: set `AEGIS_DATABASE_URL=postgresql+psycopg://...` and `pip install psycopg[binary]`. The code uses only
  SQLAlchemy and Alembic, but this path has **not been tested**.

## Environment variables

See `.env.example` for every variable with defaults. Secrets never go in `.env.example` or the database.

| Variable | Purpose |
|---|---|
| `AEGIS_DATA_DIR`, `AEGIS_DATABASE_URL` | storage location |
| `AEGIS_ADMIN_USERNAME`, `AEGIS_ADMIN_PASSWORD` | first admin (init only) |
| `AEGIS_SESSION_TTL_MINUTES`, `AEGIS_APPROVAL_TTL_SECONDS` | session and approval lifetime |
| `AEGIS_MAX_CONCURRENT_RUNS`, `AEGIS_TOOL_TIMEOUT_SECONDS`, `AEGIS_AGENT_RUN_TIMEOUT_SECONDS` | execution limits |
| `OLLAMA_BASE_URL`, `OLLAMA_TIMEOUT_SECONDS`, `OLLAMA_API_KEY` | Ollama (key only for direct cloud calls) |
| `AEGIS_HOSTED_BASE_URL`, `AEGIS_HOSTED_API_KEY_ENV` | hosted OpenAI-compatible provider; the key lives in the variable named by the latter |

## Ollama (optional)

```bash
curl -fsSL https://ollama.com/install.sh | sh    # or the app from ollama.com/download
ollama pull qwen3:8b                              # 5.2 GB, supports tool calling, fits 16 GB Apple Silicon
ollama run qwen3:8b "say hi"                      # sanity check
```

Then Integrations, Ollama, **Check now**. The built-in agent *Ops assistant (Ollama local model)* uses `qwen3:8b`.
Faster but weaker: `qwen3:4b`. Cloud models: `ollama signin`, then set the agent's model to a `:cloud` name (for example
`gemma4:cloud`). Cloud usage needs Ollama credits or a paid plan. Direct cloud API instead of the local server: set
`OLLAMA_BASE_URL=https://ollama.com` and export `OLLAMA_API_KEY` (create it at https://ollama.com/settings/keys).

## Hosted provider (optional, may cost money)

Any OpenAI-compatible Chat Completions endpoint with tool calling (OpenAI, Groq, OpenRouter, Together, LM Studio).
Set `AEGIS_HOSTED_BASE_URL` (for example `https://api.groq.com/openai/v1`), export the key as `AEGIS_HOSTED_API_KEY`
in your shell or `.env`, then register a *Hosted model* agent. Check the provider's current free tier and pricing
before running suites: each scenario sends several requests.

## Docker (optional, not verified)

```bash
docker compose up --build      # http://127.0.0.1:8000
```

The image builds the UI and serves it from FastAPI. It was written but **not built or run** during development
because Docker was not available there. Read the container log for the generated admin password.

## Running outside localhost

Not recommended for this prototype. If you must: terminate TLS in a reverse proxy, set `AEGIS_CORS_ORIGINS` to the
real origin, keep port 8000 bound to localhost behind the proxy, use strong passwords, back up `data/`, and read
`docs/THREAT_MODEL.md` and `docs/LIMITATIONS.md` first.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Database is not initialised. Run make init` on start | run `make init` |
| UI says "The backend is not reachable" | start `make dev-backend`; check port 8000 is free (`lsof -i :8000`) |
| Port 5173 in use | stop the other Vite server or edit `frontend/vite.config.ts` |
| Sign-in returns 429 | five failed attempts in 5 minutes; wait or restart the backend |
| Live dot stays amber or red | backend stopped or restarted; the UI reconnects automatically and replays missed events |
| Ollama agent results are ERROR | Integrations, Check now; install the model (`ollama pull qwen3:8b`); ensure it supports tools |
| `database is locked` | another process holds `data/aegis.db` (for example two backends); stop extras |
| Forgot the admin password | stop the app, `make reset-demo` (deletes data) or create another admin with the CLI |
