"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

// A delete in two clicks (Delete, then "Yes, delete"), like Stop claim. After it succeeds the
// page goes to `goTo`, or refreshes when there is none. Admin-only screens render it.
export default function ConfirmDelete({ label, question, action, goTo, small = false }: {
  label: string; question: string; action: () => Promise<unknown>; goTo?: string; small?: boolean;
}) {
  const router = useRouter();
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const size = small ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs";

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await action();
      if (goTo) router.push(goTo);
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
      setSure(false);
    } finally {
      setBusy(false);
    }
  }

  // Inside a <summary>, a click would also open or close the row.
  const keep = (e: React.MouseEvent) => { e.preventDefault(); e.stopPropagation(); };
  return (
    <span className="inline-flex flex-wrap items-center gap-2" onClick={keep}>
      {sure ? (
        <>
          <span className="text-xs text-slate-700">{question}</span>
          <button type="button" disabled={busy} onClick={remove}
            className={`rounded-lg bg-rose-600 font-semibold text-white hover:bg-rose-700 disabled:opacity-50 ${size}`}>
            {busy ? "Deleting…" : "Yes, delete"}
          </button>
          <button type="button" disabled={busy} className={`btn-ghost ${size}`} onClick={() => setSure(false)}>Cancel</button>
        </>
      ) : (
        <button type="button" onClick={() => setSure(true)}
          className={`rounded-lg border border-rose-200 bg-white font-semibold text-rose-700 hover:bg-rose-50 ${size}`}>
          {label}
        </button>
      )}
      {error && <span role="alert" className="text-xs text-rose-700">{error}</span>}
    </span>
  );
}
