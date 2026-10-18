"use client";

import { useState } from "react";

// Per-agent output for one case. Each agent is a collapsible section. The header always
// shows the agent's status and a one-line summary, so the panel can be scanned with
// everything closed.

type Obj = Record<string, unknown>;

export interface AgentItem {
  key: string;
  title: string;
  summary: string;
  status: "ran" | "error" | "not run";
  data: Obj | null;
}

export interface TraceItem {
  agent: string;
  status: string;
  duration_ms: number;
  note: string;
}

const STATUS_STYLE: Record<AgentItem["status"], string> = {
  ran: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  error: "bg-rose-50 text-rose-700 ring-rose-600/20",
  "not run": "bg-slate-100 text-slate-500 ring-slate-500/20",
};

function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-0.5 text-sm font-medium text-slate-900">{value === undefined || value === null ? "—" : String(value)}</p>
    </div>
  );
}

function Flags({ items }: { items: unknown }) {
  if (!Array.isArray(items) || items.length === 0) {
    return <p className="mt-2 text-sm text-slate-400">None</p>;
  }
  return (
    <ul className="mt-2 space-y-1.5">
      {items.map((x, i) => (
        <li key={i} className="flex gap-2 text-sm text-slate-700">
          <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-slate-400" />
          <span>{typeof x === "string" ? x : JSON.stringify(x)}</span>
        </li>
      ))}
    </ul>
  );
}

function Body({ item, trace }: { item: AgentItem; trace: TraceItem[] }) {
  if (item.key === "trace") {
    if (trace.length === 0) return <p className="text-sm text-slate-400">No trace recorded.</p>;
    return (
      <ol className="relative space-y-4 border-l border-slate-200 pl-6">
        {[...trace].reverse().map((t, i) => (               // newest first
          <li key={i}>
            <span className={`absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-white ${t.status === "ok" ? "bg-emerald-500" : "bg-rose-500"}`} />
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm font-medium text-slate-900">{t.agent}</p>
              <p className="text-xs tabular-nums text-slate-500">{t.duration_ms} ms</p>
            </div>
            {t.note && <p className="mt-0.5 text-xs text-rose-600">{t.note}</p>}
          </li>
        ))}
      </ol>
    );
  }

  const d = item.data;
  if (d == null) return <p className="text-sm text-slate-400">This check did not apply to this document type.</p>;
  if (d.error) return <p className="rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">{String(d.error)}</p>;

  switch (item.key) {
    case "preprocessor": {
      const fields = (d.fields ?? {}) as Record<string, string>;
      return (
        <>
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Blur variance" value={d.blur_variance} />
            <Stat label="Sharpness" value={d.is_blurry ? "Blurry" : "Sharp"} />
          </div>
          <p className="mt-4 text-xs font-medium uppercase tracking-wider text-slate-500">Read fields</p>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            {Object.entries(fields).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-slate-500">{k.replaceAll("_", " ")}</dt>
                <dd className="font-medium text-slate-900">{v}</dd>
              </div>
            ))}
          </dl>
          <details className="group mt-4">
            <summary className="cursor-pointer text-sm font-medium text-brand-700">Raw text</summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-700 ring-1 ring-slate-200">{String(d.raw_text ?? "")}</pre>
          </details>
        </>
      );
    }
    case "classifier":
      return (
        <div className="grid grid-cols-3 gap-4">
          <Stat label="Type" value={String(d.doc_type ?? "—").replaceAll("_", " ")} />
          <Stat label="Confidence" value={d.confidence} />
          <Stat label="Source" value={d.source} />
        </div>
      );
    case "kyc":
      return (
        <>
          <div className="flex items-center gap-4">
            <Stat label="Passed" value={d.kyc_passed ? "Yes" : "No"} />
            <Stat label="Confidence" value={d.confidence} />
          </div>
          <Flags items={d.flags} />
        </>
      );
    case "claims":
      return (
        <>
          <Stat label="Schema valid" value={d.schema_valid ? "Yes" : "No"} />
          <pre className="mt-4 max-h-64 overflow-auto rounded-xl bg-slate-50 p-3 font-mono text-xs text-slate-700 ring-1 ring-slate-200">{JSON.stringify(d.extracted_fields ?? {}, null, 2)}</pre>
          <Flags items={d.validation_errors} />
        </>
      );
    case "policy":
      return (
        <>
          <div className="grid grid-cols-3 gap-4">
            <Stat label="Covered" value={d.covered ? "Yes" : "No"} />
            <Stat label="Coverage" value={d.coverage_percentage !== undefined ? `${d.coverage_percentage}%` : undefined} />
            <Stat label="Confidence" value={d.confidence} />
          </div>
          <p className="mt-4 text-xs font-medium uppercase tracking-wider text-slate-500">Clause</p>
          <p className="mt-1 text-sm leading-relaxed text-slate-700">{String(d.policy_clause ?? "—")}</p>
          <Flags items={d.exclusions} />
        </>
      );
    case "fraud":
      return (
        <>
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Score" value={d.fraud_score} />
            <Stat label="Risk" value={d.risk_level} />
          </div>
          <Flags items={(d.anomalies as unknown[] | undefined)?.map((a) => {
            const x = a as Obj;
            return `${String(x.type)} (severity ${String(x.severity)}): ${String(x.detail)}`;
          })} />
        </>
      );
    case "orchestrator":
      return (
        <>
          <div className="grid grid-cols-3 gap-4">
            <Stat label="Decision" value={d.decision} />
            <Stat label="Confidence" value={d.confidence} />
            <Stat label="Needs review" value={d.needs_review ? "Yes" : "No"} />
          </div>
          <p className="mt-4 text-xs font-medium uppercase tracking-wider text-slate-500">Justification</p>
          <p className="mt-1 text-sm leading-relaxed text-slate-700">{String(d.justification ?? "—")}</p>
        </>
      );
    default:
      return <pre className="max-h-64 overflow-auto rounded-xl bg-slate-50 p-3 font-mono text-xs text-slate-700">{JSON.stringify(d, null, 2)}</pre>;
  }
}

export default function AgentPanel({ agents, trace }: { agents: AgentItem[]; trace: TraceItem[] }) {
  const items: AgentItem[] = [
    ...agents,
    { key: "trace", title: "Agent trace", summary: `${trace.length} steps`, status: "ran", data: {} },
  ];
  // Errors and the final decision open by default; everything else starts collapsed.
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(items.filter((i) => i.status === "error" || i.key === "orchestrator").map((i) => i.key)),
  );
  const allOpen = open.size === items.length;

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section className="card animate-fade-up overflow-hidden" aria-label="Agent outputs">
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5 sm:px-6">
        <h2 className="text-sm font-semibold text-slate-900">Agent outputs</h2>
        <button type="button" className="btn-ghost px-2.5 py-1.5 text-xs text-brand-700"
          onClick={() => setOpen(allOpen ? new Set() : new Set(items.map((i) => i.key)))}>
          {allOpen ? "Collapse all" : "Expand all"}
        </button>
      </div>

      <ul className="divide-y divide-slate-100">
        {items.map((item, i) => {
          const isOpen = open.has(item.key);
          const panelId = `agent-panel-${item.key}`;
          return (
            <li key={item.key} className="animate-fade-up" style={{ animationDelay: `${Math.min(i, 10) * 40}ms` }}>
              <button type="button" aria-expanded={isOpen} aria-controls={panelId} onClick={() => toggle(item.key)}
                className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition-colors duration-150 hover:bg-slate-50/80 sm:px-6">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600">{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-slate-900">{item.title}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${STATUS_STYLE[item.status]}`}>{item.status}</span>
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-slate-500">{item.summary}</span>
                </span>
                <svg className={`h-4 w-4 shrink-0 text-slate-400 transition-transform duration-300 ${isOpen ? "rotate-180" : ""}`}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
                </svg>
              </button>
              <div id={panelId} hidden={!isOpen} className="px-5 pb-5 sm:px-6">
                <Body item={item} trace={trace} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
