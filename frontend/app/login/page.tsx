import { Suspense } from "react";
import LoginHero from "@/components/LoginHero";
import pkg from "@/package.json";
import LoginForm from "./LoginForm";
import LoginIntro from "./LoginIntro";

const FEATURES = [
  { title: "Multi-agent claim review", body: "KYC, claims, policy and fraud checks run side by side." },
  { title: "Fraud signals up front", body: "Suspicious patterns are flagged before a decision is made." },
  { title: "Every decision logged", body: "Each action is recorded for audit and compliance." },
];

// Mobile first: one centred column in the order hero, sign-in, features.
// From lg up: two columns. Hero and features sit on the left, sign-in on the right.
export default function LoginPage() {
  return (
    <LoginIntro>
    <main className="relative min-h-[100dvh] overflow-hidden bg-slate-950 text-white">
      {/* Background: soft glows plus a faint grid that fades toward the edges */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-24 -top-24 h-72 w-72 animate-blob rounded-full bg-brand-600/40 blur-3xl sm:h-96 sm:w-96" />
        <div className="absolute -bottom-32 -right-16 h-80 w-80 animate-blob rounded-full bg-indigo-500/30 blur-3xl [animation-delay:-5s] sm:h-[28rem] sm:w-[28rem]" />
        <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(255,255,255,0.035)_1px,transparent_1px),linear-gradient(to_bottom,rgba(255,255,255,0.035)_1px,transparent_1px)] bg-[size:48px_48px] [mask-image:radial-gradient(ellipse_at_center,black_35%,transparent_75%)]" />
      </div>

      <div className="relative mx-auto flex min-h-[100dvh] w-full max-w-6xl flex-col items-center gap-10 px-5 py-10 sm:px-8 lg:grid lg:grid-cols-2 lg:content-center lg:items-start lg:gap-x-16 lg:px-10 lg:py-12">
        {/* Left column. On mobile it is display:contents, so its children take part in the outer stack. */}
        <div className="contents lg:col-start-1 lg:row-start-1 lg:flex lg:max-w-lg lg:flex-col lg:justify-self-start lg:gap-10">
          {/* Brand: hero and headline */}
          <section className="order-1 flex w-full max-w-md animate-fade-up flex-col items-center text-center lg:max-w-none lg:items-start lg:text-left">
            <div className="relative grid h-24 w-24 place-items-center">
              <span className="absolute inset-0 rounded-full bg-brand-500/30 animate-ripple" />
              <span className="absolute inset-0 rounded-full bg-brand-500/30 animate-ripple [animation-delay:1.2s]" />
              <LoginHero className="relative h-24 w-24 drop-shadow-[0_0_20px_rgba(91,140,255,0.6)]" />
            </div>

            <h1 className="mt-6 text-4xl font-bold tracking-tight sm:text-5xl">
              <span className="bg-gradient-to-r from-white via-brand-100 to-brand-500 bg-clip-text text-transparent">
                MediShield
              </span>
            </h1>
            <p className="mt-3 max-w-md text-base text-slate-300">
              Intelligent claim review for insurance teams. Sign in to work through today&apos;s cases.
            </p>
          </section>

          {/* Feature list */}
          <ul className="order-3 grid w-full max-w-md gap-3 lg:max-w-none lg:gap-4">
            {FEATURES.map((f) => (
              <li
                key={f.title}
                className="flex items-start gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4 backdrop-blur"
              >
                <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-brand-500 shadow-[0_0_10px_rgba(91,140,255,0.9)]" />
                <div>
                  <p className="text-sm font-semibold text-white">{f.title}</p>
                  <p className="text-sm text-slate-400">{f.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* Sign-in card */}
        <section className="order-2 w-full max-w-md animate-scale-in lg:col-start-2 lg:row-start-1 lg:max-w-lg lg:justify-self-end">
          <div className="rounded-3xl bg-gradient-to-b from-white/20 via-white/5 to-white/0 p-px shadow-2xl shadow-black/40">
            <div className="rounded-[23px] bg-slate-900/80 p-6 backdrop-blur-xl sm:p-9 lg:p-12">
              <Suspense fallback={<div className="h-80" />}>
                <LoginForm />
              </Suspense>
            </div>
          </div>

          <div className="mt-6 space-y-1 text-center text-xs text-slate-500">
            <p>Access is limited to authorised staff. All activity is logged.</p>
            <p className="text-slate-600">MediShield v{pkg.version}</p>
          </div>
        </section>
      </div>
    </main>
    </LoginIntro>
  );
}
