import Link from "next/link";
import { AuditEvent } from "@/lib/api";
import { serverApi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

const TYPES = ["claim", "case", "patient", "policy", "user"] as const;
type EntityType = (typeof TYPES)[number];

interface Row extends AuditEvent { entity_type: EntityType; entity_id: string }

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function link(r: Row) {
  if (r.entity_type === "claim") return `/bundles/${r.entity_id}`;
  if (r.entity_type === "case") return `/cases/${r.entity_id}`;
  if (r.entity_type === "patient") return `/patients/${r.entity_id}`;
  return null;
}

// Every audited action across the system (MySQL table audit_event), newest first.
export default async function AuditPage({ searchParams }: { searchParams: Promise<{ type?: string; actor?: string }> }) {
  const { type, actor } = await searchParams;
  const active = TYPES.includes(type as EntityType) ? (type as EntityType) : undefined;
  const qs = new URLSearchParams();
  if (active) qs.set("entity_type", active);
  if (actor) qs.set("actor", actor);
  const rows = await serverApi<Row[]>(`/api/audit?${qs.toString()}`);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Audit log</h1>
        <p className="mt-1 text-sm text-slate-500">Every agent step and human action, timestamped, from its own table (audit_event). Newest first.</p>
      </div>
      <nav className="flex flex-wrap gap-2" aria-label="Filter by entity">
        {[undefined, ...TYPES].map((t) => (
          <Link key={t ?? "all"} href={t ? `/audit?type=${t}` : "/audit"} aria-current={active === t ? "page" : undefined}
            className={`rounded-full px-3 py-1.5 text-xs font-semibold capitalize ring-1 transition ${
              active === t ? "bg-gradient-to-r from-brand-600 to-indigo-500 text-white shadow-md shadow-brand-600/25 ring-transparent"
                : "bg-white text-slate-600 ring-slate-200 hover:-translate-y-px hover:shadow-sm"}`}>
            {t ?? "all"}
          </Link>
        ))}
        {actor && <Link href={active ? `/audit?type=${active}` : "/audit"} className="rounded-full bg-slate-100 px-3 py-1.5 text-xs text-slate-600">actor: {actor} ✕</Link>}
      </nav>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <thead className="border-b border-slate-100 bg-slate-50/60">
            <tr><th className="th">Time</th><th className="th">Entity</th><th className="th">Actor</th><th className="th">Event</th><th className="th">Detail</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => {
              const href = link(r);
              const d = r.detail ?? {};
              const text = String(d.justification ?? d.note ?? d.error ?? d.decision ?? d.file_name ?? (d.changed ? `changed: ${(d.changed as string[]).join(", ")}` : "") ?? "");
              return (
                <tr key={r.id} className="transition hover:bg-slate-50/80">
                  <td className="td whitespace-nowrap tabular-nums text-xs text-slate-500">{fmt(r.created_at)}</td>
                  <td className="td text-xs">
                    <span className="mr-1.5 rounded-md bg-slate-100 px-1.5 py-0.5 font-semibold capitalize text-slate-600">{r.entity_type}</span>
                    {href ? <Link href={href} className="font-mono text-brand-700 hover:underline">{r.entity_id.slice(0, 8)}</Link>
                      : <span className="font-mono text-slate-600">{r.entity_id.slice(0, 24)}</span>}
                  </td>
                  <td className="td text-xs"><Link href={`/audit?${active ? `type=${active}&` : ""}actor=${encodeURIComponent(r.actor)}`} className="hover:underline">{r.actor}</Link></td>
                  <td className={`td text-xs ${r.status === "error" ? "text-rose-700" : "text-slate-700"}`}>{r.event.replaceAll("_", " ")}</td>
                  <td className="td max-w-md truncate text-xs text-slate-500" title={text}>{text}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && <p className="p-8 text-center text-sm text-slate-500">No audit events.</p>}
      </div>
    </div>
  );
}
