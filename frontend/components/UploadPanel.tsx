"use client";

import { SINGLE_UPLOAD_EXTENSIONS } from "@/lib/settings";
import { useRef, useState } from "react";
import { DOC_CATEGORIES, DocType } from "@/lib/api";
import { useUploads } from "./UploadProvider";

const ACCEPT = SINGLE_UPLOAD_EXTENSIONS.join(",");

// Choose one or many documents, pick a category (or auto-detect), and send them to
// the background queue. Progress appears in the tray, so the panel can close.
export default function UploadPanel({ onQueued, patientId }: { onQueued?: () => void; patientId?: string }) {
  const { enqueue } = useUploads();
  const [files, setFiles] = useState<File[]>([]);
  const [category, setCategory] = useState<DocType | "">("");
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  function addFiles(list: FileList | File[]) {
    const accepted = Array.from(list).filter((f) => SINGLE_UPLOAD_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)));
    setFiles((prev) => [...prev, ...accepted]);
  }

  function submit() {
    if (files.length === 0) return;
    enqueue(files, { category: category || undefined, patientId });
    setFiles([]);
    onQueued?.();
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
        <div>
          <label htmlFor="category" className="label">Document category</label>
          <select id="category" className="input" value={category} onChange={(e) => setCategory(e.target.value as DocType | "")}>
            <option value="">Auto-detect (recommended)</option>
            {DOC_CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </div>
        <p className="text-xs text-slate-500 sm:max-w-[16rem]">Set a category only when you know the document type is wrong.</p>
      </div>

      <div
        role="button"
        tabIndex={0}
        aria-label="Drop documents here or press Enter to choose files"
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
        className={`group grid cursor-pointer place-items-center rounded-2xl border-2 border-dashed px-4 py-8 text-center transition-all duration-300 sm:px-6 sm:py-10 ${
          dragging ? "scale-[1.01] border-brand-500 bg-brand-50" : "border-slate-300 bg-slate-50/60 hover:border-brand-500/60 hover:bg-brand-50/40"
        }`}
      >
        <input ref={input} type="file" multiple accept={ACCEPT} className="hidden"
          onChange={(e) => e.target.files && addFiles(e.target.files)} />
        <div className="grid h-12 w-12 place-items-center rounded-2xl bg-white text-brand-600 shadow-sm ring-1 ring-slate-200 transition duration-300 group-hover:-translate-y-1">
          <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.6}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0l-4 4m4-4l4 4M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
          </svg>
        </div>
        <p className="mt-3 text-sm font-semibold text-slate-900">Drop documents here</p>
        <p className="mt-1 text-xs text-slate-500">or tap to choose one or more files · PNG, JPG, TIFF, BMP</p>
      </div>

      {files.length > 0 && (
        <ul className="max-h-56 divide-y divide-slate-100 overflow-y-auto rounded-xl ring-1 ring-slate-200">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center justify-between gap-3 px-4 py-2.5 animate-fade-up">
              <span className="min-w-0 truncate text-sm text-slate-800">{f.name}</span>
              <div className="flex shrink-0 items-center gap-3">
                <span className="text-xs tabular-nums text-slate-400">{(f.size / 1024).toFixed(0)} KB</span>
                <button type="button" aria-label={`Remove ${f.name}`}
                  className="rounded-md p-1 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                  onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}>✕</button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        <button type="button" className="btn-ghost" onClick={() => setFiles([])} disabled={files.length === 0}>Clear</button>
        <button type="button" className="btn-primary w-full sm:w-auto" onClick={submit} disabled={files.length === 0}>
          Upload {files.length > 0 ? `${files.length} file${files.length > 1 ? "s" : ""}` : "documents"}
        </button>
      </div>
    </div>
  );
}
