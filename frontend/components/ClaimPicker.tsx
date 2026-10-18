"use client";

import { useRouter } from "next/navigation";

export interface AuditClaim { claim_id: string; title: string | null; status: string; last_at: string; events: number }

function day(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}

// Pick one claim to scope the Audit log to it. Claims that still exist come first; claims
// deleted since are listed by ID, since their audit trail is still here.
export default function ClaimPicker({ claims, selected, hrefFor }: {
  claims: AuditClaim[]; selected?: string; hrefFor: Record<string, string>;
}) {
  const router = useRouter();
  const live = claims.filter((c) => c.status !== "DELETED");
  const gone = claims.filter((c) => c.status === "DELETED");
  const label = (c: AuditClaim) =>
    `${c.title ?? c.claim_id.slice(0, 8)} · ${c.status.replaceAll("_", " ").toLowerCase()} · ${day(c.last_at)} · ${c.events} events`;
  return (
    <label className="flex items-center gap-2 text-xs font-semibold text-slate-600">
      <span>Claim</span>
      <select value={selected ?? ""} onChange={(e) => router.push(hrefFor[e.target.value] ?? hrefFor[""])}
        className="input max-w-[22rem] py-1.5 text-xs font-normal" aria-label="Show the audit log of one claim">
        <option value="">All claims</option>
        {selected && !claims.some((c) => c.claim_id === selected) && <option value={selected}>{selected.slice(0, 8)}</option>}
        {live.length > 0 && (
          <optgroup label="Current claims">
            {live.map((c) => <option key={c.claim_id} value={c.claim_id}>{label(c)}</option>)}
          </optgroup>
        )}
        {gone.length > 0 && (
          <optgroup label="Deleted claims (audit trail only)">
            {gone.map((c) => <option key={c.claim_id} value={c.claim_id}>{label(c)}</option>)}
          </optgroup>
        )}
      </select>
    </label>
  );
}
