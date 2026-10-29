# Known limitations and unimplemented features

## Not verified
- **Live Ollama or hosted models.** The adapters follow the documented APIs (Ollama `/api/chat` with tools and
  `tool_name` results; OpenAI-compatible `/chat/completions`) and are exercised for the unavailable path, but no live
  model was run during development. Small local models may call tools poorly; results will show that honestly.
- **Docker** image and compose file were written but not built.
- **PostgreSQL** is designed for but untested.
- **The e-commerce assistant with a real model.** All shop tests drive the agent with an Ollama-protocol test
  double. No request to a real model (local or ollama.com) could be made from the development environment, because
  its network blocks ollama.com and no local Ollama was available. The cloud default `gemma4:31b` is the example from
  Ollama's documentation; whether it calls tools reliably is not verified. Run `make -C ecommerce-agent-platform
  check-model` after configuring a model.

## Not implemented
- In-app **garak** runs (status detection and a manual command only).
- PDF export (reports are HTML; print to PDF from the browser).
- AI-generated scenarios (all scenarios are deterministic templates; custom scenarios can be created via the API).
- Arbitrary external agents. Supported adapters: simulated, Ollama, OpenAI-compatible, and the e-commerce platform's remote agent. Uploaded code is never executed.
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

## E-commerce platform
- Store tool functions trust the enforcement point for ownership of agent calls; the storefront API checks
  ownership itself. Calling the tool functions directly (outside `pep.handle`) would bypass SENTINEL.
- Payment is a local fixture only; nothing reaches a payment provider.
- Approved store actions can be executed only from the Workbench, and only for live (not sandbox) runs.
- The injection scanner is pattern based; paraphrased instructions in product text can evade it. Ownership and
  staff-tool rules still apply regardless.
- Sandboxes under `data/sandboxes/` are not cleaned automatically.
- Customer and staff sessions use bearer tokens in session storage; there is no CSRF exposure, but there is also
  no rate limit on the chat endpoint beyond the model round limit.

## Measurement caveats
- Results apply to the executed scenarios, agent revision and policy set only. A 34-scenario suite cannot establish
  that a configuration is secure, and the comparison view says so when the sample is small.
- Token counts appear only when a provider reports them; simulated agents have none.
