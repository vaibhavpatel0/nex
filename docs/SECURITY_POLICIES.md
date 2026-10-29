# Security policies (SENTINEL)

## Decision procedure

1. Every **enabled** policy whose `applies_to` glob matches the tool is evaluated. Disabled policies are listed as `disabled`.
2. Any triggered **BLOCK** makes the decision BLOCK (deny overrides). An ALLOW can never override it, whatever its priority.
3. Otherwise any triggered **REQUIRE_APPROVAL** makes the decision REQUIRE_APPROVAL.
4. Otherwise a triggered **ALLOW** (only `granted_by_profile` produces one) makes it ALLOW.
5. Otherwise **BLOCK by default** (no policy allowed the action).
6. A rule that raises, or an unknown rule type, counts as a triggered BLOCK (fail closed). If the whole evaluation
   fails, the gateway blocks.
7. **CONSTRAIN** policies never change the decision; they attach limits (execution timeout).

Priority (higher first) only chooses which triggered policy is reported as the matched policy. Rules that need valid
arguments are reported as `skipped` when SEN-003 has already rejected the arguments.

**Approvals**: a REQUIRE_APPROVAL becomes ALLOW only when the request carries an approval that is `approved`, not
expired, not consumed, and bound to the same agent, tool and canonical argument hash. It is consumed atomically on use.
An approval never turns a BLOCK into an ALLOW, and approving does not execute anything: execution is a new request
evaluated from scratch.

## Built-in policies

| Key | Effect | Rule | What it enforces |
|---|---|---|---|
| SEN-001 | BLOCK (locked) | unknown_tool | tools not registered with SENTINEL |
| SEN-002 | BLOCK (locked) | tool_not_granted | tool must be enabled on the agent and granted by its profile |
| SEN-003 | BLOCK (locked) | invalid_arguments | strict schema: no extra fields, typed IDs, size limits |
| SEN-010 | BLOCK | path_scope | normalised paths inside the store; deny `documents/restricted/**`, `**/*.env`, dotfiles; must match granted globs |
| SEN-011 | BLOCK | customer_scope | order/customer records must belong to the session customer; unknown owner is denied |
| SEN-012 | BLOCK | db_table_scope | restricted tables (`payment_methods`, `employees`) never reachable; tables/columns must be granted; writes need `write_tables` |
| SEN-020 | BLOCK | recipient_allowlist | every recipient domain must be granted |
| SEN-021 | BLOCK | attachment_restriction | attachments must be allowed, inside granted paths, not confidential |
| SEN-022 | BLOCK | sensitive_egress | outbound subject/body scanned for keys, tokens, card numbers (Luhn), tax IDs, fixture markers |
| SEN-023 | REQUIRE_APPROVAL | suspicious_sequence | external send after this session read internal or confidential data |
| SEN-030 | REQUIRE_APPROVAL | approval_required | email to recipients outside `halden.test` |
| SEN-031 | REQUIRE_APPROVAL | approval_required | `db.update`, `inventory.adjust_stock` |
| SEN-040 | BLOCK | rate_limit | 8 calls per session per 60 s |
| SEN-041 | BLOCK | rate_limit | 300 calls per agent per 60 s (live traffic; each test run counted separately) |
| SEN-042 | BLOCK | failure_limit | 3 consecutive failures of the same tool in a session |
| SEN-050 | BLOCK | url_allowlist | HTTPS only, allowlisted hosts, no credentials, no non-standard ports, no private/loopback/link-local/IP-literal hosts |
| SEN-060 | CONSTRAIN | execution_timeout | 3 s execution timeout |
| SEN-100 | ALLOW | granted_by_profile | the only source of ALLOW |

Locked policies cannot be disabled or re-scoped (their priority and description can still change).

## Permission profiles

Grants live in `permission_profiles.grants.tools.<tool>` and carry the scopes the rules read: `paths`,
`customer_scope` (`session` or `any`), `fields` (returned customer fields), `tables` and `write_tables` (column lists),
`recipient_domains`, `allow_attachments`, `attachment_paths`. Built-in profiles: `support-standard`, `operations-full`,
`read-only-docs` (see `backend/app/seed/catalog.py`).

## Creating a custom policy

Policies page, **Create policy** (admin). Keys use `CUS-001` to `CUS-999`. Choose a rule type, effect, priority,
tools (`*` globs) and JSON parameters. Example: at most 3 stock lookups per session:

```json
{"key": "CUS-001", "rule_type": "rate_limit", "effect": "BLOCK", "priority": 600,
 "applies_to": ["inventory.get_stock"], "params": {"max_calls": 3, "window_seconds": 60, "scope": "session", "per_tool": true}}
```

Every change creates a policy version with author and note; decisions record the policy-set hash that produced them.
