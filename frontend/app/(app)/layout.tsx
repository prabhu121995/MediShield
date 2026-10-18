import { redirect } from "next/navigation";
import AppShell from "@/components/AppShell";
import { DashboardStats } from "@/lib/api";
import { currentUser, serverApi } from "@/lib/server-api";

// Every page here depends on the signed-in user, so never prerender it.
export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const user = await currentUser();
  if (!user) redirect("/login");
  // Cases that need a person: escalated or failed. Shown as a badge in the sidebar.
  // A failed stats call must not block the app, so it falls back to no badge.
  const stats = await serverApi<DashboardStats>("/api/dashboard/stats").catch(() => null);
  const attention = stats ? (stats.review_queue ?? 0) : 0;
  return <AppShell user={user} attention={attention}>{children}</AppShell>;
}
