import Link from "next/link";
import { notFound } from "next/navigation";
import AutoRefresh from "@/components/AutoRefresh";
import Badge from "@/components/Badge";
import PatientForm from "../PatientForm";
import { CaseSummary, Patient } from "@/lib/api";
import { ApiError, serverApi } from "@/lib/server-api";

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
  try {
    [patient, cases] = await Promise.all([
      serverApi<Patient>(`/api/patients/${id}`),
      serverApi<CaseSummary[]>(`/api/patients/${id}/cases`),
    ]);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }
  const busy = cases.some((c) => c.status === "PROCESSING");

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
          {patient.member_id && (
            <span className="rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700 ring-1 ring-inset ring-sky-600/20">
              Member {patient.member_id}
            </span>
          )}
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
          <h2 className="text-sm font-semibold text-slate-900">Documents</h2>
          <span className="text-xs text-slate-500">{busy ? "Processing…" : `${cases.length} total`}</span>
        </div>
        {cases.length === 0 ? (
          <p className="px-6 py-12 text-center text-sm text-slate-500">No documents yet for this patient.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50/70">
                <tr>
                  <th className="th">File</th>
                  <th className="th">Document</th>
                  <th className="th">Decision</th>
                  <th className="th">Status</th>
                  <th className="th">Uploaded</th>
                  <th className="th"><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {cases.map((c) => (
                  <tr key={c.case_id} className="transition-colors duration-150 hover:bg-brand-50/50">
                    <td className="td max-w-xs truncate font-medium text-slate-900">
                      <Link href={`/cases/${c.case_id}`} className="hover:text-brand-700 hover:underline">{c.file_name}</Link>
                    </td>
                    <td className="td capitalize text-slate-600">{c.doc_type?.replaceAll("_", " ").toLowerCase() ?? "—"}</td>
                    <td className="td"><Badge value={c.decision} /></td>
                    <td className="td"><Badge value={c.status} /></td>
                    <td className="td whitespace-nowrap text-slate-500">
                      {fmt(c.created_at)}{c.created_by && <span className="block text-xs text-slate-400">by {c.created_by}</span>}
                    </td>
                    <td className="td text-right">
                      <Link href={`/cases/${c.case_id}`} className="btn-ghost text-brand-700">Open <span aria-hidden>→</span></Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
