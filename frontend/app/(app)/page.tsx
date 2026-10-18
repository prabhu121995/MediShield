import Link from "next/link";
import AutoRefresh from "@/components/AutoRefresh";
import Badge from "@/components/Badge";
import KpiCard from "@/components/KpiCard";
import Pager from "@/components/Pager";
import { CaseSummary, DashboardStats } from "@/lib/api";
import { pageOf, slicePage } from "@/lib/paging";
import { serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

// Each tab filters on the decision, or on the processing status for in-flight and failed cases.
const TABS = [
  { key: "ESCALATE", label: "Escalated", hint: "Needs a human decision", match: (c: CaseSummary) => c.decision === "ESCALATE" },
  { key: "REJECT", label: "Rejected", hint: "Failed KYC, schema, or coverage", match: (c: CaseSummary) => c.decision === "REJECT" },
  { key: "APPROVE", label: "Approved", hint: "All applicable checks passed", match: (c: CaseSummary) => c.decision === "APPROVE" },
  { key: "PROCESSING", label: "Processing", hint: "Still being read and checked", match: (c: CaseSummary) => c.status === "PROCESSING" },
  { key: "FAILED", label: "Failed", hint: "Could not be processed; retry from the case", match: (c: CaseSummary) => c.status === "FAILED" },
  { key: "ALL", label: "All", hint: "Every case, newest first", match: () => true },
] as const;
type TabKey = (typeof TABS)[number]["key"];

// A coloured bar on the left of rows that need a person: amber for escalated, red for failed.
// The badges in the row carry the same meaning in text, so colour is never the only cue.
function rowAccent(c: CaseSummary): string | null {
  if (c.status === "FAILED") return "bg-rose-500";
  if (c.decision === "ESCALATE" || c.needs_review) return "bg-amber-400";
  return null;
}

// A page uploaded as part of a claim opens that claim (agent run, documents, audit log);
// a standalone upload has no claim, so it opens its own document page.
function openHref(c: CaseSummary): string {
  return c.bundle_id ? `/bundles/${c.bundle_id}` : `/cases/${c.case_id}`;
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function Confidence({ value }: { value: number | null }) {
  if (value == null) return <span className="text-slate-400">—</span>;
  return (
    <div className="inline-flex items-center gap-2">
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-200">
        <span className="block h-full rounded-full bg-brand-500" style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      <span className="tabular-nums">{value.toFixed(2)}</span>
    </div>
  );
}

export default async function CasesPage({ searchParams }: { searchParams: Promise<{ tab?: string; q?: string; page?: string }> }) {
  const sp = await searchParams;
  const { tab, q } = sp;
  const page = pageOf(sp);
  const term = (q ?? "").trim().toLowerCase();
  const active: TabKey = TABS.some((t) => t.key === tab) ? (tab as TabKey) : "ESCALATE";
  const [all, stats] = await Promise.all([
    serverApi<CaseSummary[]>("/api/cases"),
    serverApi<DashboardStats>("/api/dashboard/stats"),
  ]);
  const tabDef = TABS.find((t) => t.key === active)!;
  const matching = all
    .filter(tabDef.match)
    .filter((c) => !term || c.file_name.toLowerCase().includes(term) || c.case_id.startsWith(term));
  const { rows, hasNext, total } = slicePage(matching, page);       // the tab counts need every row; show one page
  const count = (t: (typeof TABS)[number]) => all.filter(t.match).length;
  const busy = all.some((c) => c.status === "PROCESSING");
  const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

  return (
    <div className="space-y-6">
      <AutoRefresh active={busy} />

      <div className="flex flex-col gap-4 animate-fade-up sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Claim cases</h1>
          <p className="mt-1 text-sm text-slate-500">Review the decisions made on each document</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          <Link href="/bundles#new-case" className="btn-primary">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path strokeLinecap="round" d="M12 5v14M5 12h14" />
            </svg>
            New case
          </Link>
          <form action="/" className="relative">
            <input type="hidden" name="tab" value={active} />
            <svg className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.3-4.3M10.5 18a7.5 7.5 0 110-15 7.5 7.5 0 010 15z" />
            </svg>
            <input name="q" defaultValue={q ?? ""} placeholder="Search file name or case ID" aria-label="Search cases"
              className="input w-64 pl-9" />
          </form>
        </div>
        {busy && (
          <p className="inline-flex items-center gap-2 self-start rounded-full bg-sky-50 px-3 py-1 text-xs font-medium text-sky-700 sm:self-auto">
            <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" />
            Processing documents
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <KpiCard delay={0} icon="folder" label="Total cases" value={String(stats.total)}
          hint={`${stats.documents ?? 0} documents · ${stats.processing} processing · ${stats.failed} failed`} href="/bundles" />
        <KpiCard delay={40} icon="calendar" tone="info" label="Received today" value={String(stats.today)}
          hint={`${stats.today === 1 ? "1 case" : `${stats.today} cases`} · ${stats.documents_today ?? 0} documents`} />
        <KpiCard delay={80} icon="check" tone="good" label="Approval rate" value={pct(stats.approval_rate)}
          meter={stats.approval_rate == null ? null : stats.approval_rate * 100}
          hint={`of ${stats.decided} decided cases`} />
        <KpiCard delay={120} icon="alert" tone="warning" label="Needs review" value={String(stats.escalated)}
          hint={stats.escalated > 0 ? "Waiting for a person · open the queue" : "Nothing waiting"}
          href="/review" highlight={stats.escalated > 0} />
        <KpiCard delay={160} icon="check" tone="good" label="Approved" value={String(stats.approved)}
          hint="By the agents or a reviewer" href="/approved" />
        <KpiCard delay={200} icon="x" tone="critical" label="Rejected" value={String(stats.rejected)}
          hint="Not a member, or not covered" href="/rejected" />
        <KpiCard delay={240} icon="gauge" tone="info" label="Avg. confidence" value={pct(stats.avg_confidence)}
          meter={stats.avg_confidence == null ? null : stats.avg_confidence * 100}
          hint={`${stats.reviewed} reviewed by a person`} />
        <KpiCard delay={280} icon="user" label="Patients" value={String(stats.patients)}
          hint="Registered patients" href="/patients" />
      </div>

      <div className="card animate-fade-up overflow-hidden [animation-delay:120ms]">
        <div className="-mx-1 flex gap-1 overflow-x-auto border-b border-slate-200 px-3 pt-2 [scrollbar-width:none] sm:px-4">
          {TABS.map((t) => (
            <Link key={t.key} href={`/?tab=${t.key}`}
              className={`relative shrink-0 rounded-t-lg px-3 py-2.5 text-sm font-medium transition duration-200 sm:px-4 ${active === t.key ? "text-brand-700" : "text-slate-500 hover:text-slate-800"}`}>
              {t.label}
              <span className={`ml-1.5 rounded-full px-2 py-0.5 text-xs tabular-nums transition-colors ${active === t.key ? "bg-brand-50 text-brand-700" : "bg-slate-100 text-slate-500"}`}>
                {count(t)}
              </span>
              {active === t.key && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-brand-600 animate-fade-in" />}
            </Link>
          ))}
        </div>

        <div className="flex items-center justify-between px-4 py-3 text-xs text-slate-500 sm:px-6">
          <span>{tabDef.hint}</span>
          <span className="tabular-nums">{total} {total === 1 ? "case" : "cases"} · page {page}</span>
        </div>

        {rows.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-16 text-center animate-fade-in">
            <div className="grid h-12 w-12 place-items-center rounded-full bg-slate-100 text-slate-400">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.6}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6M7 4h10a2 2 0 012 2v12a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2z" />
              </svg>
            </div>
            <p className="text-sm font-medium text-slate-700">{term ? `No cases match “${q?.trim()}”` : "No cases in this view"}</p>
            <p className="text-xs text-slate-500">
              {term ? <Link href={`/?tab=${active}`} className="font-medium text-brand-700 hover:underline">Clear search</Link> : "Uploaded documents appear here once processing starts."}
            </p>
          </div>
        ) : (
          <>
            {/* Phones and tablets: one card per case */}
            <ul className="divide-y divide-slate-100 md:hidden">
              {rows.map((c, i) => (
                <li key={c.case_id} className="relative animate-fade-up px-4 py-4" style={{ animationDelay: `${Math.min(i, 12) * 35}ms` }}>
                  {rowAccent(c) && <span className={`absolute inset-y-3 left-0 w-1 rounded-r-full ${rowAccent(c)}`} aria-hidden />}
                  <Link href={openHref(c)} className="block active:bg-slate-50">
                    <div className="flex items-start justify-between gap-3">
                      <p className="min-w-0 truncate text-sm font-semibold text-slate-900">{c.file_name}</p>
                      <Badge value={c.decision ?? c.status} />
                    </div>
                    <p className="mt-1 text-xs capitalize text-slate-500">
                      {c.doc_type?.replaceAll("_", " ").toLowerCase() ?? "unclassified"} · {fmtDate(c.created_at)}
                    </p>
                    <div className="mt-2 text-xs text-slate-600"><Confidence value={c.confidence} /></div>
                  </Link>
                </li>
              ))}
            </ul>

            {/* Desktop: full table */}
            <div className="hidden overflow-x-auto md:block">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50/70">
                  <tr>
                    <th className="th">File</th>
                    <th className="th">Document</th>
                    <th className="th">Decision</th>
                    <th className="th">Status</th>
                    <th className="th text-right">Confidence</th>
                    <th className="th">Received</th>
                    <th className="th"><span className="sr-only">Open</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((c, i) => (
                    <tr key={c.case_id} style={{ animationDelay: `${Math.min(i, 12) * 35}ms` }}
                      className="animate-fade-up transition-colors duration-150 hover:bg-brand-50/50">
                      <td className="td relative max-w-xs truncate font-medium text-slate-900">
                        {rowAccent(c) && <span className={`absolute inset-y-2 left-0 w-1 rounded-r-full ${rowAccent(c)}`} aria-hidden />}
                        <Link href={openHref(c)} className="hover:text-brand-700 hover:underline">{c.file_name}</Link>
                      </td>
                      <td className="td capitalize text-slate-600">{c.doc_type?.replaceAll("_", " ").toLowerCase() ?? "—"}</td>
                      <td className="td"><Badge value={c.decision} /></td>
                      <td className="td"><Badge value={c.status} /></td>
                      <td className="td text-right"><Confidence value={c.confidence} /></td>
                      <td className="td whitespace-nowrap text-slate-500">{fmtDate(c.created_at)}</td>
                      <td className="td text-right">
                        <Link href={openHref(c)} className="btn-ghost text-brand-700">Open <span aria-hidden>→</span></Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager path="/" params={{ tab: active, q }} page={page} hasNext={hasNext} shown={rows.length} total={total}
              className="border-t border-slate-100 px-4 pb-3 sm:px-6" />
          </>
        )}
      </div>
    </div>
  );
}
