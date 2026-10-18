// Frontend settings, all from frontend/.env.local. NEXT_PUBLIC_* values are read by name so
// Next.js can build them into the browser bundle; a missing one fails loudly instead of
// falling back to a value hidden in code.

function required(name: string, value: string | undefined): string {
  if (!value || !value.trim()) throw new Error(`missing ${name} in frontend/.env.local`);
  return value.trim();
}

export const REFRESH_MS = Number(required("NEXT_PUBLIC_REFRESH_MS", process.env.NEXT_PUBLIC_REFRESH_MS));
export const MAX_UPLOAD_BYTES = Number(required("NEXT_PUBLIC_MAX_UPLOAD_MB", process.env.NEXT_PUBLIC_MAX_UPLOAD_MB)) * 1024 * 1024;
export const MAX_BATCH_FILES = Number(required("NEXT_PUBLIC_MAX_BATCH_FILES", process.env.NEXT_PUBLIC_MAX_BATCH_FILES));
export const UPLOAD_EXTENSIONS = required("NEXT_PUBLIC_UPLOAD_EXTENSIONS", process.env.NEXT_PUBLIC_UPLOAD_EXTENSIONS)
  .split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);

export function allowedFile(name: string): boolean {
  const lower = name.toLowerCase();
  return UPLOAD_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export const SINGLE_UPLOAD_EXTENSIONS = required("NEXT_PUBLIC_SINGLE_UPLOAD_EXTENSIONS", process.env.NEXT_PUBLIC_SINGLE_UPLOAD_EXTENSIONS)
  .split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);

// An agent running longer than this is shown as "in progress, continuing in the background".
export const LONG_STEP_S = Number(required("NEXT_PUBLIC_LONG_STEP_S", process.env.NEXT_PUBLIC_LONG_STEP_S));
