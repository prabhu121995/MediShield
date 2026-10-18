"use client";

import { ReactNode, useEffect, useState } from "react";
import LoginHero from "@/components/LoginHero";

// Timings in ms. The splash shows, fades out, then the login page is revealed.
const SPLASH_MS = 2400;
const FADE_MS = 600;

type Phase = "splash" | "leaving" | "done";

// Shows the animated shield logo full-screen first, then reveals the login page.
export default function LoginIntro({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("splash");

  useEffect(() => {
    const leave = setTimeout(() => setPhase("leaving"), SPLASH_MS);
    const done = setTimeout(() => setPhase("done"), SPLASH_MS + FADE_MS);
    return () => {
      clearTimeout(leave);
      clearTimeout(done);
    };
  }, []);

  return (
    <>
      {phase !== "splash" && children}

      {phase !== "done" && (
        <div
          aria-hidden="true"
          className={`fixed inset-0 z-50 grid place-items-center overflow-hidden bg-slate-950 px-6 transition-opacity duration-500 ${
            phase === "leaving" ? "pointer-events-none opacity-0" : "opacity-100"
          }`}
        >
          <div className="pointer-events-none absolute inset-0">
            <div className="absolute -left-24 -top-24 h-72 w-72 animate-blob rounded-full bg-brand-600/40 blur-3xl" />
            <div className="absolute -bottom-32 -right-16 h-80 w-80 animate-blob rounded-full bg-indigo-500/30 blur-3xl [animation-delay:-5s]" />
          </div>

          <div className="relative flex flex-col items-center text-center">
            <div className="relative grid h-36 w-36 place-items-center">
              <span className="absolute inset-0 rounded-full bg-brand-500/30 animate-ripple" />
              <span className="absolute inset-0 rounded-full bg-brand-500/30 animate-ripple [animation-delay:1.2s]" />
              <LoginHero className="relative h-28 w-28 drop-shadow-[0_0_24px_rgba(91,140,255,0.6)]" />
            </div>

            <h1 className="mt-6 animate-fade-up text-3xl font-bold tracking-tight text-white">
              MediShield
            </h1>
            <p className="mt-2 animate-fade-in text-sm text-slate-400 [animation-delay:0.3s]">
              Securing claim decisions
            </p>

            <div className="mt-8 h-1 w-44 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-gradient-to-r from-brand-500 to-indigo-500"
                style={{ animation: `progress ${SPLASH_MS}ms ease-in-out both` }}
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
