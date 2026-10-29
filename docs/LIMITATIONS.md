# Known limitations and unimplemented features

## Not verified
- **Live Ollama or hosted models.** The adapters follow the documented APIs (Ollama `/api/chat` with tools and
  `tool_name` results; OpenAI-compatible `/chat/completions`) and are exercised for the unavailable path, but no live
  model was run during development. Small local models may call tools poorly; results will show that honestly.
- **Docker** image and compose file were written but not built.
- **PostgreSQL** is designed for but untested.

## Not implemented
- In-app **garak** runs (status detection and a manual command only).
- PDF export (reports are HTML; print to PDF from the browser).
- AI-generated scenarios (all scenarios are deterministic templates; custom scenarios can be created via the API).
- Arbitrary external agents. Supported adapters: simulated, Ollama, OpenAI-compatible. Uploaded code is never executed.
- Multi-tenant isolation between platform users (single-tenant, role-based).
- Process-level isolation of tools (see below).

## Security boundaries
- The gateway capability is an **in-process** guard: supported paths cannot reach tools without it, but code in the
  same Python process could use introspection. Production use would move tools behind a separate service.
- SENTINEL only sees tool calls made through its adapters.
- Pattern scanning misses paraphrased or encoded secrets.
- Approval arguments are held in memory; after a restart approved actions cannot be executed and must be re-run.
- Login throttling and the platform API rate limit are in memory and reset on restart.
- Simulated agents implement a small grammar to model failure modes; they are not evidence about any real model.

## Measurement caveats
- Results apply to the executed scenarios, agent revision and policy set only. A 34-scenario suite cannot establish
  that a configuration is secure, and the comparison view says so when the sample is small.
- Token counts appear only when a provider reports them; simulated agents have none.
