// Same-origin proxy to the FastAPI backend. It adds the session token from the
// httpOnly cookie, so the browser never holds the token. Multipart uploads and
// images pass through unchanged.
import { NextRequest, NextResponse } from "next/server";
import { API_URL, SESSION_COOKIE } from "@/lib/server-api";

type Ctx = { params: Promise<{ path: string[] }> };

async function proxy(req: NextRequest, { params }: Ctx) {
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  }
  const { path } = await params;
  const headers = new Headers({ Authorization: `Bearer ${token}` });
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const upstream = await fetch(`${API_URL}/api/${path.join("/")}${req.nextUrl.search}`, {
    method: req.method,
    headers,
    body: hasBody ? await req.arrayBuffer() : undefined,
    cache: "no-store",
  }).catch(() => null);

  if (!upstream) {
    return NextResponse.json({ detail: "server unavailable" }, { status: 503 });
  }
  // Downloads (CSV, PDF) keep their file name.
  const out = new Headers({ "Content-Type": upstream.headers.get("content-type") ?? "application/json" });
  const disposition = upstream.headers.get("content-disposition");
  if (disposition) out.set("Content-Disposition", disposition);
  return new NextResponse(upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST };
