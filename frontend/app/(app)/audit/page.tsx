import Link from "next/link";
import { HBars, Point, StackedColumns } from "@/components/LogCharts";
import Pager from "@/components/Pager";
import SearchBox from "@/components/SearchBox";
import ClaimDownloads from "@/components/ClaimDownloads";
import ClaimPicker, { AuditClaim } from "@/components/ClaimPicker";
import Badge from "@/components/Badge";
import { AuditEvent } from "@/lib/api";
import { apiQuery, pageOf, pageRows } from "@/lib/paging";
import { serverApi } from "@/lib/server-api";
import { LONG_STEP_S } from "@/lib/settings";

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
  investigations: { claim_id: string; title: string; steps: number; total_ms: number; errors: number; last_at: string;
                    agent_decision: string | null; reviewer_decision: string | null; summary: string }[];
  notes: { started_at: string | null; created_at: string; entity_type: string; entity_id: string; actor: string; status: string;
           duration_ms: number | null; note: string }[];
  colors: Record<EntityType, string>;
}

// "agent_completed" rows carry the agents' investigation notes (see backend app/claim/_node.py).
const NOTE_EVENT = "agent_completed";

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function ms(n: number) {
  return n >= 60000 ? `${(n / 60000).toFixed(1)} min` : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${n} ms`;
}

// An agent_completed row is written as the agent finishes, with its duration: start = finish - duration.
function startedAt(r: { created_at: string; duration_ms: number | null }) {
  return new Date(new Date(r.created_at).getTime() - (r.duration_ms ?? 0)).toISOString();
}

// The note reads "<agent> on <file> took <time> and returned: <result>"; the columns already say the rest.
function returnedPart(note: string) {
  const i = note.indexOf(" and returned: ");
  return i >= 0 ? note.slice(i + " and returned: ".length) : note;
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
  searchParams: Promise<{ tab?: string; range?: string; type?: string; actor?: string; notes?: string; claim?: string; q?: string; page?: string }>;
}) {
  const sp = await searchParams;
  const tab: Tab = sp.tab === "analytics" ? "analytics" : "records";
  const range = RANGES.some(([r]) => r === sp.range) ? sp.range! : "24h";
  const claim = (sp.claim ?? "").trim() || undefined;                         // one claim picked: everything is scoped to it
  const type = claim ? "claim" : TYPES.includes(sp.type as EntityType) ? (sp.type as EntityType) : undefined;
  const actor = sp.actor;
  const notes = sp.notes === "1" ? "1" : undefined;                             // investigation notes only
  const event = notes && NOTE_EVENT;
  const q = (sp.q ?? "").trim() || undefined;
  const page = pageOf(sp);

  const api = new URLSearchParams({ range, ...(type && { entity_type: type }), ...(actor && { actor }), ...(event && { event }),
                                    ...(claim && { entity_id: claim }) });
  const [fetched, stats, claims] = await Promise.all([
    tab === "records" ? serverApi<Row[]>(`/api/audit?${apiQuery(page, { range, entity_type: type, actor, event, entity_id: claim, q })}`) : Promise.resolve([] as Row[]),
    tab === "analytics" ? serverApi<AuditStats>(`/api/audit/stats?${api}`) : Promise.resolve(null),
    serverApi<AuditClaim[]>("/api/audit/claims").catch(() => [] as AuditClaim[]),
  ]);
  const picked = claim ? claims.find((c) => c.claim_id === claim) : undefined;

  const { rows, hasNext } = pageRows(fetched);
  const listParams = { tab, range, type: claim ? undefined : type, actor, notes, claim, q };
  const href = (patch: Record<string, string | undefined>) => {                 // a filter change goes back to page 1
    const next = new URLSearchParams({ tab, range, ...(type && !claim && { type }), ...(actor && { actor }), ...(notes && { notes }),
                                       ...(claim && { claim }), ...(q && { q }) });
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
        {!claim && <>
          <Link href={href({ type: undefined })} className={chip(!type)}>all entities</Link>
          {TYPES.map((t) => <Link key={t} href={href({ type: t })} className={chip(type === t)}>{t}</Link>)}
          <span className="mx-1 h-5 w-px bg-slate-200" aria-hidden />
        </>}
        {/* Picking a claim shows all of its history: the range goes to "all time" */}
        <ClaimPicker claims={claims} selected={claim}
          hrefFor={Object.fromEntries([["", href({ claim: undefined })],
            ...[...claims.map((c) => c.claim_id), ...(claim ? [claim] : [])].map((id) => [id, href({ claim: id, range: "all", type: undefined })])])} />
        <span className="mx-1 h-5 w-px bg-slate-200" aria-hidden />
        <Link href={href({ notes: notes ? undefined : "1" })} className={chip(!!notes)} aria-pressed={!!notes}
          title="Only the agents' investigation notes: what each agent worked on, how long it took and what it returned">
          investigation notes only
        </Link>
        {actor && <Link href={href({ actor: undefined })} className="rounded-full bg-slate-100 px-3 py-1.5 text-xs text-slate-600">actor: {actor} ✕</Link>}
        {tab === "records" && (
          <SearchBox path="/audit" params={listParams} keep={["tab", "range", "type", "actor", "notes", "claim"]}
            placeholder="Search event, actor, ID or detail" className="ml-auto" />
        )}
      </nav>

      {claim && (
        <div className="card flex flex-wrap items-center justify-between gap-3 border-l-4 border-l-brand-500 px-4 py-3" role="status">
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900">
              <span className="truncate">{picked?.title ?? `Claim ${claim.slice(0, 8)}`}</span>
              <Badge value={picked?.status ?? "DELETED"} />
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              Showing only this claim · <span className="font-mono">{claim}</span>
              {picked && <> · {picked.events} events</>}
              {picked?.status === "DELETED" && " · the claim was deleted; its audit trail remains"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {picked?.status !== "DELETED" && (
              <Link href={`/bundles/${claim}`} className="btn-ghost bg-white text-xs shadow-sm ring-1 ring-slate-200">Open claim →</Link>
            )}
            <ClaimDownloads claimId={claim} />
            <Link href={href({ claim: undefined })} className="rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100" aria-label="Clear the claim filter">✕ Clear</Link>
          </div>
        </div>
      )}

      {tab === "records" && notes ? (
        // One row per agent run: when it started and finished, how long it took, and what it returned.
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead className="border-b border-slate-100 bg-slate-50/60">
              <tr><th className="th">Claim</th><th className="th">Agent · worked on</th><th className="th">Started</th><th className="th">Finished</th>
                <th className="th text-right">Duration</th><th className="th">What it returned</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => {
                const to = link(r);
                const d = r.detail ?? {};
                const returned = Object.fromEntries(Object.entries(d).filter(([k]) => k !== "note"));
                const result = returnedPart(String(d.note ?? ""));
                const failed = r.status === "error";
                return (
                  <tr key={r.id} className={`align-top transition hover:bg-slate-50/80 ${failed ? "bg-rose-50/40" : ""}`}>
                    <td className="td whitespace-nowrap text-xs">
                      {to ? <Link href={to} className="font-mono text-brand-700 hover:underline">{r.entity_id.slice(0, 8)}</Link>
                        : <span className="font-mono text-slate-600">{r.entity_id.slice(0, 24)}</span>}
                      {r.entity_type === "claim" && !claim && (
                        <span className="mt-1 flex items-center gap-1">
                          {claim !== r.entity_id && (
                            <Link href={href({ claim: r.entity_id, range: "all", type: undefined })} title="Show only this claim"
                              className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50">
                              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden><path strokeLinecap="round" strokeLinejoin="round" d="M3 5h18l-7 8v6l-4 2v-8L3 5z" /></svg>select
                            </Link>
                          )}
                          <ClaimDownloads claimId={r.entity_id} compact />
                        </span>
                      )}
                    </td>
                    <td className="td text-xs">
                      <Link href={href({ actor: r.actor })} className={`font-semibold hover:underline ${failed ? "text-rose-700" : "text-slate-800"}`}>{r.actor}</Link>
                      {typeof d.file_name === "string" && <span className="block max-w-[180px] truncate text-slate-400" title={d.file_name}>{d.file_name}</span>}
                    </td>
                    <td className="td whitespace-nowrap tabular-nums text-xs text-slate-600">{r.duration_ms != null ? fmt(startedAt(r)) : "—"}</td>
                    <td className="td whitespace-nowrap tabular-nums text-xs text-slate-600">{fmt(r.created_at)}</td>
                    <td className={`td whitespace-nowrap text-right tabular-nums text-xs font-semibold ${(r.duration_ms ?? 0) >= LONG_STEP_S * 1000 ? "text-indigo-700" : "text-slate-800"}`}>
                      {r.duration_ms != null ? ms(r.duration_ms) : "—"}
                    </td>
                    <td className={`td max-w-xl break-words text-xs ${failed ? "text-rose-700" : "text-slate-600"}`}>
                      <details>
                        <summary className="cursor-pointer list-none">{result || "—"} <span className="whitespace-nowrap text-brand-700">▸ values</span></summary>
                        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-2 text-[10px] text-slate-700">{JSON.stringify(returned, null, 2)}</pre>
                      </details>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && <p className="p-8 text-center text-sm text-slate-500">{q ? `No investigation notes match "${q}".` : "No investigation notes in this range."}</p>}
          <Pager path="/audit" params={listParams} page={page} hasNext={hasNext} shown={rows.length} className="px-4 pb-3" />
        </div>
      ) : tab === "records" ? (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead className="border-b border-slate-100 bg-slate-50/60">
              <tr><th className="th">Time</th><th className="th">Entity</th><th className="th">Actor</th><th className="th">Event</th><th className="th">Detail · investigation note</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => {
                const to = link(r);
                const d = r.detail ?? {};
                const text = String(d.note ?? d.justification ?? d.error ?? d.decision ?? d.file_name ?? (d.changed ? `changed: ${(d.changed as string[]).join(", ")}` : "") ?? "");
                // An agent's investigation note is shown in full, with what it returned one click away.
                const isNote = r.event === NOTE_EVENT && Boolean(d.note);
                const returned = Object.fromEntries(Object.entries(d).filter(([k]) => k !== "note"));
                return (
                  <tr key={r.id} className="align-top transition hover:bg-slate-50/80">
                    <td className="td whitespace-nowrap tabular-nums text-xs text-slate-500">
                      {fmt(r.created_at)}
                      {isNote && r.duration_ms != null && <span className="block text-slate-400">started {fmt(startedAt(r))}</span>}
                    </td>
                    <td className="td whitespace-nowrap text-xs">
                      <span className="mr-1.5 rounded-md bg-slate-100 px-1.5 py-0.5 font-semibold capitalize text-slate-600">{r.entity_type}</span>
                      {to ? <Link href={to} className="font-mono text-brand-700 hover:underline">{r.entity_id.slice(0, 8)}</Link>
                        : <span className="font-mono text-slate-600">{r.entity_id.slice(0, 24)}</span>}
                      {r.entity_type === "claim" && !claim && (
                        <span className="mt-1 flex items-center gap-1">
                          {claim !== r.entity_id && (
                            <Link href={href({ claim: r.entity_id, range: "all", type: undefined })} title="Show only this claim"
                              className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50">
                              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden><path strokeLinecap="round" strokeLinejoin="round" d="M3 5h18l-7 8v6l-4 2v-8L3 5z" /></svg>select
                            </Link>
                          )}
                          <ClaimDownloads claimId={r.entity_id} compact />
                        </span>
                      )}
                    </td>
                    <td className="td text-xs">
                      <Link href={href({ actor: r.actor })} className="hover:underline">{r.actor}</Link>
                      {r.duration_ms != null && <span className="block text-slate-400">took {ms(r.duration_ms)}</span>}
                    </td>
                    <td className={`td text-xs ${r.status === "error" ? "text-rose-700" : "text-slate-700"}`}>{r.event.replaceAll("_", " ")}</td>
                    {isNote ? (
                      <td className={`td max-w-xl break-words text-xs ${r.status === "error" ? "text-rose-700" : "text-slate-600"}`}>
                        <details>
                          <summary className="cursor-pointer list-none">{text} <span className="whitespace-nowrap text-brand-700">▸ returned</span></summary>
                          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-2 text-[10px] text-slate-700">{JSON.stringify(returned, null, 2)}</pre>
                        </details>
                      </td>
                    ) : (
                      <td className="td max-w-md truncate text-xs text-slate-500" title={text}>{text}</td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && <p className="p-8 text-center text-sm text-slate-500">{q ? `No audit events match "${q}".` : notes ? "No investigation notes in this range." : "No audit events in this range."}</p>}
          <Pager path="/audit" params={listParams} page={page} hasNext={hasNext} shown={rows.length} className="px-4 pb-3" />
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

          <section className="card overflow-x-auto" aria-labelledby="inv-h">
            <h2 id="inv-h" className="px-5 pt-5 text-sm font-semibold text-slate-900">Claim investigations</h2>
            <p className="px-5 text-xs text-slate-500">The latest claims the agents investigated: steps run, agent time, failed steps, and what was decided. The PDF lists every claim in the range. Download one claim for its whole investigation.</p>
            <table className="mt-2 w-full min-w-[760px] text-xs">
              <thead className="bg-slate-50/70">
                <tr><th className="th">Claim</th><th className="th text-right">Steps</th><th className="th text-right">Agent time</th>
                  <th className="th text-right">Failed</th><th className="th">Agents</th><th className="th">Reviewer</th><th className="th">Orchestrator note</th><th className="th">Download</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {stats.investigations.map((c) => (
                  <tr key={c.claim_id} className="align-top hover:bg-slate-50/70">
                    <td className="td py-2">
                      <Link href={`/bundles/${c.claim_id}`} className="font-medium text-brand-700 hover:underline">{c.title || c.claim_id.slice(0, 8)}</Link>
                      <span className="block text-slate-400">{fmt(c.last_at)}</span>
                    </td>
                    <td className="td py-2 text-right tabular-nums">{c.steps}</td>
                    <td className="td py-2 text-right tabular-nums">{ms(c.total_ms)}</td>
                    <td className={`td py-2 text-right tabular-nums ${c.errors ? "font-semibold text-rose-700" : ""}`}>{c.errors}</td>
                    <td className="td py-2 font-semibold">{c.agent_decision?.toLowerCase() ?? "—"}</td>
                    <td className="td py-2 font-semibold">{c.reviewer_decision?.toLowerCase() ?? "—"}</td>
                    <td className="td max-w-md py-2 text-slate-500"><p className="line-clamp-3" title={c.summary}>{c.summary || "—"}</p></td>
                    <td className="td py-2">
                      <span className="flex items-center gap-1">
                        {claim !== c.claim_id && (
                          <Link href={href({ claim: c.claim_id, range: "all", type: undefined })} title="Show only this claim"
                            className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50">
                            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden><path strokeLinecap="round" strokeLinejoin="round" d="M3 5h18l-7 8v6l-4 2v-8L3 5z" /></svg>select
                          </Link>
                        )}
                        <ClaimDownloads claimId={c.claim_id} compact />
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {stats.investigations.length === 0 && <p className="p-6 text-center text-xs text-slate-500">No claims investigated in this range.</p>}
          </section>

          <section className="card p-5" aria-labelledby="notes-h">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="notes-h" className="text-sm font-semibold text-slate-900">Latest investigation notes</h2>
              <Link href={href({ tab: "records", notes: "1" })} className="text-xs font-semibold text-brand-700 hover:underline">All notes in Records →</Link>
            </div>
            <ol className="mt-3 space-y-0">
              {stats.notes.map((n, i) => (
                <li key={i} className={`border-l-2 py-1.5 pl-3 text-xs ${n.status === "error" ? "border-rose-300" : "border-slate-100"}`}>
                  <p className="text-slate-400">
                    <Link href={link(n) ?? "#"} className="font-mono text-brand-700 hover:underline">{n.entity_id.slice(0, 8)}</Link>
                    {" "}· <span className={`font-medium ${n.status === "error" ? "text-rose-700" : "text-slate-700"}`}>{n.actor}</span>
                    {n.started_at && <> · started <span className="tabular-nums">{fmt(n.started_at)}</span></>}
                    {" "}· finished <span className="tabular-nums">{fmt(n.created_at)}</span>
                    {n.duration_ms != null && <> · took <span className="font-semibold text-slate-700">{ms(n.duration_ms)}</span></>}
                  </p>
                  <p className={`break-words ${n.status === "error" ? "text-rose-700" : "text-slate-600"}`}>{n.note}</p>
                </li>
              ))}
              {stats.notes.length === 0 && <li className="py-3 text-xs text-slate-500">No investigation notes in this range.</li>}
            </ol>
          </section>
        </div>
      )}
    </div>
  );
}
