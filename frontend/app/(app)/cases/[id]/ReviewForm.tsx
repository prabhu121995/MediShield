"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Decision, DOC_CATEGORIES, DocType, reviewCase } from "@/lib/api";

const DECISIONS: { value: Decision; label: string; tone: string }[] = [
  { value: "APPROVE", label: "Approve", tone: "data-[on=true]:bg-emerald-600 data-[on=true]:text-white data-[on=true]:border-emerald-600" },
  { value: "ESCALATE", label: "Escalate", tone: "data-[on=true]:bg-amber-500 data-[on=true]:text-white data-[on=true]:border-amber-500" },
  { value: "REJECT", label: "Reject", tone: "data-[on=true]:bg-rose-600 data-[on=true]:text-white data-[on=true]:border-rose-600" },
];

export default function ReviewForm({
  caseId,
  currentDocType,
  currentDecision,
}: {
  caseId: string;
  currentDocType: string;
  currentDecision: string;
}) {
  const router = useRouter();
  const [docType, setDocType] = useState<DocType | "">("");
  const [decision, setDecision] = useState<Decision | "">("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!docType && !decision) {
      setError("Choose a document type to re-run, or a decision to set.");
      return;
    }
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await reviewCase(caseId, {
        ...(docType ? { doc_type: docType } : {}),
        ...(decision ? { decision } : {}),
        ...(note ? { note } : {}),
      });
      setSaved(true);
      setNote("");
      setDocType("");
      setDecision("");
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="card animate-fade-up space-y-5 p-6 [animation-delay:100ms]">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Human review</h2>
          <p className="mt-1 text-xs text-slate-500">
            Current type <span className="font-medium text-slate-700">{currentDocType.replaceAll("_", " ").toLowerCase() || "—"}</span>
          </p>
        </div>
        {saved && <span className="animate-scale-in rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">Saved</span>}
      </div>

      <div>
        <label htmlFor="doc_type" className="label">Correct the document type</label>
        <select id="doc_type" className="input" value={docType} onChange={(e) => setDocType(e.target.value as DocType | "")}>
          <option value="">Keep current type</option>
          {DOC_CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
        <p className="mt-1.5 text-xs text-slate-500">Re-runs the full pipeline with the corrected type.</p>
      </div>

      <div>
        <p className="label">Final decision</p>
        <div className="grid grid-cols-3 gap-2">
          {DECISIONS.map((d) => (
            <button
              type="button"
              key={d.value}
              data-on={decision === d.value}
              onClick={() => setDecision(decision === d.value ? "" : d.value)}
              className={`rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-semibold text-slate-700 transition duration-200 hover:-translate-y-px hover:shadow-sm active:scale-[0.97] ${d.tone}`}
            >
              {d.label}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-slate-500">Currently <span className="font-medium text-slate-700">{currentDecision || "—"}</span></p>
      </div>

      <div>
        <label htmlFor="note" className="label">Reviewer note</label>
        <textarea id="note" rows={3} className="input resize-none" placeholder="Why this decision?" value={note} onChange={(e) => setNote(e.target.value)} />
      </div>

      {error && <p className="animate-shake rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}

      <button type="submit" disabled={busy} className="btn-primary w-full">
        {busy && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
        {busy ? (docType ? "Re-running pipeline…" : "Saving…") : "Save review"}
      </button>
    </form>
  );
}
