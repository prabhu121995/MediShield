import Link from "next/link";
import ClaimRow from "@/components/ClaimRow";
import Pager from "@/components/Pager";
import SearchBox from "@/components/SearchBox";
import { Patient, QueueItem } from "@/lib/api";
import { apiQuery, pageOf, pageRows, Params } from "@/lib/paging";
import { serverApi } from "@/lib/server-api";

export type QueueName = "review" | "escalated" | "approved" | "rejected";

function age(iso: string) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `${mins} min`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h`;
  return `${Math.round(mins / 1440)} d`;
}

const COPY: Record<QueueName, { title: string; intro: string; empty: string; ago: string }> = {
  review: {
    title: "Review queue",
    intro: "Every claim waiting for a person: escalated by the agents, or on hold because a page is unknown, blurry or unreadable or an agent did not complete. Oldest first.",
    empty: "Nothing waiting. Escalated claims will appear here.",
    ago: "waiting",
  },
  escalated: {
    title: "Escalated",
    intro: "Claims the agents decided to ESCALATE and that wait at the human-review checkpoint: fraud score ≥ 0.30, an agent below 0.60 confidence, or conflicting facts. Recording a decision resumes the run. Oldest first.",
    empty: "No escalated claims waiting.",
    ago: "waiting",
  },
  approved: {
    title: "Approved",
    intro: "Claims whose final decision is APPROVE: by the agents, or by a reviewer's override. Newest first.",
    empty: "No approved claims yet.",
    ago: "submitted",
  },
  rejected: {
    title: "Rejected",
    intro: "Claims whose final decision is REJECT: KYC failed, a procedure is not covered, or a required field is missing (or a reviewer rejected it). Newest first.",
    empty: "No rejected claims yet.",
    ago: "submitted",
  },
};

// One list for the three queues. Each row opens the claim page (decision, agents, review, audit).
export default async function ClaimQueue({ name, params = {} }: { name: QueueName; params?: Params }) {
  const page = pageOf(params);
  const [fetched, patients] = await Promise.all([
    serverApi<QueueItem[]>(`/api/queues/${name}?${apiQuery(page, { q: params.q })}`),
    serverApi<Patient[]>("/api/patients").catch(() => [] as Patient[]),
  ]);
  const { rows: queue, hasNext } = pageRows(fetched);
  const names = new Map(patients.map((p) => [p.patient_id, p.full_name]));
  const copy = COPY[name];
  const path = `/${name}`;
  const waiting = name === "review" || name === "escalated";
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{copy.title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">{copy.intro}</p>
      </div>
      {/* Same card and rows as "All claims" on the Claims page */}
      <section aria-labelledby="queue-h" className="card animate-fade-up p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="queue-h" className="text-sm font-semibold text-slate-900">{copy.title}</h2>
            <span className="text-xs text-slate-400">
              {waiting ? "Oldest first" : "Newest first"} · queues:{" "}
              {QUEUES.filter(([q]) => q !== name).map(([q, label], i) => (
                <span key={q}>{i > 0 && " · "}<Link href={`/${q}`} className="text-brand-700 hover:underline">{label}</Link></span>
              ))}
              {" · "}<Link href="/bundles#claims" className="text-brand-700 hover:underline">All claims</Link>
            </span>
          </div>
          <SearchBox path={path} params={params} placeholder="Search claim, ID or patient" />
        </div>

        {queue.length === 0 ? (
          <p className="mt-6 text-center text-xs text-slate-500">{params.q ? `No claims match "${params.q}".` : copy.empty}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-2">
            {queue.map((c, i) => (
              <li key={c.bundle_id} className="animate-fade-up" style={{ animationDelay: `${Math.min(i, 12) * 30}ms` }}>
                <ClaimRow claim={c} patient={c.patient_id ? names.get(c.patient_id) : undefined}
                  status={waiting ? c.status ?? "NEEDS_REVIEW" : c.status ?? c.decision}
                  subtitle={<>
                    {c.fraud_score != null && c.fraud_score > 0 && (
                      <span className="mr-1.5 rounded-full bg-amber-50 px-1.5 font-semibold text-amber-800 ring-1 ring-inset ring-amber-600/20">fraud {c.fraud_score.toFixed(2)}</span>
                    )}
                    <span title={c.created_at}>{copy.ago} {age(c.created_at)} ago</span>
                    {c.confidence != null && <> · conf {Math.round(c.confidence * 100)}%</>}
                    {" · "}{c.override ? `Reviewer (${c.override.reviewer}): ${c.override.note}` : (c.reasons.slice(0, 3).join(" · ") || c.justification || "—")}
                  </>} />
              </li>
            ))}
          </ul>
        )}
        <Pager path={path} params={params} page={page} hasNext={hasNext} shown={queue.length} />
      </section>
    </div>
  );
}

const QUEUES: [QueueName, string][] = [["review", "Review"], ["escalated", "Escalated"], ["approved", "Approved"], ["rejected", "Rejected"]];
