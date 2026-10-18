import NewCaseForm from "./NewCaseForm";
import CaseHistory from "./CaseHistory";
import { ClaimBundle, Patient } from "@/lib/api";
import { serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

const RECENT = Number(process.env.RECENT_UPLOADS);          // frontend/.env.local

// Upload form, then the last few uploads. Decided claims live in the Approved, Rejected and
// Review queues, so this page never loads the whole history.
export default async function CaseFilesPage() {
  const [bundles, patients] = await Promise.all([
    serverApi<ClaimBundle[]>(`/api/claim-bundles?limit=${RECENT}`),
    serverApi<Patient[]>("/api/patients"),
  ]);
  const names = new Map(patients.map((p) => [p.patient_id, p.full_name]));

  return (
    <div className="flex flex-col gap-4">
      <NewCaseForm patients={patients} />
      <CaseHistory bundles={bundles} names={names} />
    </div>
  );
}
