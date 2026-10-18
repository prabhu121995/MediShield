"use client";

import { useState } from "react";

// Scan viewer for the uploaded document. Zoom changes the rendered width, so the frame
// scrolls in both directions and the image stays sharp at every size.
const MIN = 50;
const MAX = 300;
const STEP = 25;

export default function DocumentViewer({ src, alt, href }: { src: string; alt: string; href: string }) {
  const [zoom, setZoom] = useState(100);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  const clamp = (v: number) => Math.min(MAX, Math.max(MIN, v));

  return (
    <section className="card animate-fade-up overflow-hidden" aria-label="Document image">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-3 py-2.5 sm:px-4">
        <div className="flex items-center gap-1">
          <button type="button" className="btn-ghost px-2.5 py-1.5" aria-label="Zoom out"
            onClick={() => setZoom((z) => clamp(z - STEP))} disabled={zoom <= MIN}>
            <span className="text-base leading-none">−</span>
          </button>
          <span className="w-14 text-center text-xs font-medium tabular-nums text-slate-600" aria-live="polite">{zoom}%</span>
          <button type="button" className="btn-ghost px-2.5 py-1.5" aria-label="Zoom in"
            onClick={() => setZoom((z) => clamp(z + STEP))} disabled={zoom >= MAX}>
            <span className="text-base leading-none">+</span>
          </button>
          <button type="button" className="btn-ghost px-2.5 py-1.5 text-xs" onClick={() => setZoom(100)}>Fit</button>
        </div>
        <a href={href} target="_blank" rel="noreferrer" className="btn-ghost px-2.5 py-1.5 text-xs text-brand-700">
          Open original <span aria-hidden>↗</span>
        </a>
      </div>

      <div className="relative max-h-[70vh] min-h-[16rem] overflow-auto bg-slate-100/70 p-3">
        {!loaded && !failed && (
          <div className="absolute inset-0 grid place-items-center">
            <span className="h-7 w-7 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
          </div>
        )}
        {failed ? (
          <p className="grid h-40 place-items-center text-sm text-slate-500">The document image could not be loaded.</p>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt={alt} onLoad={() => setLoaded(true)} onError={() => setFailed(true)}
            style={{ width: `${zoom}%`, maxWidth: "none" }}
            className={`mx-auto block rounded-lg bg-white shadow-sm transition-opacity duration-500 ${loaded ? "opacity-100" : "opacity-0"}`} />
        )}
      </div>
    </section>
  );
}
