import Link from "next/link";
import AutoRefresh from "@/components/AutoRefresh";
import NewCaseForm from "./NewCaseForm";
import CaseHistory from "./CaseHistory";
import { ClaimBundle, ClaimRequirements, Patient } from "@/lib/api";
import { apiQuery, pageOf, pageRows } from "@/lib/paging";
import { serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

type Active = { active: { bundle_id: string; title: string; status: string } | null };

// Upload form, then every claim, newest first: searchable and one page at a time, so the page
// never loads the whole history. Only one claim runs at a time: while one is queued or
// processing, the form is locked and links to it.
export default async function CaseFilesPage({ searchParams }: { searchParams: Promise<{ q?: string; page?: string }> }) {
  const sp = await searchParams;
  const page = pageOf(sp);
  const [fetched, patients, { active }, requirements] = await Promise.all([
    serverApi<ClaimBundle[]>(`/api/claim-bundles?${apiQuery(page, { q: sp.q })}`),
    serverApi<Patient[]>("/api/patients"),
    serverApi<Active>("/api/claim-bundles/active").catch(() => ({ active: null })),
    serverApi<ClaimRequirements>("/api/claim-bundles/requirements").catch(() => null),
  ]);
  const names = new Map(patients.map((p) => [p.patient_id, p.full_name]));
  const { rows: bundles, hasNext } = pageRows(fetched);

  return (
    <div className="flex flex-col gap-4">
      <AutoRefresh active={!!active} />
      {active && (
        <div className="rounded-xl border border-sky-200 bg-sky-50/70 px-4 py-3 text-sm text-sky-900" role="status">
          <span className="font-semibold">One claim at a time:</span>{" "}
          <Link href={`/bundles/${active.bundle_id}`} className="font-medium underline">{active.title}</Link>{" "}
          is {active.status === "QUEUED" ? "pending" : "in progress"}. Uploads open again when it finishes, goes on hold, or is stopped.
        </div>
      )}
      <NewCaseForm patients={patients} locked={!!active} requirements={requirements} />
      <CaseHistory bundles={bundles} names={names} params={sp} page={page} hasNext={hasNext} />
    </div>
  );
}
