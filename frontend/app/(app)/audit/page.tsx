import Link from "next/link";
import SearchBox from "@/components/SearchBox";
import ClaimPicker, { AuditClaim } from "@/components/ClaimPicker";
import Badge from "@/components/Badge";
import { serverApi } from "@/lib/server-api";
import { LONG_STEP_S } from "@/lib/settings";

export const dynamic = "force-dynamic";

type Tab = "steps" | "analytics";

// One case's audit trail as numbered steps, oldest first (backend app/case_timeline.py): an
// agent's start and finish rows are one step; every other event is a step of its own.
interface Step {
  id: number; run: number; step: number; actor: string; kind: "agent" | "system" | "person"; event: string;
  worked_on: string; started_at: string; finished_at: string | null; duration_ms: number | null; offset_ms: number;
  status: "ok" | "error" | "running"; result: string; detail: Record<string, unknown>;
}
interface Run {
  run: number; started_at: string; finished_at: string; wall_ms: number; agent_ms: number; steps: number;
  agent_steps: number; errors: number; running: boolean; trigger: string; decision: string | null;
  confidence: number | null; reviewer_decision: string | null; reviewer: string | null;
}
interface CaseTimeline {
  case: { case_id: string; title: string; status: string };
  totals: { runs: number; steps: number; agent_steps: number; people_steps: number; agent_ms: number; wall_ms: number;
            errors: number; decision: string | null; reviewer_decision: string | null };
  runs: Run[];
  by_agent: { agent: string; calls: number; total_ms: number; avg_ms: number; max_ms: number; errors: number; share: number; color: string }[];
  steps: Step[];
  colors: Record<string, string>;
}

const OTHER_COLOR = "#94a3b8";

function when(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

// Steps of one run are often less than a second apart, so step times show milliseconds.
function clock(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.toLocaleTimeString("en-GB", { hour12: false })}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

function ms(n: number) {
  return n >= 60000 ? `${(n / 60000).toFixed(1)} min` : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${n} ms`;
}

function what(s: Step) {
  return s.kind === "agent" ? "agent run" : s.event.replaceAll("_", " ");
}

function Tile({ label, value, sub, critical }: { label: string; value: string; sub?: string; critical?: boolean }) {
  return (
    <div className="card-elevated relative overflow-hidden p-4 pt-5">
      <span className={`absolute inset-x-0 top-0 h-1 bg-gradient-to-r ${critical ? "from-rose-500 to-pink-500" : "from-brand-500 to-indigo-500"}`} aria-hidden />
      <p className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
        {critical && <span className="grid h-4 w-4 place-items-center rounded bg-rose-600 text-[10px] font-bold text-white" aria-hidden>✕</span>}
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold capitalize tracking-tight text-slate-900">{value}</p>
      {sub && <p className="mt-0.5 truncate text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

// The run's one-line story: what started it, when, how long it took, what was decided.
function RunHeader({ r }: { r: Run }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pt-4">
      <h2 className="text-sm font-semibold text-slate-900">
        Run {r.run} <span className="font-normal text-slate-500">· {r.trigger} · {when(r.started_at)}</span>
      </h2>
      <p className="text-xs text-slate-500">
        {r.steps} steps · agent time <span className="font-semibold text-slate-700">{ms(r.agent_ms)}</span>
        {" "}· start to end <span className="font-semibold text-slate-700">{ms(r.wall_ms)}</span>
        {r.decision && <> · agents: <span className="font-semibold text-slate-800">{r.decision.toLowerCase()}</span></>}
        {r.reviewer_decision && <> · reviewer: <span className="font-semibold text-slate-800">{r.reviewer_decision.toLowerCase()}</span></>}
        {r.running && <span className="ml-1 font-semibold text-indigo-700">· still running</span>}
      </p>
    </div>
  );
}

// A Gantt of one run: each step on a shared time axis, in the order it was called.
function RunGantt({ r, steps, colors }: { r: Run; steps: Step[]; colors: Record<string, string> }) {
  const span = Math.max(r.wall_ms, 1);
  return (
    <div className="overflow-x-auto px-5 pb-5 pt-3">
      <div className="min-w-[640px]">
        <div className="ml-[13rem] mr-14 flex justify-between text-[10px] tabular-nums text-slate-400" aria-hidden>
          {[0, 0.25, 0.5, 0.75, 1].map((f) => <span key={f}>{ms(Math.round(span * f))}</span>)}
        </div>
        <ol className="mt-1" aria-label={`Run ${r.run}, steps on a time axis`}>
          {steps.map((s) => {
            const left = (Math.min(s.offset_ms, span) / span) * 100;
            const width = Math.max(((s.duration_ms ?? 0) / span) * 100, 0.4);
            const color = s.kind === "agent" ? colors[s.actor] ?? OTHER_COLOR : OTHER_COLOR;
            return (
              <li key={s.id} className="flex items-center gap-2 border-b border-slate-100 py-0.5 text-[11px]">
                <span className="w-[12.5rem] shrink-0 truncate text-slate-700" title={`${s.step}. ${s.actor}${s.worked_on ? ` · ${s.worked_on}` : ""}`}>
                  <span className="tabular-nums text-slate-400">{s.step}.</span>{" "}
                  {s.kind === "agent" ? <b className="font-semibold">{s.actor}</b> : `${s.actor}: ${what(s)}`}
                  {s.worked_on && <span className="text-slate-400"> · {s.worked_on}</span>}
                </span>
                <span className="relative h-3 flex-1">
                  <span className={`absolute top-0 h-3 rounded-sm ${s.status === "error" ? "ring-2 ring-rose-500" : ""}`}
                    style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%`, minWidth: 2, background: color }}
                    title={`${s.actor} · started ${clock(s.started_at)} · ${s.duration_ms != null ? `took ${ms(s.duration_ms)}` : what(s)}`} />
                </span>
                <span className="w-12 shrink-0 text-right tabular-nums text-slate-500">{s.duration_ms != null ? ms(s.duration_ms) : ""}</span>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

// The Audit log: pick a case, then read its steps in order and their analysis. Both downloads
// (CSV, PDF) are the same steps, from the same backend function.
export default async function AuditPage({ searchParams }: {
  searchParams: Promise<{ tab?: string; claim?: string; actor?: string; notes?: string; q?: string }>;
}) {
  const sp = await searchParams;
  const tab: Tab = sp.tab === "analytics" ? "analytics" : "steps";
  const claim = (sp.claim ?? "").trim() || undefined;
  const actor = sp.actor || undefined;
  const notes = sp.notes === "1" ? "1" : undefined;                           // agent runs only
  const q = (sp.q ?? "").trim() || undefined;

  const [t, claims] = await Promise.all([
    claim ? serverApi<CaseTimeline>(`/api/audit/case/${encodeURIComponent(claim)}`).catch(() => null) : Promise.resolve(null),
    serverApi<AuditClaim[]>("/api/audit/claims").catch(() => [] as AuditClaim[]),
  ]);
  const picked = claim ? claims.find((c) => c.claim_id === claim) : undefined;

  const listParams = { tab, claim, actor, notes, q };
  const href = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(Object.entries({ ...listParams, ...patch }).filter(([, v]) => v) as [string, string][]);
    return `/audit?${next}`;
  };
  const chip = (on: boolean) => `rounded-full px-3 py-1.5 text-xs font-semibold ring-1 transition ${
    on ? "bg-gradient-to-r from-brand-600 to-indigo-500 text-white shadow-md shadow-brand-600/25 ring-transparent"
      : "bg-white text-slate-600 ring-slate-200 hover:-translate-y-px hover:shadow-sm"}`;

  // Filters narrow what is shown; the step numbers stay those of the whole trail.
  const needle = q?.toLowerCase();
  const shown = (t?.steps ?? []).filter((s) => (!actor || s.actor === actor) && (!notes || s.kind === "agent")
    && (!needle || [s.actor, s.event, s.worked_on, s.result].some((v) => v.toLowerCase().includes(needle))));
  const base = claim && `/api/bff/claim-bundles/${encodeURIComponent(claim)}/audit`;
  const icon = <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" /></svg>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Audit log</h1>
          <p className="mt-1 text-sm text-slate-500">Every step on a case in the order it happened: which agent was called, on what, when, and how long it took.</p>
        </div>
        {/* Both downloads are the whole case, step by step, oldest first */}
        <div className="flex gap-2">
          {([["export.csv", "Steps (CSV for Excel)", "btn-ghost bg-white shadow-sm ring-1 ring-slate-200"], ["report.pdf", "Case report (PDF)", "btn-primary"]] as const)
            .map(([file, label, cls]) => base && t ? (
              <a key={file} href={`${base}/${file}`} download className={cls} title="The whole case, every step in order">{icon}{label}</a>
            ) : (
              <span key={file} aria-disabled title="Select a case first" className={`${cls} cursor-not-allowed opacity-50`}>{icon}{label}</span>
            ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <ClaimPicker claims={claims} selected={claim}
          hrefFor={Object.fromEntries([...claims.map((c) => c.claim_id), ...(claim ? [claim] : [])]
            .map((id) => [id, `/audit?${new URLSearchParams({ tab, claim: id })}`]))} />
        {t && (
          <p className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-slate-500">
            <Badge value={t.case.status} />
            <span className="font-mono">{t.case.case_id}</span>
            {picked?.status !== "DELETED" && t.case.status !== "DELETED"
              ? <Link href={`/bundles/${claim}`} className="font-semibold text-brand-700 hover:underline">Open case →</Link>
              : <span>the case was deleted; its audit trail remains</span>}
          </p>
        )}
      </div>

      {!t ? (
        <div className="card px-6 py-12 text-center" role="status">
          <p className="text-sm font-semibold text-slate-900">{claim ? "This case has no audit trail" : "Select a case to see its audit log"}</p>
          <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">
            {claims.length
              ? "You will see every step in order (step 1, step 2, …) with the agent that ran, what it worked on, when it started and finished, and how long it took. The CSV and the PDF follow the same order."
              : "No case has any audit events yet."}
          </p>
        </div>
      ) : (
        <>
          <div className="flex gap-1 border-b border-slate-200" role="tablist" aria-label="Audit views">
            {([["steps", "Steps"], ["analytics", "Analytics"]] as const).map(([key, label]) => (
              <Link key={key} href={href({ tab: key })} role="tab" aria-selected={tab === key}
                className={`relative -mb-px rounded-t-lg px-4 py-2 text-sm font-semibold transition ${
                  tab === key ? "border-x border-t border-slate-200 bg-white text-brand-700" : "text-slate-500 hover:text-slate-800"}`}>
                {label}
                {tab === key && <span className="absolute inset-x-3 -bottom-px h-0.5 rounded bg-gradient-to-r from-brand-500 to-indigo-500" aria-hidden />}
              </Link>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Tile label="Runs" value={String(t.totals.runs)} sub={t.totals.runs > 1 ? `${t.totals.runs - 1} re-run${t.totals.runs > 2 ? "s" : ""}` : "first run only"} />
            <Tile label="Steps" value={String(t.totals.steps)} sub={`${t.totals.agent_steps} agent calls`} />
            <Tile label="Agent time" value={ms(t.totals.agent_ms)} sub="all agent calls added up" />
            <Tile label="Start to end" value={ms(t.totals.wall_ms)} sub="first step to last, with waits" />
            <Tile label="Decision" value={(t.totals.reviewer_decision ?? t.totals.decision ?? "—").toLowerCase()}
              sub={t.totals.reviewer_decision ? "by a reviewer" : t.totals.decision ? "by the agents, latest run" : "not decided yet"} />
            <Tile label="Errors" value={String(t.totals.errors)} critical={t.totals.errors > 0} />
          </div>

          {tab === "steps" ? (
            <>
              <nav className="flex flex-wrap items-center gap-2" aria-label="Filters">
                <Link href={href({ notes: undefined })} className={chip(!notes)}>all steps</Link>
                <Link href={href({ notes: "1" })} className={chip(!!notes)} title="Only the agent calls: who ran, on what, how long">agent runs only</Link>
                {actor && <Link href={href({ actor: undefined })} className="rounded-full bg-slate-100 px-3 py-1.5 text-xs text-slate-600">who: {actor} ✕</Link>}
                <SearchBox path="/audit" params={listParams} keep={["tab", "claim", "actor", "notes"]}
                  placeholder="Search agent, file or result" className="ml-auto" />
              </nav>

              {t.runs.map((r) => {
                const rows = shown.filter((s) => s.run === r.run);
                if (rows.length === 0) return null;
                const longest = Math.max(1, ...rows.map((s) => s.duration_ms ?? 0));
                return (
                  <section key={r.run} className="card overflow-x-auto" aria-label={`Run ${r.run}`}>
                    <RunHeader r={r} />
                    <table className="mt-3 w-full min-w-[960px]">
                      <thead className="border-y border-slate-100 bg-slate-50/60">
                        <tr><th className="th w-12">Step</th><th className="th">Started</th><th className="th">Finished</th><th className="th">Who</th>
                          <th className="th">What · worked on</th><th className="th w-44">Duration</th><th className="th">Result</th></tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {rows.map((s) => {
                          const failed = s.status === "error";
                          const color = s.kind === "agent" ? t.colors[s.actor] ?? OTHER_COLOR : OTHER_COLOR;
                          return (
                            <tr key={s.id} className={`align-top transition hover:bg-slate-50/80 ${failed ? "bg-rose-50/40" : ""}`}>
                              <td className="td text-sm font-semibold tabular-nums text-slate-900">{s.step}</td>
                              <td className="td whitespace-nowrap tabular-nums text-xs text-slate-600">{clock(s.started_at)}</td>
                              <td className="td whitespace-nowrap tabular-nums text-xs text-slate-600">
                                {s.status === "running" ? <span className="font-semibold text-indigo-700">running…</span> : clock(s.finished_at)}
                              </td>
                              <td className="td whitespace-nowrap text-xs">
                                <Link href={href({ actor: s.actor })} className={`hover:underline ${s.kind === "agent" ? "font-semibold text-slate-900" : "text-slate-700"}`}>
                                  {s.kind === "agent" && <span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: color }} aria-hidden />}
                                  {s.actor}
                                </Link>
                                <span className="block text-[10px] uppercase tracking-wide text-slate-400">{s.kind}</span>
                              </td>
                              <td className={`td text-xs ${failed ? "text-rose-700" : "text-slate-700"}`}>
                                {what(s)}
                                {s.worked_on && <span className="block max-w-[200px] truncate text-slate-400" title={s.worked_on}>{s.worked_on}</span>}
                              </td>
                              <td className="td text-xs">
                                {s.duration_ms != null ? (
                                  <span className="flex items-center gap-2">
                                    <span className="h-2 w-20 shrink-0 overflow-hidden rounded-full bg-slate-100" aria-hidden>
                                      <span className="block h-full rounded-full" style={{ width: `${Math.max((s.duration_ms / longest) * 100, 2)}%`, background: color }} />
                                    </span>
                                    <span className={`tabular-nums font-semibold ${s.duration_ms >= LONG_STEP_S * 1000 ? "text-indigo-700" : "text-slate-800"}`}>{ms(s.duration_ms)}</span>
                                  </span>
                                ) : <span className="text-slate-400">—</span>}
                              </td>
                              <td className={`td max-w-xl break-words text-xs ${failed ? "text-rose-700" : "text-slate-600"}`}>
                                {Object.keys(s.detail).length ? (
                                  <details>
                                    <summary className="cursor-pointer list-none">{s.result || "—"} <span className="whitespace-nowrap text-brand-700">▸ values</span></summary>
                                    <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-2 text-[10px] text-slate-700">{JSON.stringify(s.detail, null, 2)}</pre>
                                  </details>
                                ) : (s.result || "—")}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </section>
                );
              })}
              {shown.length === 0 && <p className="card p-8 text-center text-sm text-slate-500">{q ? `No steps match "${q}".` : "No steps match these filters."}</p>}
            </>
          ) : (
            <div className="space-y-4">
              {t.runs.map((r) => (
                <section key={r.run} className="card overflow-hidden" aria-label={`Run ${r.run} timeline`}>
                  <RunHeader r={r} />
                  <p className="px-5 text-xs text-slate-500">When each step ran, in order; a bar&apos;s length is how long the agent took.</p>
                  <RunGantt r={r} steps={t.steps.filter((s) => s.run === r.run)} colors={t.colors} />
                </section>
              ))}

              <div className="grid gap-4 lg:grid-cols-2">
                <section className="card overflow-x-auto" aria-labelledby="agents-h">
                  <h2 id="agents-h" className="px-5 pt-5 text-sm font-semibold text-slate-900">Time by agent</h2>
                  <p className="px-5 text-xs text-slate-500">In the order the agents were called, across all runs.</p>
                  <table className="mt-2 w-full text-xs">
                    <thead className="bg-slate-50/70"><tr><th className="th">Agent</th><th className="th text-right">Calls</th><th className="th text-right">Total</th>
                      <th className="th text-right">Average</th><th className="th text-right">Longest</th><th className="th">Share of agent time</th></tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {t.by_agent.map((a) => (
                        <tr key={a.agent} className="hover:bg-slate-50/70">
                          <td className="td py-2 font-semibold"><span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: a.color }} aria-hidden />{a.agent}
                            {a.errors > 0 && <span className="ml-1 text-rose-700">· {a.errors} failed</span>}</td>
                          <td className="td py-2 text-right tabular-nums">{a.calls}</td>
                          <td className="td py-2 text-right tabular-nums font-semibold">{ms(a.total_ms)}</td>
                          <td className="td py-2 text-right tabular-nums">{ms(a.avg_ms)}</td>
                          <td className="td py-2 text-right tabular-nums">{ms(a.max_ms)}</td>
                          <td className="td py-2">
                            <span className="flex items-center gap-2">
                              <span className="h-2 w-24 overflow-hidden rounded-full bg-slate-100" aria-hidden>
                                <span className="block h-full rounded-full" style={{ width: `${a.share * 100}%`, background: a.color }} />
                              </span>
                              <span className="tabular-nums">{Math.round(a.share * 100)}%</span>
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {t.by_agent.length === 0 && <p className="p-6 text-center text-xs text-slate-500">No agent has run on this case yet.</p>}
                </section>

                <section className="card overflow-x-auto" aria-labelledby="runs-h">
                  <h2 id="runs-h" className="px-5 pt-5 text-sm font-semibold text-slate-900">Runs compared</h2>
                  <p className="px-5 text-xs text-slate-500">Each time the agents worked the case, and what they decided.</p>
                  <table className="mt-2 w-full text-xs">
                    <thead className="bg-slate-50/70"><tr><th className="th">Run</th><th className="th">Started</th><th className="th text-right">Steps</th>
                      <th className="th text-right">Agent time</th><th className="th text-right">Start to end</th><th className="th">Agents</th><th className="th">Reviewer</th></tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {t.runs.map((r) => (
                        <tr key={r.run} className="hover:bg-slate-50/70">
                          <td className="td py-2 font-semibold">{r.run}</td>
                          <td className="td whitespace-nowrap py-2">{when(r.started_at)}<span className="block text-slate-400">{r.trigger}</span></td>
                          <td className="td py-2 text-right tabular-nums">{r.steps}</td>
                          <td className="td py-2 text-right tabular-nums">{ms(r.agent_ms)}</td>
                          <td className="td py-2 text-right tabular-nums">{ms(r.wall_ms)}</td>
                          <td className="td py-2 font-semibold">{r.decision?.toLowerCase() ?? "—"}
                            {r.confidence != null && <span className="font-normal text-slate-400"> · {Math.round(r.confidence * 100)}%</span>}</td>
                          <td className="td py-2 font-semibold">{r.reviewer_decision?.toLowerCase() ?? "—"}
                            {r.reviewer && <span className="font-normal text-slate-400"> · {r.reviewer}</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
