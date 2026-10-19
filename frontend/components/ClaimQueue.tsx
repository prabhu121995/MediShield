import Link from "next/link";
import ClaimRow from "@/components/ClaimRow";
import Pager from "@/components/Pager";
import SearchBox from "@/components/SearchBox";
import { Patient, QueueItem } from "@/lib/api";
import { apiQuery, pageOf, pageRows, Params } from "@/lib/paging";
import { serverApi } from "@/lib/server-api";

export type QueueName = "review" | "escalated" | "approved" | "rejected";

const COPY: Record<QueueName, { title: string; empty: string }> = {
  review: { title: "Review queue", empty: "Nothing waiting. Escalated claims will appear here." },
  escalated: { title: "Escalated", empty: "No escalated claims waiting." },
  approved: { title: "Approved", empty: "No approved claims yet." },
  rejected: { title: "Rejected", empty: "No rejected claims yet." },
};

// One list for the four queues, the same card and rows as "All claims" on the Claims page.
// Each row opens the claim page (decision, agents, review, audit).
export default async function ClaimQueue({ name, params = {} }: { name: QueueName; params?: Params }) {
  const page = pageOf(params);
  const [fetched, patients] = await Promise.all([
    serverApi<QueueItem[]>(`/api/queues/${name}?${apiQuery(page, { q: params.q })}`),
    serverApi<Patient[]>("/api/patients").catch(() => [] as Patient[]),
  ]);
  const { rows: queue, hasNext } = pageRows(fetched);
  const names = new Map(patients.map((p) => [p.patient_id, p]));
  const copy = COPY[name];
  const path = `/${name}`;
  return (
    <section aria-labelledby="queue-h" className="card animate-fade-up p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="queue-h" className="text-sm font-semibold text-slate-900">{copy.title}</h2>
          <span className="text-xs text-slate-400">
            Newest first · queues:{" "}
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
              <ClaimRow claim={c} patient={c.patient_id ? names.get(c.patient_id) : undefined} />
            </li>
          ))}
        </ul>
      )}
      <Pager path={path} params={params} page={page} hasNext={hasNext} shown={queue.length} />
    </section>
  );
}

const QUEUES: [QueueName, string][] = [["review", "Review"], ["escalated", "Escalated"], ["approved", "Approved"], ["rejected", "Rejected"]];
