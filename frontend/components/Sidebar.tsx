"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import Logo from "./Logo";
import type { Role, User } from "@/lib/server-api";

type Nav = { href: string; label: string; roles: Role[]; icon: string };

const NAV: Nav[] = [
  { href: "/", label: "Cases", roles: ["admin", "agent"], icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/bundles", label: "Claims", roles: ["admin", "agent"], icon: "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" },
  { href: "/review", label: "Review queue", roles: ["admin", "agent"], icon: "M9 12l2 2 4-4m5.6-4.4A11.9 11.9 0 0112 2.9 11.9 11.9 0 013.4 5.6 12 12 0 003 9c0 5.6 3.8 10.3 9 11.6 5.2-1.3 9-6 9-11.6 0-1.2-.1-2.3-.4-3.4z" },
  { href: "/approved", label: "Approved", roles: ["admin", "agent"], icon: "M5 13l4 4L19 7" },
  { href: "/rejected", label: "Rejected", roles: ["admin", "agent"], icon: "M6 6l12 12M6 18L18 6" },
  { href: "/patients", label: "Patients", roles: ["admin", "agent"], icon: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" },
  { href: "/audit", label: "Audit log", roles: ["admin"], icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" },
  { href: "/logs", label: "System logs", roles: ["admin"], icon: "M4 6h16M4 10h16M4 14h10M4 18h7" },
  { href: "/policies", label: "Policies", roles: ["admin"], icon: "M9 12l2 2 4-4M7 4h10a2 2 0 012 2v12a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2z" },
];

// Light sidebar: white panel, slate text, and the current page marked with a soft brand
// background and an accent bar on the left edge.
// Lists whose count is work waiting for a person.
const NEEDS_PERSON = new Set(["/review"]);

// `counts`: the number beside each list (keyed by href); lists without one show no badge.
export function SidebarBody({ user, counts = {}, onNavigate }: { user: User; counts?: Record<string, number>; onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function signOut() {
    setBusy(true);
    await fetch("/api/auth/session", { method: "DELETE" });
    router.replace("/login");
    router.refresh();
  }

  const initials = user.full_name.split(" ").map((p) => p[0]).join("").slice(0, 2);

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center gap-3 border-b border-slate-200 px-5">
        <Logo className="h-8 w-8" />
        <div className="leading-tight">
          <p className="text-sm font-semibold text-slate-900">MediShield</p>
          <p className="text-xs text-slate-500">Claim review</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4" aria-label="Main">
        <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Workspace</p>
        {NAV.filter((item) => item.roles.includes(user.role)).map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} onClick={onNavigate} aria-current={active ? "page" : undefined}
              className={`group nav-item ${active ? "nav-item-active" : ""}`}>
              {active && <span className="absolute -left-3 inset-y-2 w-1 rounded-r-full bg-gradient-to-b from-brand-400 to-indigo-500 shadow-[0_0_8px_rgba(59,108,246,0.7)]" aria-hidden />}
              <svg className={`h-5 w-5 shrink-0 transition-transform duration-200 group-hover:scale-110 ${active ? "text-white" : "text-slate-400 group-hover:text-brand-600"}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d={item.icon} />
              </svg>
              {item.label}
              {counts[item.href] != null && (() => {
                const n = counts[item.href];
                const urgent = NEEDS_PERSON.has(item.href) && n > 0;       // waiting for a person: amber, pulsing
                return (
                  <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums ring-1 ring-inset ${
                    urgent
                      ? `animate-pulse-dot shadow-sm ${active ? "bg-white/90 text-amber-800 ring-white/40" : "bg-gradient-to-r from-amber-100 to-orange-100 text-amber-800 ring-amber-600/20"}`
                      : active ? "bg-white/20 text-white ring-white/30" : "bg-slate-100 text-slate-500 ring-slate-200"}`}
                    title={urgent ? `${n} ${n === 1 ? "claim needs" : "claims need"} a person` : `${n.toLocaleString()} ${item.label.toLowerCase()}`}>
                    {n.toLocaleString()}
                    {urgent && <span className="sr-only"> waiting for a person</span>}
                  </span>
                );
              })()}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-slate-200 p-3">
        <div className="flex items-center gap-3 rounded-lg px-2 py-2">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-600 text-xs font-semibold text-white">{initials}</div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-slate-900">{user.full_name}</p>
            <span className={`text-xs font-medium ${user.role === "admin" ? "text-amber-700" : "text-sky-700"}`}>
              {user.role === "admin" ? "Administrator" : "Claims agent"}
            </span>
          </div>
        </div>
        <button onClick={signOut} disabled={busy}
          className="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium text-slate-600 transition duration-150 hover:bg-slate-100 hover:text-slate-900 disabled:opacity-50">
          <svg className="h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 17l5-5-5-5M20 12H9M12 21H5a2 2 0 01-2-2V5a2 2 0 012-2h7" />
          </svg>
          {busy ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </div>
  );
}

export default function Sidebar({ user }: { user: User }) {
  return (
    <aside className="hidden h-full w-64 shrink-0 border-r border-slate-200 bg-white text-slate-700 md:block">
      <SidebarBody user={user} />
    </aside>
  );
}
