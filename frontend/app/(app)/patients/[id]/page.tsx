import Link from "next/link";
import { notFound } from "next/navigation";
import AutoRefresh from "@/components/AutoRefresh";
import Badge from "@/components/Badge";
import PatientForm from "../PatientForm";
import DeletePatient from "./DeletePatient";
import { CaseSummary, ClaimBundle, Patient } from "@/lib/api";
import { ApiError, currentUser, serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

function fmt(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-0.5 break-words text-sm font-medium text-slate-900">{value || "—"}</p>
    </div>
  );
}

export default async function PatientPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let patient: Patient;
  let cases: CaseSummary[];
  let claims: ClaimBundle[] | null;
  const user = await currentUser();
  try {
    [patient, cases, claims] = await Promise.all([
      serverApi<Patient>(`/api/patients/${id}`),
      serverApi<CaseSummary[]>(`/api/patients/${id}/cases`),
      // A failure here must not hide the patient: show the record and say the cases did not load.
      serverApi<ClaimBundle[]>(`/api/patients/${id}/claim-bundles`).catch(() => null),
    ]);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }
  // Pages uploaded outside a claim have no case to open, so they are listed on their own.
  const loose = cases.filter((c) => !c.bundle_id);
  const statusOf = (b: ClaimBundle) => b.status ?? b.summary.status;
  const busy = cases.some((c) => c.status === "PROCESSING")
    || !!claims?.some((b) => statusOf(b) === "PROCESSING" || statusOf(b) === "QUEUED");
  // Only a patient with nothing linked can be deleted (the API refuses otherwise).
  const unlinked = claims != null && claims.length === 0 && cases.length === 0;

  return (
    <div className="space-y-6">
      <AutoRefresh active={busy} />
      <nav className="flex items-center gap-2 text-sm text-slate-500 animate-fade-in">
        <Link href="/patients" className="hover:text-brand-700">Patients</Link>
        <span>/</span>
        <span className="truncate text-slate-800">{patient.full_name}</span>
      </nav>

      <div className="card animate-fade-up p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold tracking-tight text-slate-900">{patient.full_name}</h1>
            <p className="mt-1 text-sm text-slate-500">Patient ID {patient.patient_id.slice(0, 8)}</p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {patient.member_id && (
              <span className="rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700 ring-1 ring-inset ring-sky-600/20">
                Member {patient.member_id}
              </span>
            )}
            {user?.role === "admin" && (unlinked
              ? <DeletePatient patientId={patient.patient_id} />
              : <span className="text-xs text-slate-400">Delete this patient&apos;s cases to delete the record</span>)}
          </div>
        </div>

        <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-5 md:grid-cols-4">
          <Field label="Date of birth" value={patient.date_of_birth} />
          <Field label="Gender" value={patient.gender} />
          <Field label="Policy number" value={patient.policy_number} />
          <Field label="Phone" value={patient.phone} />
          <Field label="Email" value={patient.email} />
          <div className="col-span-2">
            <Field label="Address" value={patient.address} />
          </div>
        </dl>

        {/* Audit trail: who created and last changed this record, and when. */}
        <div className="mt-6 grid gap-x-6 gap-y-3 rounded-xl bg-slate-50 p-4 text-xs text-slate-600 ring-1 ring-slate-200/80 sm:grid-cols-2">
          <p><span className="font-semibold text-slate-800">Created</span> by {patient.created_by} on {fmt(patient.created_at)}</p>
          <p><span className="font-semibold text-slate-800">Last updated</span> by {patient.updated_by ?? "—"} on {fmt(patient.updated_at)}</p>
        </div>

        <details className="group mt-6 border-t border-slate-200 pt-5">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-brand-700">
            <span className="transition-transform duration-300 group-open:rotate-90" aria-hidden>›</span>
            Edit patient details
          </summary>
          <div className="mt-5">
            <PatientForm initial={patient} />
          </div>
        </details>
      </div>

      <div className="card animate-fade-up overflow-hidden [animation-delay:120ms]">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 sm:px-6">
          <h2 className="text-sm font-semibold text-slate-900">Cases</h2>
          <span className="text-xs text-slate-500">{busy ? "Processing…" : claims ? `${claims.length} total` : ""}</span>
        </div>
        {claims == null ? (
          <p className="px-6 py-12 text-center text-sm text-rose-600">Could not load this patient&apos;s cases. Restart the API server and refresh.</p>
        ) : claims.length === 0 ? (
          <p className="px-6 py-12 text-center text-sm text-slate-500">No cases yet for this patient.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50/70">
                <tr>
                  <th className="th">Case</th>
                  <th className="th">Documents</th>
                  <th className="th">Decision</th>
                  <th className="th">Status</th>
                  <th className="th">Received</th>
                  <th className="th"><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {claims.map((b) => (
                  <tr key={b.bundle_id} className="transition-colors duration-150 hover:bg-brand-50/50">
                    <td className="td max-w-xs">
                      <Link href={`/bundles/${b.bundle_id}`} className="block truncate font-medium text-slate-900 hover:text-brand-700 hover:underline">{b.title}</Link>
                      <span className="block truncate font-mono text-[11px] text-slate-400">{b.bundle_id.slice(0, 8)}</span>
                    </td>
                    <td className="td max-w-xs text-slate-600">
                      <span className="tabular-nums">{b.summary.documents}</span>
                      {b.summary.failed > 0 && <span className="ml-1.5 text-xs text-rose-600">{b.summary.failed} failed</span>}
                      <span className="block truncate text-xs capitalize text-slate-400">
                        {b.documents.map((d) => d.doc_type?.replaceAll("_", " ").toLowerCase() ?? "unclassified").join(", ")}
                      </span>
                    </td>
                    <td className="td"><Badge value={b.decision ?? b.summary.decision} /></td>
                    <td className="td"><Badge value={statusOf(b)} /></td>
                    <td className="td whitespace-nowrap text-slate-500">
                      {fmt(b.created_at)}{b.created_by && <span className="block text-xs text-slate-400">by {b.created_by}</span>}
                    </td>
                    <td className="td text-right">
                      <Link href={`/bundles/${b.bundle_id}`} className="btn-ghost text-brand-700">Open <span aria-hidden>→</span></Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {loose.length > 0 && (
        <div className="card animate-fade-up overflow-hidden [animation-delay:160ms]">
          <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 sm:px-6">
            <h2 className="text-sm font-semibold text-slate-900">Documents not in a case</h2>
            <span className="text-xs text-slate-500">{loose.length} total</span>
          </div>
          <ul className="divide-y divide-slate-100">
            {loose.map((c) => (
              <li key={c.case_id} className="flex items-center justify-between gap-4 px-5 py-3 text-sm sm:px-6">
                <span className="min-w-0 truncate font-medium text-slate-900">{c.file_name}</span>
                <span className="capitalize text-slate-500">{c.doc_type?.replaceAll("_", " ").toLowerCase() ?? "—"}</span>
                <Badge value={c.decision ?? c.status} />
                <Link href={`/cases/${c.case_id}`} className="btn-ghost text-brand-700">Open <span aria-hidden>→</span></Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
