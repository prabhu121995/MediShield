import Link from "next/link";
import { notFound } from "next/navigation";
import AutoRefresh from "@/components/AutoRefresh";
import Badge from "@/components/Badge";
import { AgentSummary, AuditEvent, ClaimBundle, ClaimResult, Patient } from "@/lib/api";
import { claimProgress, fmtMs, PageProgress, Progress, Stage, StageState } from "@/lib/progress";
import { ApiError, serverApi } from "@/lib/server-api";
import { LONG_STEP_S } from "@/lib/settings";
import { HoldPanel, ReplacePageButton, StopButton } from "./ClaimControls";
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

const AGENTS: { key: string; title: string; role: string }[] = [
  { key: "preprocess", title: "Preprocessor agent", role: "Vision model: image → text, image quality, blur score" },
  { key: "classify", title: "Classifier agent", role: "Vision model: document type, or UNKNOWN below the confidence bar" },
  { key: "extract", title: "Extractor agent", role: "Text model: that type's fields from the page text" },
  { key: "kyc", title: "KYC agent", role: "Member, ID validity, identity across pages" },
  { key: "claims", title: "Claims agent", role: "Claim form schema and clinical facts" },
  { key: "policy", title: "Policy agent", role: "CPT coverage: retrieved clauses + LLM (RAG)" },
  { key: "fraud", title: "Fraud agent", role: "Six signal tags across documents and history" },
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
            {["preprocess", "classify", "extract"].includes(s.key) && s.state !== "waiting" && s.state !== "running" && <span className="font-normal opacity-75">{s.note}</span>}
            {s.state === "running" && s.startedAt && <span className="font-normal tabular-nums opacity-75">{isLong(s) ? "in progress " : ""}{elapsed(s.startedAt)}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

// Same size for every card; long content scrolls inside the card.
function AgentCard({ title, role, stage, summary, confidence, children }: {
  title: string; role: string; stage: Stage; summary?: string; confidence?: number | null; children?: React.ReactNode;
}) {
  const finished = stage.state === "done" || stage.state === "error";
  return (
    <div className={`card flex h-80 flex-col p-4 transition ${stage.state === "running" ? "ring-2 ring-sky-400" : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">{title}</p>
          <p className="truncate text-[11px] text-slate-500" title={role}>{role}</p>
        </div>
        <StateChip stage={stage} />
      </div>
      {finished && confidence != null && <p className="mt-1 text-[11px] tabular-nums text-slate-500">confidence {pct(confidence)}</p>}
      <div className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1 text-xs">
        {stage.state === "running" && (isLong(stage)
          ? <p className="text-indigo-800">In progress for {elapsed(stage.startedAt)}. It keeps working in the background; you can leave this page.</p>
          : <p className="text-sky-800">Working on it. This card fills in when the agent finishes.</p>)}
        {stage.state === "hold" && <p className="text-orange-800">On hold, waiting for a person.</p>}
        {stage.state === "waiting" && <p className="text-slate-400">Waiting for the earlier steps.</p>}
        {stage.state === "skipped" && <p className="text-slate-400">Not applicable to these documents.</p>}
        {finished && summary && <p className={stage.state === "error" ? "text-rose-700" : "text-slate-700"}>{summary}</p>}
        {children}
      </div>
    </div>
  );
}

// What one page step returned, one line per page, from that step's audit row.
function pageLine(step: string, p: PageProgress): string {
  const st = p.steps[step];
  const d = st.detail ?? {};
  if (st.state === "running") return `running · ${elapsed(st.startedAt)}`;
  if (st.state === "waiting") return "queued";
  if (d.skipped) return "skipped (earlier step failed)";
  if (d.error) return `failed: ${String(d.error).slice(0, 80)}`;
  const t = st.durationMs != null ? ` · ${fmtMs(st.durationMs)}` : "";
  if (step === "preprocess") return `${Number(d.text_chars ?? 0).toLocaleString()} chars · ${d.image_quality ?? "clear"}${d.is_blurry ? " · blurry" : ""}${t}`;
  if (step === "classify") return `${String(d.doc_type ?? "").replaceAll("_", " ").toLowerCase()} · ${pct(d.classifier_confidence as number)}${t}`;
  return `${((d.fields_extracted as string[]) ?? []).length} fields${((d.fields_marked_missing as string[]) ?? []).length ? ` · ${((d.fields_marked_missing as string[])).length} missing` : ""}${t}`;
}

function PageSteps({ step, progress }: { step: string; progress: Progress }) {
  return (
    <ul className="space-y-1 text-[11px] text-slate-600">
      {progress.pages.map((p) => {
        const st = p.steps[step];
        const bad = st.state === "error" || (step === "classify" && p.steps.classify.detail?.doc_type === "UNKNOWN");
        return (
          <li key={p.doc_id} className="flex items-center justify-between gap-2">
            <span className="truncate" title={p.file_name}>{p.file_name}</span>
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
    if (key === "kyc") return d.kyc_passed === false ? `failed: ${((d.flags as string[]) ?? []).join(", ")}` : "passed";
    if (key === "claims") return d.schema_valid === false ? "schema errors found" : "schema valid";
    if (key === "policy") return d.covered === false ? "not covered" : "covered";
    if (key === "fraud") return `score ${d.fraud_score ?? 0}${(d.tags as string[] | undefined)?.length ? `: ${(d.tags as string[]).join(", ")}` : ""}`;
    return "";
  };
  const props = (i: number, key: string) => ({
    ...AGENTS[i], stage: st[key],
    summary: full ? s[key]?.summary : liveSummary(key),
    confidence: full ? s[key]?.confidence : (st[key].detail?.confidence as number | undefined),
  });
  const pageCard = (i: number) => {
    const key = AGENTS[i].key;
    const stage = st[key];
    return (
      <AgentCard {...AGENTS[i]} stage={stage} summary={stage.state === "done" || stage.state === "error" ? stage.note : undefined}>
        <PageSteps step={key} progress={progress} />
      </AgentCard>
    );
  };
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {pageCard(0)}
      {pageCard(1)}
      {pageCard(2)}
      <AgentCard {...props(3, "kyc")}>
        {full && kyc?.member && <p className="text-[11px] text-slate-600">Member {kyc.member.member_id} · {kyc.member.full_name}</p>}
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
      <AgentCard {...props(4, "claims")}>
        {full && claims?.claim && (
          <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px] text-slate-600">
            <dt>Claim no.</dt><dd className="text-slate-800">{String(fields.claim_number ?? "—")}</dd>
            <dt>Amount</dt><dd className="text-slate-800">{fields.claim_amount != null ? `$${Number(fields.claim_amount).toLocaleString()}` : "—"}</dd>
            <dt>Service</dt><dd className="text-slate-800">{String(fields.service_date ?? "—")}</dd>
            <dt>ICD-10</dt><dd className="text-slate-800">{((fields.icd10_codes as string[]) ?? []).join(", ") || "—"}</dd>
            <dt>CPT</dt><dd className="text-slate-800">{((fields.cpt_codes as { code: string }[]) ?? []).map((c) => c.code).join(", ") || "—"}</dd>
          </dl>
        )}
        {full && (claims?.schema_errors ?? []).map((e) => <p key={e} className="text-[11px] text-rose-700">✕ {e}</p>)}
      </AgentCard>
      <AgentCard {...props(5, "policy")}>
        {full && policy && !policy.error && (
          <>
            <p className="text-[11px] text-slate-600">{policy.plan ?? "plan unknown"} · CPT {policy.cpt_codes_checked.join(", ")}</p>
            {policy.exclusions.map((e) => <p key={e} className="text-[11px] text-rose-700">✕ {e}</p>)}
            {(policy.notes ?? []).map((n) => <p key={n} className="text-[11px] text-amber-700">! {n}</p>)}
            <p className="text-[11px] italic text-slate-500">“{policy.policy_clause}”</p>
          </>
        )}
      </AgentCard>
      <AgentCard {...props(6, "fraud")}>
        {full && fraud && !fraud.error && (
          <>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-100" role="meter" aria-valuenow={fraud.fraud_score} aria-valuemin={0} aria-valuemax={1} aria-label="Fraud score">
              <div className={`h-full ${fraud.fraud_score >= 0.3 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${Math.max(3, fraud.fraud_score * 100)}%` }} />
            </div>
            {fraud.anomalies.map((a) => (
              <p key={a.type} className="text-[11px] text-amber-800"><span className="font-semibold">{a.type}</span> ({a.severity}): {a.detail}</p>
            ))}
          </>
        )}
      </AgentCard>
    </div>
  );
}

const AGENT_ACTORS = ["preprocess", "classify", "extract", "ingest_hold", "plan", "kyc", "claims", "policy", "fraud",
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

function AuditLog({ events }: { events: AuditEvent[] }) {
  return (
    <section className="card p-5" aria-labelledby="audit-h">
      <div className="flex items-baseline justify-between">
        <h2 id="audit-h" className="text-sm font-semibold text-slate-900">Audit log · investigation notes</h2>
        <span className="text-xs text-slate-400">{events.length} events · UTC timestamps</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">Each agent row says what it worked on, how long it took and what it returned. Open a row for the full returned values.</p>
      <ol className="mt-4 space-y-0">
        {events.map((e) => {
          const d = e.detail ?? {};
          const human = !AGENT_ACTORS.includes(e.actor);
          const started = e.event === "agent_started";
          const returned = Object.fromEntries(Object.entries(d).filter(([k]) => k !== "note"));
          const long = (e.duration_ms ?? 0) >= LONG_STEP_S * 1000;
          return (
            <li key={e.id} className={`grid grid-cols-[88px_130px_1fr] gap-3 border-l-2 py-1.5 pl-3 text-xs sm:grid-cols-[96px_170px_1fr] ${
              e.status === "error" ? "border-rose-300" : e.event === "claim_on_hold" ? "border-orange-300" : "border-slate-100"}`}>
              <time className="tabular-nums text-slate-400" dateTime={e.created_at}>{fmtTime(e.created_at)}</time>
              <span className={`truncate font-medium ${e.status === "error" ? "text-rose-700" : human ? "text-brand-700" : started ? "text-slate-500" : "text-slate-800"}`}>
                {e.actor}
                {e.duration_ms != null && (
                  <span className={`font-normal ${long ? "text-indigo-700" : "text-slate-400"}`}> · took {fmtMs(e.duration_ms)}</span>
                )}
              </span>
              <span className="min-w-0 break-words text-slate-600">
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
    </section>
  );
}

export default async function ClaimPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let claim: ClaimBundle;
  try {
    claim = await serverApi<ClaimBundle>(`/api/claim-bundles/${id}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }
  const [audit, patient] = await Promise.all([
    serverApi<AuditEvent[]>(`/api/claim-bundles/${id}/audit`).catch(() => []),
    claim.patient_id ? serverApi<Patient>(`/api/patients/${claim.patient_id}`).catch(() => null) : Promise.resolve(null),
  ]);

  const status = claim.summary.status;
  const running = status === "QUEUED" || status === "PROCESSING";
  const result = claim.result ?? {};
  const out = result.decision;
  const ov = claim.override;
  const shown = claim.decision ?? out?.decision ?? null;
  const awaitingHuman = status === "NEEDS_REVIEW" && !!result.awaiting_human;
  const onHold = status === "ON_HOLD";
  const progress = claimProgress(audit, claim.documents, running, awaitingHuman || (onHold && result.awaiting === "human_review"),
                                 status === "REVIEWED", onHold);
  const current = progress.stages.filter((s) => s.state === "running");
  const lastStart = audit.map((e) => e.event).lastIndexOf("claim_started");
  const stopping = running && audit.slice(Math.max(0, lastStart)).some((e) => e.event === "stop_requested");
  const longStep = current.find(isLong);

  return (
    <div className="space-y-6">
      <AutoRefresh active={running} />
      <nav className="flex flex-wrap items-center gap-2 text-sm text-slate-500">
        <Link href="/bundles" className="hover:text-brand-700">Claims</Link><span>/</span>
        <span className="min-w-0 truncate text-slate-800">{claim.title}</span>
      </nav>

      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-4 p-6">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold tracking-tight text-slate-900">{claim.title}</h1>
            <p className="mt-1 text-sm text-slate-500">
              {patient ? <>Patient <Link href={`/patients/${patient.patient_id}`} className="font-medium text-brand-700 hover:underline">{patient.full_name}</Link> · </> : null}
              {claim.documents.length} document(s) · submitted by {claim.created_by} on {fmt(claim.created_at)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {claim.confidence != null && <span className="text-sm tabular-nums text-slate-500" title="weighted agent confidence">confidence {pct(claim.confidence)}</span>}
            <Badge value={shown} />
            <Badge value={status} />
          </div>
        </div>

        {running && (
          <div className="mx-6 mb-6 rounded-xl border border-sky-200 bg-sky-50/70 px-4 py-3 text-sky-900" role="status">
            <p className="flex items-center gap-2 text-sm font-semibold">
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" aria-hidden />
              {current.length === 0
                ? (status === "QUEUED" ? "Pending: waiting to start" : "Starting…")
                : current.map((s) => ["preprocess", "classify", "extract"].includes(s.key)
                  ? `${s.label} agent: ${s.note} · ${elapsed(s.startedAt)}`
                  : `${s.label} agent running · ${elapsed(s.startedAt)}`).join(" · ")}
            </p>
            {longStep ? (
              <p className="mt-0.5 text-xs font-medium text-indigo-900">
                In progress: the {longStep.label.toLowerCase()} step has run over {LONG_STEP_S}s. It keeps working in the background; you can leave this page. Only one claim runs at a time, so new uploads wait until it finishes.
              </p>
            ) : (
              <p className="mt-0.5 text-xs">Each page goes through preprocess → classify → extract, one page at a time. If every page is read, KYC and claims → policy run in parallel, then fraud, then the orchestrator decides. If a page fails or is unknown, the claim goes on hold for a person. This page updates every few seconds.</p>
            )}
            <div className="mt-2"><StopButton claimId={claim.bundle_id} stopping={stopping} /></div>
          </div>
        )}
        {result.error && (
          <div className={`mx-6 mb-6 rounded-xl border px-4 py-3 ${TONE.REJECT}`} role="alert">
            <p className="text-sm font-semibold">Processing failed</p><p className="mt-0.5 text-xs">{result.error}</p>
          </div>
        )}
        {out && !running && (
          <div className={`mx-6 mb-6 rounded-xl border px-4 py-3 ${TONE[out.decision]}`}>
            <p className="text-sm font-semibold">Orchestrator: {out.decision} <span className="font-normal opacity-80">· confidence {pct(out.confidence)}</span></p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">{out.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          </div>
        )}
        {ov && (
          <div className="mx-6 mb-6 rounded-xl border border-brand-200 bg-brand-50/70 px-4 py-3 text-brand-900">
            <p className="text-sm font-semibold">Reviewer decision: {ov.decision} <span className="font-normal opacity-80">(was {ov.original_decision ?? "—"})</span></p>
            <p className="mt-0.5 text-xs">“{ov.note}” · {ov.reviewer}, {fmt(ov.reviewed_at)}</p>
          </div>
        )}
      </div>

      {onHold && result.hold && (
        <HoldPanel claimId={claim.bundle_id} hold={result.hold} waitingAtHold={result.awaiting === "ingest_hold"} />
      )}
      {status === "STOPPED" && (
        <div className="rounded-xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-800" role="status">
          <p className="text-sm font-semibold">Stopped by a person</p>
          <p className="mt-0.5 text-xs">No further agents run. Re-run it or re-upload pages from Human review to start again.</p>
        </div>
      )}
      {awaitingHuman && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900" role="status">
          <p className="text-sm font-semibold">Paused for human review</p>
          <p className="mt-0.5 text-xs">The run is stopped at the human_review checkpoint (LangGraph interrupt). Recording a decision below resumes it from exactly this point.</p>
        </div>
      )}
      {(running || out || onHold || status === "STOPPED") && <Stepper progress={progress} />}
      {(running || out || onHold || status === "STOPPED") && <Detail result={result} progress={progress} />}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-6">
          <section aria-label="Documents" className="space-y-3">
            <h2 className="text-sm font-semibold text-slate-900">Documents</h2>
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {claim.documents.map((d) => (
                <li key={d.case_id} className="card flex gap-3 p-3">
                  <a href={`/api/bff/cases/${d.case_id}/document`} target="_blank" rel="noreferrer"
                    className="relative h-20 w-16 shrink-0 overflow-hidden rounded-md bg-slate-100 ring-1 ring-slate-200">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={`/api/bff/cases/${d.case_id}/document`} alt={`Preview of ${d.file_name}`} loading="lazy" className="h-full w-full object-cover" />
                  </a>
                  <span className="flex min-w-0 flex-col text-xs">
                    <span className="truncate font-semibold text-slate-900" title={d.file_name}>{d.file_name}</span>
                    <span className="capitalize text-slate-500">{d.doc_type?.replaceAll("_", " ").toLowerCase() ?? "not read yet"}</span>
                    <span className="mt-auto flex flex-wrap items-center gap-2">
                      <Badge value={d.status} />
                      {!running && (d.doc_type === "UNKNOWN" || d.status === "UNREADABLE") && (
                        <ReplacePageButton claimId={claim.bundle_id} docId={d.case_id} fileName={d.file_name} />
                      )}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </section>
          {(result.documents ?? []).some((d) => d.raw_text) && (
            <section className="card p-5" aria-labelledby="text-h">
              <div className="flex items-baseline justify-between">
                <h2 id="text-h" className="text-sm font-semibold text-slate-900">Page text (preprocessor)</h2>
                <span className="text-xs text-slate-400">what the vision model read from each image</span>
              </div>
              <div className="mt-3 space-y-2">
                {(result.documents ?? []).map((d) => {
                  const row = claim.documents.find((c) => c.case_id === d.doc_id);
                  return (
                    <details key={d.doc_id} className="group rounded-xl ring-1 ring-slate-200">
                      <summary className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-xs">
                        <span className="truncate font-medium text-slate-800">{d.file_name}</span>
                        <span className="shrink-0 text-slate-500">
                          {d.doc_type.replaceAll("_", " ").toLowerCase()} · {d.image_quality ?? "clear"} · {(d.raw_text ?? "").length} chars
                        </span>
                      </summary>
                      <div className="grid gap-3 border-t border-slate-100 p-3 md:grid-cols-2">
                        {row && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={`/api/bff/cases/${row.case_id}/document`} alt={`Scan of ${d.file_name}`} loading="lazy"
                            className="max-h-96 w-full rounded-lg object-contain ring-1 ring-slate-200" />
                        )}
                        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-700">
                          {d.raw_text || (d.error ?? "no text")}
                        </pre>
                      </div>
                    </details>
                  );
                })}
              </div>
            </section>
          )}
          <AuditLog events={audit} />
        </div>
        {!running && <ClaimReview claimId={claim.bundle_id} docs={result.documents ?? []} busyClaim={running} />}
      </div>
    </div>
  );
}
