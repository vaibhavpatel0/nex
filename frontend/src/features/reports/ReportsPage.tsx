import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useApp } from "../../app/context";
import { IconDownload } from "../../components/Icons";
import { Empty, Load, PageHead, Panel, Pager, Short, Time } from "../../components/ui";
import { useApi } from "../../hooks/useApi";
import { downloadReport, qs } from "../../services/api";

export function ReportsPage() {
  const [offset, setOffset] = useState(0);
  const r = useApi(`/reports${qs({ limit: 20, offset })}`, [offset]);
  const { toast } = useApp();
  return (
    <>
      <PageHead title="Reports" intro="Evidence-based HTML reports generated from validated run data. Open one and use your browser's Print to save it as PDF." />
      <Panel flush>
        <Load r={r} empty={(d: any) => d.items.length === 0 ? <Empty title="No reports yet">Generate one from a completed test run or an investigation.</Empty> : null}>
          {(d: any) => (
            <>
              <div className="table-wrap"><table className="t">
                <thead><tr><th>Report</th><th>Run</th><th>Data hash</th><th>Generated</th><th /></tr></thead>
                <tbody>{d.items.map((x: any) => (
                  <tr key={x.id}><td><Link to={`/reports/${x.id}`}>{x.title}</Link></td><td><Short id={x.run_id} to={`/runs/${x.run_id}`} /></td>
                    <td><Short id={x.data_hash} /></td><td className="small"><Time iso={x.generated_at} /><div className="dim">{x.generated_by}</div></td>
                    <td><button className="btn-sm" onClick={() => downloadReport(x.id).catch((e) => toast(e.message, true))}><IconDownload />Download HTML</button></td></tr>
                ))}</tbody>
              </table></div>
              <Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} />
            </>
          )}
        </Load>
      </Panel>
    </>
  );
}

export function ReportViewPage() {
  const { id } = useParams();
  const { toast } = useApp();
  const r = useApi(`/reports/${id}`, [id]);
  return (
    <Load r={r} rows={6}>
      {(x: any) => (
        <>
          <PageHead title={x.title} intro={<>Generated <Time iso={x.generated_at} /> by {x.generated_by}. Data hash <Short id={x.data_hash} />.</>}
            actions={<button className="btn-primary" onClick={() => downloadReport(x.id).catch((e) => toast(e.message, true))}><IconDownload />Download HTML</button>} />
          <Panel flush>
            {/* Rendered in a sandboxed frame with scripts disabled. */}
            <iframe title={`Report ${x.title}`} sandbox="" srcDoc={x.content} style={{ width: "100%", height: "78vh", border: 0, background: "#fff" }} />
          </Panel>
        </>
      )}
    </Load>
  );
}
