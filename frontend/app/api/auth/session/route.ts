// Sign in / sign out. The backend token goes into an httpOnly cookie, so browser
// scripts never see it.
import { NextResponse } from "next/server";
import { API_URL, SESSION_COOKIE } from "@/lib/server-api";

const EIGHT_HOURS = 60 * 60 * 8;

export async function POST(req: Request) {
  const body = await req.json().catch(() => null);
  if (!body?.username || !body?.password) {
    return NextResponse.json({ detail: "enter your username and password" }, { status: 400 });
  }

  const res = await fetch(`${API_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: body.username, password: body.password }),
    cache: "no-store",
  }).catch(() => null);

  if (!res) {
    return NextResponse.json({ detail: "cannot reach the server; try again shortly" }, { status: 503 });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json({ detail: data.detail ?? "sign-in failed" }, { status: res.status });
  }

  const out = NextResponse.json({ user: data.user });
  out.cookies.set(SESSION_COOKIE, data.token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: EIGHT_HOURS,
  });
  return out;
}

export async function DELETE() {
  const out = NextResponse.json({ ok: true });
  out.cookies.set(SESSION_COOKIE, "", { path: "/", maxAge: 0 });
  return out;
}
