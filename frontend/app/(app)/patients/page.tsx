import Link from "next/link";
import PatientForm from "./PatientForm";
import Pager from "@/components/Pager";
import SearchBox from "@/components/SearchBox";
import { Patient } from "@/lib/api";
import { apiQuery, pageOf, pageRows } from "@/lib/paging";
import { serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

function fmt(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export default async function PatientsPage({ searchParams }: { searchParams: Promise<{ q?: string; page?: string }> }) {
  const sp = await searchParams;
  const term = (sp.q ?? "").trim();
  const page = pageOf(sp);
  const { rows: patients, hasNext } = pageRows(await serverApi<Patient[]>(`/api/patients?${apiQuery(page, { q: term })}`));

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 animate-fade-up sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Patients</h1>
          <p className="mt-1 text-sm text-slate-500">Register a patient, then attach their claim documents to them.</p>
        </div>
        <SearchBox path="/patients" params={sp} placeholder="Search name or policy" />
      </div>

      <details className="card animate-fade-up group overflow-hidden [animation-delay:60ms]">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4 sm:px-6">
          <span>
            <span className="block text-sm font-semibold text-slate-900">New patient</span>
            <span className="block text-xs text-slate-500">Name is required. Everything else can be added later.</span>
          </span>
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-50 text-lg font-semibold leading-none text-brand-700 transition-transform duration-300 group-open:rotate-45">+</span>
        </summary>
        <div className="border-t border-slate-200 px-5 py-5 sm:px-6">
          <PatientForm />
        </div>
      </details>

      <div className="card animate-fade-up overflow-hidden [animation-delay:120ms]">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5 text-xs text-slate-500 sm:px-6">
          <span>{term ? `Matching “${term}”` : "All patients, A to Z"}</span>
          <span className="tabular-nums">page {page} · {patients.length} shown</span>
        </div>

        {patients.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-16 text-center animate-fade-in">
            <p className="text-sm font-medium text-slate-700">{term ? "No patients match this search" : "No patients yet"}</p>
            <p className="text-xs text-slate-500">{term ? "Try a different name or policy number." : "Use New patient above to register the first one."}</p>
          </div>
        ) : (
          <>
            {/* Phones: one card per patient */}
            <ul className="divide-y divide-slate-100 md:hidden">
              {patients.map((p, i) => (
                <li key={p.patient_id} className="animate-fade-up px-4 py-4" style={{ animationDelay: `${Math.min(i, 12) * 35}ms` }}>
                  <Link href={`/patients/${p.patient_id}`} className="block active:bg-slate-50">
                    <p className="truncate text-sm font-semibold text-slate-900">{p.full_name}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {p.policy_number ?? "No policy"} · added {fmt(p.created_at)} by {p.created_by}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>

            {/* Desktop: full table with audit columns */}
            <div className="hidden overflow-x-auto md:block">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50/70">
                  <tr>
                    <th className="th">Patient</th>
                    <th className="th">Date of birth</th>
                    <th className="th">Policy</th>
                    <th className="th">Created</th>
                    <th className="th">Last updated</th>
                    <th className="th"><span className="sr-only">Open</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {patients.map((p, i) => (
                    <tr key={p.patient_id} style={{ animationDelay: `${Math.min(i, 12) * 35}ms` }}
                      className="animate-fade-up transition-colors duration-150 hover:bg-brand-50/50">
                      <td className="td font-medium text-slate-900">
                        <Link href={`/patients/${p.patient_id}`} className="hover:text-brand-700 hover:underline">{p.full_name}</Link>
                      </td>
                      <td className="td whitespace-nowrap text-slate-600">{p.date_of_birth ?? "—"}</td>
                      <td className="td text-slate-600">{p.policy_number ?? "—"}</td>
                      <td className="td whitespace-nowrap text-slate-500">
                        {fmt(p.created_at)}<span className="block text-xs text-slate-400">by {p.created_by}</span>
                      </td>
                      <td className="td whitespace-nowrap text-slate-500">
                        {fmt(p.updated_at)}<span className="block text-xs text-slate-400">by {p.updated_by ?? "—"}</span>
                      </td>
                      <td className="td text-right">
                        <Link href={`/patients/${p.patient_id}`} className="btn-ghost text-brand-700">Open <span aria-hidden>→</span></Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <Pager path="/patients" params={sp} page={page} hasNext={hasNext} shown={patients.length}
          className="border-t border-slate-100 px-5 pb-3 sm:px-6" />
      </div>
    </div>
  );
}
