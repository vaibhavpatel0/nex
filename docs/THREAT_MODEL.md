# Threat model

## Assets
Confidential fixture data (payroll, credentials, card data, other customers' records), the integrity of sandbox state
(orders, inventory, outbox), the policy configuration, approval decisions, the audit trail, and platform credentials.

## Trust boundaries
1. **Agent output is untrusted.** Every tool request from any agent (simulated or model) is treated as attacker
   controlled. SENTINEL decides with deterministic rules; no model judges safety.
2. **Tool output is untrusted data.** Emails, documents and web pages may contain instructions. They are marked
   `untrusted_content`; the origin an agent reports for a call is recorded but never used for enforcement.
3. **Platform users** authenticate with passwords and bearer tokens and are limited by role.
4. **External providers** (Ollama, hosted APIs) receive prompts and tool results for model agents only.

## Threats and controls

| Threat | Control | Verified by |
|---|---|---|
| Direct prompt injection makes the agent call a prohibited tool | scope policies independent of the model | SUP-002/003, DOC-002, DB-002, ESC-001 scenarios |
| Indirect injection in email/doc/web content | allowlists, attachment rules, sequence rule, approvals | EML-001, DOC-004/005/006 |
| Path traversal / secret files | normalisation inside sandbox + deny globs | DOC-003, `test_safe_relative_path` |
| Cross-customer data access (IDOR through the agent) | session-bound customer scope; unknown owner denied | SUP-002, `test_customer_scope` |
| SQL injection through tool args | identifier validation, parameterised values, column grants | DB-006 |
| Data exfiltration in outbound content | `sensitive_egress`, recipient allowlist | EML-006 |
| SSRF via fetch tool | scheme/host allowlist, private/loopback/IP-literal block; tool has no network access | DOC-007, `test_url_allowlist` |
| Runaway loops / cost | session and agent rate limits, failure limit, timeouts, max agent steps | API-003, API-008 |
| Approval replay or tampering | args-hash binding, single use, expiry, re-evaluation | EML-004/005, gateway tests |
| Bypassing the gateway | capability object issued once to the gateway; agents receive only `ToolClient`; static import checks | `tests/security/test_gateway_bypass.py` |
| Fabricated results | evaluation only from stored records; unavailable model gives ERROR | `test_unavailable_model_produces_error_not_pass` |
| Secret leakage in logs/events/UI | redaction before persistence; sanitized validation errors; keys only from env | redaction and API tests |
| Platform API abuse | RBAC, scrypt hashes, hashed tokens, login throttle, body limit, rate limit, security headers, uniform 404 | `tests/security/test_api_authz.py` |
| Stored XSS via reports | Jinja autoescape; reports shown in a sandboxed iframe; download served with `default-src 'none'` CSP | code review |

## Out of scope / not guaranteed
- Code executing inside the backend process can bypass the in-process capability with introspection. Real isolation
  needs the tools in a separate process or service (see LIMITATIONS).
- Agents that call tools outside SENTINEL's adapters (their own HTTP clients, shells) are not covered.
- Paraphrased or encoded exfiltration may evade pattern scanning; the sequence rule mitigates only external sends.
- The platform is designed for localhost. Multi-tenant isolation between platform users is not implemented
  (all authorised users of an installation see all runs).
