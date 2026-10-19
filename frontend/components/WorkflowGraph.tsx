"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

// The claim's agent graph drawn like a LangGraph diagram: nodes on a grid, edges as arrows
// between them. Nodes are placed by the server; this component measures where they ended up
// and draws the edges in an SVG layer under them, so the arrows follow the cards at any width.
// Where the graph does not fit (phones, tablets, narrow windows) the same nodes are stacked
// step by step, top to bottom, with parallel steps side by side.

export type EdgeKind = "taken" | "active" | "hold" | "error" | "idle" | "unused";

export interface GraphNode {
  id: string;
  col: string;                 // CSS grid-column, e.g. "2" or "2 / span 2"
  row: number;
  content: React.ReactNode;
  fill?: boolean;              // stretch to the cell (agent cards); otherwise sized to content and centred
  step?: number;               // order in the stacked layout; nodes with the same step sit side by side
}

export interface GraphEdge {
  from: string;
  to: string;                  // same as `from` draws a self-loop (an agent that runs once per page)
  kind: EdgeKind;
  conditional?: boolean;       // a LangGraph conditional edge: dashed until it is taken
  label?: string;
  offset?: number;             // shifts a vertical edge sideways, to separate an edge from its reverse
}

// The stacked layout is used below this share of the graph's minimum width.
const STACK_BELOW = 0.8;
const KIND_RANK: EdgeKind[] = ["active", "hold", "error", "taken", "idle", "unused"];
const FLOW_CSS = `@keyframes wf-flow { to { stroke-dashoffset: -14 } }
  @media (prefers-reduced-motion: no-preference) { .wf-active { animation: wf-flow .7s linear infinite } }`;

const COLOR: Record<EdgeKind, string> = {
  taken: "#10b981", active: "#0ea5e9", hold: "#f97316", error: "#f43f5e", idle: "#94a3b8", unused: "#cbd5e1",
};

interface Rect { l: number; t: number; r: number; b: number }
interface Drawn { key: string; d: string; kind: EdgeKind; dashed: boolean; label?: string; lx: number; ly: number; sx?: number; sy?: number }

// Horizontal edges meet a node at its header (n8n style), so they stay put when the node opens.
const HEADER_Y = 30;
const anchorY = (r: Rect) => r.t + Math.min((r.b - r.t) / 2, HEADER_Y);

function route(e: GraphEdge, a: Rect, b: Rect): Omit<Drawn, "key" | "kind" | "dashed" | "label"> {
  if (e.from === e.to) {                                   // self-loop over the top of the node
    const cx = (a.l + a.r) / 2;
    return { d: `M ${cx + 22} ${a.t} C ${cx + 22} ${a.t - 30}, ${cx - 22} ${a.t - 30}, ${cx - 22} ${a.t}`, lx: cx, ly: a.t - 30 };
  }
  const left = Math.max(a.l, b.l), right = Math.min(a.r, b.r);
  if (right - left > 12) {                                 // one above the other: a straight vertical edge
    const x = (left + right) / 2 + (e.offset ?? 0);
    const down = b.t >= a.b - 1;
    const y1 = down ? a.b : a.t, y2 = down ? b.t : b.b;
    return { d: `M ${x} ${y1} L ${x} ${y2}`, lx: x, ly: (y1 + y2) / 2, sx: x, sy: y1 };
  }
  const forward = b.l >= a.r - 1;                          // side by side: a horizontal S-curve
  const x1 = forward ? a.r : a.l, x2 = forward ? b.l : b.r;
  const y1 = anchorY(a), y2 = anchorY(b);
  const dx = Math.max(18, Math.abs(x2 - x1) / 2) * (forward ? 1 : -1);
  // A short edge has no room for its label between the nodes: put the label in the gap above them.
  const ly = Math.abs(x2 - x1) < 90 ? Math.min(a.t, b.t) - 12 : (y1 + y2) / 2;
  return { d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`, lx: (x1 + x2) / 2, ly, sx: x1, sy: y1 };
}

export default function WorkflowGraph({ nodes, edges, columns, minWidth = 1180, label, title }: {
  nodes: GraphNode[]; edges: GraphEdge[]; columns: string; minWidth?: number; label: string; title?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const outer = useRef<HTMLDivElement>(null);
  const [allOpen, setAllOpen] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [choice, setChoice] = useState<"graph" | "steps" | null>(null);   // null: by width
  const stacked = choice ? choice === "steps" : narrow;

  useLayoutEffect(() => {
    const el = outer.current;
    if (!el) return;
    const check = () => setNarrow(el.clientWidth < minWidth * STACK_BELOW);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [minWidth]);

  // Nodes fold their points under their own toggle (<details data-points>); this keeps the
  // "Expand all" switch in step with them.
  const panels = () => Array.from(box.current?.querySelectorAll<HTMLDetailsElement>("details[data-points]") ?? []);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const sync = () => setAllOpen(panels().length > 0 && panels().every((d) => d.open));
    sync();
    el.addEventListener("toggle", sync, true);              // "toggle" does not bubble: listen while capturing
    return () => el.removeEventListener("toggle", sync, true);
  }, [stacked]);
  const toggleAll = () => {
    const open = !allOpen;
    panels().forEach((d) => { d.open = open; });
    setAllOpen(open);
  };
  const refs = useRef(new Map<string, HTMLDivElement>());
  const [drawn, setDrawn] = useState<{ w: number; h: number; edges: Drawn[] }>({ w: 0, h: 0, edges: [] });

  useLayoutEffect(() => {
    const el = box.current;
    if (!el || stacked) return;
    const measure = () => {
      const o = el.getBoundingClientRect();
      const rect = (id: string): Rect | null => {
        const n = refs.current.get(id);
        if (!n) return null;
        const r = n.getBoundingClientRect();
        return { l: r.left - o.left, t: r.top - o.top, r: r.right - o.left, b: r.bottom - o.top };
      };
      const out: Drawn[] = [];
      for (const e of edges) {
        const a = rect(e.from), b = rect(e.to);
        if (!a || !b) continue;
        out.push({ key: `${e.from}-${e.to}`, kind: e.kind, dashed: e.kind === "unused" || e.kind === "active" || (!!e.conditional && e.kind === "idle"),
                   label: e.label, ...route(e, a, b) });
      }
      const next = { w: el.scrollWidth, h: el.scrollHeight, edges: out };
      setDrawn((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    refs.current.forEach((n) => ro.observe(n));
    return () => ro.disconnect();
  }, [nodes, edges, stacked]);

  return (
    <div ref={outer} className="card overflow-hidden" role="figure" aria-label={label}>
      <style>{FLOW_CSS}</style>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <h2 className="text-sm font-semibold text-slate-900">{title ?? label}</h2>
          <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500" aria-label="Edge colours">
            {([["taken", "taken"], ["active", "running"], ["hold", "waiting for a person"], ["error", "failed"], ["unused", "not taken"]] as const).map(([k, text]) => (
              <li key={k} className="flex items-center gap-1.5">
                <svg width="18" height="6" aria-hidden><line x1="1" y1="3" x2="17" y2="3" stroke={COLOR[k]} strokeWidth="2" strokeDasharray={k === "unused" || k === "active" ? "4 3" : undefined} /></svg>
                {text}
              </li>
            ))}
          </ul>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg p-0.5 text-xs font-semibold ring-1 ring-slate-200" role="group" aria-label="Layout">
            {(["graph", "steps"] as const).map((m) => (
              <button key={m} type="button" onClick={() => setChoice(m)} aria-pressed={stacked === (m === "steps")}
                className={`rounded-md px-2.5 py-1 capitalize transition ${stacked === (m === "steps") ? "bg-slate-900 text-white" : "text-slate-500 hover:text-slate-800"}`}>
                {m}
              </button>
            ))}
          </div>
          <button type="button" onClick={toggleAll} aria-pressed={allOpen}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-brand-700 ring-1 ring-brand-200 transition hover:bg-brand-50">
            <svg viewBox="0 0 24 24" className={`h-4 w-4 transition ${allOpen ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
            </svg>
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
        </div>
      </div>
      {stacked ? <div ref={box}><Stacked nodes={nodes} edges={edges} /></div> : (
      <div className="overflow-x-auto overflow-y-hidden p-5">
      <div ref={box} className="relative grid items-start gap-x-7 gap-y-11 pt-4" style={{ gridTemplateColumns: columns, minWidth }}>
        <svg className="pointer-events-none absolute inset-0" width={drawn.w} height={drawn.h} aria-hidden>
          <defs>
            {(Object.keys(COLOR) as EdgeKind[]).map((k) => (
              <marker key={k} id={`wf-arrow-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill={COLOR[k]} />
              </marker>
            ))}
          </defs>
          {drawn.edges.map((e) => (
            <path key={e.key} d={e.d} fill="none" stroke={COLOR[e.kind]} markerEnd={`url(#wf-arrow-${e.kind})`}
              strokeWidth={e.kind === "unused" ? 1.25 : 2} strokeDasharray={e.dashed ? "5 4" : undefined}
              strokeLinecap="round" className={e.kind === "active" ? "wf-active" : undefined} />
          ))}
          {drawn.edges.filter((e) => e.sx != null).map((e) => (     // the connection point where the edge leaves its node
            <circle key={`${e.key}-dot`} cx={e.sx} cy={e.sy} r={3.5} fill="#fff" stroke={COLOR[e.kind]} strokeWidth={1.75} />
          ))}
        </svg>
        {drawn.edges.filter((e) => e.label).map((e) => (
          <span key={`${e.key}-l`} className={`pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-full bg-white px-1.5 text-[10px] font-medium ring-1 ${
            e.kind === "unused" ? "text-slate-400 ring-slate-200" : "text-slate-600 ring-slate-300"}`} style={{ left: e.lx, top: e.ly }}>
            {e.label}
          </span>
        ))}
        {nodes.map((n) => (
          <div key={n.id} ref={(el) => { if (el) refs.current.set(n.id, el); else refs.current.delete(n.id); }}
            className={`relative z-[1] self-start ${n.fill ? "" : "justify-self-center"}`} style={{ gridColumn: n.col, gridRow: n.row }}>
            {n.content}
          </div>
        ))}
      </div>
      </div>
      )}
    </div>
  );
}

// The narrow layout: one row per step, top to bottom; nodes of one step (parallel agents) side
// by side where there is room. The arrow into a row takes the colour of the strongest edge
// entering it from an earlier row, and carries those edges' labels.
function Stacked({ nodes, edges }: { nodes: GraphNode[]; edges: GraphEdge[] }) {
  const stepOf = (n: GraphNode) => n.step ?? parseInt(n.col, 10);
  const steps = [...new Set(nodes.map(stepOf))].sort((a, b) => a - b);
  const rows = steps.map((st) => nodes.filter((n) => stepOf(n) === st).sort((a, b) => a.row - b.row));
  const where = new Map(nodes.map((n) => [n.id, stepOf(n)]));
  return (
    <ol className="p-3 sm:p-5">
      {rows.map((row, i) => {
        const here = new Set(row.map((n) => n.id));
        const into = edges.filter((e) => here.has(e.to) && !here.has(e.from) && (where.get(e.from) ?? 0) < stepOf(row[0]));
        const kind = KIND_RANK.find((k) => into.some((e) => e.kind === k)) ?? "idle";
        const labels = [...new Set(into.filter((e) => e.label && e.kind !== "unused").map((e) => e.label!))];
        return (
          <li key={row.map((n) => n.id).join("-")}>
            {i > 0 && (
              <div className="flex items-center justify-center gap-2 py-1" aria-hidden>
                <svg width="14" height="26" viewBox="0 0 14 26" className="shrink-0">
                  <line x1="7" y1="0" x2="7" y2="19" stroke={COLOR[kind]} strokeWidth="2"
                    strokeDasharray={kind === "unused" || kind === "active" ? "4 3" : undefined} className={kind === "active" ? "wf-active" : undefined} />
                  <path d="M 2 18 L 7 25 L 12 18 z" fill={COLOR[kind]} />
                </svg>
                {labels.length > 0 && <span className="rounded-full bg-white px-1.5 text-[10px] font-medium text-slate-500 ring-1 ring-slate-200">{labels.join(" · ")}</span>}
              </div>
            )}
            <div className={`grid gap-3 ${row.length > 1 ? "sm:grid-cols-2" : ""} ${row.length > 2 ? "lg:grid-cols-3" : ""}`}>
              {row.map((n) => (
                <div key={n.id} className={`min-w-0 [&>div]:!w-full ${n.fill ? "" : "flex justify-center [&>span]:mt-0"}`}>{n.content}</div>
              ))}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
