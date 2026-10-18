"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { uploadPolicy } from "@/lib/api";

export default function PolicyUploader() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const res = await uploadPolicy(file);
      setMessage(`${res.filename} indexed: ${res.chunks} sections`);
      setFile(null);
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="card animate-fade-up space-y-4 p-6 [animation-delay:60ms]">
      <div>
        <label htmlFor="pdf" className="label">Policy PDF</label>
        <input id="pdf" type="file" accept="application/pdf,.pdf" disabled={busy}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full cursor-pointer rounded-xl border border-slate-200 bg-white text-sm text-slate-600 file:mr-4 file:rounded-lg file:border-0 file:bg-brand-50 file:px-4 file:py-2.5 file:text-sm file:font-semibold file:text-brand-700 transition hover:file:bg-brand-100" />
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <button type="submit" className="btn-primary" disabled={!file || busy}>
          {busy && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
          {busy ? "Indexing (first run downloads models)…" : "Upload and index"}
        </button>
        {message && <p className="animate-scale-in text-sm font-medium text-emerald-700">{message}</p>}
        {error && <p className="animate-shake text-sm text-rose-700">{error}</p>}
      </div>
    </form>
  );
}
