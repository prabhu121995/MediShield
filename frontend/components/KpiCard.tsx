import Link from "next/link";

// One stat tile. Status tones (good / warning / critical) are used only where the number
// means a good or bad outcome, and every tile carries an icon and a text label, so colour
// never stands alone.
export type KpiTone = "brand" | "good" | "warning" | "critical" | "info";

const TONE: Record<KpiTone, { chip: string; bar: string; edge: string; glow: string }> = {
  brand: { chip: "bg-gradient-to-br from-brand-50 to-indigo-100 text-brand-700 ring-brand-500/20", bar: "bg-gradient-to-r from-brand-500 to-indigo-500",
           edge: "from-brand-500 via-indigo-500 to-sky-400", glow: "hover:shadow-brand-500/20" },
  good: { chip: "bg-gradient-to-br from-emerald-50 to-teal-100 text-emerald-700 ring-emerald-500/20", bar: "bg-gradient-to-r from-emerald-500 to-teal-500",
          edge: "from-emerald-500 via-teal-500 to-emerald-300", glow: "hover:shadow-emerald-500/20" },
  warning: { chip: "bg-gradient-to-br from-amber-50 to-orange-100 text-amber-800 ring-amber-500/25", bar: "bg-gradient-to-r from-amber-500 to-orange-500",
             edge: "from-amber-400 via-orange-500 to-amber-300", glow: "hover:shadow-amber-500/25" },
  critical: { chip: "bg-gradient-to-br from-rose-50 to-pink-100 text-rose-700 ring-rose-500/20", bar: "bg-gradient-to-r from-rose-500 to-pink-500",
              edge: "from-rose-500 via-pink-500 to-rose-300", glow: "hover:shadow-rose-500/20" },
  info: { chip: "bg-gradient-to-br from-sky-50 to-cyan-100 text-sky-700 ring-sky-500/20", bar: "bg-gradient-to-r from-sky-500 to-cyan-500",
          edge: "from-sky-500 via-cyan-500 to-sky-300", glow: "hover:shadow-sky-500/20" },
};

export const KPI_ICONS = {
  folder: "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z",
  calendar: "M8 7V3m8 4V3M4 11h16M5 5h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1z",
  check: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z",
  alert: "M12 9v4m0 4h.01M10.3 3.9L2.6 17a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  x: "M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z",
  gauge: "M13 10V3L4 14h7v7l9-11h-7z",
  user: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z",
} as const;

// Tint for a tile that needs attention now, such as cases waiting for a reviewer.
const HIGHLIGHT: Record<KpiTone, { card: string; dot: string }> = {
  brand: { card: "bg-brand-50/70 ring-brand-500/40", dot: "bg-brand-500" },
  good: { card: "bg-emerald-50/70 ring-emerald-300", dot: "bg-emerald-500" },
  warning: { card: "bg-amber-50/80 ring-amber-300", dot: "bg-amber-500" },
  critical: { card: "bg-rose-50/80 ring-rose-300", dot: "bg-rose-500" },
  info: { card: "bg-sky-50/70 ring-sky-300", dot: "bg-sky-500" },
};

export default function KpiCard({
  label, value, hint, icon, tone = "brand", meter, href, active, highlight, delay = 0,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: keyof typeof KPI_ICONS;
  tone?: KpiTone;
  /** 0 to 100. Draws a thin track under the value. */
  meter?: number | null;
  href?: string;
  active?: boolean;
  /** Tint the tile in its tone and add a pulsing dot: something here needs a person. */
  highlight?: boolean;
  delay?: number;
}) {
  const t = TONE[tone];
  const body = (
    <>
      {/* accent edge in the tile's tone, and a light sweep on hover */}
      <span className={`pointer-events-none absolute inset-x-0 top-0 h-1 bg-gradient-to-r ${t.edge}`} aria-hidden />
      <span className="kpi-shine" aria-hidden />
      <div className="relative flex items-start justify-between gap-3">
        <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
          {highlight && (
            <span className="relative flex h-2 w-2" aria-hidden>
              <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${HIGHLIGHT[tone].dot}`} />
              <span className={`relative inline-flex h-2 w-2 rounded-full ${HIGHLIGHT[tone].dot}`} />
            </span>
          )}
          {label}
        </p>
        <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl shadow-sm ring-1 transition duration-300 group-hover:-rotate-6 group-hover:scale-110 ${t.chip}`}>
          <svg className="h-[18px] w-[18px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d={KPI_ICONS[icon]} />
          </svg>
        </span>
      </div>
      <p className="relative mt-3 text-2xl font-semibold tracking-tight text-slate-900 transition-transform duration-300 group-hover:scale-[1.03] origin-left sm:text-3xl">
        {value}
      </p>
      {meter != null && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100" role="meter" aria-label={label}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(meter)}>
          <div className={`h-full rounded-full ${t.bar} transition-all duration-1000 ease-out`}
            style={{ width: `${Math.min(100, Math.max(0, meter))}%` }} />
        </div>
      )}
      {hint && <p className="mt-2 truncate text-xs text-slate-500">{hint}</p>}
    </>
  );

  const cls = `card-elevated group relative block animate-fade-up overflow-hidden p-4 pt-5 sm:p-5 sm:pt-6 ${t.glow} ${
    href ? "cursor-pointer" : ""} ${highlight ? HIGHLIGHT[tone].card : ""} ${active ? "ring-2 ring-brand-500/60 shadow-lg shadow-brand-500/15" : ""}`;
  const style = { animationDelay: `${delay}ms` };

  return href ? (
    <Link href={href} className={cls} style={style}>{body}</Link>
  ) : (
    <div className={cls} style={style}>{body}</div>
  );
}
