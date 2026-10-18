// Server-only helpers: read the session cookie and call the FastAPI backend with it.
import "server-only";
import { cookies } from "next/headers";

export const API_URL = process.env.API_URL as string;      // frontend/.env.local
if (!API_URL) throw new Error("missing API_URL in frontend/.env.local");
export const SESSION_COOKIE = "ms_session";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function sessionToken(): Promise<string | undefined> {
  return (await cookies()).get(SESSION_COOKIE)?.value;
}

export async function serverApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await sessionToken();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: { ...(init.headers ?? {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export type Role = "admin" | "agent";
export interface User {
  username: string;
  role: Role;
  full_name: string;
}

export async function currentUser(): Promise<User | null> {
  try {
    return await serverApi<User>("/api/auth/me");
  } catch {
    return null;
  }
}
