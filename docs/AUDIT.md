# Repository audit (30 October 2026)

Before adding the e-commerce platform, the existing AEGIS AI + SENTINEL code was checked for fabricated data,
fake execution and misleading presentation. Findings and what was done:

| Area | Finding | Action |
|---|---|---|
| Dashboard numbers | All counts and metrics are computed from stored records; empty installs show an honest empty state. No hardcoded statistics, testimonials or logos. | Kept. |
| "Run the quick demo" (Overview) | The button starts a real test run, but the word "demo" suggested a canned animation. | Renamed to "Run the quick suite" with wording that says the results are recorded. |
| Overview counts | Live runtime traffic and test-run traffic were added together, so security tests inflated "allowed" and "blocked" totals. | Overview now shows live and test figures separately (`decisions_live`, `executed_live`). |
| Baseline comparison | There was no way to measure the agent without SENTINEL, so "what did SENTINEL prevent" could not be shown with evidence. | Added baseline mode: SENTINEL records but does not enforce, only for running test runs, which always use sandboxes. Launcher, run list, run page and trace spine show the mode. |
| Simulated agents | Rule-based agents are clearly labelled "Simulated"; they model failure modes and are not presented as a real model. | Kept, label unchanged. |
| Terminal-style views | None. Traces are tables and a labelled spine. | Kept. |
| Approvals for remote tools | The AEGIS approval page offered "Execute" for actions whose arguments live in another service. | Shop actions now say to execute them from the Agent Workbench, which holds the original arguments. |
| Fixtures | Sandbox documents, inbox and store data are synthetic and labelled; canary markers detect leaks. | Kept. |
| Race in request handling | FastAPI can run the commit of a request's database session after the response is sent, so a just-issued token could be rejected by the next request. Found by the e-commerce end-to-end tests. | Both backends now commit before the response leaves (`CommitBeforeResponseRoute`). |
| WebSocket replay | A client asking to replay from sequence 0 received nothing. | `since_seq` omitted means live only; any number, including 0, replays. Fixed in both backends and the AEGIS live client. |

Nothing that runs real tests or enforcement was removed.
