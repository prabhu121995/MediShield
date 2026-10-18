import { redirect } from "next/navigation";
import PolicyUploader from "./PolicyUploader";
import PolicyChat from "./PolicyChat";
import { PolicyDoc } from "@/lib/api";
import { currentUser, serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function PoliciesPage() {
  const user = await currentUser();
  if (!user) redirect("/login");

  if (user.role !== "admin") {
    return (
      <div className="card mx-auto mt-16 max-w-md animate-scale-in p-10 text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-amber-50 text-amber-700">!</div>
        <h1 className="mt-4 text-lg font-semibold text-slate-900">Administrators only</h1>
        <p className="mt-2 text-sm text-slate-500">Policy documents are managed by administrators. Ask one to add or update a policy.</p>
      </div>
    );
  }

  const docs = await serverApi<PolicyDoc[]>("/api/policies");

  return (
    <div className="space-y-6">
      <div className="animate-fade-up">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Policies</h1>
        <p className="mt-1 text-sm text-slate-500">
          Coverage decisions are grounded in these documents. Ask the assistant to check a procedure code against them.
        </p>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] xl:items-start">
        <div className="space-y-6">
          <PolicyUploader />

          <section className="card animate-fade-up overflow-hidden [animation-delay:120ms]">
            <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
              <h2 className="text-sm font-semibold text-slate-900">Indexed policies</h2>
              <span className="text-xs tabular-nums text-slate-500">{docs.length} {docs.length === 1 ? "document" : "documents"}</span>
            </div>
            {docs.length === 0 ? (
              <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                <p className="text-sm font-medium text-slate-700">Nothing indexed yet</p>
                <p className="text-xs text-slate-500">Coverage checks need at least one policy. Upload a PDF above.</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-100">
                  <thead className="bg-slate-50/70">
                    <tr>
                      <th className="th">File</th>
                      <th className="th text-right">Sections indexed</th>
                      <th className="th">Added</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {docs.map((d, i) => (
                      <tr key={d.id} className="animate-fade-up transition-colors hover:bg-brand-50/40" style={{ animationDelay: `${i * 40}ms` }}>
                        <td className="td font-medium text-slate-900">{d.filename}</td>
                        <td className="td text-right tabular-nums">{d.chunks}</td>
                        <td className="td whitespace-nowrap text-slate-500">{new Date(d.ingested_at).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>

        <div className="xl:sticky xl:top-6">
          <PolicyChat />
        </div>
      </div>
    </div>
  );
}
