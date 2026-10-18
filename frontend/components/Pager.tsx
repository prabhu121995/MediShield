import Link from "next/link";
import { hrefWith, offsetOf, PAGE_SIZE, Params } from "@/lib/paging";

// Previous / Page N / Next under a list. Server-rendered links, so it works without JavaScript
// and keeps every other filter in the URL. Hidden when everything fits on one page.
export default function Pager({ path, params, page, hasNext, shown, total, pageKey = "page", size = PAGE_SIZE, hash, className = "" }: {
  path: string; params: Params; page: number; hasNext: boolean; shown: number; total?: number;
  pageKey?: string; size?: number; hash?: string; className?: string;
}) {
  if (page === 1 && !hasNext) return null;
  const href = (p: number) => hrefWith(path, params, { [pageKey]: p > 1 ? String(p) : undefined }) + (hash ? `#${hash}` : "");
  const from = shown ? offsetOf(page, size) + 1 : 0;
  const to = offsetOf(page, size) + shown;
  const btn = "inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-semibold ring-1 transition";
  const on = `${btn} bg-white text-slate-700 ring-slate-200 hover:bg-slate-50 hover:text-brand-700`;
  const off = `${btn} cursor-not-allowed bg-slate-50 text-slate-300 ring-slate-100`;
  return (
    <nav className={`flex flex-wrap items-center justify-between gap-2 pt-3 text-xs text-slate-500 ${className}`} aria-label="Pagination">
      <span className="tabular-nums">
        Rows {from}–{to}{total != null ? ` of ${total}` : ""} · page {page}
      </span>
      <span className="flex items-center gap-2">
        {page > 1 ? <Link href={href(page - 1)} className={on} rel="prev">← Previous</Link>
          : <span className={off} aria-disabled="true">← Previous</span>}
        <span className="grid h-7 min-w-7 place-items-center rounded-lg bg-brand-600 px-2 font-semibold tabular-nums text-white" aria-current="page">{page}</span>
        {hasNext ? <Link href={href(page + 1)} className={on} rel="next">Next →</Link>
          : <span className={off} aria-disabled="true">Next →</span>}
      </span>
    </nav>
  );
}
