"use client";

import { useRouter } from "next/navigation";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import { ClaimRequirements, createClaimBundle, DocType, Patient, PatientRef, resolvePatientRef } from "@/lib/api";
import { allowedFile, MAX_UPLOAD_BYTES as MAX_BYTES, UPLOAD_EXTENSIONS } from "@/lib/settings";

const ACCEPT = UPLOAD_EXTENSIONS.join(",");
const DEFAULT_REF = "PT_\\d+";

type Attached = { file: File; pages: number | null };

const icon = (d: string) => (
  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.6} aria-hidden>
    <path strokeLinecap="round" strokeLinejoin="round" d={d} />
  </svg>
);

// One slot per document type, in processing order (rules: page_order): ID, claim form,
// discharge summary, prescription, policy amendment. Files are sent in this order too.
const SLOTS: { value: DocType; label: string; hint: string; icon: ReactNode }[] = [
  { value: "ID_DOCUMENT", label: "ID document", hint: "Licence, passport or member card",
    icon: icon("M3 6a2 2 0 012-2h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6zm6 7a2 2 0 100-4 2 2 0 000 4zm-3 4c.5-1.5 1.7-2 3-2s2.5.5 3 2m3-7h3m-3 3h3") },
  { value: "CLAIM_FORM", label: "Claim form", hint: "CMS-1500, UB-04 or bill",
    icon: icon("M9 12h6m-6 4h6M7 3h7l5 5v11a2 2 0 01-2 2H7a2 2 0 01-2-2V5a2 2 0 012-2zm7 0v5h5") },
  { value: "DISCHARGE_SUMMARY", label: "Discharge summary", hint: "Hospital discharge report",
    icon: icon("M3 21h18M5 21V7l7-4 7 4v14M12 8v5m-2.5-2.5h5M9 21v-4h6v4") },
  { value: "PRESCRIPTION", label: "Prescription", hint: "Medicines, signed by prescriber",
    icon: icon("M10.5 20.5l10-10a4.95 4.95 0 00-7-7l-10 10a4.95 4.95 0 007 7zM8.5 8.5l7 7") },
  { value: "POLICY_AMENDMENT", label: "Policy amendment", hint: "Endorsement or change request",
    icon: icon("M11 4H6a2 2 0 00-2 2v12a2 2 0 002 2h12a2 2 0 002-2v-5M18.5 2.5a2.12 2.12 0 013 3L12 15l-4 1 1-4 9.5-9.5z") },
];
const LABELS = new Map(SLOTS.map((s) => [s.value, s.label]));

function fmtSize(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

// Same matching as the server (app.claim.intake): whole words of the file name.
function nameTypes(fileName: string, hints: ClaimRequirements["filename_hints"]): DocType[] {
  const words = new Set(fileName.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  return (Object.entries(hints) as [DocType, string[]][]).filter(([, w]) => w.some((x) => words.has(x))).map(([t]) => t);
}

function patientRefs(fileNames: string[], pattern: string): string[] {
  let re: RegExp;
  try { re = new RegExp(pattern, "gi"); } catch { re = new RegExp(DEFAULT_REF, "gi"); }
  return [...new Set(fileNames.flatMap((n) => (n.match(re) ?? []).map((m) => m.toUpperCase())))];
}

// Best-effort page count of a PDF without a PDF library: count the page objects, else read the
// page tree's /Count. Returns null when the pages sit in compressed object streams; the server
// still enforces the limit after splitting.
async function pdfPages(file: File): Promise<number | null> {
  const text = new TextDecoder("latin1").decode(await file.arrayBuffer());
  const objects = text.match(/\/Type\s*\/Page(?![a-zA-Z])/g)?.length ?? 0;
  if (objects > 0) return objects;
  const counts = Array.from(text.matchAll(/\/Count\s+(\d+)/g), (m) => Number(m[1]));
  return counts.length ? Math.max(...counts) : null;
}

async function pageCount(file: File): Promise<number | null> {
  return file.name.toLowerCase().endsWith(".pdf") ? pdfPages(file).catch(() => null) : 1;
}

// One claim = one file per document type, attached to its slot. Nothing is sent until
// "Upload claim", which posts every file with its type in one multi-part request. Before that:
// the required slots must be filled, a file named for another type ("claim_PT_1.png" in the ID
// slot) is refused, and the patient reference in the file names (PT_…) picks the patient, or
// says one will be registered from the member record. The server repeats every check.
export default function NewCaseForm({ patients, locked = false, requirements }: {
  patients: Pick<Patient, "patient_id" | "full_name" | "member_id">[];
  locked?: boolean;
  requirements?: ClaimRequirements | null;
}) {
  const router = useRouter();
  const maxPages = requirements?.max_pages ?? null;
  const hints = requirements?.filename_hints ?? {};
  // Without the server's list, require every slot: the server refuses an incomplete claim anyway.
  const required = new Set<DocType>(requirements?.required_documents ?? SLOTS.map((s) => s.value));

  const [title, setTitle] = useState("");
  const [patientId, setPatientId] = useState("");
  const [slots, setSlots] = useState<Partial<Record<DocType, Attached>>>({});
  const [dragOver, setDragOver] = useState<DocType | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [ref, setRef] = useState<PatientRef | null>(null);

  const filled = SLOTS.filter((s) => slots[s.value]);
  const refs = patientRefs(filled.map((s) => slots[s.value]!.file.name), requirements?.patient_ref_pattern ?? DEFAULT_REF);
  const refKey = refs.length === 1 ? refs[0] : null;

  // Look up the one patient the file names point to, and select them when registered.
  useEffect(() => {
    if (!refKey) { setRef(null); return; }
    let live = true;
    resolvePatientRef(refKey).then((r) => {
      if (!live) return;
      setRef(r);
      if (r.patient) setPatientId(r.patient.patient_id);
    }).catch(() => live && setRef(null));
    return () => { live = false; };
  }, [refKey]);

  const options = ref?.patient && !patients.some((p) => p.patient_id === ref.patient!.patient_id)
    ? [...patients, { patient_id: ref.patient.patient_id, full_name: ref.patient.full_name, member_id: ref.member_id }]
    : patients;
  const chosenMember = options.find((p) => p.patient_id === patientId)?.member_id?.toUpperCase() ?? null;
  const patientMismatch = !!refKey && !!chosenMember && chosenMember !== refKey;

  async function attach(type: DocType, list: FileList | File[]) {
    const file = Array.from(list)[0];
    if (!file) return;
    if (!allowedFile(file.name) || file.size > MAX_BYTES) {
      setNotice(`${file.name} was not attached: only ${UPLOAD_EXTENSIONS.join(" ")} up to ${MAX_BYTES / 1024 / 1024} MB.`);
      return;
    }
    const named = nameTypes(file.name, hints);
    if (named.length && !named.includes(type)) {
      setNotice(`${file.name} looks like ${named.map((t) => LABELS.get(t) ?? t).join(" / ")}, not ${LABELS.get(type)}. Attach it there.`);
      return;
    }
    const attached = { file, pages: await pageCount(file) };
    setSlots((prev) => ({ ...prev, [type]: attached }));
    setNotice(list.length > 1 ? `Only one file per document: ${file.name} was attached to ${LABELS.get(type)}.` : null);
    setError(null);
  }

  function detach(type: DocType) {
    setSlots((prev) => {
      const next = { ...prev };
      delete next[type];
      return next;
    });
  }

  const knownPages = filled.reduce((n, s) => n + (slots[s.value]!.pages ?? 0), 0);
  const unknownPages = filled.some((s) => slots[s.value]!.pages === null);
  const overPages = maxPages !== null && knownPages > maxPages;
  const missing = SLOTS.filter((s) => required.has(s.value) && !slots[s.value]).map((s) => s.label);
  const mixedPatients = refs.length > 1;
  const blocked = filled.length === 0 || overPages || missing.length > 0 || mixedPatients || patientMismatch;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (blocked) {
      setError(missing.length ? `Attach the ${missing.join(" and ")} first.` : "Fix the problems above before uploading.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const claim = await createClaimBundle({
        title: title.trim(), patientId: patientId || undefined,
        items: filled.map((s) => ({ file: slots[s.value]!.file, category: s.value })),
      });
      router.push(`/bundles/${claim.bundle_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="card animate-fade-up p-5" noValidate>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div>
          <label htmlFor="case_title" className="label">Claim title <span className="font-normal text-slate-400">(optional)</span></label>
          <input id="case_title" className="input" value={title} onChange={(e) => setTitle(e.target.value)}
            placeholder="Jane Doe · March claim" maxLength={160} disabled={busy} />
        </div>
        <div>
          <label htmlFor="case_patient" className="label">Patient <span className="font-normal text-slate-400">(optional, read from file names)</span></label>
          <select id="case_patient" className="input" value={patientId} onChange={(e) => setPatientId(e.target.value)} disabled={busy}>
            <option value="">{ref?.status === "new" ? `New: ${ref.member?.full_name} · ${ref.member_id}` : "None"}</option>
            {options.map((p) => (
              <option key={p.patient_id} value={p.patient_id}>{p.full_name}{p.member_id ? ` · ${p.member_id}` : ""}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {SLOTS.map((slot, i) => {
          const att = slots[slot.value];
          const need = required.has(slot.value);
          const inputId = `attach_${slot.value}`;
          return (
            <section key={slot.value} aria-label={slot.label}
              onDragOver={(e) => { e.preventDefault(); if (!busy) setDragOver(slot.value); }}
              onDragLeave={() => setDragOver(null)}
              onDrop={(e) => { e.preventDefault(); setDragOver(null); if (!busy) attach(slot.value, e.dataTransfer.files); }}
              className={`flex min-w-0 flex-col gap-3 rounded-xl p-3 transition ${
                dragOver === slot.value ? "bg-brand-50 ring-2 ring-brand-500"
                  : att ? "bg-emerald-50/50 ring-1 ring-emerald-300"
                  : "border border-dashed border-slate-300 bg-slate-50/60"}`}>
              <div className="flex items-start gap-2.5">
                <span className={`relative grid h-9 w-9 shrink-0 place-items-center rounded-lg ${att ? "bg-emerald-500 text-white" : "bg-white text-brand-600 ring-1 ring-slate-200"}`}>
                  {att ? icon("M5 13l4 4L19 7") : slot.icon}
                  <span className="absolute -left-1.5 -top-1.5 grid h-4 w-4 place-items-center rounded-full bg-slate-700 text-[9px] font-bold text-white" aria-label={`Step ${i + 1}`}>{i + 1}</span>
                </span>
                <div className="min-w-0">
                  <h3 className="flex flex-wrap items-center gap-1.5 text-sm font-semibold text-slate-900">
                    {slot.label}
                    {need && <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rose-600">Required</span>}
                  </h3>
                  <p className="truncate text-xs text-slate-500" title={slot.hint}>{slot.hint}</p>
                </div>
              </div>

              {att ? (
                <div className="flex min-w-0 items-center gap-2 rounded-md bg-white px-2 py-1.5 text-xs ring-1 ring-slate-200">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-slate-700" title={att.file.name}>{att.file.name}</span>
                    <span className="tabular-nums text-slate-400">
                      {att.pages === null ? "? pages" : `${att.pages} page${att.pages === 1 ? "" : "s"}`} · {fmtSize(att.file.size)}
                    </span>
                  </span>
                  <label htmlFor={inputId} className={`shrink-0 cursor-pointer rounded px-1.5 py-0.5 font-medium text-brand-600 hover:bg-brand-50 ${busy ? "pointer-events-none opacity-50" : ""}`}>
                    Replace
                  </label>
                  <button type="button" aria-label={`Remove ${att.file.name}`} disabled={busy} onClick={() => detach(slot.value)}
                    className="shrink-0 rounded p-0.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600">✕</button>
                </div>
              ) : (
                <label htmlFor={inputId} className={`flex cursor-pointer items-center justify-center gap-1.5 rounded-md bg-white px-2 py-2 text-xs font-medium text-brand-600 ring-1 ring-slate-200 transition hover:ring-brand-500 ${busy ? "pointer-events-none opacity-50" : ""}`}>
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0l-4 4m4-4l4 4M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" />
                  </svg>
                  Attach or drop
                </label>
              )}
              <input id={inputId} type="file" accept={ACCEPT} className="sr-only" disabled={busy}
                onChange={(e) => { if (e.target.files) attach(slot.value, e.target.files); e.target.value = ""; }} />
            </section>
          );
        })}
      </div>

      {refKey && ref && !patientMismatch && (
        <p role="status" className={`mt-3 rounded-lg px-3 py-2 text-xs font-medium ${
          ref.status === "unknown" ? "bg-amber-50 text-amber-800" : "bg-sky-50 text-sky-800"}`}>
          {ref.status === "registered" && <>File names are for <b>{ref.patient?.full_name}</b> ({ref.member_id}); the claim is linked to this patient.</>}
          {ref.status === "new" && <><b>{ref.member?.full_name}</b> ({ref.member_id}) is not a patient yet and will be registered from the member record on upload.</>}
          {ref.status === "unknown" && <>{ref.member_id} in the file names is not a known insurance member; the claim is uploaded without a patient.</>}
        </p>
      )}

      <div className="mt-4 flex flex-col gap-3 border-t border-slate-200 pt-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-slate-500">
          {filled.length === 0 ? (
            <>Attach one file to each document, then upload the claim in one go · {UPLOAD_EXTENSIONS.join(" ")} · {MAX_BYTES / 1024 / 1024} MB each.</>
          ) : (
            <>
              <span className="font-medium text-slate-700">{filled.length} of {SLOTS.length} documents attached</span>
              {" · "}{knownPages}{unknownPages ? "+" : ""} page{knownPages === 1 && !unknownPages ? "" : "s"}{maxPages !== null ? ` of ${maxPages}` : ""}
            </>
          )}
        </p>
        <div className="flex items-center gap-2">
          <button type="button" className="btn-ghost h-[42px] px-3" disabled={busy || filled.length === 0}
            onClick={() => { setSlots({}); setNotice(null); setError(null); }}>Clear all</button>
          <button type="submit" disabled={busy || locked || blocked} className="btn-primary h-[42px] px-5"
            title={locked ? "Another claim is processing; only one runs at a time"
              : missing.length ? `Attach the ${missing.join(" and ")} first` : undefined}>
            {busy && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden />}
            {busy ? "Uploading…" : `Upload claim${filled.length ? ` (${filled.length})` : ""}`}
          </button>
        </div>
      </div>

      {filled.length > 0 && missing.length > 0 && (
        <p role="status" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700">
          Attach the {missing.join(" and ")} to upload this claim.
        </p>
      )}
      {mixedPatients && (
        <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700">
          The files are for different patients ({refs.join(", ")}). A claim covers one patient: replace the files that don&apos;t belong.
        </p>
      )}
      {patientMismatch && (
        <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700">
          The files are for {refKey}, but the chosen patient is {chosenMember}. Choose the right patient or replace the files.
        </p>
      )}
      {overPages && (
        <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700">
          {knownPages} pages in all; a claim can hold at most {maxPages}. Remove a file to continue.
        </p>
      )}
      {notice && <p role="status" className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">{notice}</p>}
      {error && <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700">{error}</p>}
    </form>
  );
}
