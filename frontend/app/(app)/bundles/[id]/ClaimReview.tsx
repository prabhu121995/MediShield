"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Decision, DOC_CATEGORIES, DocType, IngestedDoc, rerunClaim, reviewClaim } from "@/lib/api";

const DECISIONS: { value: Decision; label: string; tone: string }[] = [
  { value: "APPROVE", label: "Approve", tone: "data-[on=true]:bg-emerald-600 data-[on=true]:text-white data-[on=true]:border-emerald-600" },
  { value: "REJECT", label: "Reject", tone: "data-[on=true]:bg-rose-600 data-[on=true]:text-white data-[on=true]:border-rose-600" },
  { value: "ESCALATE", label: "Keep escalated", tone: "data-[on=true]:bg-amber-500 data-[on=true]:text-white data-[on=true]:border-amber-500" },
];

// Ops staff: decide the claim with a note (recorded as an override, the agents' decision is kept),
// or correct a page's document type and re-run the whole claim. Docked at the bottom right like a
// chat widget, so it stays in reach while the page scrolls; it opens by itself when a person is needed.
export default function ClaimReview({ claimId, docs, busyClaim, needsPerson = false, agentDecision }: {
  claimId: string; docs: IngestedDoc[]; busyClaim: boolean; needsPerson?: boolean; agentDecision?: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [greet, setGreet] = useState(needsPerson);       // the "Hi, I'm here" bubble
  useEffect(() => {                                         // it says hello, then gets out of the way
    if (!greet) return;
    const t = setTimeout(() => setGreet(false), 5000);
    return () => clearTimeout(t);
  }, [greet]);
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
    const done = { APPROVE: "Approved: status updated", REJECT: "Rejected: status updated",
                   ESCALATE: "Kept escalated: still in review" }[decision];
    run(() => reviewClaim(claimId, { decision, note: note.trim() }), done);
  }

  const changed = Object.keys(types).length > 0;
  const disabled = busy || busyClaim;

  if (!open) {
    // Minimised: a chat-bot launcher. While a decision is needed it dances every few seconds,
    // pulses, and greets with a speech bubble (motion is off under prefers-reduced-motion).
    return (
      <div className="fixed bottom-4 right-4 z-40 flex flex-col items-end gap-2">
        {needsPerson && greet && (
          <div role="status" className="relative max-w-[16rem] animate-bubble-in rounded-2xl rounded-br-sm bg-white px-4 py-3 text-sm shadow-xl ring-1 ring-slate-900/10">
            <button type="button" onClick={() => setGreet(false)} aria-label="Hide message"
              className="absolute right-1.5 top-1.5 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden>
                <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
            <p className="pr-4 font-semibold text-slate-900">
              <span className="inline-block origin-[70%_70%] animate-wave" aria-hidden>👋</span> Hi, I&apos;m here!
            </p>
            <p className="mt-0.5 text-xs text-slate-600">
              This claim is waiting for your decision{agentDecision ? ` (agents: ${agentDecision.toLowerCase()})` : ""}.
            </p>
            <button type="button" onClick={() => setOpen(true)} className="mt-2 text-xs font-semibold text-brand-700 hover:underline">
              Review now →
            </button>
          </div>
        )}
        <span className={`relative inline-flex ${needsPerson ? "animate-dance" : ""}`}>
          {needsPerson && <span className="absolute inset-0 animate-ripple rounded-full bg-brand-500" aria-hidden />}
          <button type="button" onClick={() => setOpen(true)} aria-expanded={false} aria-controls="review-panel"
            className="relative flex items-center gap-2 rounded-full bg-brand-600 py-3 pl-4 pr-5 text-sm font-semibold text-white shadow-lg ring-1 ring-black/5 transition hover:scale-105 hover:bg-brand-700">
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 10h8M8 14h5m-9 6l3-3h11a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v14z" />
            </svg>
            Human review
            {needsPerson && <span className="h-2.5 w-2.5 animate-pulse-dot rounded-full bg-amber-300 ring-2 ring-brand-600" aria-label="needs a decision" />}
          </button>
        </span>
      </div>
    );
  }

  return (
    <section id="review-panel" aria-labelledby="review-h"
      className="fixed inset-x-4 bottom-4 z-40 flex max-h-[calc(100vh-6rem)] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-slate-900/10 sm:left-auto sm:w-[400px]">
      <header className="flex items-start justify-between gap-3 bg-brand-600 px-4 py-3 text-white">
        <div className="min-w-0">
          <h2 id="review-h" className="text-sm font-semibold">Human review</h2>
          <p className="mt-0.5 text-[11px] text-white/80">
            {needsPerson ? "This claim is waiting for your decision." : "Override the decision or correct a page."}
            {agentDecision ? ` Agents: ${agentDecision.toLowerCase()}.` : ""}
          </p>
        </div>
        <button type="button" onClick={() => setOpen(false)} aria-label="Minimise human review" aria-expanded={true}
          className="rounded-md p-1 text-white/80 hover:bg-white/10 hover:text-white">
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
          </svg>
        </button>
      </header>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-slate-500">An escalated claim is paused at a LangGraph checkpoint; your decision resumes it. Every action is audited with your name.</p>
        {saved && <span className="shrink-0 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">{saved}</span>}
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
              <select aria-label={`Document type of ${d.file_name}`} className="input w-44 py-1.5 text-xs" disabled={disabled}
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
      </div>
    </section>
  );
}
