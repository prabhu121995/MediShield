// Pagination for every raw-data list. Lists ask the API for one row more than a page, so they
// know whether a next page exists without counting the whole table.

export const PAGE_SIZE = 20;

export type Params = Record<string, string | undefined>;

/** The 1-based page number from the `page` search param (bad values fall back to 1). */
export function pageOf(params: Params, key = "page"): number {
  const n = Number(params[key]);
  return Number.isInteger(n) && n > 0 ? n : 1;
}

/** The API's offset for a page. */
export function offsetOf(page: number, size = PAGE_SIZE): number {
  return (page - 1) * size;
}

/** Split the rows the API returned (page size + 1) into this page and whether more exist. */
export function pageRows<T>(rows: T[], size = PAGE_SIZE): { rows: T[]; hasNext: boolean } {
  return { rows: rows.slice(0, size), hasNext: rows.length > size };
}

/** One page of an array that is already loaded in full (small lists). */
export function slicePage<T>(rows: T[], page: number, size = PAGE_SIZE): { rows: T[]; hasNext: boolean; total: number } {
  const start = offsetOf(page, size);
  return { rows: rows.slice(start, start + size), hasNext: rows.length > start + size, total: rows.length };
}

/** `path?…` with the given params, dropping empty ones. */
export function hrefWith(path: string, params: Params, patch: Params = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...params, ...patch })) if (v) qs.set(k, v);
  const s = qs.toString();
  return s ? `${path}?${s}` : path;
}

/** A query string for the API: limit = page size + 1, the offset, and any filters. */
export function apiQuery(page: number, filters: Params = {}, size = PAGE_SIZE): string {
  return hrefWith("", { ...filters, limit: String(size + 1), offset: String(offsetOf(page, size)) }).slice(1);
}
