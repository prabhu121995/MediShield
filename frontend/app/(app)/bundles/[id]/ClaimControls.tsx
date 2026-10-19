"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import ConfirmDelete from "@/components/ConfirmDelete";
import { ClaimHold, deleteClaim, deleteDocument, replaceAllDocuments, replaceDocument, rerunClaim, resolveHold, resumeClaim, stopClaim } from "@/lib/api";
import { allowedFile, MAX_BATCH_FILES, UPLOAD_EXTENSIONS } from "@/lib/settings";

const ACCEPT = UPLOAD_EXTENSIONS.join(",");

function useAction() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

// A running claim: stop it at once. The model call in flight is abandoned; Resume continues
// from the last finished step.
export function StopButton({ claimId }: { claimId: string }) {
  const { busy, error, run } = useAction();
  const [sure, setSure] = useState(false);
  return (
    <span className="flex flex-wrap items-center gap-2">
      {sure ? (
        <>
          <span className="text-xs text-slate-700">Stop this claim now? You can resume it later.</span>
          <button type="button" className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-700 disabled:opacity-50"
            disabled={busy} onClick={() => run(() => stopClaim(claimId))}>Yes, stop</button>
          <button type="button" className="btn-ghost px-3 py-1.5 text-xs" onClick={() => setSure(false)}>Keep running</button>
        </>
      ) : (
        <button type="button" className="rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-50"
          onClick={() => setSure(true)}>Stop claim</button>
      )}
      {error && <span role="alert" className="text-xs text-rose-700">{error}</span>}
    </span>
  );
}

// A stopped claim: pick up where it stopped.
export function ResumeButton({ claimId }: { claimId: string }) {
  const { busy, error, run } = useAction();
  return (
    <span className="flex flex-wrap items-center gap-2">
      <button type="button" className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
        disabled={busy} onClick={() => run(() => resumeClaim(claimId))}>{busy ? "Resuming…" : "Resume claim"}</button>
      {error && <span role="alert" className="text-xs text-rose-700">{error}</span>}
    </span>
  );
}

// Admin only: delete the whole claim, then back to the claims list.
export function DeleteClaimButton({ claimId }: { claimId: string }) {
  return <ConfirmDelete label="Delete claim" question="Delete this claim and all its pages? This cannot be undone."
    action={() => deleteClaim(claimId)} goTo="/bundles" />;
}

// Admin only: delete one page; the claim re-runs on the pages left.
export function DeletePageButton({ claimId, docId }: { claimId: string; docId: string }) {
  return <ConfirmDelete small label="Delete" question="Delete this page and re-run the claim?"
    action={() => deleteDocument(claimId, docId)} />;
}

// Re-upload one page: a hidden file input behind a button. The claim re-runs; pages already
// read come from the model cache, so only the new page costs model time.
export function ReplacePageButton({ claimId, docId, fileName, label = "Re-upload" }: {
  claimId: string; docId: string; fileName: string; label?: string;
}) {
  const { busy, error, run } = useAction();
  const input = useRef<HTMLInputElement>(null);
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button type="button" disabled={busy} onClick={() => input.current?.click()}
        className="rounded-md border border-brand-200 bg-white px-2 py-1 text-[11px] font-semibold text-brand-700 hover:bg-brand-50 disabled:opacity-50">
        {busy ? "Uploading…" : label}
      </button>
      <input ref={input} type="file" accept={ACCEPT} className="sr-only" aria-label={`Replace ${fileName}`}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          if (!allowedFile(f.name)) return;
          run(() => replaceDocument(claimId, docId, f));
        }} />
      {error && <span role="alert" className="text-[11px] text-rose-700">{error}</span>}
    </span>
  );
}

export function ReplaceAllButton({ claimId }: { claimId: string }) {
  const { busy, error, run } = useAction();
  const input = useRef<HTMLInputElement>(null);
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button type="button" disabled={busy} onClick={() => input.current?.click()} className="btn-ghost border border-slate-200 px-3 py-1.5 text-xs">
        {busy ? "Uploading…" : "Re-upload all pages"}
      </button>
      <input ref={input} type="file" multiple accept={ACCEPT} className="sr-only" aria-label="Replace every page of the claim"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []).filter((f) => allowedFile(f.name)).slice(0, MAX_BATCH_FILES);
          e.target.value = "";
          if (files.length) run(() => replaceAllDocuments(claimId, files));
        }} />
      {error && <span role="alert" className="text-[11px] text-rose-700">{error}</span>}
    </span>
  );
}

// Human in the loop for a claim ON_HOLD. Every page is classified first; an unknown, blurry
// or failed page stops the claim before any page is read. A person re-uploads, restarts or stops.
export function HoldPanel({ claimId, hold, waitingAtHold }: { claimId: string; hold: ClaimHold; waitingAtHold: boolean }) {
  const { busy, error, run } = useAction();
  const [note, setNote] = useState("");
  const ingest = hold.stage === "ingest";
  const atClassify = ingest && hold.step !== "extract";
  return (
    <section className="rounded-xl border border-orange-300 bg-orange-50 px-4 py-4 text-orange-950" role="status" aria-labelledby="hold-h">
      <h2 id="hold-h" className="text-sm font-semibold">
        On hold: {atClassify ? "a page is unknown or blurry" : ingest ? "a page could not be read" : "an agent did not complete"}. A person must decide.
      </h2>
      <p className="mt-0.5 text-xs">
        {ingest
          ? `${atClassify ? "No page was read yet and the" : "The"} claim agents (KYC, claims, policy, fraud) did not run. Re-upload the page, re-upload every page, restart the claim from the first page, or stop the claim.`
          : "The claim was not decided on complete information. Re-run it, re-upload the pages, stop it, or record a decision in Human review."}
      </p>
      <ul className="mt-3 space-y-2">
        {(hold.pages ?? []).map((p) => (
          <li key={p.doc_id} className="flex flex-wrap items-start justify-between gap-2 rounded-lg bg-white/80 px-3 py-2 text-xs ring-1 ring-orange-200">
            <span className="min-w-0 flex-1">
              <span className="font-semibold">{p.file_name}</span>
              <span className="block text-orange-900/80">{p.reason}</span>
            </span>
            <ReplacePageButton claimId={claimId} docId={p.doc_id} fileName={p.file_name} label="Re-upload this page" />
          </li>
        ))}
        {!ingest && hold.reasons.map((r) => (
          <li key={r} className="rounded-lg bg-white/80 px-3 py-2 text-xs ring-1 ring-orange-200">{r}</li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        {ingest && waitingAtHold && (
          <label className="min-w-[220px] flex-1 text-xs">
            <span className="mb-1 block font-medium">Note (optional, audited)</span>
            <input className="input py-1.5 text-xs" value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Why stop?" maxLength={1000} />
          </label>
        )}
        {/* A page-reading hold is never continued past: the claim restarts from the first page */}
        <button type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={busy}
          onClick={() => run(() => rerunClaim(claimId))}>{ingest ? "Restart claim" : "Re-run claim"}</button>
        <ReplaceAllButton claimId={claimId} />
        <button type="button" disabled={busy}
          className="rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50"
          onClick={() => run(() => (ingest && waitingAtHold ? resolveHold(claimId, "stop", note.trim() || undefined) : stopClaim(claimId, note.trim() || undefined)))}>
          Stop claim
        </button>
      </div>
      {error && <p role="alert" className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>}
    </section>
  );
}
