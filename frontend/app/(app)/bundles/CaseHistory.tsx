import Link from "next/link";
import Badge from "@/components/Badge";
import { CaseSummary, ClaimBundle, DocType } from "@/lib/api";

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// The five document slots, in the same order as the upload cards.
const SLOTS: { key: DocType; short: string; label: string }[] = [
  { key: "CLAIM_FORM", short: "Claim", label: "Claim form" },
  { key: "DISCHARGE_SUMMARY", short: "Discharge", label: "Discharge summary" },
  { key: "ID_DOCUMENT", short: "ID", label: "ID document" },
  { key: "PRESCRIPTION", short: "Rx", label: "Prescription" },
  { key: "POLICY_AMENDMENT", short: "Policy", label: "Policy amendment" },
];

type SlotState = "missing" | "file" | "pass" | "fail" | "review";

const SLOT_STYLE: Record<SlotState, { tone: string; text: string }> = {
  missing: { tone: "bg-slate-100 text-slate-300", text: "No file" },
  file: { tone: "bg-slate-200 text-slate-600", text: "File uploaded, not decided" },
  pass: { tone: "bg-emerald-100 text-emerald-700", text: "Passed" },
  fail: { tone: "bg-rose-100 text-rose-700", text: "Failed" },
  review: { tone: "bg-amber-100 text-amber-800", text: "Needs review" },
};

// The state of one document slot. If several files share a type (PDF pages), the worst one wins.
function slotState(docs: CaseSummary[], type: DocType): SlotState {
  const mine = docs.filter((d) => d.doc_type === type);
  if (mine.length === 0) return "missing";
  if (mine.some((d) => d.status === "FAILED" || d.decision === "ESCALATE" || d.needs_review)) return "review";
  if (mine.some((d) => d.decision === "REJECT")) return "fail";
  if (mine.every((d) => d.decision === "APPROVE")) return "pass";
  return "file";
}

function SlotIcon({ state }: { state: SlotState }) {
  const common = "h-3.5 w-3.5";
  const stroke = { fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", strokeWidth: 2.4, "aria-hidden": true } as const;
  if (state === "pass") return <svg className={common} {...stroke}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>;
  if (state === "fail") return <svg className={common} {...stroke}><path strokeLinecap="round" d="M6 6l12 12M6 18L18 6" /></svg>;
  if (state === "review") return <svg className={common} {...stroke}><path strokeLinecap="round" d="M12 8v5m0 3h.01" /></svg>;
  if (state === "file") return <svg className={common} {...stroke}><path strokeLinecap="round" strokeLinejoin="round" d="M7 3h7l5 5v13H7z M14 3v5h5" /></svg>;
  return <svg className={common} {...stroke}><path strokeLinecap="round" d="M7 12h10" /></svg>;
}

function DocSlots({ docs }: { docs: CaseSummary[] }) {
  return (
    <div className="hidden shrink-0 items-center gap-1.5 md:flex">
      {SLOTS.map((s) => {
        const state = slotState(docs, s.key);
        const style = SLOT_STYLE[state];
        return (
          <span key={s.key} title={`${s.label}: ${style.text}`} aria-label={`${s.label}: ${style.text}`}
            className="flex flex-col items-center gap-0.5">
            <span className={`grid h-6 w-6 place-items-center rounded-full ${style.tone}`}>
              <SlotIcon state={state} />
            </span>
            <span className="text-[10px] leading-none text-slate-400">{s.short}</span>
          </span>
        );
      })}
    </div>
  );
}

// Row 3: one row per case file, newest first. Each row shows the five document slots.
export default function CaseHistory({ bundles, names }: { bundles: ClaimBundle[]; names: Map<string, string> }) {
  return (
    <section aria-labelledby="panel-history" className="card animate-fade-up p-5 [animation-delay:120ms]">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="panel-history" className="text-sm font-semibold text-slate-900">Last {bundles.length} uploads</h2>
        <span className="text-xs text-slate-400">Older claims: <Link href="/approved" className="text-brand-700 hover:underline">Approved</Link> · <Link href="/rejected" className="text-brand-700 hover:underline">Rejected</Link> · <Link href="/review" className="text-brand-700 hover:underline">Review queue</Link></span>
      </div>

      {bundles.length === 0 ? (
        <p className="mt-6 text-center text-xs text-slate-500">No cases yet. Your first case will appear here.</p>
      ) : (
        <ul className="mt-4 flex flex-col gap-2">
          {bundles.map((b, i) => (
            <li key={b.bundle_id} className="animate-fade-up" style={{ animationDelay: `${Math.min(i, 12) * 30}ms` }}>
              <Link href={`/bundles/${b.bundle_id}`}
                className="flex items-center justify-between gap-4 rounded-xl bg-slate-50/70 px-4 py-3 ring-1 ring-slate-200/70 transition duration-200 hover:bg-white hover:shadow-sm">
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900">{b.title}</span>
                <span className="hidden w-40 shrink-0 truncate text-xs text-slate-500 lg:block">
                  {b.patient_id ? (names.get(b.patient_id) ?? "Patient") : "No patient"}
                </span>
                <DocSlots docs={b.documents} />
                <span className="hidden w-36 shrink-0 text-xs text-slate-500 xl:block">{fmt(b.created_at)}</span>
                <span className="shrink-0"><Badge value={b.summary.decision ?? b.summary.status} /></span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
