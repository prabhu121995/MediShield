"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { REFRESH_MS } from "@/lib/settings";

// Re-renders the server page on an interval while work is running, so statuses update.
export default function AutoRefresh({ active, ms = REFRESH_MS }: { active: boolean; ms?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => router.refresh(), ms);
    return () => clearInterval(timer);
  }, [active, ms, router]);
  return null;
}
