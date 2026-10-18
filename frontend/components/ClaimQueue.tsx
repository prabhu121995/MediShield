import Link from "next/link";
import Badge from "@/components/Badge";
import { QueueItem } from "@/lib/api";
import { serverApi } from "@/lib/server-api";

export type QueueName = "review" | "approved" | "rejected";

function age(iso: string) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `${mins} min`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h`;
  return `${Math.round(mins / 1440)} d`;
}

const COPY: Record<QueueName, { title: string; intro: string; empty: string; ago: string }> = {
  review: {
    title: "Review queue",
    intro: "Claims the orchestrator escalated: fraud score ≥ 0.30, an agent below 0.60 confidence, an unreadable or unknown page, or a missing document. Oldest first.",
    empty: "Nothing waiting. Escalated claims will appear here.",
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
export default async function ClaimQueue({ name }: { name: QueueName }) {
  const queue = await serverApi<QueueItem[]>(`/api/queues/${name}`);
  const copy = COPY[name];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{copy.title}</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">{copy.intro}</p>
        </div>
        <span className="text-sm tabular-nums text-slate-500">{queue.length} {queue.length === 1 ? "claim" : "claims"}</span>
      </div>
      {queue.length === 0 ? (
        <div className="card p-10 text-center text-sm text-slate-500">{copy.empty}</div>
      ) : (
        <ul className="flex flex-col gap-3">
          {queue.map((c) => (
            <li key={c.bundle_id}>
              <Link href={`/bundles/${c.bundle_id}`} className="card flex flex-col gap-2 p-4 transition hover:shadow-md sm:flex-row sm:items-center sm:gap-4">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-slate-900">{c.title}</span>
                  <span className="mt-1 block text-xs text-slate-600">
                    {c.override ? `Reviewer (${c.override.reviewer}): ${c.override.note}` : (c.reasons.slice(0, 3).join(" · ") || c.justification || "—")}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-3 text-xs text-slate-500">
                  {c.fraud_score != null && c.fraud_score > 0 && (
                    <span className="rounded-full bg-amber-50 px-2 py-0.5 font-semibold text-amber-800 ring-1 ring-inset ring-amber-600/20">fraud {c.fraud_score.toFixed(2)}</span>
                  )}
                  {c.confidence != null && <span className="tabular-nums">conf {Math.round(c.confidence * 100)}%</span>}
                  <span>{c.documents.length} docs</span>
                  <span title={c.created_at}>{copy.ago} {age(c.created_at)} ago</span>
                  <Badge value={name === "review" ? "NEEDS_REVIEW" : c.status ?? c.decision} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
