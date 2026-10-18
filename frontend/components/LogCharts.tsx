"use client";

import { useState } from "react";

// Charts for the System logs and Audit log analytics tabs. Marks follow one spec: columns <= 24px with a
// 4px rounded top and square base, a 2px surface gap between stacked segments, hairline
// solid gridlines, one y-axis, text in ink (never in the series colour), a legend for the
// stacked series, a hover tooltip on every mark, and a table view of the same numbers.

export type Level = "ERROR" | "WARNING" | "INFO" | "DEBUG";
export interface VolumePoint { bucket: string; ERROR: number; WARNING: number; INFO: number; DEBUG: number }
export type Point = { bucket: string } & Record<string, number | string>;
/** One stacked series. Listed top of the stack first (the legend order); drawn bottom-up. */
export interface Series { key: string; label: string; color: string; icon?: string }

const LEVEL_SERIES: { key: Level; label: string; icon: string }[] = [
  { key: "ERROR", label: "Error", icon: "✕" },
  { key: "WARNING", label: "Warning", icon: "!" },
  { key: "INFO", label: "Info", icon: "i" },
  { key: "DEBUG", label: "Debug", icon: "·" },
];

export function VolumeChart({ data, unit, colors, totals }: {
  data: VolumePoint[]; unit: "hour" | "day"; colors: Record<Level, string>; totals: Record<Level, number>;
}) {
  return <StackedColumns data={data as unknown as Point[]} unit={unit} totals={totals} label="Log records, stacked by level"
    series={LEVEL_SERIES.map((l) => ({ ...l, color: colors[l.key] }))} />;
}

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v) ?? v;
}

function compact(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K` : String(n);
}

function shortBucket(b: string, unit: "hour" | "day") {
  const d = new Date(unit === "hour" ? b.replace(" ", "T") + ":00" : b + "T00:00:00");
  return unit === "hour"
    ? d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric" })
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function StackedColumns({ data, unit, series, totals, label }: {
  data: Point[]; unit: "hour" | "day"; series: Series[]; totals: Record<string, number>; label: string;
}) {
  const LEGEND = series;
  const STACK = [...series].reverse().map((s) => s.key);                   // bottom -> top
  const v = (d: Point, k: string) => Number(d[k] ?? 0);
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const W = 1200, H = 260, padL = 44, padR = 8, padT = 12, padB = 28;    // ~1:1 on a desktop card, so text stays at its real size
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = niceMax(Math.max(1, ...data.map((d) => STACK.reduce((s, l) => s + v(d, l), 0))));
  const slot = plotW / Math.max(1, data.length);
  const bw = Math.min(24, Math.max(2, slot * 0.6));
  const y = (v: number) => padT + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  const labelEvery = Math.max(1, Math.ceil(data.length / 6));
  const h = hover != null ? data[hover] : null;

  return (
    <div className="viz-root">
      <ul className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-700" aria-label="Series">
        {LEGEND.map((l) => (
          <li key={l.key} className="flex items-center gap-1.5">
            <span className="grid h-4 w-4 place-items-center rounded text-[10px] font-bold text-white" style={{ background: l.color }} aria-hidden>{l.icon ?? ""}</span>
            {l.label} <span className="font-semibold text-slate-900">{(totals[l.key] ?? 0).toLocaleString()}</span>
          </li>
        ))}
      </ul>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`${label}, per ${unit}`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="#e7e6e2" strokeWidth={1} />
              <text x={padL - 6} y={y(t) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">{compact(t)}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const x = padL + i * slot + (slot - bw) / 2;
            let acc = 0;
            const segs = STACK.filter((l) => v(d, l) > 0);
            return (
              <g key={d.bucket} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                 onFocus={() => setHover(i)} onBlur={() => setHover(null)} tabIndex={0}
                 aria-label={`${shortBucket(d.bucket, unit)}: ${LEGEND.map((l) => `${l.label} ${v(d, l.key)}`).join(", ")}`}>
                <rect x={padL + i * slot} y={padT} width={slot} height={plotH} fill="transparent" />{/* hit target = the whole slot */}
                {segs.map((l, k) => {
                  const top = y(acc + v(d, l));
                  const bottom = y(acc);
                  acc += v(d, l);
                  const gap = k < segs.length - 1 ? 2 : 0;           // 2px surface gap between segments
                  const height = Math.max(1, bottom - top - gap);
                  const isTop = k === segs.length - 1;
                  const r = isTop ? Math.min(4, bw / 2, height) : 0;
                  return isTop ? (
                    <path key={l} fill={series.find((x) => x.key === l)!.color} opacity={hover == null || hover === i ? 1 : 0.55}
                      d={`M${x},${top + height} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${top + height} Z`} />
                  ) : (
                    <rect key={l} x={x} y={top + gap} width={bw} height={height} fill={series.find((x) => x.key === l)!.color} opacity={hover == null || hover === i ? 1 : 0.55} />
                  );
                })}
                {i % labelEvery === 0 && (
                  <text x={padL + i * slot + slot / 2} y={H - 8} textAnchor="middle" className="fill-slate-500 text-[10px]">{shortBucket(d.bucket, unit)}</text>
                )}
              </g>
            );
          })}
          <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} stroke="#c9c8c3" strokeWidth={1} />
        </svg>
        {h && hover != null && (
          <div className="pointer-events-none absolute top-1 z-10 min-w-36 rounded-lg bg-white px-3 py-2 text-xs shadow-lg ring-1 ring-slate-200"
            style={{ left: `min(calc(${((padL + hover * slot + slot / 2) / W) * 100}% + 8px), calc(100% - 10rem))` }}>
            <p className="mb-1 font-semibold text-slate-900">{shortBucket(h.bucket, unit)}</p>
            {LEGEND.map((l) => (
              <p key={l.key} className="flex items-center justify-between gap-4 text-slate-600">
                <span className="flex items-center gap-1.5"><span className="h-0.5 w-3 rounded" style={{ background: l.color }} />{l.label}</span>
                <span className="font-semibold tabular-nums text-slate-900">{v(h, l.key).toLocaleString()}</span>
              </p>
            ))}
          </div>
        )}
      </div>
      <button type="button" onClick={() => setTable((t) => !t)} className="mt-2 text-xs font-medium text-brand-700 hover:underline">
        {table ? "Hide data table" : "Show data table"}
      </button>
      {table && (
        <div className="mt-2 max-h-64 overflow-auto rounded-lg ring-1 ring-slate-200">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-50"><tr><th className="th">{unit === "hour" ? "Hour" : "Day"}</th>{LEGEND.map((l) => <th key={l.key} className="th text-right">{l.label}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-100">
              {data.filter((d) => STACK.some((l) => v(d, l) > 0)).map((d) => (
                <tr key={d.bucket}><td className="td py-1.5">{shortBucket(d.bucket, unit)}</td>{LEGEND.map((l) => <td key={l.key} className="td py-1.5 text-right tabular-nums">{v(d, l.key)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Horizontal bars for one measure across named rows. One hue unless the rows are statuses.
export function HBars({ rows, color, colors, label }: {
  rows: { name: string; value: number; note?: string }[]; color?: string; colors?: Record<string, string>; label: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (rows.length === 0) return <p className="py-6 text-center text-xs text-slate-500">No data in this range.</p>;
  return (
    <ul className="space-y-2" aria-label={label}>
      {rows.map((r) => (
        <li key={r.name} className="grid grid-cols-[minmax(0,9rem)_1fr] items-center gap-3 text-xs"
          onMouseEnter={() => setHover(r.name)} onMouseLeave={() => setHover(null)} title={`${r.name}: ${r.value.toLocaleString()}${r.note ? ` (${r.note})` : ""}`}>
          <span className="truncate text-slate-600" title={r.name}>{r.name}</span>
          <span className="flex items-center gap-2">
            <span className="h-3.5 rounded-r-[4px] transition-all duration-500" aria-hidden
              style={{ width: `${Math.max(1.5, (r.value / max) * 85)}%`, background: colors?.[r.name] ?? color,
                       opacity: hover == null || hover === r.name ? 1 : 0.55, minWidth: r.value ? 3 : 0 }} />
            <span className="shrink-0 font-semibold tabular-nums text-slate-900">{r.value.toLocaleString()}</span>
            {r.note && <span className="shrink-0 text-slate-400">{r.note}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}
