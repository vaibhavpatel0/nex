import { Link, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Load, PageHead, Panel } from "../lib/ui";

export function PoliciesPage() {
  const { topic } = useParams();
  const list = useApi<{ items: { topic: string; title: string }[] }>("/policies");
  const doc = useApi<{ topic: string; title: string; body: string }>(topic ? `/policies/${encodeURIComponent(topic)}` : null);
  return (
    <>
      <PageHead title="Store policies" intro="Synthetic policies for this test store. The assistant reads the same text through its policy tool." />
      <div className="grid-main-side" style={{ gridTemplateColumns: "240px minmax(0, 1fr)" }}>
        <Panel flush title="Topics">
          <Load r={list}>
            {(d) => (
              <ul style={{ listStyle: "none", margin: 0, padding: 8, display: "grid", gap: 2 }}>
                {d.items.map((p) => (
                  <li key={p.topic}><Link to={`/policies/${p.topic}`} aria-current={p.topic === topic ? "page" : undefined}>{p.title.replace(" (synthetic store policy)", "")}</Link></li>
                ))}
              </ul>
            )}
          </Load>
        </Panel>
        {topic ? (
          <Panel title={doc.data?.title ?? "Policy"}>
            <Load r={doc}>
              {/* Rendered as plain text: the markdown source can contain hidden comments used by injection tests. */}
              {(d) => <div className="policy-body">{d.body.replace(/^# .*\n+/, "")}</div>}
            </Load>
          </Panel>
        ) : (
          <Panel><p className="muted">Choose a topic.</p></Panel>
        )}
      </div>
    </>
  );
}
