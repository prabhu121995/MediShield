import { Suspense } from "react";
import LoginHero from "@/components/LoginHero";
import Logo from "@/components/Logo";
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
    <main className="relative min-h-[100dvh] overflow-hidden bg-slate-50 text-slate-900">
      {/* Background, as the dashboard: light, with soft brand glows and a faint grid fading to the edges */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-24 -top-24 h-72 w-72 animate-blob rounded-full bg-brand-100 blur-3xl sm:h-96 sm:w-96" />
        <div className="absolute -bottom-32 -right-16 h-80 w-80 animate-blob rounded-full bg-indigo-100 blur-3xl [animation-delay:-5s] sm:h-[28rem] sm:w-[28rem]" />
        <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(15,23,42,0.045)_1px,transparent_1px),linear-gradient(to_bottom,rgba(15,23,42,0.045)_1px,transparent_1px)] bg-[size:48px_48px] [mask-image:radial-gradient(ellipse_at_center,black_35%,transparent_75%)]" />
      </div>

      {/* App name, top left: the same mark as the dashboard sidebar */}
      <header className="relative z-10 flex w-full items-center gap-2.5 px-5 pt-5 sm:px-6 lg:absolute lg:left-0 lg:top-0 lg:w-auto lg:px-6 lg:pt-5">
        <Logo className="h-9 w-9" />
        <div className="leading-tight">
          <p className="text-base font-semibold text-slate-900">MediShield</p>
          <p className="text-xs text-slate-500">Claim review</p>
        </div>
      </header>

      <div className="relative mx-auto flex min-h-[100dvh] w-full max-w-6xl flex-col items-center gap-10 px-5 pb-24 pt-10 sm:px-8 lg:grid lg:grid-cols-2 lg:content-center lg:items-stretch lg:gap-x-16 lg:px-10 lg:pb-24 lg:pt-12">
        {/* Left column. On mobile it is display:contents, so its children take part in the outer stack. */}
        <div className="contents lg:col-start-1 lg:row-start-1 lg:flex lg:max-w-lg lg:flex-col lg:justify-self-start lg:gap-10">
          {/* Brand: hero and headline */}
          {/* From lg up the headline row lines up with "Welcome back" in the card (card top + its
              padding), so both headings read on one line. The app name is in the header, top left. */}
          <section className="order-1 flex w-full max-w-md animate-fade-up flex-col items-center text-center lg:max-w-none lg:items-start lg:pt-8 lg:text-left">
            <div className="flex flex-col items-center gap-5 lg:flex-row lg:gap-4">
              <div className="relative grid h-20 w-20 shrink-0 place-items-center lg:h-16 lg:w-16">
                <span className="absolute inset-0 rounded-full bg-brand-500/15 animate-ripple" />
                <span className="absolute inset-0 rounded-full bg-brand-500/15 animate-ripple [animation-delay:1.2s]" />
                <LoginHero className="relative h-20 w-20 drop-shadow-[0_8px_16px_rgba(39,83,224,0.25)] lg:h-16 lg:w-16" />
              </div>
              <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
                <span className="bg-gradient-to-r from-slate-900 via-brand-700 to-indigo-500 bg-clip-text text-transparent">
                  Intelligent claim review
                </span>
              </h1>
            </div>
            <p className="mt-3 max-w-md text-base text-slate-500">
              Multi-agent checks for insurance teams. Sign in to work through today&apos;s cases.
            </p>
          </section>

          {/* Feature list */}
          <ul className="order-3 grid w-full max-w-md gap-3 lg:max-w-none lg:gap-4">
            {FEATURES.map((f) => (
              <li
                key={f.title}
                className="card-elevated flex items-start gap-3 p-4"
              >
                <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-brand-500 ring-4 ring-brand-100" />
                <div>
                  <p className="text-sm font-semibold text-slate-900">{f.title}</p>
                  <p className="text-sm text-slate-500">{f.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* Sign-in card. From lg up it stretches to the left column's height, so its top lines up
            with the headline and its bottom with the last feature card. */}
        <section className="order-2 w-full max-w-md animate-scale-in lg:col-start-2 lg:row-start-1 lg:flex lg:max-w-lg lg:flex-col lg:justify-self-end">
          <div className="relative overflow-hidden rounded-3xl bg-white lg:flex lg:flex-1 lg:flex-col shadow-[0_1px_2px_rgba(15,23,42,0.05),0_24px_48px_-16px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/80">
            <span className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-brand-500 to-indigo-500" aria-hidden />
            <div className="p-6 sm:p-9 lg:p-12">
              <Suspense fallback={<div className="h-80" />}>
                <LoginForm />
              </Suspense>
            </div>
          </div>

        </section>
      </div>

      {/* Bottom centre of the screen */}
      <footer className="absolute inset-x-0 bottom-0 space-y-1 px-5 pb-6 text-center text-xs text-slate-500">
        <p>Access is limited to authorised staff. All activity is logged.</p>
        <p className="text-slate-400">MediShield v{pkg.version}</p>
      </footer>
    </main>
    </LoginIntro>
  );
}
