import { useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { FormError, Json, Load, PageHead, Panel } from "../lib/ui";

interface ModelStatus {
  provider: string; mode: string; base_url: string; model: string; api_key_configured: boolean; reachable: boolean;
  model_listed: boolean | null; tool_support: boolean | null; models: string[]; error?: string; max_rounds: number; timeout_seconds: number; staff_tools_exposed: boolean;
}

function regText(r: any): string {
  if (!r) return "Loading";
  if (r.status !== "ok") return `${r.status}: ${r.detail}`;
  const d = r.detail ?? {};
  const n = (d.created?.length ?? 0) + (d.updated?.length ?? 0) + (typeof d.unchanged === "number" ? d.unchanged : 0);
  return `Registered ${n} tools in namespace ${d.namespace ?? "shop"}`;
}

const yn = (v: boolean | null | undefined, unknown = "Not checked") => (v === null || v === undefined ? unknown : v ? "Yes" : "No");

export function SetupView() {
  const model = useApi<ModelStatus>("/workbench/model");
  const integ = useApi<any>("/workbench/integration");
  const reg = useApi<any>("/workbench/registration");
  const tools = useApi<{ items: any[] }>("/workbench/tools");
  const [check, setCheck] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const runCheck = async () => {
    setBusy(true);
    setErr(null);
    try {
      setCheck(await api.post("/workbench/model/check"));
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead title="Model and integration" intro="Configuration is read from the backend .env file. Keys are never sent to the browser; this page only shows whether one is set." />
      <div className="grid-2">
        <Panel title="Model" actions={<button className="btn-sm" onClick={model.reload}>Refresh</button>}>
          <Load r={model}>
            {(m) => (
              <div style={{ display: "grid", gap: 10 }}>
                <dl className="kv">
                  <dt>Provider</dt><dd>{m.provider} ({m.mode === "cloud" ? "Ollama cloud, requests leave this computer" : m.mode})</dd>
                  <dt>Endpoint</dt><dd><code>{m.base_url}</code></dd>
                  <dt>Model</dt><dd><code>{m.model}</code></dd>
                  <dt>API key</dt><dd>{m.api_key_configured ? "Configured in .env (not shown)" : m.mode === "cloud" ? "Missing. Set OLLAMA_API_KEY in the backend .env" : "Not needed for local Ollama"}</dd>
                  <dt>Reachable</dt><dd>{yn(m.reachable)}</dd>
                  <dt>Model available</dt><dd>{yn(m.model_listed)}</dd>
                  <dt>Supports tools</dt><dd>{yn(m.tool_support)}</dd>
                  <dt>Limits</dt><dd>{m.max_rounds} model rounds per message, {m.timeout_seconds} s per request</dd>
                  <dt>Staff tools offered</dt><dd>{m.staff_tools_exposed ? "Yes (SENTINEL must block them; used by security tests)" : "No"}</dd>
                </dl>
                {m.error ? <div className="notice notice-bad">{m.error}</div> : null}
                <div className="row">
                  <button onClick={runCheck} disabled={busy}>{busy ? "Checking" : "Check tool calling"}</button>
                  <span className="small muted">Sends one short request to the configured model{m.mode === "cloud" ? ", which counts against your Ollama cloud usage" : ""}. No tool is executed.</span>
                </div>
                <FormError msg={err} />
                {check ? (
                  <div className={`notice ${check.ok ? "" : "notice-bad"}`} role="status">
                    {check.ok ? "The model proposed the expected tool call." : "The model did not propose the expected tool call."} {check.error ? `${check.error_class}: ${check.error}` : ""}
                    <Json value={check} />
                  </div>
                ) : null}
              </div>
            )}
          </Load>
        </Panel>
        <Panel title="AEGIS" actions={<button className="btn-sm" onClick={() => { integ.reload(); reg.reload(); }}>Refresh</button>}>
          <Load r={integ}>
            {(i) => (
              <dl className="kv">
                <dt>API</dt><dd><code>{i.aegis_base_url}</code></dd>
                <dt>Reachable</dt><dd>{i.reachable ? "Yes" : `No. ${i.error ?? ""}`}</dd>
                <dt>Agent</dt><dd><code>{i.agent_key}</code></dd>
                <dt>Service token</dt><dd>{i.token_configured ? "Configured in .env (not shown)" : "Missing. Run make link-ecommerce"}</dd>
                <dt>Internal token</dt><dd>{i.internal_token_configured ? "Configured (AEGIS can run test scenarios here)" : "Missing. Run make link-ecommerce"}</dd>
                <dt>Tool registration</dt><dd>{regText(reg.data)}</dd>
                <dt>Scenarios</dt><dd>{i.scenarios ? `${i.scenarios.length} available for this agent` : "Unavailable"}</dd>
                <dt>AEGIS interface</dt><dd><a href={i.aegis_ui_url} target="_blank" rel="noreferrer">{i.aegis_ui_url}</a></dd>
              </dl>
            )}
          </Load>
        </Panel>
      </div>
      <Panel flush title="Tools registered with SENTINEL">
        <Load r={tools}>
          {(d) => (
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>Tool</th><th>Operation</th><th>Sensitivity</th><th>Offered to model</th><th>Description</th></tr></thead>
                <tbody>
                  {d.items.map((t) => (
                    <tr key={t.name}>
                      <td><code>{t.name}</code>{t.staff_only ? <div className="small" style={{ color: "var(--amber)" }}>Staff only</div> : null}</td>
                      <td>{t.operation}</td>
                      <td>{t.sensitivity}</td>
                      <td>{t.exposed_to_model ? "Yes" : "No"}</td>
                      <td className="wrap small">{t.description}<details><summary>Argument schema</summary><Json value={t.schema} /></details></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Load>
      </Panel>
    </>
  );
}
