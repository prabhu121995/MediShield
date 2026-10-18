import Link from "next/link";
import { HBars, Level, VolumePoint, VolumeChart } from "@/components/LogCharts";
import { serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

interface LogRow {
  id: number;
  created_at: string;
  level: string;
  logger: string;
  source: string;
  message: string;
  exc_text: string | null;
}

interface LogStats {
  bucket: "hour" | "day";
  generated_at: string;
  totals: { records: number; error_rate: number; requests: number; server_errors: number; sources: number } & Record<Level, number>;
  volume: VolumePoint[];
  by_source: { source: string; count: number }[];
  status_classes: { class: string; count: number }[];
  top_endpoints: { endpoint: string; requests: number; "4xx": number; "5xx": number }[];
  top_errors: { message: string; count: number; sources: string[]; last_seen: string }[];
  requests_scanned: number;
  colors: Record<Level, string>;
}

type Tab = "records" | "analytics";
const RANGES = [["24h", "Last 24 hours"], ["7d", "7 days"], ["30d", "30 days"], ["all", "All time"]] as const;
const LEVELS = ["ERROR", "WARNING", "INFO", "DEBUG"] as const;
const LEVEL_STYLE: Record<string, string> = {
  CRITICAL: "bg-rose-600 text-white", ERROR: "bg-rose-50 text-rose-700 ring-rose-600/20",
  WARNING: "bg-amber-50 text-amber-800 ring-amber-600/20", INFO: "bg-sky-50 text-sky-700 ring-sky-600/20",
  DEBUG: "bg-slate-100 text-slate-600 ring-slate-500/20",
};
// HTTP classes are states, so they use the status palette (with their label beside every bar).
const STATUS_COLOR: Record<string, string> = { "2xx": "#0ca30c", "3xx": "#898781", "4xx": "#ec835a", "5xx": "#d03b3b" };
const STATUS_NOTE: Record<string, string> = { "2xx": "success", "3xx": "redirect", "4xx": "client error", "5xx": "server error" };

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "critical" | "warning" }) {
  const accent = tone === "critical" ? "from-rose-500 to-pink-500" : tone === "warning" ? "from-amber-400 to-orange-500" : "from-brand-500 to-indigo-500";
  return (
    <div className="card-elevated relative overflow-hidden p-4 pt-5">
      <span className={`absolute inset-x-0 top-0 h-1 bg-gradient-to-r ${accent}`} aria-hidden />
      <p className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
        {tone === "critical" && <span className="grid h-4 w-4 place-items-center rounded bg-rose-600 text-[10px] font-bold text-white" aria-hidden>✕</span>}
        {tone === "warning" && <span className="grid h-4 w-4 place-items-center rounded bg-amber-400 text-[10px] font-bold text-white" aria-hidden>!</span>}
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">{value}</p>
      {sub && <p className="mt-0.5 truncate text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

export default async function LogsPage({ searchParams }: {
  searchParams: Promise<{ tab?: string; range?: string; level?: string; source?: string; q?: string }>;
}) {
  const sp = await searchParams;
  const tab: Tab = sp.tab === "analytics" ? "analytics" : "records";
  const range = RANGES.some(([r]) => r === sp.range) ? sp.range! : "24h";
  const { level, source, q } = sp;

  const filters = new URLSearchParams({ range, ...(level && { level }), ...(source && { source }) });
  const recordQs = new URLSearchParams(filters);
  if (q) recordQs.set("q", q);
  const [{ sources, rows }, stats] = await Promise.all([
    serverApi<{ sources: string[]; rows: LogRow[] }>(`/api/logs?${recordQs}`),
    tab === "analytics" ? serverApi<LogStats>(`/api/logs/stats?${filters}`) : Promise.resolve(null),
  ]);

  const href = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams({ tab, range, ...(level && { level }), ...(source && { source }), ...(q && { q }) });
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    return `/logs?${next}`;
  };
  const chip = (on: boolean) => `rounded-full px-3 py-1.5 text-xs font-semibold ring-1 transition ${
    on ? "bg-gradient-to-r from-brand-600 to-indigo-500 text-white shadow-md shadow-brand-600/25 ring-transparent"
      : "bg-white text-slate-600 ring-slate-200 hover:-translate-y-px hover:shadow-sm"}`;
  const csvHref = `/api/bff/logs/export.csv?${recordQs}`;
  const pdfHref = `/api/bff/logs/report.pdf?${filters}`;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">System logs</h1>
          <p className="mt-1 text-sm text-slate-500">Application logs from the database (app_log): API, server and scripts.</p>
        </div>
        <div className="flex gap-2">
          <a href={csvHref} download className="btn-ghost bg-white shadow-sm ring-1 ring-slate-200">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" /></svg>
            Raw data (CSV)
          </a>
          <a href={pdfHref} download className="btn-primary">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" /></svg>
            Analysis (PDF)
          </a>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-slate-200" role="tablist" aria-label="Log views">
        {([["records", "Records"], ["analytics", "Analytics"]] as const).map(([key, label]) => (
          <Link key={key} href={href({ tab: key })} role="tab" aria-selected={tab === key}
            className={`relative -mb-px rounded-t-lg px-4 py-2 text-sm font-semibold transition ${
              tab === key ? "border-x border-t border-slate-200 bg-white text-brand-700" : "text-slate-500 hover:text-slate-800"}`}>
            {label}
            {tab === key && <span className="absolute inset-x-3 -bottom-px h-0.5 rounded bg-gradient-to-r from-brand-500 to-indigo-500" aria-hidden />}
          </Link>
        ))}
      </div>

      {/* One filter row; it scopes everything below and both downloads */}
      <div className="flex flex-wrap items-center gap-2">
        {RANGES.map(([r, label]) => <Link key={r} href={href({ range: r })} className={chip(range === r)}>{label}</Link>)}
        <span className="mx-1 h-5 w-px bg-slate-200" aria-hidden />
        <Link href={href({ level: undefined })} className={chip(!level)}>all levels</Link>
        {LEVELS.map((l) => <Link key={l} href={href({ level: l })} className={chip(level === l)}>{l.toLowerCase()}</Link>)}
        <span className="mx-1 h-5 w-px bg-slate-200" aria-hidden />
        <Link href={href({ source: undefined })} className={chip(!source)}>all sources</Link>
        {sources.map((s) => <Link key={s} href={href({ source: s })} className={chip(source === s)}>{s}</Link>)}
        {tab === "records" && (
          <form action="/logs" className="ml-auto flex gap-2">
            <input type="hidden" name="tab" value="records" />
            <input type="hidden" name="range" value={range} />
            {level && <input type="hidden" name="level" value={level} />}
            {source && <input type="hidden" name="source" value={source} />}
            <input name="q" defaultValue={q} placeholder="Search messages" aria-label="Search messages" className="input w-56 py-1.5 text-xs" />
          </form>
        )}
      </div>

      {tab === "records" ? (
        <div className="card divide-y divide-slate-100">
          {rows.map((r) => (
            <details key={r.id} className="group px-4 py-2 text-xs open:bg-slate-50/60">
              <summary className="flex cursor-pointer items-start gap-3">
                <time className="w-28 shrink-0 tabular-nums text-slate-400" dateTime={r.created_at}>{fmt(r.created_at)}</time>
                <span className={`w-16 shrink-0 rounded-md px-1.5 py-0.5 text-center text-[10px] font-semibold ring-1 ring-inset ${LEVEL_STYLE[r.level] ?? LEVEL_STYLE.DEBUG}`}>{r.level}</span>
                <span className="w-28 shrink-0 truncate text-slate-500" title={r.source}>{r.source}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-slate-700" title={r.message}>{r.message}</span>
              </summary>
              <div className="mt-2 space-y-2 pl-[7.75rem]">
                <p className="text-slate-500">{r.logger}</p>
                <pre className="whitespace-pre-wrap break-words rounded-lg bg-white p-2 font-mono text-[11px] text-slate-700 ring-1 ring-slate-200">{r.message}</pre>
                {r.exc_text && <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-rose-50 p-2 font-mono text-[11px] text-rose-800">{r.exc_text}</pre>}
              </div>
            </details>
          ))}
          {rows.length === 0 && <p className="p-8 text-center text-sm text-slate-500">No log records in this range.</p>}
        </div>
      ) : stats && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Tile label="Records" value={stats.totals.records.toLocaleString()} sub={`${stats.totals.sources} sources`} />
            <Tile label="Errors" value={stats.totals.ERROR.toLocaleString()} tone="critical" sub="ERROR and CRITICAL" />
            <Tile label="Warnings" value={stats.totals.WARNING.toLocaleString()} tone="warning" />
            <Tile label="Error rate" value={`${(stats.totals.error_rate * 100).toFixed(1)}%`} sub="errors / records" />
            <Tile label="HTTP requests" value={stats.totals.requests.toLocaleString()} sub={`${stats.requests_scanned.toLocaleString()} access lines read`} />
            <Tile label="5xx responses" value={stats.totals.server_errors.toLocaleString()} tone={stats.totals.server_errors ? "critical" : undefined} sub="server errors" />
          </div>

          <section className="card p-5" aria-labelledby="vol-h">
            <h2 id="vol-h" className="text-sm font-semibold text-slate-900">Log volume per {stats.bucket}, by level</h2>
            <p className="mb-3 text-xs text-slate-500">Hover a column for its counts. Gaps are periods with nothing logged.</p>
            <VolumeChart data={stats.volume} unit={stats.bucket} colors={stats.colors} totals={stats.totals} />
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="card p-5" aria-labelledby="src-h">
              <h2 id="src-h" className="mb-3 text-sm font-semibold text-slate-900">Records per source</h2>
              <HBars label="Records per source" color="#2a78d6"
                rows={stats.by_source.map((s) => ({ name: s.source, value: s.count,
                  note: stats.totals.records ? `${((s.count / stats.totals.records) * 100).toFixed(1)}%` : undefined }))} />
            </section>
            <section className="card p-5" aria-labelledby="http-h">
              <h2 id="http-h" className="mb-3 text-sm font-semibold text-slate-900">HTTP responses by status class</h2>
              <HBars label="HTTP responses by status class" colors={STATUS_COLOR}
                rows={stats.status_classes.map((s) => ({ name: s.class, value: s.count, note: STATUS_NOTE[s.class] }))} />
            </section>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="card overflow-hidden" aria-labelledby="ep-h">
              <h2 id="ep-h" className="px-5 pt-5 text-sm font-semibold text-slate-900">Top endpoints</h2>
              <table className="mt-2 w-full text-xs">
                <thead className="bg-slate-50/70"><tr><th className="th">Endpoint</th><th className="th text-right">Requests</th><th className="th text-right">4xx</th><th className="th text-right">5xx</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {stats.top_endpoints.map((e) => (
                    <tr key={e.endpoint} className="hover:bg-slate-50/70">
                      <td className="td py-2 font-mono">{e.endpoint}</td>
                      <td className="td py-2 text-right tabular-nums">{e.requests}</td>
                      <td className={`td py-2 text-right tabular-nums ${e["4xx"] ? "font-semibold text-orange-700" : ""}`}>{e["4xx"]}</td>
                      <td className={`td py-2 text-right tabular-nums ${e["5xx"] ? "font-semibold text-rose-700" : ""}`}>{e["5xx"]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {stats.top_endpoints.length === 0 && <p className="p-6 text-center text-xs text-slate-500">No requests in this range.</p>}
            </section>
            <section className="card overflow-hidden" aria-labelledby="err-h">
              <h2 id="err-h" className="px-5 pt-5 text-sm font-semibold text-slate-900">Recurring errors</h2>
              <p className="px-5 text-xs text-slate-500">Ids and numbers are folded (#), so one problem is one row.</p>
              <table className="mt-2 w-full text-xs">
                <thead className="bg-slate-50/70"><tr><th className="th">Message</th><th className="th text-right">Count</th><th className="th">Last seen</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {stats.top_errors.map((e) => (
                    <tr key={e.message} className="hover:bg-slate-50/70">
                      <td className="td max-w-xs py-2"><p className="truncate font-mono" title={e.message}>{e.message}</p><p className="truncate text-[11px] text-slate-400">{e.sources.join(", ")}</p></td>
                      <td className="td py-2 text-right font-semibold tabular-nums text-rose-700">{e.count}</td>
                      <td className="td whitespace-nowrap py-2 text-slate-500">{fmt(e.last_seen)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {stats.top_errors.length === 0 && <p className="p-6 text-center text-xs text-slate-500">No errors in this range.</p>}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
