"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Patient, submitClaim } from "@/lib/api";
import { allowedFile, MAX_BATCH_FILES as MAX_FILES, MAX_UPLOAD_BYTES as MAX_BYTES, UPLOAD_EXTENSIONS } from "@/lib/settings";

const ACCEPT = UPLOAD_EXTENSIONS.join(",");

function fmtSize(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

// One claim = every document of one patient episode, uploaded with one button. No document
// types are chosen here: the classifier agent reads each page and decides (or marks it UNKNOWN).
export default function NewCaseForm({ patients, locked = false }: { patients: Pick<Patient, "patient_id" | "full_name" | "member_id">[]; locked?: boolean }) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [patientId, setPatientId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function add(list: FileList | File[]) {
    const incoming = Array.from(list);
    const refused = incoming.filter((f) => !allowedFile(f.name) || f.size > MAX_BYTES);
    const ok = incoming.filter((f) => !refused.includes(f));
    setNotice(refused.length ? `Skipped ${refused.map((f) => f.name).join(", ")}: only ${UPLOAD_EXTENSIONS.join(" ")} up to ${MAX_BYTES / 1024 / 1024} MB.` : null);
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...ok.filter((f) => !seen.has(`${f.name}:${f.size}`))].slice(0, MAX_FILES);
    });
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (files.length === 0) {
      setError("Add the claim's documents first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const claim = await submitClaim({ title: title.trim() || undefined, files, patientId: patientId || undefined });
      router.push(`/bundles/${claim.bundle_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="card animate-fade-up p-5" noValidate>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end">
        <div className="grid flex-1 gap-4 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <div>
            <label htmlFor="case_title" className="label">Claim title <span className="font-normal text-slate-400">(optional)</span></label>
            <input id="case_title" className="input" value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder="Jane Doe · March claim" maxLength={160} disabled={busy} />
          </div>
          <div>
            <label htmlFor="case_patient" className="label">Patient <span className="font-normal text-slate-400">(optional)</span></label>
            <select id="case_patient" className="input" value={patientId} onChange={(e) => setPatientId(e.target.value)} disabled={busy}>
              <option value="">None</option>
              {patients.map((p) => (
                <option key={p.patient_id} value={p.patient_id}>{p.full_name}{p.member_id ? ` · ${p.member_id}` : ""}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex items-center gap-2 lg:shrink-0">
          <button type="submit" disabled={busy || locked || files.length === 0} className="btn-primary h-[42px] px-5" title={locked ? "Another claim is processing; only one runs at a time" : undefined}>
            {busy && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden />}
            {busy ? "Uploading…" : `Upload & process${files.length ? ` (${files.length})` : ""}`}
          </button>
          <button type="button" className="btn-ghost h-[42px] px-3" disabled={busy || files.length === 0}
            onClick={() => { setFiles([]); setNotice(null); setError(null); }}>Clear</button>
        </div>
      </div>

      <label
        onDragOver={(e) => { e.preventDefault(); if (!busy) setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); if (!busy) add(e.dataTransfer.files); }}
        className={`mt-4 flex cursor-pointer flex-col items-center justify-center gap-1 rounded-2xl border-2 border-dashed px-4 py-8 text-center transition ${
          dragOver ? "border-brand-500 bg-brand-50" : "border-slate-300 bg-slate-50/60 hover:border-brand-500 hover:bg-white"} ${busy ? "pointer-events-none opacity-50" : ""}`}>
        <svg className="h-8 w-8 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.6} aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0l-4 4m4-4l4 4M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
        </svg>
        <span className="text-sm font-semibold text-slate-900">Drop all of the claim&apos;s documents here, or click to choose</span>
        <span className="text-xs text-slate-500">Claim form, ID, discharge summary, prescription, amendment… in any order. Each page is classified automatically.</span>
        <input type="file" multiple accept={ACCEPT} className="sr-only" disabled={busy} aria-label="Choose documents"
          onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
      </label>

      {files.length > 0 && (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-label="Documents to upload">
          {files.map((f) => (
            <li key={`${f.name}:${f.size}`} className="flex min-w-0 items-center justify-between gap-3 rounded-lg bg-white px-3 py-2 text-xs ring-1 ring-slate-200">
              <span className="truncate text-slate-700">{f.name}</span>
              <span className="flex shrink-0 items-center gap-3">
                <span className="tabular-nums text-slate-400">{fmtSize(f.size)}</span>
                <button type="button" aria-label={`Remove ${f.name}`} disabled={busy}
                  onClick={() => setFiles((prev) => prev.filter((x) => x !== f))}
                  className="rounded p-0.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600">✕</button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {notice && <p role="status" className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">{notice}</p>}
      {error && <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700">{error}</p>}
    </form>
  );
}
