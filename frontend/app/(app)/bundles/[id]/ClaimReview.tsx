"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Decision, DOC_CATEGORIES, DocType, IngestedDoc, rerunClaim, reviewClaim } from "@/lib/api";

const DECISIONS: { value: Decision; label: string; tone: string }[] = [
  { value: "APPROVE", label: "Approve", tone: "data-[on=true]:bg-emerald-600 data-[on=true]:text-white data-[on=true]:border-emerald-600" },
  { value: "REJECT", label: "Reject", tone: "data-[on=true]:bg-rose-600 data-[on=true]:text-white data-[on=true]:border-rose-600" },
  { value: "ESCALATE", label: "Keep escalated", tone: "data-[on=true]:bg-amber-500 data-[on=true]:text-white data-[on=true]:border-amber-500" },
];

// Ops staff: decide the claim with a note (recorded as an override, the agents' decision is kept),
// or correct a page's document type and re-run the whole claim.
export default function ClaimReview({ claimId, docs, busyClaim }: { claimId: string; docs: IngestedDoc[]; busyClaim: boolean }) {
  const router = useRouter();
  const [decision, setDecision] = useState<Decision | "">("");
  const [note, setNote] = useState("");
  const [types, setTypes] = useState<Record<string, DocType>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      await action();
      setSaved(done);
      setNote("");
      setDecision("");
      setTypes({});
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onDecide(e: FormEvent) {
    e.preventDefault();
    if (!decision) return setError("Choose a decision.");
    if (!note.trim()) return setError("Write a note: overrides are audited.");
    run(() => reviewClaim(claimId, { decision, note: note.trim() }), "Decision recorded");
  }

  const changed = Object.keys(types).length > 0;
  const disabled = busy || busyClaim;

  return (
    <section className="card animate-fade-up space-y-5 p-6" aria-labelledby="review-h">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="review-h" className="text-sm font-semibold text-slate-900">Human review</h2>
          <p className="mt-1 text-xs text-slate-500">An escalated claim is paused at a LangGraph checkpoint; your decision resumes it. Every action is audited with your name.</p>
        </div>
        {saved && <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">{saved}</span>}
      </div>

      <form onSubmit={onDecide} className="space-y-3">
        <p className="label">Final decision</p>
        <div className="grid grid-cols-3 gap-2">
          {DECISIONS.map((d) => (
            <button type="button" key={d.value} data-on={decision === d.value} disabled={disabled}
              onClick={() => setDecision(decision === d.value ? "" : d.value)}
              className={`rounded-xl border border-slate-200 bg-white px-2 py-2.5 text-sm font-semibold text-slate-700 transition hover:shadow-sm disabled:opacity-50 ${d.tone}`}>
              {d.label}
            </button>
          ))}
        </div>
        <label htmlFor="note" className="label">Reviewer note (required)</label>
        <textarea id="note" rows={3} className="input resize-none" value={note} disabled={disabled}
          onChange={(e) => setNote(e.target.value)} placeholder="What did you check, and why this decision?" />
        <button type="submit" disabled={disabled} className="btn-primary w-full">{busy ? "Saving…" : "Record decision"}</button>
      </form>

      <div className="space-y-3 border-t border-slate-100 pt-5">
        <p className="label">Correct a document type, then re-run the claim</p>
        <ul className="space-y-2">
          {docs.map((d) => (
            <li key={d.doc_id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-slate-700" title={d.file_name}>{d.file_name}</span>
              <select aria-label={`Document type of ${d.file_name}`} className="input w-48 py-1.5 text-xs" disabled={disabled}
                value={types[d.doc_id] ?? ""} onChange={(e) => setTypes((prev) => {
                  const next = { ...prev };
                  if (e.target.value) next[d.doc_id] = e.target.value as DocType; else delete next[d.doc_id];
                  return next;
                })}>
                <option value="">{d.doc_type.replaceAll("_", " ").toLowerCase()} (detected)</option>
                {DOC_CATEGORIES.filter((c) => c.value !== d.doc_type).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </li>
          ))}
        </ul>
        <div className="flex gap-2">
          <button type="button" className="btn-primary flex-1" disabled={disabled || !changed}
            onClick={() => run(() => reviewClaim(claimId, { doc_types: types }), "Re-run queued")}>Apply &amp; re-run</button>
          <button type="button" className="btn-ghost" disabled={disabled}
            onClick={() => run(() => rerunClaim(claimId), "Re-run queued")}>Re-run as is</button>
        </div>
      </div>

      {error && <p role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
    </section>
  );
}
