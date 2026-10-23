import Pager from "@/components/Pager";
import SearchBox from "@/components/SearchBox";
import { pageOf, slicePage } from "@/lib/paging";
import Link from "next/link";
import { notFound } from "next/navigation";
import AutoRefresh from "@/components/AutoRefresh";
import Badge from "@/components/Badge";
import ClaimDownloads from "@/components/ClaimDownloads";
import WorkflowGraph, { EdgeKind, GraphEdge, GraphNode } from "@/components/WorkflowGraph";
import { AgentSummary, AuditEvent, ClaimBundle, ClaimResult, Patient } from "@/lib/api";
import { claimProgress, fmtMs, PAGE_STEPS, PageProgress, Progress, Stage, StageState } from "@/lib/progress";
import { ApiError, currentUser, serverApi } from "@/lib/server-api";
import { LONG_STEP_S } from "@/lib/settings";
import { DeleteClaimButton, HoldPanel, ReplacePageButton, ResumeButton, StopButton } from "./ClaimControls";
import ClaimReview from "./ClaimReview";

export const dynamic = "force-dynamic";

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    + "." + iso.slice(20, 23);
}

const TONE: Record<string, string> = {
  APPROVE: "border-emerald-200 bg-emerald-50/70 text-emerald-900",
  REJECT: "border-rose-200 bg-rose-50/70 text-rose-900",
  ESCALATE: "border-amber-200 bg-amber-50/80 text-amber-900",
};

// Icon paths (24x24, stroked) for each agent card.
const ICONS: Record<string, string> = {
  classify: "M7 7h.01M3 11l8.6 8.6a2 2 0 002.8 0l5.2-5.2a2 2 0 000-2.8L11 3H3v8z",
  extract: "M9 13h6m-6 4h4M7 3h7l5 5v13H7zM14 3v5h5",
  kyc: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z",
  claims: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2M9 12h6m-6 4h4",
  policy: "M9 12l2 2 4-4m5.6-4.4A11.9 11.9 0 0112 2.9 11.9 11.9 0 013.4 5.6 12 12 0 003 9c0 5.6 3.8 10.3 9 11.6 5.2-1.3 9-6 9-11.6 0-1.2-.1-2.3-.4-3.4z",
  fraud: "M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
};

// Per state: the accent strip on top of a card, and the tint of its icon tile.
const CARD_TONE: Record<string, { bar: string; tile: string }> = {
  done: { bar: "bg-emerald-500", tile: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  error: { bar: "bg-rose-500", tile: "bg-rose-50 text-rose-700 ring-rose-200" },
  running: { bar: "bg-sky-500 animate-pulse", tile: "bg-sky-50 text-sky-700 ring-sky-200" },
  hold: { bar: "bg-orange-400", tile: "bg-orange-50 text-orange-700 ring-orange-200" },
  waiting: { bar: "bg-slate-200", tile: "bg-slate-50 text-slate-400 ring-slate-200" },
  skipped: { bar: "bg-slate-200", tile: "bg-slate-50 text-slate-400 ring-slate-200" },
};

const AGENTS: { key: string; title: string; role: string }[] = [
  { key: "classify", title: "Classifier agent", role: "Every page first: vision model document type (or UNKNOWN), image quality, blur score" },
  { key: "extract", title: "Preprocess + extract agent", role: "Preprocess: vision model, image → text · Extract: text model, that type's fields" },
  { key: "kyc", title: "KYC agent", role: "Name in the member database; insurance card valid (else human review)" },
  { key: "claims", title: "Claims agent", role: "Extracts the claim details and briefs the policy agent" },
  { key: "policy", title: "Policy agent", role: "Covered or not: claim brief + retrieved clauses + LLM (RAG)" },
  { key: "fraud", title: "Fraud agent", role: "Investigates every fraud signal and flag, incl. missing fields" },
];

function pct(v: number | null | undefined) {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function seconds(from?: string) {
  return from ? Math.max(0, Math.round((Date.now() - new Date(from).getTime()) / 1000)) : 0;
}

function elapsed(from?: string) {
  if (!from) return "";
  const s = seconds(from);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

// A step running longer than LONG_STEP_S is "in progress": it keeps working in the background.
const isLong = (s: { state: StageState; startedAt?: string }) => s.state === "running" && seconds(s.startedAt) >= LONG_STEP_S;

const STATE_STYLE: Record<StageState, { chip: string; text: string }> = {
  running: { chip: "bg-sky-100 text-sky-800 ring-sky-600/20", text: "Running" },
  done: { chip: "bg-emerald-50 text-emerald-700 ring-emerald-600/20", text: "Done" },
  error: { chip: "bg-rose-50 text-rose-700 ring-rose-600/20", text: "Error" },
  waiting: { chip: "bg-slate-100 text-slate-500 ring-slate-500/20", text: "Waiting" },
  skipped: { chip: "bg-slate-50 text-slate-400 ring-slate-400/20", text: "Not applicable" },
  hold: { chip: "bg-orange-100 text-orange-800 ring-orange-600/25", text: "On hold" },
};
const LONG_STYLE = { chip: "bg-indigo-50 text-indigo-800 ring-indigo-600/25", text: "In progress · background" };

function StateChip({ stage }: { stage: Stage }) {
  const st = isLong(stage) ? LONG_STYLE : STATE_STYLE[stage.state];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${st.chip}`}>
      {stage.state === "running" && <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" aria-hidden />}
      {st.text}{stage.state === "running" && stage.startedAt ? ` · ${elapsed(stage.startedAt)}` : ""}
      {(stage.state === "done" || stage.state === "error") && stage.durationMs != null ? ` · ${fmtMs(stage.durationMs)}` : ""}
    </span>
  );
}

// The whole pipeline in one row; the running step is highlighted and timed.
function Stepper({ progress }: { progress: Progress }) {
  return (
    <ol className="card flex flex-wrap items-center gap-x-1 gap-y-2 p-3" aria-label="Pipeline progress">
      {progress.stages.map((s, i) => (
        <li key={s.key} className="flex items-center gap-1" aria-current={s.state === "running" ? "step" : undefined}>
          {i > 0 && <span className={`h-px w-4 sm:w-6 ${s.state === "waiting" ? "bg-slate-200" : "bg-slate-400"}`} aria-hidden />}
          <span className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium ${
            isLong(s) ? "bg-indigo-50 text-indigo-800 ring-1 ring-indigo-300"
              : s.state === "running" ? "bg-sky-50 text-sky-800 ring-1 ring-sky-300"
              : s.state === "hold" ? "bg-orange-50 text-orange-800 ring-1 ring-orange-300"
              : s.state === "done" ? "text-emerald-700" : s.state === "error" ? "text-rose-700" : "text-slate-400"}`}>
            {s.state === "running"
              ? <span className="h-3 w-3 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" aria-hidden />
              : <span aria-hidden>{s.state === "done" ? "✓" : s.state === "error" ? "✕" : s.state === "hold" ? "⏸" : s.state === "skipped" ? "–" : "○"}</span>}
            {s.label}
            {PAGE_STEPS.includes(s.key as never) && s.state !== "waiting" && s.state !== "running" && <span className="font-normal opacity-75">{s.note}</span>}
            {s.state === "running" && s.startedAt && <span className="font-normal tabular-nums opacity-75">{isLong(s) ? "in progress " : ""}{elapsed(s.startedAt)}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

// A graph node, n8n style: the header (icon, name, status, one-line result) is always shown;
// the points (confidence and details) fold away under its toggle so the graph stays compact.
// Uncontrolled <details>, so a node stays open across the page's auto-refresh.
function AgentCard({ title, role, stage, summary, confidence, perPage, children }: {
  title: string; role: string; stage: Stage; summary?: string; confidence?: number | null; perPage?: boolean; children?: React.ReactNode;
}) {
  const finished = stage.state === "done" || stage.state === "error";
  const tone = CARD_TONE[stage.state] ?? CARD_TONE.waiting;
  const headline = finished && summary ? summary : role;
  return (
    <details data-points className={`group/node relative overflow-hidden rounded-2xl bg-white ring-1 transition duration-300 ease-out
      ${stage.state === "skipped" ? "opacity-70" : ""}
      shadow-[0_1px_2px_rgba(15,23,42,0.05),0_8px_24px_-12px_rgba(15,23,42,0.18)] hover:shadow-[0_2px_4px_rgba(15,23,42,0.06),0_16px_32px_-12px_rgba(15,23,42,0.25)]
      ${stage.state === "running" ? "ring-2 ring-sky-400" : stage.state === "hold" ? "ring-2 ring-orange-400" : "ring-slate-200/80"}`}>
      <span className={`absolute inset-x-0 top-0 h-1 ${tone.bar}`} aria-hidden />
      <summary className="block cursor-pointer list-none [&::-webkit-details-marker]:hidden" title={`${title}: ${role}`}>
       <span className="flex items-center gap-2.5 px-3 pb-2 pt-2.5">
        <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ring-1 ring-inset ${tone.tile}`} aria-hidden>
          <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.9">
            <path strokeLinecap="round" strokeLinejoin="round" d={ICONS[stage.key] ?? ICONS.claims} />
          </svg>
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="min-w-0 break-words text-sm font-semibold leading-tight text-slate-900">{title}</span>
            {perPage && <span className="shrink-0 rounded bg-slate-100 px-1 text-[10px] font-medium text-slate-500" title="Runs once for each page">↻ page</span>}
          </span>
          <span className="mt-0.5 flex min-w-0 items-center gap-1.5">
            <StateChip stage={stage} />
          </span>
        </span>
        <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-slate-400 transition group-open/node:rotate-180" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
        </svg>
        <span className="sr-only">Show or hide details</span>
       </span>
       {/* The one-line result, while the points are folded away */}
       <span className={`block truncate border-t border-slate-100 px-3 py-1.5 text-[11px] group-open/node:hidden ${stage.state === "error" ? "text-rose-700" : "text-slate-500"}`}>
         {headline}
       </span>
      </summary>
      {finished && confidence != null && (
        <div className="flex items-center gap-2 border-t border-slate-100 px-3 pt-2 text-[11px] tabular-nums text-slate-500">
          <span>confidence {pct(confidence)}</span>
          <span className="h-1 flex-1 overflow-hidden rounded-full bg-slate-100" aria-hidden>
            <span className={`block h-full rounded-full ${confidence >= 0.6 ? "bg-emerald-400" : "bg-amber-400"}`} style={{ width: `${Math.round(confidence * 100)}%` }} />
          </span>
        </div>
      )}
      <div className="mx-3 mb-3 mt-2 max-h-56 space-y-1.5 overflow-y-auto rounded-xl bg-slate-50/70 p-2.5 text-xs ring-1 ring-inset ring-slate-100">
        {stage.state === "running" && (isLong(stage)
          ? <p className="text-indigo-800">In progress for {elapsed(stage.startedAt)}. It keeps working in the background; you can leave this page.</p>
          : <p className="text-sky-800">Working on it. This card fills in when the agent finishes.</p>)}
        {stage.state === "hold" && <p className="text-orange-800">On hold, waiting for a person.</p>}
        {stage.state === "waiting" && <p className="text-slate-400">Waiting for the earlier steps.</p>}
        {stage.state === "skipped" && <p className="text-slate-400">Not applicable to these documents.</p>}
        {finished && summary && <p className={stage.state === "error" ? "text-rose-700" : "text-slate-700"}>{summary}</p>}
        {children}
      </div>
    </details>
  );
}

// What one page step returned, one line per page, from that step's audit row.
function pageLine(step: string, p: PageProgress): string {
  const st = p.steps[step] ?? { state: "waiting" as const };
  const d = st.detail ?? {};
  if (st.state === "running") {
    if (step !== "extract") return `running · ${elapsed(st.startedAt)}`;
    const pre = p.steps.preprocess;
    return pre?.state === "running" ? `preprocess: image → text · ${elapsed(pre.startedAt)}`
      : `extract: fields · ${elapsed(st.startedAt)}`;
  }
  if (st.state === "waiting") return "queued";
  if (d.skipped) return "skipped (earlier step failed)";
  if (d.error) return `failed: ${String(d.error).slice(0, 80)}`;
  const t = st.durationMs != null ? ` · ${fmtMs(st.durationMs)}` : "";
  if (step === "classify") return `${String(d.doc_type ?? "").replaceAll("_", " ").toLowerCase()} · ${pct(d.classifier_confidence as number)}${d.is_blurry ? " · blurry" : ""}${t}`;
  return `${Number(d.text_chars ?? 0).toLocaleString()} chars · ${((d.fields_extracted as string[]) ?? []).length} fields${((d.fields_marked_missing as string[]) ?? []).length ? ` · ${((d.fields_marked_missing as string[])).length} missing` : ""}${t}`;
}

function PageSteps({ step, progress }: { step: string; progress: Progress }) {
  return (
    <ul className="space-y-1 text-[11px] text-slate-600">
      {progress.pages.map((p, i) => {
        const st = p.steps[step] ?? { state: "waiting" as const };
        const bad = st.state === "error" || (step === "classify" && (p.steps.classify.detail?.doc_type === "UNKNOWN" || Boolean(p.steps.classify.detail?.is_blurry)));
        return (
          <li key={p.doc_id} className="flex items-center justify-between gap-2">
            <span className="truncate" title={p.file_name}><span className="tabular-nums text-slate-400">{i + 1}.</span> {p.file_name}</span>
            <span title={String(st.detail?.note ?? "")} className={`shrink-0 text-right font-medium ${st.state === "running" ? "text-sky-700"
              : bad ? "text-rose-700" : st.state === "waiting" ? "text-slate-400" : "text-slate-800"}`}>{pageLine(step, p)}</span>
          </li>
        );
      })}
    </ul>
  );
}

// While the claim runs, cards show what the audit log already knows; once it ends, the full outputs.
function Detail({ result, progress }: { result: ClaimResult; progress: Progress }) {
  const { kyc, claims, policy, fraud } = result;
  const s = result.decision?.agent_summaries ?? {};
  const fields = claims?.claim?.extracted_fields ?? {};
  const st = Object.fromEntries(progress.stages.map((x) => [x.key, x])) as Record<string, Stage>;
  const full = !progress.running && !!result.decision;
  const liveSummary = (key: string) => {
    const d = st[key].detail ?? {};
    if (d.error) return String(d.error);
    if (key === "kyc") return d.kyc_passed === false ? `failed: ${((d.flags as string[]) ?? []).join(", ")}`
      : d.needs_review ? "member found; insurance card not valid: human review" : "passed";
    if (key === "claims") return "details extracted · sent to the policy agent";
    if (key === "policy") return d.covered === false ? "not covered" : "covered";
    if (key === "fraud") return `score ${d.fraud_score ?? 0}${(d.tags as string[] | undefined)?.length ? `: ${(d.tags as string[]).join(", ")}` : ""}`;
    return "";
  };
  // title and role only: a spread `key` prop is a React error
  const agent = (key: string) => {
    const { title, role } = AGENTS.find((a) => a.key === key)!;
    return { title, role };
  };
  const props = (key: string) => ({
    ...agent(key), stage: st[key],
    summary: full ? s[key]?.summary : liveSummary(key),
    confidence: full ? s[key]?.confidence : (st[key].detail?.confidence as number | undefined),
  });
  const pageCard = (key: string) => {
    const stage = st[key];
    return (
      <AgentCard {...agent(key)} stage={stage} perPage summary={stage.state === "done" || stage.state === "error" ? stage.note : undefined}>
        <PageSteps step={key} progress={progress} />
      </AgentCard>
    );
  };

  const kycCard = (
    <AgentCard {...props("kyc")}>
      {full && kyc?.member && <p className="text-[11px] text-slate-600">Member {kyc.member.member_id} · {kyc.member.full_name}</p>}
      {full && kyc?.needs_review && (
        <p className="rounded-md bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-800">⚑ Human review: {(kyc.review_reasons ?? []).join("; ")}</p>
      )}
      {full && (
        <ul className="space-y-1 text-[11px]">
          {(kyc?.checks ?? []).map((c, i) => (
            <li key={i} className={c.passed === false ? "text-rose-700" : c.passed ? "text-slate-600" : "text-amber-700"}>
              {c.passed === false ? "✕" : c.passed ? "✓" : "?"} {c.detail}
            </li>
          ))}
        </ul>
      )}
    </AgentCard>
  );
  const claimsCard = (
    <AgentCard {...props("claims")}>
      {full && claims?.claim && (
        <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px] text-slate-600">
          <dt>Claim no.</dt><dd className="text-slate-800">{String(fields.claim_number ?? "—")}</dd>
          <dt>Amount</dt><dd className="text-slate-800">{fields.claim_amount != null ? `$${Number(fields.claim_amount).toLocaleString()}` : "—"}</dd>
          <dt>Service</dt><dd className="text-slate-800">{String(fields.service_date ?? "—")}</dd>
          <dt>ICD-10</dt><dd className="text-slate-800">{((fields.icd10_codes as string[]) ?? []).join(", ") || "—"}</dd>
          <dt>CPT</dt><dd className="text-slate-800">{((fields.cpt_codes as { code: string }[]) ?? []).map((c) => c.code).join(", ") || "—"}</dd>
          {(claims.policy_brief?.medications ?? []).length > 0 && <>
            <dt>Medications</dt><dd className="text-slate-800">{claims.policy_brief!.medications.join(", ")}</dd>
          </>}
        </dl>
      )}
      {full && claims?.policy_brief && <p className="text-[11px] text-slate-500">→ Brief sent to the policy agent</p>}
    </AgentCard>
  );
  const policyCard = (
    <AgentCard {...props("policy")}>
      {full && policy && !policy.error && (
        <>
          <p className="text-[11px] text-slate-600">{policy.plan ?? "plan unknown"} · CPT {policy.cpt_codes_checked.join(", ")}</p>
          {policy.exclusions.map((e) => <p key={e} className="text-[11px] text-rose-700">✕ {e}</p>)}
          {(policy.notes ?? []).map((n) => <p key={n} className="text-[11px] text-amber-700">! {n}</p>)}
          <p className="text-[11px] italic text-slate-500">“{policy.policy_clause}”</p>
        </>
      )}
    </AgentCard>
  );
  const fraudCard = (
    <AgentCard {...props("fraud")}>
      {full && fraud && !fraud.error && (
        <>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100" role="meter" aria-valuenow={fraud.fraud_score} aria-valuemin={0} aria-valuemax={1} aria-label="Fraud score">
            <div className={`h-full ${fraud.fraud_score >= 0.3 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${Math.max(3, fraud.fraud_score * 100)}%` }} />
          </div>
          <p className="text-[11px] font-semibold text-slate-700">Fraud signals</p>
          {fraud.anomalies.length === 0 && <p className="text-[11px] text-emerald-700">none</p>}
          {fraud.anomalies.map((a) => (
            <p key={a.type} className="text-[11px] text-amber-800"><span className="font-semibold">{a.type}</span> ({a.severity}): {a.detail}</p>
          ))}
          {(fraud.missing_fields ?? []).length > 0 && (
            <>
              <p className="text-[11px] font-semibold text-slate-700">Missing fields <span className="font-normal text-slate-500">(tracked, not scored)</span></p>
              {(fraud.missing_fields ?? []).map((m) => (
                <p key={m.document} className="text-[11px] text-slate-600">{m.detail}</p>
              ))}
            </>
          )}
        </>
      )}
    </AgentCard>
  );

  // ---- the graph: the same nodes and edges as backend app/claim/graph.py ----
  const realHold = st.ingest_hold;
  const hold: Stage = realHold ?? {
    key: "ingest_hold", label: "Hold (person)", note: "only if a page is unknown, blurry or unreadable",
    state: ["waiting", "running"].includes(st.plan.state) ? "waiting" : "skipped",
  };
  const order = progress.stages.map((x) => x.key);
  const holdAfterClassify = !!realHold && order.indexOf("ingest_hold") < order.indexOf("extract");
  const holdAfterExtract = !!realHold && !holdAfterClassify;
  const human = st.human_review;
  const started: StageState = st.classify.state === "waiting" && !progress.running ? "waiting" : "done";
  const ended: StageState = human.state === "done" || (st.decide.state === "done" && human.state === "skipped") ? "done" : "waiting";
  const decision = (st.decide.detail?.decision as string | undefined) ?? result.decision?.decision;
  const dup = st.duplicate_check;
  const isDuplicate = dup.detail?.is_duplicate === true || result.duplicate?.is_duplicate === true;
  const dupOf = (dup.detail?.duplicate_of as string | undefined) ?? result.duplicate?.duplicate_of ?? undefined;

  const edges: GraphEdge[] = [
    { from: "start", to: "classify", kind: edgeKind(started, st.classify.state) },
    { from: "classify", to: "classify", kind: edgeKind(st.classify.state, st.classify.state), label: "each page" },
    { from: "classify", to: "extract", kind: holdAfterClassify ? "unused" : edgeKind(st.classify.state, st.extract.state) },
    { from: "classify", to: "hold", conditional: true, label: "unknown · blurry", offset: -10,
      kind: holdAfterClassify ? edgeKind(st.classify.state, hold.state) : "unused" },
    { from: "hold", to: "extract", conditional: true, offset: 10, kind: holdAfterClassify ? edgeKind(hold.state, st.extract.state) : "unused" },
    { from: "extract", to: "extract", kind: edgeKind(st.extract.state, st.extract.state), label: "each page" },
    { from: "extract", to: "hold", conditional: true, label: "unreadable", offset: -10,
      kind: holdAfterExtract ? edgeKind(st.extract.state, hold.state) : "unused" },
    { from: "hold", to: "plan", conditional: true, kind: holdAfterExtract ? edgeKind(hold.state, st.plan.state) : "unused" },
    { from: "extract", to: "plan", kind: holdAfterExtract ? "unused" : edgeKind(st.extract.state, st.plan.state) },
    { from: "plan", to: "duplicate", kind: edgeKind(st.plan.state, dup.state) },
    { from: "duplicate", to: "kyc", conditional: true, kind: edgeKind(dup.state, st.kyc.state) },
    { from: "duplicate", to: "claims", conditional: true, kind: edgeKind(dup.state, st.claims.state) },
    { from: "duplicate", to: "decide", conditional: true, label: "duplicate",
      kind: isDuplicate ? edgeKind(dup.state, st.decide.state) : "unused" },
    { from: "claims", to: "policy", conditional: true, label: "claim form", kind: edgeKind(st.claims.state, st.policy.state) },
    { from: "claims", to: "fraud", conditional: true, label: "no claim form",
      kind: st.policy.state === "skipped" ? edgeKind(st.claims.state, st.fraud.state) : "unused" },
    { from: "kyc", to: "fraud", kind: edgeKind(st.kyc.state, st.fraud.state) },
    { from: "policy", to: "fraud", kind: edgeKind(st.policy.state, st.fraud.state) },
    { from: "fraud", to: "decide", kind: edgeKind(st.fraud.state, st.decide.state) },
    { from: "decide", to: "end", conditional: true, kind: ["done", "hold"].includes(human.state) ? "unused" : edgeKind(st.decide.state, ended) },
    { from: "decide", to: "human", conditional: true, label: "ESCALATE", kind: edgeKind(st.decide.state, human.state) },
    { from: "human", to: "end", kind: edgeKind(human.state, ended) },
  ];

  const nodes: GraphNode[] = [
    { id: "start", col: "1", row: 2, step: 0, content: <Terminal label="START" state={started} /> },
    { id: "classify", col: "2", row: 2, step: 1, fill: true, content: pageCard("classify") },
    { id: "hold", col: "2 / span 2", row: 1, step: holdAfterExtract ? 4 : 2, content: <FlowNode stage={hold} title="Ingest hold" sub="a person: continue, stop or re-upload" human wide /> },
    { id: "extract", col: "3", row: 2, step: 3, fill: true, content: pageCard("extract") },
    { id: "plan", col: "4", row: 2, step: 5, content: (
      <FlowNode stage={st.plan} title="Plan" compact
        sub={st.plan.state === "done" ? (((st.plan.detail?.agents as string[]) ?? []).join(", ") || "no agents") : undefined} />
    ) },
    { id: "duplicate", col: "5", row: 2, step: 6, content: (
      <FlowNode stage={dup} title="Duplicate check" sub={isDuplicate ? `duplicate of ${dupOf?.slice(0, 8) ?? "a stored claim"} · agents skipped` : dup.state === "done" ? "no earlier claim with this DOS + amount" : "checkpoint · no model call"} />
    ) },
    { id: "kyc", col: "6", row: 1, step: 7, fill: true, content: kycCard },
    { id: "claims", col: "6", row: 2, step: 7, fill: true, content: claimsCard },
    { id: "policy", col: "6", row: 3, step: 8, fill: true, content: policyCard },
    { id: "fraud", col: "7", row: 2, step: 9, fill: true, content: fraudCard },
    { id: "decide", col: "8", row: 2, step: 10, content: (
      <FlowNode stage={st.decide} title="Orchestrator" sub="decides the claim">
        {decision && st.decide.state === "done" && <span className="mt-1 block"><Badge value={decision} /></span>}
      </FlowNode>
    ) },
    { id: "human", col: "8", row: 3, step: 11, content: <FlowNode stage={human} title="Human review" sub={human.note} human /> },
    { id: "end", col: "9", row: 2, step: 12, content: <Terminal label="END" state={ended} /> },
  ];

  return (
    <WorkflowGraph nodes={nodes} edges={edges} label="Claim agent workflow" title="Agent workflow"
      minWidth={1330} columns="48px minmax(180px,1fr) minmax(180px,1fr) 60px 116px minmax(180px,1fr) minmax(180px,1fr) 116px 48px" />
  );
}

// Edge state from the two ends: taken (green), running into the target (blue, moving),
// waiting on a person (orange), failed (red), not reached yet (grey), or a path not taken.
function edgeKind(from: StageState, to: StageState): EdgeKind {
  if (from === "skipped" || to === "skipped") return "unused";
  if (from !== "done" && from !== "error") return "idle";
  if (to === "running") return "active";
  if (to === "hold") return "hold";
  if (to === "error") return "error";
  return to === "done" ? "taken" : "idle";
}

function Terminal({ label, state }: { label: string; state: StageState }) {
  return (
    <span className={`mt-2 grid h-11 w-11 place-items-center rounded-full text-[9px] font-bold tracking-wider ring-4 ${
      state === "done" ? "bg-slate-900 text-white ring-emerald-100" : "bg-slate-200 text-slate-500 ring-slate-100"}`}>
      {label}
    </span>
  );
}

// A small graph node for steps without an agent card: plan, the orchestrator, the two human steps.
function FlowNode({ stage, title, sub, human, compact, wide, children }: {
  stage: Stage; title: string; sub?: string; human?: boolean; compact?: boolean; wide?: boolean; children?: React.ReactNode;
}) {
  const tone = stage.state === "done" ? "bg-emerald-50 ring-emerald-300 text-emerald-900"
    : stage.state === "error" ? "bg-rose-50 ring-rose-300 text-rose-900"
    : stage.state === "running" ? "bg-sky-50 ring-2 ring-sky-400 text-sky-900"
    : stage.state === "hold" ? "bg-orange-50 ring-2 ring-orange-400 text-orange-900"
    : "bg-white ring-slate-200 text-slate-500";
  const dashed = stage.state === "waiting" || stage.state === "skipped";
  return (
    <div className={`rounded-xl px-3 py-2 text-center ring-1 ${tone} ${dashed ? "border border-dashed border-slate-300 ring-0" : ""} ${
      stage.state === "skipped" ? "opacity-60" : ""} ${compact ? "w-[60px] px-1.5" : wide ? "w-[200px]" : "w-[116px]"}`}
      title={`${title}: ${stage.note}`}>
      <p className="flex items-center justify-center gap-1 text-xs font-semibold">
        {human && <span aria-hidden>👤</span>}
        {stage.state === "running" && <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" aria-hidden />}
        {title}
      </p>
      <p className="mt-0.5 text-[10px] leading-tight opacity-80">
        {stage.state === "done" && stage.durationMs != null ? fmtMs(stage.durationMs) : stage.state === "skipped" ? "not taken" : stage.state}
      </p>
      {sub && !compact && <p className="mt-0.5 truncate text-[10px] leading-tight opacity-70" title={sub}>{sub}</p>}
      {sub && compact && <p className="mt-0.5 break-words text-[9px] leading-tight opacity-70">{sub}</p>}
      {children}
    </div>
  );
}

const AGENT_ACTORS = ["preprocess", "classify", "extract", "ingest_hold", "plan", "kyc", "claims", "duplicate_check", "policy", "fraud",
  "decide", "human_review", "orchestrator", "system"];

function auditText(e: AuditEvent): string {
  const d = e.detail ?? {};
  if (e.event === "agent_started") return `started${d.file_name ? ` on ${d.file_name}` : ""}`;
  if (d.note && e.event === "agent_completed") return String(d.note);
  if (Array.isArray(d.reasons) && e.event === "claim_on_hold") return `${d.note ?? ""} ${(d.reasons as string[]).join("; ")}`;
  return (d.justification ?? d.error ?? d.note ?? (d.decision ? `${d.from ? `${d.from} → ` : ""}${d.decision}` : null)
    ?? (d.to ? `${d.file_name}: ${d.from} → ${d.to}` : null)
    ?? (e.event === "document_replaced" ? `${d.from} → ${d.file_name}` : null)
    ?? (Array.isArray(d.files) ? `${(d.files as string[]).join(", ")}` : null)
    ?? (d.documents !== undefined ? `${d.documents} document(s)` : "")) as string;
}

// The claim's audit log: newest first, searchable (?aq=) and paged (?ap=). It opens by itself
// while a search or a later page is shown.
function AuditLog({ events, claimId, q, page }: { events: AuditEvent[]; claimId: string; q?: string; page: number }) {
  const term = (q ?? "").trim().toLowerCase();
  const matching = [...events].reverse().filter((e) => !term
    || [e.actor, e.event, auditText(e), JSON.stringify(e.detail ?? {})].some((t) => t.toLowerCase().includes(term)));
  const { rows, hasNext } = slicePage(matching, page);
  const params = { aq: q };
  return (
    <details id="audit" className="card group p-5" open={Boolean(term) || page > 1}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
        <span>
          <span className="block text-sm font-semibold text-slate-900">Audit log · investigation notes</span>
          <span className="block text-xs text-slate-400">{term ? `${matching.length} of ${events.length}` : events.length} events · newest first · UTC timestamps</span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-brand-700 ring-1 ring-brand-200 hover:bg-brand-50">
          <span className="group-open:hidden">Show logs</span><span className="hidden group-open:inline">Hide logs</span>
          <svg viewBox="0 0 24 24" className="h-4 w-4 transition group-open:rotate-180" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </summary>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-500">Each agent row says what it worked on, how long it took and what it returned. Open a row for the full returned values.</p>
        <div className="flex flex-wrap items-center gap-2">
          <ClaimDownloads claimId={claimId} />
          <SearchBox path={`/bundles/${claimId}`} params={params} name="aq" placeholder="Search agent, event or detail" hash="audit" />
        </div>
      </div>
      {rows.length === 0 && <p className="mt-4 text-center text-xs text-slate-500">No events match &ldquo;{q}&rdquo;.</p>}
      <ol className="mt-4 space-y-0">
        {rows.map((e) => {                               // newest first; the progress view reads them oldest first
          const d = e.detail ?? {};
          const human = !AGENT_ACTORS.includes(e.actor);
          const started = e.event === "agent_started";
          const returned = Object.fromEntries(Object.entries(d).filter(([k]) => k !== "note"));
          const long = (e.duration_ms ?? 0) >= LONG_STEP_S * 1000;
          return (
            <li key={e.id} className={`grid grid-cols-[76px_1fr] gap-x-3 gap-y-0.5 border-l-2 py-1.5 pl-3 text-xs sm:grid-cols-[96px_170px_1fr] ${
              e.status === "error" ? "border-rose-300" : e.event === "claim_on_hold" ? "border-orange-300" : "border-slate-100"}`}>
              <time className="tabular-nums text-slate-400" dateTime={e.created_at}>{fmtTime(e.created_at)}</time>
              <span className={`truncate font-medium ${e.status === "error" ? "text-rose-700" : human ? "text-brand-700" : started ? "text-slate-500" : "text-slate-800"}`}>
                {e.actor}
                {e.duration_ms != null && (
                  <span className={`font-normal ${long ? "text-indigo-700" : "text-slate-400"}`}> · took {fmtMs(e.duration_ms)}</span>
                )}
              </span>
              <span className="col-span-2 min-w-0 break-words text-slate-600 sm:col-span-1">
                <span className="text-slate-400">{e.event.replaceAll("_", " ")}</span>{" "}
                {e.event === "agent_completed" && Object.keys(returned).length > 0 ? (
                  <details className="inline">
                    <summary className="inline cursor-pointer list-none">{auditText(e)} <span className="text-brand-700">▸ returned</span></summary>
                    <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-2 text-[10px] text-slate-700">{JSON.stringify(returned, null, 2)}</pre>
                  </details>
                ) : auditText(e)}
              </span>
            </li>
          );
        })}
      </ol>
      <Pager path={`/bundles/${claimId}`} params={params} page={page} hasNext={hasNext} shown={rows.length}
        total={matching.length} pageKey="ap" hash="audit" />
    </details>
  );
}

export default async function ClaimPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<{ aq?: string; ap?: string }>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  let claim: ClaimBundle;
  try {
    claim = await serverApi<ClaimBundle>(`/api/claim-bundles/${id}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }
  const [audit, patient, user] = await Promise.all([
    serverApi<AuditEvent[]>(`/api/claim-bundles/${id}/audit`).catch(() => []),
    claim.patient_id ? serverApi<Patient>(`/api/patients/${claim.patient_id}`).catch(() => null) : Promise.resolve(null),
    currentUser(),
  ]);

  const status = claim.summary.status;
  const running = status === "QUEUED" || status === "PROCESSING";
  const canDelete = user?.role === "admin" && !running;      // admins delete; never a claim mid-run
  const result = claim.result ?? {};
  const out = result.decision;
  const ov = claim.override;
  const shown = claim.decision ?? out?.decision ?? null;
  const awaitingHuman = status === "NEEDS_REVIEW" && !!result.awaiting_human;
  const onHold = status === "ON_HOLD";
  const progress = claimProgress(audit, claim.documents, running, awaitingHuman || (onHold && result.awaiting === "human_review"),
                                 !!ov && ov.decision !== "ESCALATE" && (status === "APPROVED" || status === "REJECTED" || status === "REVIEWED"), onHold);
  const current = progress.stages.filter((s) => s.state === "running");
  const longStep = current.find(isLong);

  return (
    <div className="space-y-4">
      <AutoRefresh active={running} />

      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 py-3">
          <div className="min-w-0">
            <h1 className="flex min-w-0 items-baseline gap-2 text-xl font-semibold tracking-tight text-slate-900">
              <Link href="/bundles" className="shrink-0 text-sm font-normal text-slate-400 hover:text-brand-700">Claims /</Link>
              <span className="truncate">{claim.title}</span>
            </h1>
            <p className="mt-0.5 text-xs text-slate-500">
              {patient ? <>Patient <Link href={`/patients/${patient.patient_id}`} className="font-medium text-brand-700 hover:underline">{patient.full_name}</Link> · </> : null}
              {claim.documents.length} document(s) · submitted by {claim.created_by} on {fmt(claim.created_at)}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <dl className="flex items-stretch divide-x divide-slate-200 rounded-xl bg-slate-50/80 ring-1 ring-slate-200/80">
              <div className="px-3.5 py-1.5" title="The verdict: from the AI orchestrator, or from a reviewer who overrode it">
                <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  Decision{shown ? (ov ? " · reviewer" : " · AI") : ""}
                </dt>
                <dd className="mt-0.5 flex items-center gap-2">
                  <Badge value={shown} />
                  {shown && claim.confidence != null && !ov && (
                    <span className="text-xs tabular-nums text-slate-500" title="weighted agent confidence">{pct(claim.confidence)} confidence</span>
                  )}
                </dd>
              </div>
              <div className="px-3.5 py-1.5" title="Where the claim is in the workflow">
                <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Claim status</dt>
                <dd className="mt-0.5"><Badge value={status} /></dd>
              </div>
            </dl>
            {canDelete && <DeleteClaimButton claimId={claim.bundle_id} />}
          </div>
        </div>

        {running && (
          <div className="mx-5 mb-4 rounded-xl border border-sky-200 bg-sky-50/70 px-4 py-3 text-sky-900" role="status">
            <p className="flex items-center gap-2 text-sm font-semibold">
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" aria-hidden />
              {current.length === 0
                ? (status === "QUEUED" ? "Pending: waiting to start" : "Starting…")
                : current.map((s) => PAGE_STEPS.includes(s.key as never)
                  ? `${s.label} agent: ${s.note} · ${elapsed(s.startedAt)}`
                  : `${s.label} agent running · ${elapsed(s.startedAt)}`).join(" · ")}
            </p>
            {longStep ? (
              <p className="mt-0.5 text-xs font-medium text-indigo-900">
                In progress: the {longStep.label.toLowerCase()} step has run over {LONG_STEP_S}s. It keeps working in the background; you can leave this page. Only one claim runs at a time, so new uploads wait until it finishes.
              </p>
            ) : (
              <p className="mt-0.5 text-xs">Every page is classified first. If a page is unknown or blurry, the claim stops on hold for a person before anything is read. Then each page is preprocessed (image → text) and extracted (fields) in one step, one page at a time; KYC and claims → policy run in parallel, then fraud, then the orchestrator decides. This page updates every few seconds.</p>
            )}
            <div className="mt-2"><StopButton claimId={claim.bundle_id} /></div>
          </div>
        )}
        {result.error && !running && (
          <div className={`mx-5 mb-4 rounded-xl border px-4 py-2.5 ${TONE.REJECT}`} role="alert">
            <p className="text-sm font-semibold">Processing failed</p><p className="mt-0.5 text-xs">{result.error}</p>
          </div>
        )}
        {out && !running && (
          <div className={`mx-5 mb-4 rounded-xl border px-4 py-2.5 ${TONE[out.decision]}`}>
            <p className="text-sm font-semibold">Orchestrator: {out.decision} <span className="font-normal opacity-80">· confidence {pct(out.confidence)}</span></p>
            <ul className="mt-1 flex flex-wrap gap-x-5 gap-y-0.5 text-xs">{out.reasons.map((r) => <li key={r}>• {r}</li>)}</ul>
            {awaitingHuman && (
              <p className="mt-1.5 border-t border-black/10 pt-1.5 text-xs font-medium" role="status">
                ⏸ Paused at the human-review checkpoint. Record a decision in <span className="font-semibold">Human review</span> (bottom right) to resume it from exactly this point.
              </p>
            )}
          </div>
        )}
        {ov && (
          <div className="mx-5 mb-4 rounded-xl border border-brand-200 bg-brand-50/70 px-4 py-2.5 text-brand-900">
            <p className="text-sm font-semibold">
              {ov.decision === "ESCALATE" ? "Reviewer kept it escalated" : `Reviewer decision: ${ov.decision}`}{" "}
              <span className="font-normal opacity-80">(agents: {ov.original_decision ?? "—"}) · status now {status.replaceAll("_", " ").toLowerCase()}</span>
            </p>
            <p className="mt-0.5 text-xs">“{ov.note}” · {ov.reviewer}, {fmt(ov.reviewed_at)}</p>
          </div>
        )}
      </div>

      {onHold && result.hold && (
        <HoldPanel claimId={claim.bundle_id} hold={result.hold} waitingAtHold={result.awaiting === "ingest_hold"} />
      )}
      {result.duplicate?.is_duplicate && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900" role="status">
          <p className="text-sm font-semibold">Possible duplicate claim: human review needed</p>
          <p className="mt-0.5 text-xs">
            This patient{result.duplicate.member_id ? ` (${result.duplicate.member_id})` : ""} already has a claim with the same date of service
            ({result.duplicate.service_date ?? "—"}) and amount
            ({result.duplicate.claim_amount != null ? `${Number(result.duplicate.claim_amount).toLocaleString()}` : "—"}),
            so the agents were skipped and it is escalated to a person. Compare it with the original before approving or rejecting.
          </p>
          {result.duplicate.duplicate_of && (
            <Link href={`/bundles/${result.duplicate.duplicate_of}`} className="mt-2 inline-block text-xs font-semibold underline">
              Open the original claim →
            </Link>
          )}
        </div>
      )}
      {status === "STOPPED" && (
        <div className="rounded-xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-800" role="status">
          <p className="text-sm font-semibold">Stopped by a person</p>
          <p className="mt-0.5 text-xs">
            {result.resume?.from === "ON_HOLD" || result.resume?.from === "NEEDS_REVIEW"
              ? "Nothing is running. Resume puts it back to waiting for a person."
              : result.resume?.thread_id
                ? "Nothing is running. Resume continues from the last finished step; pages already read are not read again."
                : "Nothing is running. Resume runs it again from the start (pages already read come from the cache)."}
          </p>
          <div className="mt-2"><ResumeButton claimId={claim.bundle_id} /></div>
        </div>
      )}
      {awaitingHuman && !out && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900" role="status">
          <p className="text-sm font-semibold">Paused for human review</p>
          <p className="mt-0.5 text-xs">The run is stopped at the human_review checkpoint (LangGraph interrupt). Recording a decision below resumes it from exactly this point.</p>
        </div>
      )}
      {(running || out || onHold || status === "STOPPED") && <Stepper progress={progress} />}
      {(running || out || onHold || status === "STOPPED") && <Detail result={result} progress={progress} />}

      <div className="pb-20">
        <div className="space-y-6">
          <section className="card p-5" aria-labelledby="docs-h">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="docs-h" className="text-sm font-semibold text-slate-900">Documents</h2>
              <span className="text-xs text-slate-400">in processing order: ID → claim form → discharge → prescription → policy amendment · open a row for the page text</span>
            </div>
            <ul className="mt-3 space-y-2">
              {claim.documents.map((d, i) => {
                const read = (result.documents ?? []).find((r) => r.doc_id === d.case_id);
                const src = `/api/bff/cases/${d.case_id}/document`;
                const issue = d.doc_type === "UNKNOWN" || d.status === "UNREADABLE" || d.status === "BLURRY" || Boolean(read?.error);
                const facts = [
                  (read?.doc_type ?? d.doc_type)?.replaceAll("_", " ").toLowerCase() ?? "not classified yet",
                  read?.image_quality,
                  read?.raw_text ? `${read.raw_text.length.toLocaleString()} chars` : null,
                ].filter(Boolean).join(" · ");
                return (
                  <li key={d.case_id}>
                    <details className={`group rounded-xl ring-1 ${issue ? "ring-orange-300" : "ring-slate-200"}`}>
                      <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2 text-xs">
                        <span className="w-4 shrink-0 text-right tabular-nums text-slate-400">{i + 1}.</span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium text-slate-900" title={d.file_name}>{d.file_name}</span>
                          <span className="block truncate capitalize text-slate-500">{facts}</span>
                        </span>
                        <Badge value={d.status} />
                        <a href={src} target="_blank" rel="noreferrer" title={`View ${d.file_name}`} aria-label={`View ${d.file_name}`}
                          className="rounded-md p-1.5 text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50 hover:text-brand-700">
                          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7S2.5 12 2.5 12z" />
                            <circle cx="12" cy="12" r="3" />
                          </svg>
                        </a>
                        {!running && (
                          <ReplacePageButton claimId={claim.bundle_id} docId={d.case_id} fileName={d.file_name}
                            label={issue ? "Re-upload" : "Replace"} />
                        )}
                        <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-slate-400 transition group-open:rotate-180" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
                        </svg>
                      </summary>
                      <div className="grid gap-3 border-t border-slate-100 p-3 md:grid-cols-2">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={src} alt={`Scan of ${d.file_name}`} loading="lazy"
                          className="max-h-96 w-full rounded-lg object-contain ring-1 ring-slate-200" />
                        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-700">
                          {read?.raw_text || read?.error || "Not read yet."}
                        </pre>
                      </div>
                    </details>
                  </li>
                );
              })}
            </ul>
          </section>
          <AuditLog events={audit} claimId={claim.bundle_id} q={sp.aq} page={pageOf(sp, "ap")} />
        </div>
        {!running && <ClaimReview claimId={claim.bundle_id} busyClaim={running}
          docs={[...(result.documents ?? [])].sort((a, b) => claim.documents.findIndex((c) => c.case_id === a.doc_id) - claim.documents.findIndex((c) => c.case_id === b.doc_id))}
          needsPerson={status === "NEEDS_REVIEW" || onHold} agentDecision={out?.decision ?? claim.decision} />}
      </div>
    </div>
  );
}
