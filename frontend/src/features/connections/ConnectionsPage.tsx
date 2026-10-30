import { FormEvent, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApp } from "../../app/context";
import { IconCheck, IconDash, IconPlay, IconRefresh } from "../../components/Icons";
import { Empty, Expandable, FormError, Load, PageHead, Panel, RunStatusTok, Time } from "../../components/ui";
import { useAction, useApi } from "../../hooks/useApi";
import { api } from "../../services/api";

// ---------------------------------------------------------------- tokens
const STATE: Record<string, [string, string, string]> = {
  pending: ["tok-warn", "Pending verification", "Discovered. Ownership of the domain is not proven yet."],
  verified: ["tok-info", "Verified", "Domain control proven. Access to the agent is not authorized yet."],
  connected: ["tok-info", "Connected", "Authorized and healthy. Monitoring and protection are off."],
  monitoring: ["tok-info", "Monitoring only", "AEGIS observes; nothing is blocked."],
  protected: ["tok-ok", "Protected by SENTINEL", "Tool calls are evaluated by SENTINEL, and recent decisions prove it."],
  degraded: ["tok-error", "Degraded", "Unhealthy, credential expired, or protection without recent evidence."],
  disconnected: ["tok-neutral", "Disconnected", "Credentials and the SENTINEL token were revoked."],
};
export function ConnStateTok({ s }: { s: string }) {
  const [cls, label, title] = STATE[s] ?? ["tok-neutral", s, ""];
  return <span className={`tok ${cls}`} title={title}>{label}</span>;
}

function Cap({ v }: { v: boolean | string }) {
  if (v === true) return <span className="tok tok-ok"><IconCheck size={12} />Supported</span>;
  if (v === "conditional") return <span className="tok tok-warn">Conditional</span>;
  return <span className="tok tok-neutral"><IconDash size={12} />Not supported</span>;
}

const PROTO: Record<string, string> = { a2a: "A2A", mcp: "MCP", openapi: "OpenAPI", custom_http: "AEGIS agent API", none: "No agent protocol" };
const PROT: Record<string, [string, string]> = {
  not_protected: ["tok-neutral", "Not protected"], awaiting_evidence: ["tok-warn", "Awaiting evidence"],
  protected: ["tok-ok", "Protected"], degraded: ["tok-error", "Degraded"],
};
const CAP_LABEL: Record<string, string> = {
  discovery: "Agent discovery", capability_inspection: "Capability inspection", interaction: "Agent interaction",
  streaming: "Streaming", tool_discovery: "Tool discovery", execution_tracing: "Execution tracing",
  security_testing: "Security testing", runtime_enforcement: "Runtime enforcement",
};

// ---------------------------------------------------------------- list
export function ConnectionsPage() {
  const { can, me } = useApp();
  const [filter, setFilter] = useState("");
  const r = useApi<{ items: any[] }>("/connections", [], 15000);
  const shown = (r.data?.items ?? []).filter((c) => !filter || c.state === filter);
  return (
    <div className="page stack">
      <PageHead title="Agent connections"
        intro={<>Connect an existing AI agent by its URL: discover it, prove you control its domain, authorize scoped access, then test, monitor and protect it. A connected URL is never shown as protected until SENTINEL has actually decided its tool calls.</>} />
      {can("connections:manage") ? <ConnectForm /> : <div className="notice">Your role can view connections but not create them.</div>}
      <Panel title={`Connections${r.data ? ` (${r.data.items.length})` : ""}`} flush
        actions={<div className="seg" role="group" aria-label="Filter by state">
          {["", "pending", "verified", "connected", "monitoring", "protected", "degraded", "disconnected"].map((s) => (
            <button key={s || "all"} aria-pressed={filter === s} onClick={() => setFilter(s)}>{s ? STATE[s][1] : "All"}</button>))}
        </div>}>
        <Load r={r} empty={(d) => !d.items.length}>
          {() => shown.length ? (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Agent</th>{me?.role === "admin" ? <th>Owner</th> : null}<th>Protocol</th><th>State</th>
                <th>Verified domain</th><th>Health</th><th>Protection</th><th>Last test</th><th>Open findings</th></tr></thead>
              <tbody>{shown.map((c) => (
                <tr key={c.id}>
                  <td><Link to={`/connections/${c.id}`}>{c.name}</Link><div className="small mono muted">{c.host}</div></td>
                  {me?.role === "admin" ? <td className="small">{c.owner}</td> : null}
                  <td><span className="tag">{PROTO[c.protocol] ?? c.protocol}</span></td>
                  <td><ConnStateTok s={c.state} /></td>
                  <td className="small mono">{c.verification.domain ?? <span className="muted">Not verified</span>}</td>
                  <td className="small">{c.health.status === "unchecked" ? <span className="muted">Not checked</span> : c.health.status === "healthy" ? "Healthy" : "Unhealthy"}</td>
                  <td><span className={`tok ${PROT[c.protection.status][0]}`}>{PROT[c.protection.status][1]}</span></td>
                  <td className="small">{c.last_test ? <Link to={`/runs/${c.last_test.id}`}><Time iso={c.last_test.created_at} rel /></Link> : <span className="muted">None</span>}</td>
                  <td className="small">{c.findings.open}</td>
                </tr>))}</tbody>
            </table></div>
          ) : <Empty title="No connections in this state" />}
        </Load>
      </Panel>
    </div>
  );
}

function ConnectForm() {
  const meta = useApi<any>("/connect/meta");
  const nav = useNavigate();
  const act = useAction();
  const { toast } = useApp();
  const [url, setUrl] = useState("");
  const [protocol, setProtocol] = useState("auto");
  const [name, setName] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const c = await act.run(() => api.post("/connections", { url, protocol, name: name || null }));
    if (c) { toast(c.protocol === "none" ? `No supported agent protocol found at ${c.host}` : `Discovered ${PROTO[c.protocol]} at ${c.host}`); nav(`/connections/${c.id}`); }
  };
  const dev = meta.data?.dev_exceptions;
  return (
    <Panel title="Connect an agent">
      <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
        <div className="field-row">
          <label className="field" style={{ gridColumn: "span 2" }}><span>Agent URL (HTTPS)</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://agents.example.com/.well-known/agent-card.json" required maxLength={2048} inputMode="url" autoComplete="off" /></label>
          <label className="field"><span>Protocol</span>
            <select value={protocol} onChange={(e) => setProtocol(e.target.value)}>
              {(meta.data?.protocols ?? [{ key: "auto", name: "Detect automatically" }]).map((p: any) => <option key={p.key} value={p.key}>{p.name}</option>)}
            </select></label>
          <label className="field"><span>Display name (optional)</span><input value={name} maxLength={128} onChange={(e) => setName(e.target.value)} /></label>
        </div>
        <p className="small muted">Accepted: an A2A Agent Card, an MCP remote server endpoint, an OpenAPI document, or an AEGIS agent manifest (<code>/.well-known/aegis-agent.json</code>). AEGIS only sends GET requests while discovering, plus an MCP <code>initialize</code> handshake when you choose MCP or the path ends in <code>/mcp</code>. Private, loopback and cloud metadata addresses are refused.</p>
        {dev && (dev.private_hosts.length || dev.http_hosts.length) ? (
          <div className="notice notice-warn small">Development exceptions are active on this server: {[...dev.private_hosts.map((h: string) => `${h} (private address allowed)`), ...dev.http_hosts.map((h: string) => `${h} (plain HTTP allowed)`)].join(", ")}.</div>) : null}
        <FormError msg={act.error} />
        <div className="row"><button className="btn-primary" disabled={act.busy || !url}>{act.busy ? "Discovering" : "Discover agent"}</button></div>
      </form>
    </Panel>
  );
}

// ---------------------------------------------------------------- detail
const STEPS = ["Discover", "Verify ownership", "Authorize access", "Connect", "Test", "Monitor", "Protect"];

function stepState(c: any): ("done" | "current" | "todo" | "na")[] {
  const interactive = c.capabilities?.interaction?.supported === true;
  const done = [
    c.protocol !== "none",
    c.verification.status === "verified",
    c.authorization.status === "authorized",
    c.mode !== "none",
    !!c.last_test,
    c.monitoring.active,
    c.protection.status === "protected",
  ];
  const na = [false, false, false, false, !interactive, false, false];
  const firstTodo = done.findIndex((d, i) => !d && !na[i]);
  return done.map((d, i) => (na[i] ? "na" : d ? "done" : i === firstTodo ? "current" : "todo"));
}

export function ConnectionDetailPage() {
  const { id } = useParams();
  const r = useApi<any>(`/connections/${id}`, [id]);
  const [c, setC] = useState<any>(null);
  const cur = c && c.id === id ? c : r.data;
  return (
    <div className="page stack">
      <Load r={r}>
        {() => <Detail c={cur} set={(x: any) => { setC(x); }} />}
      </Load>
    </div>
  );
}

function Detail({ c, set }: { c: any; set: (x: any) => void }) {
  const steps = stepState(c);
  const closed = !!c.disconnected_at;
  return (
    <>
      <PageHead title={c.name}
        intro={<><span className="mono">{c.submitted_url}</span>. Owner {c.owner}. Created <Time iso={c.created_at} />.</>}
        actions={<><ConnStateTok s={c.state} /><span className="tag">{PROTO[c.protocol] ?? c.protocol}</span>{c.dev_exception ? <span className="tok tok-warn">Development exception</span> : null}</>} />
      <ol className="conn-steps" aria-label="Connection progress">
        {STEPS.map((s, i) => (
          <li key={s} data-state={steps[i]}>
            <span className="n">{steps[i] === "done" ? <IconCheck size={12} /> : i + 1}</span>{s}
            {steps[i] === "na" ? <span className="small muted"> (not available)</span> : null}
          </li>))}
      </ol>
      {closed ? <div className="notice">This connection was disconnected <Time iso={c.disconnected_at} rel />. Credentials, the SENTINEL token and test scopes were revoked. Create a new connection to reconnect.</div> : null}
      {!c.is_owner && !closed ? <div className="notice">You are viewing another user's connection as an administrator. Verification, authorization and test scopes can only be granted by the owner ({c.owner}).</div> : null}
      <div className="grid-main-side">
        <div className="stack">
          <DiscoveryPanel c={c} set={set} closed={closed} />
          <VerifyPanel c={c} set={set} closed={closed} />
          <AuthorizePanel c={c} set={set} closed={closed} />
          <ConnectPanel c={c} set={set} closed={closed} />
          <TestPanel c={c} set={set} closed={closed} />
          <ProtectPanel c={c} set={set} closed={closed} />
        </div>
        <div className="stack">
          <SummaryPanel c={c} />
          <AuditPanel id={c.id} stamp={c.updated_at} />
          {!closed ? <DangerPanel c={c} set={set} /> : null}
        </div>
      </div>
    </>
  );
}

function SummaryPanel({ c }: { c: any }) {
  return (
    <Panel title="Status">
      <dl className="kv">
        <dt>State</dt><dd><ConnStateTok s={c.state} /></dd>
        <dt>Protocol</dt><dd>{PROTO[c.protocol] ?? c.protocol}{c.protocol_version ? ` ${c.protocol_version}` : ""}</dd>
        <dt>Endpoint</dt><dd className="mono small">{c.endpoint ?? <span className="muted">None on this host</span>}</dd>
        <dt>Verified domain</dt><dd>{c.verification.domain ? <><span className="mono">{c.verification.domain}</span> by {c.verification.method === "dns_txt" ? "DNS TXT" : "HTTPS file"}, <Time iso={c.verification.verified_at} rel /></> : <span className="muted">Not verified</span>}</dd>
        <dt>Authentication</dt><dd>{c.authorization.status === "authorized" ? (c.authorization.credential ? `${c.authorization.credential.kind.replace(/_/g, " ")}, fingerprint ${c.authorization.credential.fingerprint}` : "No credential (public endpoint)") : c.authorization.status === "none" ? <span className="muted">Not authorized</span> : c.authorization.status}</dd>
        <dt>Tools</dt><dd>{c.tool_count} declared{c.protection.registered_tools ? `, ${c.protection.registered_tools} registered with SENTINEL` : ""}</dd>
        <dt>Health</dt><dd>{c.health.status === "unchecked" ? <span className="muted">Not checked</span> : <>{c.health.status === "healthy" ? "Healthy" : "Unhealthy"}<div className="small muted">{c.health.detail}</div></>}</dd>
        <dt>Monitoring</dt><dd>{c.monitoring.active ? `Active, ${c.monitoring.events_24h} event(s) in 24 h` : "Off"}</dd>
        <dt>Protection</dt><dd><span className={`tok ${PROT[c.protection.status][0]}`}>{PROT[c.protection.status][1]}</span><div className="small muted">{c.protection.detail}</div></dd>
        <dt>Last test</dt><dd>{c.last_test ? <><Link to={`/runs/${c.last_test.id}`}>{c.last_test.status}</Link> <Time iso={c.last_test.created_at} rel /></> : <span className="muted">None</span>}</dd>
        <dt>Open findings</dt><dd>{c.findings.open}{Object.keys(c.findings.by_severity).length ? ` (${Object.entries(c.findings.by_severity).map(([k, v]) => `${v} ${k}`).join(", ")})` : ""}</dd>
      </dl>
    </Panel>
  );
}

function DiscoveryPanel({ c, set, closed }: { c: any; set: (x: any) => void; closed: boolean }) {
  const d = c.descriptor;
  const act = useAction();
  const redo = async () => { const x = await act.run(() => api.post(`/connections/${c.id}/discover`)); if (x) set(x); };
  return (
    <Panel title="1. Discovery" actions={!closed && c.is_owner ? <button className="btn-sm" onClick={redo} disabled={act.busy}><IconRefresh />Discover again</button> : null}>
      <div className="stack" style={{ gap: 12 }}>
        <p className="small muted">{d.claims_note}</p>
        {d.warnings?.length ? <div className="notice notice-warn small">{d.warnings.map((w: string, i: number) => <div key={i}>{w}</div>)}</div> : null}
        <FormError msg={act.error} />
        <dl className="kv">
          <dt>Name</dt><dd>{d.identity?.name ?? <span className="muted">Not stated</span>}</dd>
          <dt>Description</dt><dd>{d.identity?.description ?? <span className="muted">Not stated</span>}</dd>
          <dt>Version</dt><dd>{d.identity?.version ?? <span className="muted">Not stated</span>}</dd>
          <dt>Provider</dt><dd>{d.identity?.provider ?? <span className="muted">Not stated</span>}</dd>
          <dt>Authentication</dt><dd>{d.auth?.required === true ? "Required" : d.auth?.required === false ? "Not required" : "Not stated"}{d.auth?.schemes?.length ? `: ${d.auth.schemes.map((s: any) => [s.name, s.type, s.scheme].filter(Boolean).join(" ")).join("; ")}` : ""}</dd>
          {d.auth?.token_url ? <><dt>Token endpoint</dt><dd className="mono small">{d.auth.token_url}</dd></> : null}
        </dl>
        <div className="table-wrap"><table className="t">
          <thead><tr><th>Capability</th><th>AEGIS support</th><th>Detail</th></tr></thead>
          <tbody>{Object.entries(c.capabilities).map(([k, v]: any) => (
            <tr key={k}><td>{CAP_LABEL[k] ?? k}</td><td><Cap v={v.supported} /></td><td className="small">{v.detail}</td></tr>))}</tbody>
        </table></div>
        {d.skills?.length ? <Expandable summary={`Skills declared (${d.skills.length})`}>
          <ul className="small">{d.skills.map((s: any) => <li key={s.id ?? s.name}><b>{s.name}</b>{s.description ? `: ${s.description}` : ""}</li>)}</ul></Expandable> : null}
        {d.tools?.length ? <Expandable summary={`Tools or operations declared (${d.tools.length})`}>
          <ul className="small">{d.tools.map((t: any, i: number) => <li key={i}><span className="mono">{t.name}</span>{t.operation ? ` (${t.operation})` : ""}{t.description ? `: ${t.description}` : ""}</li>)}</ul></Expandable> : null}
        <Expandable summary={`Requests made during discovery (${d.evidence?.length ?? 0})`}>
          <table className="t small"><tbody>{(d.evidence ?? []).map((e: any, i: number) => (
            <tr key={i}><td className="mono">{e.url}</td><td>{e.status ?? "No response"}</td><td>{e.content_type || ""}</td><td>{e.finding}</td></tr>))}</tbody></table>
        </Expandable>
      </div>
    </Panel>
  );
}

function VerifyPanel({ c, set, closed }: { c: any; set: (x: any) => void; closed: boolean }) {
  const meta = useApi<any>("/connect/meta");
  const act = useAction();
  const { toast } = useApp();
  const labels = c.host.split(".");
  const domains = labels.slice(0, -1).map((_: string, i: number) => labels.slice(i).join("."));
  const [method, setMethod] = useState("dns_txt");
  const [domain, setDomain] = useState(c.host);
  const issue = async () => { const x = await act.run(() => api.post(`/connections/${c.id}/challenges`, { method, domain: method === "https_file" ? c.host : domain })); if (x) set(x); };
  const check = async (chid: string) => {
    const x = await act.run(() => api.post(`/connections/${c.id}/challenges/${chid}/check`));
    if (x) { set(x.connection); toast(x.verified ? "Ownership verified" : `Not verified yet: ${x.detail}`, !x.verified); }
  };
  const pending = (c.challenges ?? []).filter((ch: any) => ch.status === "pending");
  const verified = c.verification.status === "verified";
  return (
    <Panel title="2. Verify ownership">
      <div className="stack" style={{ gap: 12 }}>
        {verified ? <div className="notice"><IconCheck size={12} /> Control of <b className="mono">{c.verification.domain}</b> verified by {c.verification.method === "dns_txt" ? "DNS TXT record" : "HTTPS file"} on <Time iso={c.verification.verified_at} />, by {c.verification.verified_by}. This proves domain control only; access to the agent is authorized separately.</div>
          : <p className="small muted">Being publicly reachable is not proof of ownership. Publish the challenge value with one of these methods, then check it. Challenges expire.</p>}
        {!closed && c.is_owner && c.protocol !== "none" ? (
          <div className="field-row">
            <label className="field"><span>Method</span>
              <select value={method} onChange={(e) => setMethod(e.target.value)}>
                {(meta.data?.verification_methods ?? []).map((m: any) => <option key={m.key} value={m.key} disabled={!m.available}>{m.name}{m.available ? "" : " (not available)"}</option>)}
              </select></label>
            {method === "dns_txt" ? <label className="field"><span>Domain</span>
              <select value={domain} onChange={(e) => setDomain(e.target.value)}>{domains.map((d: string) => <option key={d}>{d}</option>)}</select></label> : null}
            <div className="field"><span>&nbsp;</span><button onClick={issue} disabled={act.busy}>{verified ? "Re-verify" : "Create challenge"}</button></div>
          </div>) : null}
        <FormError msg={act.error} />
        {pending.map((ch: any) => (
          <div key={ch.id} className="notice">
            {ch.method === "dns_txt" ? (
              <dl className="kv"><dt>Record type</dt><dd>TXT</dd><dt>Name</dt><dd className="mono">{ch.location}</dd><dt>Value</dt><dd className="mono">{ch.value}</dd></dl>
            ) : (
              <dl className="kv"><dt>Serve at</dt><dd className="mono">{ch.location}</dd><dt>Content</dt><dd className="mono">{ch.value}</dd><dt>Content type</dt><dd className="mono">text/plain</dd><dt>Redirects</dt><dd>Not followed; the endpoint host must serve the file itself</dd></dl>
            )}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn-primary btn-sm" onClick={() => check(ch.id)} disabled={act.busy}>Check now</button>
              <span className="small muted">Expires <Time iso={ch.expires_at} rel />.{ch.detail ? ` Last check: ${ch.detail}` : ""}</span>
            </div>
          </div>))}
        {(c.challenges ?? []).filter((ch: any) => ch.status !== "pending").length ? (
          <Expandable summary="Earlier challenges">
            <table className="t small"><tbody>{c.challenges.filter((ch: any) => ch.status !== "pending").map((ch: any) => (
              <tr key={ch.id}><td>{ch.method === "dns_txt" ? "DNS TXT" : "HTTPS file"}</td><td className="mono">{ch.domain}</td><td>{ch.status}</td><td>{ch.detail}</td></tr>))}</tbody></table>
          </Expandable>) : null}
      </div>
    </Panel>
  );
}

function AuthorizePanel({ c, set, closed }: { c: any; set: (x: any) => void; closed: boolean }) {
  const meta = useApi<any>("/connect/meta");
  const act = useAction();
  const { toast } = useApp();
  const interactive = c.capabilities?.interaction?.supported === true;
  const [perms, setPerms] = useState<string[]>(interactive ? ["read_descriptor", "health_check", "send_test_messages"] : ["read_descriptor"]);
  const needsCred = c.descriptor?.auth?.required === true;
  const [kind, setKind] = useState(needsCred ? (c.descriptor?.auth?.token_url ? "oauth2_client_credentials" : "bearer") : "none");
  const [secret, setSecret] = useState("");
  const [header, setHeader] = useState("X-Agent-Token");
  const [tokenUrl, setTokenUrl] = useState(c.descriptor?.auth?.token_url ?? "");
  const [clientId, setClientId] = useState("");
  const [scopes, setScopes] = useState((c.descriptor?.auth?.scopes ?? []).join(" "));
  const [expires, setExpires] = useState("");
  const [accept, setAccept] = useState(false);
  const can = c.verification.status === "verified" && c.is_owner && !closed;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const credential = kind === "none" ? null : {
      kind, secret, header_name: kind === "header" ? header : null, token_url: kind === "oauth2_client_credentials" ? tokenUrl : null,
      client_id: kind === "oauth2_client_credentials" ? clientId : null, scopes: scopes.split(/\s+/).filter(Boolean),
      expires_at: expires ? new Date(expires).toISOString() : null,
    };
    const x = await act.run(() => api.post(`/connections/${c.id}/authorize`, { accept, permissions: perms, credential }));
    if (x) { set(x); setSecret(""); setAccept(false); toast("Access authorized"); }
  };
  const revoke = async () => { const x = await act.run(() => api.post(`/connections/${c.id}/revoke-credentials`)); if (x) { set(x.connection); toast("Credentials revoked"); } };
  return (
    <Panel title="3. Authorize access">
      {!can ? <p className="small muted">{c.verification.status !== "verified" ? "Available after ownership is verified." : closed ? "Disconnected." : "Only the owner can authorize access."}</p> : (
        <form className="stack" style={{ gap: 12 }} onSubmit={submit} autoComplete="off">
          {c.authorization.status === "authorized" ? <div className="notice small">Authorized by {c.authorization.authorized_by} <Time iso={c.authorization.authorized_at} rel />. Permissions: {c.authorization.permissions.join(", ")}. Submitting again replaces the permissions and credential.</div> : null}
          <fieldset><legend>Permissions AEGIS requests (minimum needed)</legend>
            {Object.entries(meta.data?.permissions ?? {}).map(([k, label]: any) => (
              <label key={k} className="check"><input type="checkbox" checked={perms.includes(k)} disabled={k === "read_descriptor" || (!interactive && (k === "health_check" || k === "send_test_messages"))}
                onChange={(e) => setPerms(e.target.checked ? [...perms, k] : perms.filter((p) => p !== k))} /><span>{label}</span></label>))}
          </fieldset>
          <fieldset><legend>Credential</legend>
            <label className="field"><span>Type</span>
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                {(meta.data?.credential_kinds ?? []).map((k: any) => <option key={k.key} value={k.key}>{k.name}</option>)}
              </select></label>
            {needsCred && kind === "none" ? <div className="notice notice-warn small">The endpoint declares that it requires authentication.</div> : null}
            {kind !== "none" ? (
              <div className="field-row" style={{ marginTop: 8 }}>
                {kind === "header" ? <label className="field"><span>Header name</span><input value={header} onChange={(e) => setHeader(e.target.value)} maxLength={64} /></label> : null}
                {kind === "oauth2_client_credentials" ? <>
                  <label className="field"><span>Token URL (same host)</span><input value={tokenUrl} onChange={(e) => setTokenUrl(e.target.value)} /></label>
                  <label className="field"><span>Client ID</span><input value={clientId} onChange={(e) => setClientId(e.target.value)} /></label>
                  <label className="field"><span>Scopes (space separated)</span><input value={scopes} onChange={(e) => setScopes(e.target.value)} /></label>
                </> : null}
                <label className="field"><span>{kind === "oauth2_client_credentials" ? "Client secret" : "Scoped token"}</span>
                  <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" spellCheck={false} required minLength={8} /></label>
                <label className="field"><span>Expires (optional)</span><input type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} /></label>
              </div>) : null}
            <p className="small muted">Use a token scoped to this agent endpoint. Never paste a model provider API key (OpenAI, Anthropic, Google, Ollama and similar keys are refused). The secret is encrypted on the server, sent only to {c.host}, and never shown again.</p>
          </fieldset>
          <label className="check"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
            <span>I control {c.verification.domain} and authorize AEGIS to access this agent with the permissions above. I can revoke this at any time.</span></label>
          <FormError msg={act.error} />
          <div className="row">
            <button className="btn-primary" disabled={act.busy || !accept}>Authorize access</button>
            {c.authorization.credential ? <button type="button" onClick={revoke} disabled={act.busy}>Revoke credentials</button> : null}
          </div>
        </form>)}
    </Panel>
  );
}

function ConnectPanel({ c, set, closed }: { c: any; set: (x: any) => void; closed: boolean }) {
  const act = useAction();
  const { toast } = useApp();
  const ready = c.verification.status === "verified" && c.authorization.status === "authorized" && !closed;
  const go = async (path: string) => {
    const x = await act.run(() => api.post(`/connections/${c.id}/${path}`));
    if (x) { set(x.connection); toast(x.health.healthy ? "Healthy" : `Unhealthy: ${x.health.detail}`, !x.health.healthy); }
  };
  return (
    <Panel title="4. Connect and check health">
      <div className="stack" style={{ gap: 10 }}>
        {!ready ? <p className="small muted">Available after verification and authorization.</p> : (
          <>
            <p className="small muted">{c.capabilities?.interaction?.supported === true
              ? (c.authorization.permissions.includes("health_check") ? "The health check sends one short message asking the agent to reply OK." : "Without the health check permission, only the descriptor is fetched.")
              : "This endpoint is not an agent AEGIS can talk to; the check fetches its descriptor (and MCP tool list) with your credential."}</p>
            <div className="row">
              {c.mode === "none" ? <button className="btn-primary" onClick={() => go("connect")} disabled={act.busy}>Connect</button>
                : <button onClick={() => go("health")} disabled={act.busy}><IconRefresh />Check health</button>}
              {c.agent_id ? <Link to={`/agents/${c.agent_id}`} className="small">Agent record</Link> : null}
            </div>
          </>)}
        {c.health.last_checked_at ? <div className="small">Last check <Time iso={c.health.last_checked_at} rel />: {c.health.detail}</div> : null}
        <FormError msg={act.error} />
      </div>
    </Panel>
  );
}

function TestPanel({ c, set, closed }: { c: any; set: (x: any) => void; closed: boolean }) {
  const act = useAction();
  const nav = useNavigate();
  const { toast } = useApp();
  const [env, setEnv] = useState("test");
  const [runs, setRuns] = useState(3);
  const [days, setDays] = useState(7);
  const [confirm, setConfirm] = useState(false);
  const [mode, setMode] = useState("protected");
  const testable = c.capabilities?.security_testing?.supported === true && c.agent_id;
  const scope = c.test_scope;
  const approve = async (e: FormEvent) => {
    e.preventDefault();
    const x = await act.run(() => api.post(`/connections/${c.id}/test-scope`, { suites: ["connected-agent"], max_runs_per_day: runs, days, environment: env, confirm }));
    if (x) { set(x); setConfirm(false); toast("Test scope approved"); }
  };
  const revoke = async () => { const x = await act.run(() => api.post(`/connections/${c.id}/test-scope/revoke`)); if (x) set(x); };
  const run = async () => { const x = await act.run(() => api.post(`/connections/${c.id}/tests`, { mode })); if (x) { toast("Test run started"); nav(`/runs/${x.run_id}`); } };
  return (
    <Panel title="5. Test with AEGIS">
      {!testable ? <p className="small muted">{!c.agent_id && c.capabilities?.security_testing?.supported === true ? "Connect the agent first (steps 2 to 4)." : `Not available: ${c.capabilities?.security_testing?.detail ?? "no interaction endpoint"}.`}</p> : closed ? <p className="small muted">Disconnected.</p> : (
        <div className="stack" style={{ gap: 12 }}>
          <p className="small muted">The connected-agent suite sends six messages: a harmless baseline and five attempts (instruction override, hidden instructions in pasted content and in text to translate, a request for credentials, claimed admin rights). Each is judged from the reply and, if your agent reports to SENTINEL, from its tool calls. Repeating a planted marker counts as following the injected instruction.</p>
          {scope ? (
            <div className="notice small">Approved scope: {scope.suites.join(", ")}, up to {scope.max_runs_per_day} run(s) per day, environment {scope.environment.replace(/_/g, " ")}, approved by {scope.approved_by}, expires <Time iso={scope.expires_at} rel />.
              {c.is_owner ? <> <button className="btn-sm" onClick={revoke} disabled={act.busy}>Revoke scope</button></> : null}</div>
          ) : c.is_owner ? (
            <form className="stack" style={{ gap: 8 }} onSubmit={approve}>
              <div className="field-row">
                <label className="field"><span>Environment the endpoint serves</span>
                  <select value={env} onChange={(e) => setEnv(e.target.value)}><option value="test">Test</option><option value="staging">Staging</option><option value="production_test_data">Production, test data only</option></select></label>
                <label className="field"><span>Runs per day</span><input type="number" min={1} max={20} value={runs} onChange={(e) => setRuns(+e.target.value)} /></label>
                <label className="field"><span>Valid for (days)</span><input type="number" min={1} max={30} value={days} onChange={(e) => setDays(+e.target.value)} /></label>
              </div>
              <label className="check"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
                <span>I approve AEGIS sending the connected-agent test suite to this endpoint. It does not act on real customer accounts or production data.</span></label>
              <div className="row"><button className="btn-primary" disabled={act.busy || !confirm || !c.authorization.permissions.includes("send_test_messages")}>Approve test scope</button>
                {!c.authorization.permissions.includes("send_test_messages") ? <span className="small muted">Grant "send test messages" in step 3 first.</span> : null}</div>
            </form>) : <p className="small muted">The owner has not approved a test scope.</p>}
          {scope ? (
            <div className="row">
              <div className="seg" role="group" aria-label="Enforcement"><button aria-pressed={mode === "protected"} onClick={() => setMode("protected")}>Protected</button><button aria-pressed={mode === "baseline"} onClick={() => setMode("baseline")}>Baseline</button></div>
              <button className="btn-primary" onClick={run} disabled={act.busy}><IconPlay />Run connected-agent suite</button>
            </div>) : null}
          {c.last_test ? <div className="small">Last run: <Link to={`/runs/${c.last_test.id}`}>{c.last_test.id}</Link> <RunStatusTok s={c.last_test.status} /> {c.last_test.counts ? Object.entries(c.last_test.counts).filter(([k, v]) => k !== "total" && v).map(([k, v]) => `${v} ${k}`).join(", ") : ""}</div> : null}
          <FormError msg={act.error} />
        </div>)}
    </Panel>
  );
}

function ProtectPanel({ c, set, closed }: { c: any; set: (x: any) => void; closed: boolean }) {
  const act = useAction();
  const { toast } = useApp();
  const [secret, setSecret] = useState<any>(null);
  const [levels, setLevels] = useState<Record<string, string>>({});
  const grants = c.tool_grants ?? [];
  const dirty = useMemo(() => Object.entries(levels).filter(([k, v]) => grants.find((g: any) => g.name === k)?.level !== v), [levels, grants]);
  const ok = c.authorization.status === "authorized" && !closed;
  const mode = async (m: string) => {
    const x = await act.run(() => api.post(`/connections/${c.id}/mode`, { mode: m }));
    if (x) { set(x.connection); setSecret(x.sentinel ?? null); toast(m === "protected" ? "Protection configured; waiting for evidence" : "Monitoring only"); }
  };
  const save = async () => { const x = await act.run(() => api.put(`/connections/${c.id}/permissions`, { levels: Object.fromEntries(dirty) })); if (x) { set(x); setLevels({}); toast("Permissions saved"); } };
  return (
    <Panel title="6. Monitor and protect">
      <div className="stack" style={{ gap: 12 }}>
        <div className="notice small"><b>Monitoring</b> records metadata, health checks, test results and events your agent sends. It never blocks anything. <b>Protection</b> requires your agent's backend to ask SENTINEL before every tool call and run the tool only when SENTINEL allows it. This page shows "Protected" only after SENTINEL has recorded decisions for this agent in the last 24 hours.</div>
        {ok ? <div className="row">
          <div className="seg" role="group" aria-label="Mode">
            <button aria-pressed={c.mode === "monitoring"} onClick={() => mode("monitoring")} disabled={act.busy || c.mode === "none"}>Monitoring only</button>
            <button aria-pressed={c.mode === "protected"} onClick={() => mode("protected")} disabled={act.busy || c.mode === "none"}>{c.mode === "protected" ? "Protected mode (rotate token)" : "Enable protected mode"}</button>
          </div>
          {c.mode === "none" ? <span className="small muted">Connect first.</span> : null}
        </div> : null}
        {secret ? (
          <div className="notice notice-warn" role="status">
            <b>SENTINEL service token (shown once)</b>
            <div className="mono small" style={{ wordBreak: "break-all", margin: "6px 0" }}>{secret.token}</div>
            <div className="small">{secret.note}</div>
            <dl className="kv small" style={{ marginTop: 6 }}><dt>Agent key</dt><dd className="mono">{secret.agent_key}</dd><dt>Tool namespace</dt><dd className="mono">{secret.tool_namespace}</dd></dl>
            <ol className="small">{secret.steps.map((s: string, i: number) => <li key={i}>{s}</li>)}</ol>
            <div className="row"><button className="btn-sm" onClick={() => navigator.clipboard?.writeText(secret.token).then(() => toast("Copied"))}>Copy token</button><button className="btn-sm" onClick={() => setSecret(null)}>I stored it, hide</button></div>
          </div>) : null}
        <dl className="kv">
          <dt>Status</dt><dd><span className={`tok ${PROT[c.protection.status][0]}`}>{PROT[c.protection.status][1]}</span> <span className="small">{c.protection.detail}</span></dd>
          <dt>Registered tools</dt><dd>{c.protection.registered_tools}</dd>
          <dt>Decisions in 24 h</dt><dd>{c.protection.decisions_24h}{c.protection.last_decision_at ? <> (last <Time iso={c.protection.last_decision_at} rel />)</> : null}</dd>
          <dt>Token</dt><dd>{c.protection.token_active ? <>Active{c.protection.token_last_used_at ? <>, last used <Time iso={c.protection.token_last_used_at} rel /></> : ", not used yet"}</> : "None"}</dd>
          <dt>Agent key / namespace</dt><dd className="mono small">{c.protection.agent_key} / {c.protection.namespace}</dd>
        </dl>
        {grants.length ? (
          <>
            <h3 className="small">Tool permissions (nothing is allowed until you allow it)</h3>
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Tool</th><th>Operation</th><th>Permission</th></tr></thead>
              <tbody>{grants.map((g: any) => (
                <tr key={g.name}><td><span className="mono">{g.name}</span><div className="small muted">{g.description}</div></td><td>{g.operation}</td>
                  <td><select aria-label={`Permission for ${g.name}`} value={levels[g.name] ?? g.level} disabled={!ok || !c.is_owner} onChange={(e) => setLevels({ ...levels, [g.name]: e.target.value })}>
                    <option value="deny">Deny</option><option value="approval">Allow with human approval</option><option value="allow">Allow</option></select></td></tr>))}</tbody>
            </table></div>
            {c.is_owner ? <div className="row"><button className="btn-primary" onClick={save} disabled={act.busy || !dirty.length}>Save permissions</button></div> : null}
          </>) : c.mode === "protected" ? <p className="small muted">No tools registered yet. Your agent registers them with <code>PUT /api/v1/sentinel/tools</code>.</p> : null}
        <FormError msg={act.error} />
      </div>
    </Panel>
  );
}

function AuditPanel({ id, stamp }: { id: string; stamp: string }) {
  const r = useApi<{ items: any[] }>(`/connections/${id}/events`, [stamp]);
  return (
    <Panel title="Audit log" flush>
      <Load r={r} empty={(d) => !d.items.length}>
        {(d) => <ul className="audit">{d.items.slice(0, 40).map((e) => (
          <li key={e.id}><div className="row"><b className="small">{e.action.replace(/_/g, " ")}</b>{e.outcome !== "ok" ? <span className="tok tok-warn">{e.outcome}</span> : null}</div>
            <div className="small muted"><Time iso={e.created_at} rel /> by {e.actor}{e.detail?.detail ? `: ${e.detail.detail}` : ""}</div></li>))}</ul>}
      </Load>
    </Panel>
  );
}

function DangerPanel({ c, set }: { c: any; set: (x: any) => void }) {
  const act = useAction();
  const [sure, setSure] = useState(false);
  const go = async () => { const x = await act.run(() => api.post(`/connections/${c.id}/disconnect`)); if (x) set(x); };
  return (
    <Panel title="Disconnect">
      <div className="stack" style={{ gap: 8 }}>
        <p className="small muted">Revokes the stored credential, the SENTINEL service token and any test scope, and disables the agent record. Evidence and audit history are kept.</p>
        <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /><span>Disconnect {c.name}</span></label>
        <FormError msg={act.error} />
        <div className="row"><button className="btn-danger" onClick={go} disabled={!sure || act.busy}>Disconnect</button></div>
      </div>
    </Panel>
  );
}

