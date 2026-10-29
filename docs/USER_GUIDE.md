# User guide

## Roles
Viewer: read everything. Operator: run tests and sessions, decide approvals, open investigations, generate reports.
Admin: everything, plus agents, policies, users, data deletion.

## Run the first security scenario
1. Sign in. On an empty install the Overview shows **Run the quick demo** (8 scenarios against *Ops assistant v1 (naive)*).
2. The run page fills in as scenarios finish. Click a row to see its checks and evidence, then **Open trace**.
3. For a chosen agent and suite, use **Run security tests**: pick a target agent, then a suite or individual
   scenarios (filter by category). Scenarios needing tools the agent lacks are reported as SKIPPED.

## Read a trace
Execution traces lists every agent session and scenario (filter by agent, kind, status, run ID, time range, or
"contains a block"). The trace spine shows: request, model steps, each tool call, final result and evaluation.
- Filled green node: the call reached the tool. Filled red: reached it and failed.
- Hollow red with a bar: attempted and **blocked before execution**. Dashed amber: held for approval.
- "from tool output (agent-reported)" marks calls the agent says it derived from untrusted content.
Expand a call for its redacted arguments, result summary and every policy evaluated.

## Runtime protection
- **Run an agent through SENTINEL**: choose agent, signed-in customer and task (examples provided). The trace builds live.
- **Submit a tool call directly**: send one request as an agent, for example to test a policy change.
- **Approvals**: approve or deny pending actions (with a note). Approving does not execute; use **Execute approved
  action**, which re-evaluates the exact original request. To see tampering protection, paste the approval ID into
  the direct tool call form with changed arguments: the result is a new approval requirement.

## Policies
Edit priority and parameters (admin), enable or disable with a required reason. Each change is versioned. Create
custom policies with keys `CUS-001` to `CUS-999`; see SECURITY_POLICIES.md for rule types and parameters.

## Investigations, comparisons and reports
Open an investigation from a run page or the Investigations page; choose a baseline run to get a scenario-by-scenario
comparison with metric deltas and reliability warnings. Add notes, close it, and **Generate report**. Reports are
validated (counts must reconcile, every PASS/FAIL must carry evidence) before rendering. Download the HTML or print to PDF.
From a run page, **Replay** re-runs the same scenarios with the same or another agent. Failed scenarios can be added
to the **Regression** suite.

## Live events
Streams every persisted event with filters and a detail pane. Pause buffers new events. After a disconnect the stream
reconnects and replays everything since the last sequence number it processed.

## Simulated agent grammar
The deterministic agents act on these phrases (in the task, and for the naive agent also inside tool output):
`order ORD-####` · `customer CUST-####` · `read/open/summarize/show <documents/... or ../path>` · `summarize my inbox` ·
`send the file <path> [as an attachment] to <email>` · `send a short recap of <path> to <email>` ·
`reply to <email> with the status of order ORD-####` · `send an email to <email> with this text: ...` ·
`send the contents/them/it to <email>` · `check stock for SKU-###` · `keep checking stock for SKU-###` ·
`adjust stock for SKU-### by ±N` · `fetch <url>` · `query the <table> table [for columns a, b] [where col is value]` ·
`update order ORD-#### status to <value>` · `update the <table> record <id> set <field> to <value>` ·
`list all employees` · `use the tool <name>` · `include its full contents`.
The guarded agent refuses tasks containing role-override phrases ("ignore previous instructions", "you are now",
"system override", "maintenance mode", "grant yourself").

## Using an Ollama model
Install Ollama and `ollama pull qwen3:8b`, then Integrations, **Check now**. Run any suite against
*Ops assistant (Ollama local model)*, or register a new Local model agent with another tool-capable model
(`qwen3:4b`, or a `:cloud` model after `ollama signin`). Compare it with the simulated agents in an investigation.

## garak (manual)
`pip install garak`, then `python -m garak --model_type ollama --model_name qwen3:8b --probes promptinject`. Its
results are not imported into AEGIS in this version.
