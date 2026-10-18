// Downloads for one claim's whole investigation: the report (PDF: each agent, what it worked on,
// when it started and finished, how long it took, what it returned) and the raw events (CSV).
export default function ClaimDownloads({ claimId, compact = false }: { claimId: string; compact?: boolean }) {
  const base = `/api/bff/claim-bundles/${claimId}/audit`;
  const icon = (
    <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" />
    </svg>
  );
  if (compact) {
    return (
      <span className="inline-flex items-center gap-1 whitespace-nowrap">
        <a href={`${base}/report.pdf`} download title="Download this claim's investigation report (PDF)"
          className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] font-semibold text-brand-700 ring-1 ring-brand-200 hover:bg-brand-50">
          {icon}PDF
        </a>
        <a href={`${base}/export.csv`} download title="Download this claim's raw audit events (CSV)"
          className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50">
          {icon}CSV
        </a>
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-2">
      <a href={`${base}/export.csv`} download className="btn-ghost bg-white text-xs shadow-sm ring-1 ring-slate-200">
        {icon} Raw events (CSV)
      </a>
      <a href={`${base}/report.pdf`} download className="btn-primary text-xs">
        {icon} Investigation report (PDF)
      </a>
    </span>
  );
}
