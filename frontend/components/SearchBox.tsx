import Link from "next/link";
import { hrefWith, Params } from "@/lib/paging";

// A plain GET search form: submitting it sets `q` (and goes back to page 1) while keeping the
// list's other filters as hidden fields. Server-rendered, so it works without JavaScript.
export default function SearchBox({ path, params, placeholder, name = "q", keep = [], className = "", hash }: {
  path: string; params: Params; placeholder: string; name?: string; keep?: string[]; className?: string; hash?: string;
}) {
  const value = params[name] ?? "";
  return (
    <form action={`${path}${hash ? `#${hash}` : ""}`} className={`relative flex items-center ${className}`} role="search">
      {keep.filter((k) => params[k]).map((k) => <input key={k} type="hidden" name={k} value={params[k]} />)}
      <svg className="pointer-events-none absolute left-3 h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.3-4.3M10.5 18a7.5 7.5 0 110-15 7.5 7.5 0 010 15z" />
      </svg>
      <input name={name} defaultValue={value} placeholder={placeholder} aria-label={placeholder}
        className="input w-full py-1.5 pl-9 pr-8 text-sm sm:w-64" />
      {value && (
        <Link href={hrefWith(path, params, { [name]: undefined, page: undefined }) + (hash ? `#${hash}` : "")}
          aria-label="Clear search" className="absolute right-2 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden>
            <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
          </svg>
        </Link>
      )}
    </form>
  );
}
