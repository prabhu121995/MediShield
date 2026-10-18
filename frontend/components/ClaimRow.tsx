import Link from "next/link";
import Badge from "@/components/Badge";
import { CaseSummary, ClaimBundle, DocType, Patient } from "@/lib/api";

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// The five document slots, in processing order (as DOC_CATEGORIES): ID, claim form, discharge, prescription, amendment.
const SLOTS: { key: DocType; short: string; label: string }[] = [
  { key: "ID_DOCUMENT", short: "ID", label: "ID document" },
  { key: "CLAIM_FORM", short: "Claim", label: "Claim form" },
  { key: "DISCHARGE_SUMMARY", short: "Discharge", label: "Discharge summary" },
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

// The claim's status as a solid pill: green approved, yellow waiting for review, red rejected
// or failed, blue on hold or in progress; stopped stays grey.
const STATUS_PILL: Record<string, { tone: string; label: string }> = {
  APPROVED: { tone: "bg-emerald-600 text-white", label: "Approved" },
  NEEDS_REVIEW: { tone: "bg-amber-400 text-amber-950", label: "Needs review" },
  REJECTED: { tone: "bg-rose-600 text-white", label: "Rejected" },
  FAILED: { tone: "bg-rose-600 text-white", label: "Failed" },
  ON_HOLD: { tone: "bg-sky-600 text-white", label: "On hold" },
  QUEUED: { tone: "bg-sky-600 text-white", label: "Pending" },
  PROCESSING: { tone: "bg-sky-600 text-white", label: "In progress" },
  STOPPED: { tone: "bg-slate-400 text-white", label: "Stopped" },
};

function StatusPill({ status }: { status: string | null | undefined }) {
  const pill = status ? STATUS_PILL[status] : undefined;
  if (!pill) return <Badge value={status} />;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold shadow-sm ${pill.tone}`}>
      {status === "PROCESSING" && <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden />}
      {pill.label}
    </span>
  );
}

function DocSlots({ docs }: { docs: CaseSummary[] }) {
  return (
    <div className="hidden shrink-0 items-center md:flex">
      {SLOTS.map((s) => {
        const state = slotState(docs, s.key);
        const style = SLOT_STYLE[state];
        return (
          <span key={s.key} title={`${s.label}: ${style.text}`} aria-label={`${s.label}: ${style.text}`}
            className="flex w-12 flex-col items-center gap-0.5">
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

// One claim as a row, the same in "All claims" and every queue: title (and an optional line
// under it, e.g. why it is in the queue), patient (name and member ID), the five document slots,
// date and status.
export default function ClaimRow({ claim, patient, status, subtitle }: {
  claim: ClaimBundle; patient?: Pick<Patient, "full_name" | "member_id">; status?: string | null; subtitle?: React.ReactNode;
}) {
  return (
    <Link href={`/bundles/${claim.bundle_id}`}
      className="flex items-center justify-between gap-4 rounded-xl bg-slate-50/70 px-4 py-3 ring-1 ring-slate-200/70 transition duration-200 hover:bg-white hover:shadow-sm">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-slate-900">{claim.title}</span>
        {subtitle && <span className="mt-0.5 block truncate text-xs text-slate-500">{subtitle}</span>}
      </span>
      <span className="hidden w-44 shrink-0 lg:block">
        {claim.patient_id ? (
          <>
            <span className="block truncate text-xs font-medium text-slate-700">{patient?.full_name ?? "Patient"}</span>
            {patient?.member_id && <span className="block truncate text-[11px] text-slate-400">{patient.member_id}</span>}
          </>
        ) : <span className="text-xs text-slate-400">No patient</span>}
      </span>
      <DocSlots docs={claim.documents} />
      <span className="hidden w-36 shrink-0 text-xs text-slate-500 xl:block">{fmt(claim.created_at)}</span>
      {/* Fixed width, right-aligned: pills differ in width ("Needs review" vs "Stopped"),
          so a sized column keeps the slots and dates lined up from row to row. */}
      <span className="flex w-28 shrink-0 justify-end"><StatusPill status={status ?? claim.status ?? claim.summary.status} /></span>
    </Link>
  );
}
