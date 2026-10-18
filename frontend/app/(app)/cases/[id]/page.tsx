import Link from "next/link";
import { notFound } from "next/navigation";
import Badge from "@/components/Badge";
import ReviewForm from "./ReviewForm";
import DocumentViewer from "./DocumentViewer";
import AgentPanel, { type AgentItem } from "./AgentPanel";
import AutoRefresh from "@/components/AutoRefresh";
import RetryButton from "@/components/RetryButton";
import { CaseDetail } from "@/lib/api";
import { ApiError, currentUser, serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

type Obj = Record<string, unknown>;

const label = (v: unknown) => String(v ?? "—").replaceAll("_", " ").toLowerCase();

// Top edge of the case header, coloured by the decision. The decision badge says the same in text.
const DECISION_STRIP: Record<string, string> = {
  APPROVE: "bg-emerald-500",
  REJECT: "bg-rose-500",
  ESCALATE: "bg-amber-400",
};

// One line per agent, shown in the collapsed header.
function summarize(key: string, d: Obj | null): string {
  if (!d) return "Did not run for this document type";
  if (d.error) return String(d.error);
  switch (key) {
    case "preprocessor": {
      const n = Object.keys((d.fields ?? {}) as Obj).length;
      return `${n} field${n === 1 ? "" : "s"} read · ${d.is_blurry ? "blurry scan" : "sharp scan"}`;
    }
    case "classifier":
      return `${label(d.doc_type)} · confidence ${d.confidence ?? "—"}`;
    case "kyc":
      return `${d.kyc_passed ? "identity passed" : "identity failed"} · ${(d.flags as unknown[] | undefined)?.length ?? 0} flags`;
    case "claims":
      return `${label(d.kind)} · ${d.schema_valid ? "schema valid" : "schema invalid"}`;
    case "policy":
      return `${d.covered ? "covered" : "not covered"}${d.coverage_percentage !== undefined ? ` · ${d.coverage_percentage}%` : ""}`;
    case "fraud":
      return `score ${d.fraud_score ?? "—"} · ${label(d.risk_level)} risk`;
    case "orchestrator":
      return `decision ${label(d.decision)}`;
    default:
      return "";
  }
}

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let c: CaseDetail;
  try {
    c = await serverApi<CaseDetail>(`/api/cases/${id}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }
  const user = await currentUser();
  const r = c.result;
  const orch = (r.orchestrator ?? {}) as Obj;
  const confidence = c.confidence ?? 0;

  const agentSpecs: { key: string; title: string; data?: Obj }[] = [
    { key: "preprocessor", title: "Preprocessor", data: r.preprocessor as Obj | undefined },
    { key: "classifier", title: "Classifier", data: r.classifier as Obj | undefined },
    { key: "kyc", title: "KYC", data: r.kyc as Obj | undefined },
    { key: "claims", title: "Claims", data: r.claims as Obj | undefined },
    { key: "policy", title: "Policy coverage", data: r.policy as Obj | undefined },
    { key: "fraud", title: "Fraud signals", data: r.fraud as Obj | undefined },
    { key: "orchestrator", title: "Orchestrator", data: r.orchestrator as Obj | undefined },
  ];
  const agents: AgentItem[] = agentSpecs.map((a) => ({
    key: a.key,
    title: a.title,
    summary: summarize(a.key, a.data ?? null),
    status: a.data ? (a.data.error ? "error" : "ran") : "not run",
    data: a.data ?? null,
  }));

  return (
    <div className="space-y-6">
      <nav className="flex flex-wrap items-center gap-2 text-sm text-slate-500 animate-fade-in">
        <Link href="/" className="hover:text-brand-700">Cases</Link>
        <span>/</span>
        {c.patient_id && (
          <>
            <Link href={`/patients/${c.patient_id}`} className="hover:text-brand-700">Patient</Link>
            <span>/</span>
          </>
        )}
        <span className="min-w-0 truncate text-slate-800">{c.file_name}</span>
      </nav>

      {c.status === "PROCESSING" && (
        <div className="card flex animate-fade-up items-center gap-4 border-sky-200 bg-sky-50/60 p-5">
          <span className="h-6 w-6 shrink-0 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" />
          <div>
            <p className="text-sm font-semibold text-sky-900">Processing this document</p>
            <p className="text-xs text-sky-800/80">Reading, checking identity, extracting the claim and screening for fraud. This page updates by itself.</p>
          </div>
          <AutoRefresh active />
        </div>
      )}

      {c.status === "FAILED" && (
        <div className="card animate-fade-up space-y-3 border-rose-200 bg-rose-50/60 p-5">
          <div>
            <p className="text-sm font-semibold text-rose-900">Processing failed</p>
            <p className="mt-1 break-words text-xs text-rose-800/90">{String(c.result.error ?? "Unknown error")}</p>
          </div>
          <RetryButton caseId={c.case_id} />
        </div>
      )}

      <div className="card animate-fade-up relative overflow-hidden p-6">
        {c.decision && <span className={`absolute inset-x-0 top-0 h-1.5 ${DECISION_STRIP[c.decision]}`} aria-hidden />}
        {c.needs_review && c.status !== "PROCESSING" && (
          <div role="status" className="-mx-6 -mt-6 mb-5 flex items-center gap-3 border-b border-amber-200 bg-amber-50 px-6 py-3 pt-4">
            <span className="relative flex h-2.5 w-2.5" aria-hidden>
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
            </span>
            <p className="text-sm text-amber-900">
              <span className="font-semibold">Action needed.</span> This case is waiting for a reviewer decision. Use the review form below the document.
            </p>
          </div>
        )}
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold tracking-tight text-slate-900">{c.file_name}</h1>
            <p className="mt-1 text-sm capitalize text-slate-500">
              {c.doc_type?.replaceAll("_", " ").toLowerCase() ?? "unclassified"} · case {c.case_id.slice(0, 8)}
              {c.created_by && <> · uploaded by {c.created_by}</>}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Badge value={c.decision} />
            <Badge value={c.status} />
          </div>
        </div>

        <div className="mt-6 grid gap-6 md:grid-cols-[1fr_auto] md:items-center">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-slate-500">Justification</p>
            <p className="mt-1 text-sm leading-relaxed text-slate-800">{String(orch.justification ?? "—")}</p>
          </div>
          <div className="w-56">
            <div className="flex items-baseline justify-between">
              <p className="text-xs font-medium uppercase tracking-wider text-slate-500">Confidence</p>
              <p className="text-lg font-semibold tabular-nums text-slate-900">{confidence.toFixed(2)}</p>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-gradient-to-r from-brand-500 to-indigo-500 transition-all duration-1000 ease-out"
                style={{ width: `${Math.round(confidence * 100)}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] xl:items-start">
        <div className="space-y-6 xl:sticky xl:top-6">
          <DocumentViewer src={`/api/bff/cases/${c.case_id}/document`} href={`/api/bff/cases/${c.case_id}/document`} alt={c.file_name} />
          {user && <ReviewForm caseId={c.case_id} currentDocType={c.doc_type ?? ""} currentDecision={c.decision ?? ""} />}
        </div>

        <AgentPanel agents={agents} trace={r.trace ?? []} />
      </div>
    </div>
  );
}
