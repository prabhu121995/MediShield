const STYLES: Record<string, string> = {
  APPROVE: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  APPROVED: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  REJECT: "bg-rose-50 text-rose-700 ring-rose-600/20",
  REJECTED: "bg-rose-50 text-rose-700 ring-rose-600/20",
  ESCALATE: "bg-amber-50 text-amber-800 ring-amber-600/25",
  NEEDS_REVIEW: "bg-amber-50 text-amber-800 ring-amber-600/25",
  REVIEWED: "bg-brand-50 text-brand-700 ring-brand-600/20",
  QUEUED: "bg-sky-50 text-sky-700 ring-sky-600/20",
  INGESTED: "bg-slate-100 text-slate-600 ring-slate-500/20",
  PROCESSING: "bg-sky-50 text-sky-700 ring-sky-600/20",
  FAILED: "bg-rose-50 text-rose-700 ring-rose-600/20",
};

const LABELS: Record<string, string> = {
  NEEDS_REVIEW: "Needs review",
  ESCALATE: "Escalate",
  APPROVE: "Approve",
  REJECT: "Reject",
  PROCESSING: "Processing",
  QUEUED: "Queued",
  REVIEWED: "Reviewed",
  FAILED: "Failed",
};

export default function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-slate-400">—</span>;
  const style = STYLES[value] ?? "bg-slate-100 text-slate-600 ring-slate-500/20";
  const pulse = value === "ESCALATE" || value === "NEEDS_REVIEW";
  const spin = value === "PROCESSING";
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset transition-colors duration-300 ${style}`}>
      {pulse && <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-amber-500" />}
      {spin && <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-200 border-t-sky-600" />}
      {LABELS[value] ?? value.replaceAll("_", " ").toLowerCase()}
    </span>
  );
}
