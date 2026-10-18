import Link from "next/link";
import { HBars, Point, StackedColumns } from "@/components/LogCharts";
import { AuditEvent } from "@/lib/api";
import { serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

const TYPES = ["claim", "case", "patient", "policy", "user"] as const;
type EntityType = (typeof TYPES)[number];
type Tab = "records" | "analytics";
const RANGES = [["24h", "Last 24 hours"], ["7d", "7 days"], ["30d", "30 days"], ["all", "All time"]] as const;

interface Row extends AuditEvent { entity_type: EntityType; entity_id: string }

interface AuditStats {
  bucket: "hour" | "day";
  totals: { events: number; actors: number; people: number; human_events: number; agent_events: number;
            errors: number; claims: number; human_decisions: number } & Record<EntityType, number>;
  volume: Point[];
  by_actor: { actor: string; count: number; errors: number; kind: "agent" | "person" }[];
  by_event: { event: string; count: number }[];
  agents: { agent: string; runs: number; avg_ms: number; p95_ms: number; max_ms: number; errors: number }[];
  decisions: { decision: string; agents: number; people: number }[];
  errors: { created_at: string; entity_type: string; entity_id: string; actor: string; event: string; detail: string }[];
  colors: Record<EntityType, string>;
}

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function ms(n: number) {
  return n >= 60000 ? `${(n / 60000).toFixed(1)} min` : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${n} ms`;
}

function link(r: { entity_type: string; entity_id: string }) {
  if (r.entity_type === "claim") return `/bundles/${r.entity_id}`;
  if (r.entity_type === "case") return `/cases/${r.entity_id}`;
  if (r.entity_type === "patient") return `/patients/${r.entity_id}`;
  return null;
}

function Tile({ label, value, sub, critical }: { label: string; value: string; sub?: string; critical?: boolean }) {
  return (
    <div className="card-elevated relative overflow-hidden p-4 pt-5">
      <span className={`absolute inset-x-0 top-0 h-1 bg-gradient-to-r ${critical ? "from-rose-500 to-pink-500" : "from-brand-500 to-indigo-500"}`} aria-hidden />
      <p className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
        {critical && <span className="grid h-4 w-4 place-items-center rounded bg-rose-600 text-[10px] font-bold text-white" aria-hidden>✕</span>}
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">{value}</p>
      {sub && <p className="mt-0.5 truncate text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

// Every audited action across the system (MySQL table audit_event): records and analytics.
export default async function AuditPage({ searchParams }: {
  searchParams: Promise<{ tab?: string; range?: string; type?: string; actor?: string }>;
}) {
  const sp = await searchParams;
  const tab: Tab = sp.tab === "analytics" ? "analytics" : "records";
  const range = RANGES.some(([r]) => r === sp.range) ? sp.range! : "24h";
  const type = TYPES.includes(sp.type as EntityType) ? (sp.type as EntityType) : undefined;
  const actor = sp.actor;

  const api = new URLSearchParams({ range, ...(type && { entity_type: type }), ...(actor && { actor }) });
  const [rows, stats] = await Promise.all([
    tab === "records" ? serverApi<Row[]>(`/api/audit?${api}`) : Promise.resolve([] as Row[]),
    tab === "analytics" ? serverApi<AuditStats>(`/api/audit/stats?${api}`) : Promise.resolve(null),
  ]);

  const href = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams({ tab, range, ...(type && { type }), ...(actor && { actor }) });
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    return `/audit?${next}`;
  };
  const chip = (on: boolean) => `rounded-full px-3 py-1.5 text-xs font-semibold capitalize ring-1 transition ${
    on ? "bg-gradient-to-r from-brand-600 to-indigo-500 text-white shadow-md shadow-brand-600/25 ring-transparent"
      : "bg-white text-slate-600 ring-slate-200 hover:-translate-y-px hover:shadow-sm"}`;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Audit log</h1>
          <p className="mt-1 text-sm text-slate-500">Every agent step and human action, timestamped, from its own table (audit_event).</p>
        </div>
        <div className="flex gap-2">
          <a href={`/api/bff/audit/export.csv?${api}`} download className="btn-ghost bg-white shadow-sm ring-1 ring-slate-200">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" /></svg>
            Raw data (CSV)
          </a>
          <a href={`/api/bff/audit/report.pdf?${api}`} download className="btn-primary">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" /></svg>
            Analysis (PDF)
          </a>
        </div>
      </div>

      <div className="flex gap-1 border-b border-slate-200" role="tablist" aria-label="Audit views">
        {([["records", "Records"], ["analytics", "Analytics"]] as const).map(([key, label]) => (
          <Link key={key} href={href({ tab: key })} role="tab" aria-selected={tab === key}
            className={`relative -mb-px rounded-t-lg px-4 py-2 text-sm font-semibold transition ${
              tab === key ? "border-x border-t border-slate-200 bg-white text-brand-700" : "text-slate-500 hover:text-slate-800"}`}>
            {label}
            {tab === key && <span className="absolute inset-x-3 -bottom-px h-0.5 rounded bg-gradient-to-r from-brand-500 to-indigo-500" aria-hidden />}
          </Link>
        ))}
      </div>

      {/* One filter row; it scopes both tabs and both downloads */}
      <nav className="flex flex-wrap items-center gap-2" aria-label="Filters">
        {RANGES.map(([r, label]) => <Link key={r} href={href({ range: r })} className={chip(range === r)}>{label}</Link>)}
        <span className="mx-1 h-5 w-px bg-slate-200" aria-hidden />
        <Link href={href({ type: undefined })} className={chip(!type)}>all entities</Link>
        {TYPES.map((t) => <Link key={t} href={href({ type: t })} className={chip(type === t)}>{t}</Link>)}
        {actor && <Link href={href({ actor: undefined })} className="rounded-full bg-slate-100 px-3 py-1.5 text-xs text-slate-600">actor: {actor} ✕</Link>}
      </nav>

      {tab === "records" ? (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead className="border-b border-slate-100 bg-slate-50/60">
              <tr><th className="th">Time</th><th className="th">Entity</th><th className="th">Actor</th><th className="th">Event</th><th className="th">Detail</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => {
                const to = link(r);
                const d = r.detail ?? {};
                const text = String(d.note ?? d.justification ?? d.error ?? d.decision ?? d.file_name ?? (d.changed ? `changed: ${(d.changed as string[]).join(", ")}` : "") ?? "");
                return (
                  <tr key={r.id} className="transition hover:bg-slate-50/80">
                    <td className="td whitespace-nowrap tabular-nums text-xs text-slate-500">{fmt(r.created_at)}</td>
                    <td className="td text-xs">
                      <span className="mr-1.5 rounded-md bg-slate-100 px-1.5 py-0.5 font-semibold capitalize text-slate-600">{r.entity_type}</span>
                      {to ? <Link href={to} className="font-mono text-brand-700 hover:underline">{r.entity_id.slice(0, 8)}</Link>
                        : <span className="font-mono text-slate-600">{r.entity_id.slice(0, 24)}</span>}
                    </td>
                    <td className="td text-xs"><Link href={href({ actor: r.actor })} className="hover:underline">{r.actor}</Link></td>
                    <td className={`td text-xs ${r.status === "error" ? "text-rose-700" : "text-slate-700"}`}>{r.event.replaceAll("_", " ")}</td>
                    <td className="td max-w-md truncate text-xs text-slate-500" title={text}>{text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && <p className="p-8 text-center text-sm text-slate-500">No audit events in this range.</p>}
        </div>
      ) : stats && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Tile label="Events" value={stats.totals.events.toLocaleString()} sub={`${stats.totals.actors} actors`} />
            <Tile label="Claims touched" value={stats.totals.claims.toLocaleString()} />
            <Tile label="People" value={stats.totals.people.toLocaleString()} sub="reviewers and staff" />
            <Tile label="Human actions" value={stats.totals.human_events.toLocaleString()} sub={`${stats.totals.agent_events.toLocaleString()} agent steps`} />
            <Tile label="Human decisions" value={stats.totals.human_decisions.toLocaleString()} sub="overrides and reviews" />
            <Tile label="Errors" value={stats.totals.errors.toLocaleString()} critical={stats.totals.errors > 0} />
          </div>

          <section className="card p-5" aria-labelledby="vol-h">
            <h2 id="vol-h" className="text-sm font-semibold text-slate-900">Audit events per {stats.bucket}, by entity</h2>
            <p className="mb-3 text-xs text-slate-500">Hover a column for its counts. Gaps are periods with no activity.</p>
            <StackedColumns data={stats.volume} unit={stats.bucket} totals={stats.totals} label="Audit events, stacked by entity"
              series={[...TYPES].reverse().map((t) => ({ key: t, label: t[0].toUpperCase() + t.slice(1), color: stats.colors[t] }))} />
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="card p-5" aria-labelledby="act-h">
              <h2 id="act-h" className="mb-3 text-sm font-semibold text-slate-900">Most active actors</h2>
              <HBars label="Events per actor" color="#2a78d6"
                rows={stats.by_actor.map((a) => ({ name: a.actor, value: a.count, note: a.kind === "person" ? "person" : undefined }))} />
            </section>
            <section className="card p-5" aria-labelledby="evt-h">
              <h2 id="evt-h" className="mb-3 text-sm font-semibold text-slate-900">Most frequent events</h2>
              <HBars label="Events by type" color="#2a78d6" rows={stats.by_event.map((e) => ({ name: e.event.replaceAll("_", " "), value: e.count }))} />
            </section>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="card overflow-hidden" aria-labelledby="perf-h">
              <h2 id="perf-h" className="px-5 pt-5 text-sm font-semibold text-slate-900">Agent performance</h2>
              <p className="px-5 text-xs text-slate-500">Time per step, slowest first.</p>
              <table className="mt-2 w-full text-xs">
                <thead className="bg-slate-50/70"><tr><th className="th">Agent</th><th className="th text-right">Runs</th><th className="th text-right">Average</th><th className="th text-right">p95</th><th className="th text-right">Errors</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {stats.agents.map((a) => (
                    <tr key={a.agent} className="hover:bg-slate-50/70">
                      <td className="td py-2 font-mono">{a.agent}</td>
                      <td className="td py-2 text-right tabular-nums">{a.runs}</td>
                      <td className="td py-2 text-right tabular-nums">{ms(a.avg_ms)}</td>
                      <td className="td py-2 text-right tabular-nums">{ms(a.p95_ms)}</td>
                      <td className={`td py-2 text-right tabular-nums ${a.errors ? "font-semibold text-rose-700" : ""}`}>{a.errors}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {stats.agents.length === 0 && <p className="p-6 text-center text-xs text-slate-500">No agent steps in this range.</p>}
            </section>
            <section className="card overflow-hidden" aria-labelledby="dec-h">
              <h2 id="dec-h" className="px-5 pt-5 text-sm font-semibold text-slate-900">Decisions: agents vs people</h2>
              <p className="px-5 text-xs text-slate-500">The agents&apos; claim decisions, and what reviewers decided.</p>
              <table className="mt-2 w-full text-xs">
                <thead className="bg-slate-50/70"><tr><th className="th">Decision</th><th className="th text-right">By the agents</th><th className="th text-right">By a reviewer</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {stats.decisions.map((d) => (
                    <tr key={d.decision}><td className="td py-2 font-semibold">{d.decision.toLowerCase()}</td>
                      <td className="td py-2 text-right tabular-nums">{d.agents}</td><td className="td py-2 text-right tabular-nums">{d.people}</td></tr>
                  ))}
                </tbody>
              </table>
              <h3 className="px-5 pt-4 text-xs font-semibold text-slate-900">Recent errors</h3>
              <ul className="divide-y divide-slate-100 px-5 pb-4 text-xs">
                {stats.errors.map((e, i) => (
                  <li key={i} className="py-2">
                    <span className="text-slate-400">{fmt(e.created_at)}</span> · <span className="font-medium text-rose-700">{e.event.replaceAll("_", " ")}</span> · {e.actor}
                    {e.detail && <p className="truncate text-slate-500" title={e.detail}>{e.detail}</p>}
                  </li>
                ))}
                {stats.errors.length === 0 && <li className="py-3 text-slate-500">No errors in this range.</li>}
              </ul>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
