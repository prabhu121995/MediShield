import Link from "next/link";

export default function CaseNotFound() {
  return (
    <div className="card mx-auto mt-16 max-w-md animate-scale-in p-10 text-center">
      <p className="text-5xl font-semibold tabular-nums text-slate-300">404</p>
      <h1 className="mt-3 text-lg font-semibold text-slate-900">Case not found</h1>
      <p className="mt-2 text-sm text-slate-500">It may have been removed, or the link is wrong.</p>
      <Link href="/" className="btn-primary mt-6">Back to cases</Link>
    </div>
  );
}
