import { useApp } from "../../app/context";
import { IconRefresh } from "../../components/Icons";
import { Json, Load, PageHead, Panel, Time } from "../../components/ui";
import { useApi } from "../../hooks/useApi";
import { api } from "../../services/api";

const STATUS: Record<string, [string, string]> = {
  ok: ["tok-ok", "Reachable"], unreachable: ["tok-error", "Not reachable"], not_configured: ["tok-warn", "Not configured"],
  installed: ["tok-ok", "Installed"], not_installed: ["tok-neutral", "Not installed"], unchecked: ["tok-neutral", "Not checked yet"],
};

export function IntegrationsPage() {
  const { can, toast } = useApp();
  const r = useApi("/integrations");
  const check = async (key: string) => {
    try { const x = await api.post(`/integrations/${key}/check`); toast(`${x.name}: ${STATUS[x.status]?.[1] ?? x.status}`); r.reload(); }
    catch (e) { toast((e as Error).message, true); }
  };
  return (
    <>
      <PageHead title="Integrations" intro="Optional connections. The platform runs fully without them using simulated agents. Secrets are read from environment variables and never shown here." />
      <Load r={r}>
        {(d: any) => {
          const by = Object.fromEntries(d.items.map((i: any) => [i.key, i]));
          const tok = (k: string) => { const [c, l] = STATUS[by[k]?.status ?? "unchecked"] ?? ["tok-neutral", by[k]?.status]; return <span className={`tok ${c}`}>{l}</span>; };
          const btn = (k: string) => can("tests:run") ? <button className="btn-sm" onClick={() => check(k)}><IconRefresh />Check now</button> : null;
          const o = by.ollama, h = by.hosted, g = by.garak, ec = by.ecommerce;
          return (
            <div className="stack">
              <Panel title="Ollama (local or cloud models)" actions={<>{tok("ollama")}{btn("ollama")}</>}>
                <div className="grid-2">
                  <div className="stack" style={{ gap: 8 }}>
                    <dl className="kv small">
                      <dt>Base URL</dt><dd className="mono">{o?.config?.base_url}</dd>
                      <dt>Mode</dt><dd>{o?.config?.mode === "cloud" ? "Direct cloud API (needs OLLAMA_API_KEY)" : "Local server"}</dd>
                      <dt>Models used by agents</dt><dd className="mono">{o?.config?.models_used_by_agents?.join(", ") || "None"}</dd>
                      <dt>Last check</dt><dd><Time iso={o?.last_checked_at} /></dd>
                      {o?.detail?.models ? <><dt>Installed models</dt><dd className="mono">{o.detail.models.join(", ") || "None"}</dd></> : null}
                      {o?.detail?.tool_support !== undefined && o?.detail?.tool_support !== null ? <><dt>Tool calling</dt><dd>{String(o.detail.tool_support)}</dd></> : null}
                      {o?.detail?.error ? <><dt>Error</dt><dd>{o.detail.error}</dd></> : null}
                    </dl>
                    <p className="small muted">Test runs against an Ollama agent return ERROR, never PASS, while Ollama or the model is unavailable.</p>
                  </div>
                  <div className="small">
                    <h3>Set up on macOS</h3>
                    <ol style={{ paddingLeft: 18 }}>
                      <li>Install: <code>curl -fsSL https://ollama.com/install.sh | sh</code> (or the app from ollama.com/download).</li>
                      <li>Local model with tool calling that fits 16 GB: <code>ollama pull qwen3:8b</code>. Smaller and faster: <code>qwen3:4b</code>.</li>
                      <li>Cloud models instead: run <code>ollama signin</code>, then use a model name ending in <code>:cloud</code> on the agent. Cloud usage needs credits or a paid plan.</li>
                      <li>Press Check now. The agent's status changes to Ready when the model is installed.</li>
                    </ol>
                  </div>
                </div>
              </Panel>
              <Panel title="E-commerce agent platform (Halden Bazaar)" actions={<>{tok("ecommerce")}{btn("ecommerce")}</>}>
                <dl className="kv small">
                  <dt>Shop API</dt><dd className="mono">{ec?.config?.base_url}</dd>
                  <dt>Internal token</dt><dd className="mono">{ec?.config?.token_env}{ec?.config?.token_configured ? ", set" : ", not set (run make link-ecommerce)"}</dd>
                  <dt>Last check</dt><dd><Time iso={ec?.last_checked_at} /></dd>
                  {ec?.detail?.error ? <><dt>Error</dt><dd>{ec.detail.error}</dd></> : null}
                  <dt>Agent Workbench</dt><dd><a href={`${ec?.config?.ui_url}/workbench`} target="_blank" rel="noreferrer">{ec?.config?.ui_url}/workbench</a></dd>
                </dl>
                <p className="small muted" style={{ marginTop: 6 }}>The shopping agent runs in the e-commerce backend and asks SENTINEL here before every tool call. Test runs against agent <span className="mono">shop-assistant</span> execute in sandbox copies of the store; they return ERROR while the shop backend is down.</p>
              </Panel>
              <Panel title="Hosted provider (OpenAI-compatible)" actions={<>{tok("hosted")}{btn("hosted")}</>}>
                <dl className="kv small">
                  <dt>Base URL</dt><dd className="mono">{h?.config?.base_url ?? "Not set (AEGIS_HOSTED_BASE_URL)"}</dd>
                  <dt>API key variable</dt><dd className="mono">{h?.config?.api_key_env}{h?.detail?.api_key_present !== undefined ? (h.detail.api_key_present ? ", present" : ", not set") : ""}</dd>
                  <dt>Models used by agents</dt><dd className="mono">{h?.config?.models_used_by_agents?.join(", ") || "None"}</dd>
                </dl>
                <p className="small muted" style={{ marginTop: 6 }}>Optional. Set the base URL and the key in your local .env file, then register a hosted-model agent. Paid providers bill per request; the platform never sends a request until you run a test.</p>
              </Panel>
              <Panel title="garak LLM vulnerability scanner" actions={<>{tok("garak")}{btn("garak")}</>}>
                <p className="small">Running garak from inside the platform is not implemented yet. If it is installed, run it manually against the same Ollama model:</p>
                <pre className="pre">{g?.config?.manual_command}</pre>
                {g?.detail && Object.keys(g.detail).length ? <Json value={g.detail} /> : null}
              </Panel>
            </div>
          );
        }}
      </Load>
    </>
  );
}
