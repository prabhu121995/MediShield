import Link from "next/link";
import ClaimRow from "@/components/ClaimRow";
import Pager from "@/components/Pager";
import SearchBox from "@/components/SearchBox";
import { ClaimBundle, Patient } from "@/lib/api";
import { Params } from "@/lib/paging";

// Row 3: one row per case file, newest first, searchable and paged. Each row shows the five
// document slots.
export default function CaseHistory({ bundles, names, params, page, hasNext }: {
  bundles: ClaimBundle[]; names: Map<string, Patient>; params: Params; page: number; hasNext: boolean;
}) {
  return (
    <section id="claims" aria-labelledby="panel-history" className="card animate-fade-up p-5 [animation-delay:120ms]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="panel-history" className="text-sm font-semibold text-slate-900">All claims</h2>
          <span className="text-xs text-slate-400">Newest first · queues: <Link href="/approved" className="text-brand-700 hover:underline">Approved</Link> · <Link href="/rejected" className="text-brand-700 hover:underline">Rejected</Link> · <Link href="/review" className="text-brand-700 hover:underline">Review</Link></span>
        </div>
        <SearchBox path="/bundles" params={params} placeholder="Search claim, ID or patient" hash="claims" />
      </div>

      {bundles.length === 0 ? (
        <p className="mt-6 text-center text-xs text-slate-500">{params.q ? `No claims match "${params.q}".` : "No cases yet. Your first case will appear here."}</p>
      ) : (
        <ul className="mt-4 flex flex-col gap-2">
          {bundles.map((b, i) => (
            <li key={b.bundle_id} className="animate-fade-up" style={{ animationDelay: `${Math.min(i, 12) * 30}ms` }}>
              <ClaimRow claim={b} patient={b.patient_id ? names.get(b.patient_id) : undefined} />
            </li>
          ))}
        </ul>
      )}
      <Pager path="/bundles" params={params} page={page} hasNext={hasNext} shown={bundles.length} hash="claims" />
    </section>
  );
}
