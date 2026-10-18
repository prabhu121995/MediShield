import Link from "next/link";
import { notFound } from "next/navigation";
import AutoRefresh from "@/components/AutoRefresh";
import Badge from "@/components/Badge";
import { AgentSummary, AuditEvent, ClaimBundle, ClaimResult, Patient } from "@/lib/api";
import { claimProgress, Progress, Stage, StageState } from "@/lib/progress";
import { ApiError, serverApi } from "@/lib/server-api";
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
  { key: "ingest", title: "Preprocess & classify", role: "Vision model: image → text, then document type, page by page" },
  { key: "kyc", title: "KYC agent", role: "Member, ID validity, identity across pages" },
  { key: "claims", title: "Claims agent", role: "Claim form schema and clinical facts" },
  { key: "policy", title: "Policy agent", role: "CPT coverage: retrieved clauses + LLM (RAG)" },
  { key: "fraud", title: "Fraud agent", role: "Six signal tags across documents and history" },
];

function pct(v: number | null | undefined) {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function elapsed(from?: string) {
  if (!from) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(from).getTime()) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

const STATE_STYLE: Record<StageState, { chip: string; text: string }> = {
  running: { chip: "bg-sky-100 text-sky-800 ring-sky-600/20", text: "Running" },
  done: { chip: "bg-emerald-50 text-emerald-700 ring-emerald-600/20", text: "Done" },
  error: { chip: "bg-rose-50 text-rose-700 ring-rose-600/20", text: "Error" },
  waiting: { chip: "bg-slate-100 text-slate-500 ring-slate-500/20", text: "Waiting" },
  skipped: { chip: "bg-slate-50 text-slate-400 ring-slate-400/20", text: "Not applicable" },
};

function StateChip({ stage }: { stage: Stage }) {
  const st = STATE_STYLE[stage.state];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${st.chip}`}>
      {stage.state === "running" && <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" aria-hidden />}
      {st.text}{stage.state === "running" && stage.startedAt ? ` · ${elapsed(stage.startedAt)}` : ""}
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
            s.state === "running" ? "bg-sky-50 text-sky-800 ring-1 ring-sky-300"
              : s.state === "done" ? "text-emerald-700" : s.state === "error" ? "text-rose-700" : "text-slate-400"}`}>
            {s.state === "running"
              ? <span className="h-3 w-3 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" aria-hidden />
              : <span aria-hidden>{s.state === "done" ? "✓" : s.state === "error" ? "✕" : s.state === "skipped" ? "–" : "○"}</span>}
            {s.label}
            {(s.key === "pages" || s.key === "human_review") && s.state !== "waiting" && <span className="font-normal opacity-75">{s.note}</span>}
            {s.state === "running" && s.startedAt && <span className="font-normal tabular-nums opacity-75">{elapsed(s.startedAt)}</span>}
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
        {stage.state === "running" && <p className="text-sky-800">Working on it. This card fills in when the agent finishes.</p>}
        {stage.state === "waiting" && <p className="text-slate-400">Waiting for the earlier steps.</p>}
        {stage.state === "skipped" && <p className="text-slate-400">Not applicable to these documents.</p>}
        {finished && summary && <p className={stage.state === "error" ? "text-rose-700" : "text-slate-700"}>{summary}</p>}
        {children}
      </div>
    </div>
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
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
      <AgentCard {...AGENTS[0]} stage={st.pages} summary={full ? s.ingest?.summary : undefined} confidence={full ? s.ingest?.confidence : null}>
        <ul className="space-y-1 text-[11px] text-slate-600">
          {progress.pages.map((p) => (
            <li key={p.doc_id} className="flex items-center justify-between gap-2">
              <span className="truncate" title={p.file_name}>{p.file_name}</span>
              <span className={`shrink-0 font-medium ${p.state === "running" ? "text-sky-700" : p.doc_type === "UNKNOWN" ? "text-amber-700"
                : p.state === "waiting" ? "text-slate-400" : "text-slate-800"}`}>
                {p.state === "running" ? ({ preprocess: "image → text…", classify: "classifying…", extract: "extracting…" } as Record<string, string>)[p.step ?? "preprocess"]
                  : p.state === "waiting" ? "queued" : (p.doc_type ?? "").replaceAll("_", " ").toLowerCase()}
                {p.blurry ? " · blurry" : ""}
              </span>
            </li>
          ))}
        </ul>
      </AgentCard>
      <AgentCard {...props(1, "kyc")}>
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
      <AgentCard {...props(2, "claims")}>
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
      <AgentCard {...props(3, "policy")}>
        {full && policy && !policy.error && (
          <>
            <p className="text-[11px] text-slate-600">{policy.plan ?? "plan unknown"} · CPT {policy.cpt_codes_checked.join(", ")}</p>
            {policy.exclusions.map((e) => <p key={e} className="text-[11px] text-rose-700">✕ {e}</p>)}
            {(policy.notes ?? []).map((n) => <p key={n} className="text-[11px] text-amber-700">! {n}</p>)}
            <p className="text-[11px] italic text-slate-500">“{policy.policy_clause}”</p>
          </>
        )}
      </AgentCard>
      <AgentCard {...props(4, "fraud")}>
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

function AuditLog({ events }: { events: AuditEvent[] }) {
  return (
    <section className="card p-5" aria-labelledby="audit-h">
      <div className="flex items-baseline justify-between">
        <h2 id="audit-h" className="text-sm font-semibold text-slate-900">Audit log</h2>
        <span className="text-xs text-slate-400">{events.length} events · UTC timestamps</span>
      </div>
      <ol className="mt-4 space-y-0">
        {events.map((e) => {
          const d = e.detail ?? {};
          const text = (d.justification ?? d.error ?? d.note ?? (d.decision ? `${d.from ? `${d.from} → ` : ""}${d.decision}` : null)
            ?? (d.doc_type ? `${d.file_name}: ${d.doc_type}` : null) ?? (d.to ? `${d.file_name}: ${d.from} → ${d.to}` : null)
            ?? (Array.isArray(d.tags) ? `score ${d.fraud_score} ${(d.tags as string[]).join(", ")}` : null)
            ?? (d.kyc_passed !== undefined ? `kyc_passed=${d.kyc_passed} ${((d.flags as string[]) ?? []).join(", ")}` : null)
            ?? (d.covered !== undefined ? `covered=${d.covered}` : null)
            ?? (d.schema_valid !== undefined ? `schema_valid=${d.schema_valid}` : null)
            ?? (d.agents ? `agents: ${(d.agents as string[]).join(", ")}` : null)
            ?? (d.documents !== undefined ? `${d.documents} document(s)` : "")) as string;
          const human = !["ingest", "plan", "kyc", "claims", "policy", "fraud", "decide", "orchestrator", "system"].includes(e.actor);
          return (
            <li key={e.id} className="grid grid-cols-[88px_120px_1fr] gap-3 border-l-2 border-slate-100 py-1.5 pl-3 text-xs sm:grid-cols-[96px_150px_1fr]">
              <time className="tabular-nums text-slate-400" dateTime={e.created_at}>{fmtTime(e.created_at)}</time>
              <span className={`truncate font-medium ${e.status === "error" ? "text-rose-700" : human ? "text-brand-700" : "text-slate-800"}`}>
                {e.actor}{e.duration_ms != null ? <span className="font-normal text-slate-400"> · {e.duration_ms} ms</span> : null}
              </span>
              <span className="min-w-0 break-words text-slate-600"><span className="text-slate-400">{e.event.replaceAll("_", " ")}</span> {text}</span>
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
  const progress = claimProgress(audit, claim.documents, running, awaitingHuman, status === "REVIEWED");
  const current = progress.stages.filter((s) => s.state === "running");

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
                ? (status === "QUEUED" ? "Waiting in the queue behind earlier claims" : "Starting…")
                : current.map((s) => s.key === "pages"
                  ? `${progress.pages.find((p) => p.state === "running")?.file_name ?? "page"}: ${s.note}`
                  : `${s.label} agent running`).join(" · ")}
            </p>
            <p className="mt-0.5 text-xs">Pages are read one at a time, then KYC and claims → policy run in parallel, then fraud, then the orchestrator decides. This page updates every few seconds.</p>
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

      {awaitingHuman && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900" role="status">
          <p className="text-sm font-semibold">Paused for human review</p>
          <p className="mt-0.5 text-xs">The run is stopped at the human_review checkpoint (LangGraph interrupt). Recording a decision below resumes it from exactly this point.</p>
        </div>
      )}
      {(running || out) && <Stepper progress={progress} />}
      {(running || out) && <Detail result={result} progress={progress} />}

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
                    <span className="mt-auto"><Badge value={d.status} /></span>
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
