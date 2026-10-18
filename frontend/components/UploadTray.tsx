"use client";

import Link from "next/link";
import { useState } from "react";
import { Job, useUploads } from "./UploadProvider";

const DECISION_TEXT: Record<string, string> = {
  APPROVE: "Approved",
  REJECT: "Rejected",
  ESCALATE: "Escalated for review",
};

function statusText(job: Job) {
  if (job.state === "uploading") return "Sending to the server…";
  if (job.state === "processing") return "Processing in background…";
  if (job.state === "done") return DECISION_TEXT[job.decision ?? ""] ?? "Processed";
  return job.error ?? "Processing failed";
}

function Indicator({ state }: { state: Job["state"] }) {
  if (state === "done") {
    return <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-emerald-100 text-sm font-bold text-emerald-700 animate-scale-in">✓</span>;
  }
  if (state === "failed") {
    return <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-rose-100 text-sm font-bold text-rose-700 animate-scale-in">!</span>;
  }
  return (
    <span className="grid h-7 w-7 shrink-0 place-items-center">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" />
    </span>
  );
}

// Floating panel listing every upload: sending, processing, done, or failed.
export default function UploadTray() {
  const { jobs, retry, dismiss, clearFinished } = useUploads();
  const [open, setOpen] = useState(true);
  if (jobs.length === 0) return null;

  const busy = jobs.filter((j) => j.state === "uploading" || j.state === "processing").length;
  const failed = jobs.filter((j) => j.state === "failed").length;
  const summary = busy > 0 ? `${busy} in progress` : failed > 0 ? `${failed} failed` : "All uploads finished";

  return (
    <section aria-label="Upload progress" aria-live="polite"
      className="fixed inset-x-3 bottom-3 z-40 animate-fade-up sm:inset-x-auto sm:bottom-6 sm:right-6 sm:w-96">
      <div className="overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-slate-900/10">
        <div className="flex items-center justify-between gap-3 bg-slate-950 px-4 py-3 text-white">
          <div className="min-w-0">
            <p className="text-sm font-semibold">Uploads</p>
            <p className="truncate text-xs tabular-nums text-slate-300">{summary}</p>
          </div>
          <div className="flex items-center gap-1">
            <button type="button" onClick={clearFinished}
              className="rounded-lg px-2 py-1 text-xs text-slate-300 transition hover:bg-white/10 hover:text-white">
              Clear done
            </button>
            <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
              aria-label={open ? "Collapse uploads" : "Expand uploads"}
              className="rounded-lg p-1.5 text-slate-300 transition hover:bg-white/10 hover:text-white">
              <svg className={`h-4 w-4 transition-transform duration-300 ${open ? "" : "rotate-180"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 15l6-6 6 6" />
              </svg>
            </button>
          </div>
        </div>

        {open && (
          <ul className="max-h-[50vh] divide-y divide-slate-100 overflow-y-auto">
            {jobs.map((job) => {
              const live = job.state === "uploading" || job.state === "processing";
              return (
                <li key={job.key} className="flex items-start gap-3 px-4 py-3 animate-fade-up">
                  <Indicator state={job.state} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-900">{job.name}</p>
                    <p className={`mt-0.5 text-xs ${job.state === "failed" ? "text-rose-600" : "text-slate-500"}`}>
                      {statusText(job)}
                    </p>
                    {live && (
                      <div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full w-1/3 animate-progress rounded-full bg-brand-500" />
                      </div>
                    )}
                    <div className="mt-2 flex gap-3">
                      {job.state === "done" && job.caseId && (
                        <Link href={`/cases/${job.caseId}`} className="text-xs font-semibold text-brand-700 hover:underline">Open case</Link>
                      )}
                      {job.state === "failed" && job.caseId && (
                        <button type="button" onClick={() => retry(job.key)} className="text-xs font-semibold text-brand-700 hover:underline">Retry</button>
                      )}
                    </div>
                  </div>
                  {!live && (
                    <button type="button" onClick={() => dismiss(job.key)} aria-label={`Dismiss ${job.name}`}
                      className="rounded-md p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700">✕</button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
