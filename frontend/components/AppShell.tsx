"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { SidebarBody } from "./Sidebar";
import UploadTray from "./UploadTray";
import { UploadProvider } from "./UploadProvider";
import type { User } from "@/lib/server-api";

// Responsive frame: a fixed sidebar on desktop, a slide-in drawer on phones and tablets.
// Documents are uploaded from Case files and from a patient's page; the upload tray here
// shows their progress on every page.
export default function AppShell({ user, counts = {}, children }: { user: User; counts?: Record<string, number>; children: React.ReactNode }) {
  const pathname = usePathname();
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    setDrawer(false);
  }, [pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawer(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    document.body.style.overflow = drawer ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [drawer]);

  return (
    <UploadProvider>
      <div className="flex h-[100dvh] overflow-hidden bg-slate-50">
        <aside className="hidden h-full w-64 shrink-0 border-r border-slate-200 bg-white text-slate-700 md:block">
          <SidebarBody user={user} counts={counts} />
        </aside>

        {drawer && (
          <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <button className="absolute inset-0 bg-slate-950/50 animate-fade-in" aria-label="Close menu" onClick={() => setDrawer(false)} />
            <aside className="absolute inset-y-0 left-0 w-72 max-w-[85vw] animate-fade-up border-r border-slate-200 bg-white text-slate-700 shadow-2xl">
              <SidebarBody user={user} counts={counts} onNavigate={() => setDrawer(false)} />
            </aside>
          </div>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center gap-3 border-b border-slate-200 bg-white/85 px-4 backdrop-blur sm:px-6 md:hidden">
            <button type="button" onClick={() => setDrawer(true)} aria-label="Open menu"
              className="grid h-10 w-10 place-items-center rounded-xl text-slate-600 transition hover:bg-slate-100 md:hidden">
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" d="M4 7h16M4 12h16M4 17h10" />
              </svg>
            </button>
            <p className="min-w-0 flex-1 truncate text-sm text-slate-500">
              Signed in as <span className="font-medium text-slate-800">{user.username}</span>
            </p>
          </header>

          <main className="flex-1 overflow-y-auto">
            <div className="mx-auto w-[95%] px-2 py-4 sm:py-5">{children}</div>
          </main>
        </div>

        <UploadTray />
      </div>
    </UploadProvider>
  );
}
