import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "ms_session";

// Anonymous visitors go to /login. The backend still enforces roles on every call;
// this only keeps the pages from rendering for people who are not signed in.
export function middleware(req: NextRequest) {
  const signedIn = Boolean(req.cookies.get(SESSION_COOKIE)?.value);
  const { pathname } = req.nextUrl;

  if (pathname.startsWith("/login")) {
    if (signedIn) return NextResponse.redirect(new URL("/", req.url));
    return NextResponse.next();
  }
  if (!signedIn) {
    const url = new URL("/login", req.url);
    if (pathname !== "/") url.searchParams.set("next", pathname + req.nextUrl.search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/).*)"],
};
