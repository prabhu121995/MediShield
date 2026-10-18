"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { askPolicy, ClinicalFit, CodeVerdict, PolicyAnswer, PolicyCodeCheck } from "@/lib/api";

// Policy assistant. Each answer is grounded in the indexed policy clauses, and every
// procedure code in the question gets a verdict card with its cross-check result.

type Message =
  | { id: number; role: "user"; content: string }
  | { id: number; role: "assistant"; content: string; result?: PolicyAnswer; error?: boolean; question?: string };

const SUGGESTIONS = [
  "Is CPT 17000 covered?",
  "Check codes 99213 and 17000 together",
  "What is excluded under the gold plan?",
];

const VERDICT: Record<CodeVerdict, { label: string; tone: string; icon: string }> = {
  covered: {
    label: "Covered",
    tone: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    icon: "M5 13l4 4L19 7",
  },
  excluded: {
    label: "Excluded",
    tone: "bg-rose-50 text-rose-700 ring-rose-600/20",
    icon: "M6 18L18 6M6 6l12 12",
  },
  not_in_policy: {
    label: "Not in policy",
    tone: "bg-amber-50 text-amber-800 ring-amber-600/25",
    icon: "M12 9v4m0 4h.01M10.3 3.9L2.6 17a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  },
};

const FIT: Record<ClinicalFit, { label: string; tone: string }> = {
  supported: { label: "Supported", tone: "bg-emerald-50 text-emerald-700 ring-emerald-600/20" },
  doubtful: { label: "Doubtful", tone: "bg-amber-50 text-amber-800 ring-amber-600/25" },
  not_assessed: { label: "Not assessed", tone: "bg-slate-100 text-slate-600 ring-slate-500/20" },
};

function CodeCard({ check }: { check: PolicyCodeCheck }) {
  const v = VERDICT[check.verdict] ?? VERDICT.not_in_policy;
  const matched = check.cross_check === "matched";
  return (
    <div className="rounded-xl bg-white p-3.5 ring-1 ring-slate-200 animate-fade-up">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="rounded-lg bg-slate-900 px-2.5 py-1 font-mono text-xs font-semibold tracking-wide text-white">CPT {check.code}</span>
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${v.tone}`}>
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.4} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d={v.icon} />
          </svg>
          {v.label}
        </span>
      </div>
      {check.reason && <p className="mt-2 text-sm leading-relaxed text-slate-700">{check.reason}</p>}
      <div className="mt-2.5 rounded-lg bg-slate-50 px-3 py-2 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-slate-600">Clinical fit</span>
          <span className={`rounded-full px-2 py-0.5 font-semibold ring-1 ring-inset ${FIT[check.clinical_fit].tone}`}>
            {FIT[check.clinical_fit].label}
          </span>
        </div>
        {check.clinical_note && <p className="mt-1 leading-relaxed text-slate-600">{check.clinical_note}</p>}
        {check.next_step && (
          <p className="mt-1.5 leading-relaxed text-slate-700"><span className="font-semibold">Next: </span>{check.next_step}</p>
        )}
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs">
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${
          matched ? "bg-brand-50 text-brand-700" : "bg-slate-100 text-slate-600"}`}>
          {matched ? "✓ Clause found in policy" : "○ No clause names this code"}
        </span>
        {check.source && <span className="truncate text-slate-500">{check.source}</span>}
      </div>
      {check.clause && (
        <details className="group mt-2.5">
          <summary className="cursor-pointer text-xs font-medium text-brand-700">Show clause</summary>
          <blockquote className="mt-2 max-h-40 overflow-auto rounded-lg border-l-2 border-brand-500/60 bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">
            {check.clause}
          </blockquote>
        </details>
      )}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked: nothing to do */
    }
  }
  return (
    <button type="button" onClick={copy} aria-label="Copy answer"
      className="rounded-lg px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800">
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function Bubble({ message, onRetry }: { message: Message; onRetry: (q: string) => void }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end animate-fade-up">
        <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-sm leading-relaxed text-white shadow-sm">
          {message.content}
        </p>
      </div>
    );
  }
  const r = message.result;
  return (
    <div className="flex gap-3 animate-fade-up">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-indigo-600 text-xs font-bold text-white shadow-sm">AI</span>
      <div className="min-w-0 max-w-[88%] space-y-3">
        <div className={`rounded-2xl rounded-tl-md px-4 py-2.5 text-sm leading-relaxed shadow-sm ring-1 ${
          message.error ? "bg-rose-50 text-rose-800 ring-rose-200" : "bg-white text-slate-800 ring-slate-200"}`}>
          <p className="whitespace-pre-wrap">{message.content}</p>
          {message.error && message.question && (
            <button type="button" onClick={() => onRetry(message.question!)}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 text-xs font-semibold text-rose-700 ring-1 ring-rose-200 transition hover:bg-rose-100">
              ↻ Try again
            </button>
          )}
          {!message.error && <div className="-mb-1 mt-1 flex justify-end"><CopyButton text={message.content} /></div>}
        </div>

        {r && r.codes.length > 0 && (
          <div className="space-y-2.5">
            {r.codes.map((c) => <CodeCard key={c.code} check={c} />)}
          </div>
        )}

        {r && r.sources.length > 0 && (
          <details className="group rounded-xl bg-slate-50/80 px-3.5 py-2.5 ring-1 ring-slate-200/80">
            <summary className="cursor-pointer text-xs font-semibold text-slate-600">
              Sources ({r.sources.length})
            </summary>
            <ul className="mt-2 space-y-2">
              {r.sources.map((s, i) => (
                <li key={i} className="text-xs text-slate-600">
                  <span className="font-medium text-slate-800">{s.source}</span>
                  <span className="block leading-relaxed text-slate-500">{s.excerpt}…</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

export default function PolicyChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  async function send(text: string, retry = false) {
    const question = text.trim();
    if (!question || busy) return;

    // Send the last few turns as context. Error replies are left out.
    const history = messages
      .filter((m) => !(m.role === "assistant" && m.error))
      .slice(-12)
      .map((m) => ({ role: m.role, content: m.content }));

    if (!retry) setMessages((prev) => [...prev, { id: nextId.current++, role: "user", content: question }]);
    setInput("");
    setBusy(true);
    try {
      const result = await askPolicy(question, history);
      setMessages((prev) => [...prev, { id: nextId.current++, role: "assistant", content: result.answer, result }]);
    } catch (err) {
      setMessages((prev) => [...prev, {
        id: nextId.current++,
        role: "assistant",
        content: `Sorry, I could not answer that. ${(err as Error).message}`,
        error: true,
        question,
      }]);
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <section className="card animate-fade-up flex h-[38rem] flex-col overflow-hidden" aria-label="Policy assistant">
      <header className="flex items-center gap-3 border-b border-slate-200 bg-gradient-to-r from-brand-50 via-white to-indigo-50/60 px-5 py-4">
        <span className="grid h-10 w-10 place-items-center rounded-2xl bg-gradient-to-br from-brand-500 to-indigo-600 text-sm font-bold text-white shadow-md shadow-brand-500/30">AI</span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-900">Policy assistant</p>
          <p className="truncate text-xs text-slate-500">Answers come only from the indexed policies. Include CPT codes to verify them.</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {messages.length > 0 && (
            <button type="button" onClick={() => setMessages([])} disabled={busy}
              className="rounded-lg px-2 py-1 text-xs font-medium text-slate-500 transition hover:bg-white hover:text-slate-800 disabled:opacity-50">
              Clear
            </button>
          )}
          <span className="hidden items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-600/20 sm:inline-flex">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Grounded
          </span>
        </div>
      </header>

      <div ref={scroller} className="flex-1 space-y-5 overflow-y-auto bg-slate-50/60 px-4 py-5 sm:px-5" aria-live="polite">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center animate-fade-in">
            <span className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-brand-500 to-indigo-600 text-base font-bold text-white shadow-lg shadow-brand-500/30">AI</span>
            <div>
              <p className="text-sm font-semibold text-slate-900">Ask about coverage</p>
              <p className="mt-1 max-w-xs text-xs text-slate-500">Name a procedure code to cross-check it against the policy wording.</p>
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => send(s)}
                  className="rounded-full bg-white px-3 py-1.5 text-xs font-medium text-brand-700 ring-1 ring-slate-200 transition duration-200 hover:-translate-y-px hover:bg-brand-50 hover:shadow-sm">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => (
          <Bubble key={m.id} message={m} onRetry={(q) => {
            // Drop the failed reply before asking again, so the thread does not fill with errors.
            setMessages((prev) => prev.filter((x) => !(x.role === "assistant" && x.error && x.question === q)));
            send(q, true);
          }} />
        ))}

        {busy && (
          <div className="flex items-center gap-3 animate-fade-in">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-indigo-600 text-xs font-bold text-white">AI</span>
            <div className="flex items-center gap-1.5 rounded-2xl rounded-tl-md bg-white px-4 py-3 ring-1 ring-slate-200">
              {[0, 1, 2].map((i) => (
                <span key={i} className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500" style={{ animationDelay: `${i * 150}ms` }} />
              ))}
              <span className="ml-2 text-xs text-slate-500">Checking the policy…</span>
            </div>
          </div>
        )}
      </div>

      <form onSubmit={onSubmit} className="border-t border-slate-200 bg-white p-3 sm:p-4">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            maxLength={1000}
            placeholder="e.g. Is CPT 17000 covered under the silver plan?"
            aria-label="Ask the policy assistant"
            className="input max-h-32 min-h-[2.75rem] resize-none py-2.5"
          />
          <button type="submit" disabled={busy || !input.trim()} className="btn-primary h-11 shrink-0 px-4" aria-label="Send question">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 19V5m0 0l-6 6m6-6l6 6" />
            </svg>
            <span className="hidden sm:inline">Ask</span>
          </button>
        </div>
        <p className="mt-2 text-[11px] text-slate-400">
          Decision support for reviewers, not medical advice. Clinical fit is general medical reasoning; policy verdicts come only from the clauses. Enter to send.
        </p>
      </form>
    </section>
  );
}
