"use client";

import { useRouter } from "next/navigation";
import { ChangeEvent, FormEvent, useState } from "react";
import { createPatient, Patient, PatientInput, updatePatient } from "@/lib/api";

const GENDERS = ["Female", "Male", "Other", "Prefer not to say"];
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// Every field is kept as text while editing. Blank values become null on submit.
interface FormState {
  full_name: string;
  date_of_birth: string;
  gender: string;
  phone: string;
  email: string;
  address: string;
  policy_number: string;
  member_id: string;
}

type Errors = Partial<Record<keyof FormState, string>>;

const EMPTY: FormState = {
  full_name: "", date_of_birth: "", gender: "", phone: "", email: "",
  address: "", policy_number: "", member_id: "",
};

const blankToNull = (s: string) => s.trim() || null;

function toForm(p?: Patient): FormState {
  if (!p) return EMPTY;
  return {
    full_name: p.full_name,
    date_of_birth: p.date_of_birth ?? "",
    gender: p.gender ?? "",
    phone: p.phone ?? "",
    email: p.email ?? "",
    address: p.address ?? "",
    policy_number: p.policy_number ?? "",
    member_id: p.member_id ?? "",
  };
}

function toPayload(f: FormState): PatientInput {
  return {
    full_name: f.full_name.trim(),
    date_of_birth: blankToNull(f.date_of_birth),
    gender: blankToNull(f.gender),
    phone: blankToNull(f.phone),
    email: blankToNull(f.email),
    address: blankToNull(f.address),
    policy_number: blankToNull(f.policy_number),
    member_id: blankToNull(f.member_id),
  };
}

// Checks the same rules the server enforces, so most mistakes are caught before a request.
function validate(f: FormState): Errors {
  const errors: Errors = {};
  if (f.full_name.trim().length < 2) errors.full_name = "Enter the patient's full name (at least 2 characters).";
  if (f.date_of_birth && f.date_of_birth > new Date().toISOString().slice(0, 10)) {
    errors.date_of_birth = "Date of birth cannot be in the future.";
  }
  if (f.email.trim() && !EMAIL_RE.test(f.email.trim())) errors.email = "Enter an email like name@example.com.";
  if (!f.member_id.trim()) errors.member_id = "Enter the insurance member ID.";
  return errors;
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return <p id={id} className="mt-1.5 text-xs font-medium text-rose-600">{message}</p>;
}

// Create a patient (no `initial`) or edit one. Both send the same payload.
export default function PatientForm({ initial, onSaved }: { initial?: Patient; onSaved?: () => void }) {
  const router = useRouter();
  const [form, setForm] = useState<FormState>(() => toForm(initial));
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const field = (key: keyof FormState) => ({
    value: form[key],
    "aria-invalid": errors[key] ? true : undefined,
    "aria-describedby": errors[key] ? `${key}-error` : undefined,
    onChange: (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      setForm((prev) => ({ ...prev, [key]: e.target.value }));
      setSaved(false);
      if (errors[key]) setErrors((prev) => ({ ...prev, [key]: undefined }));
    },
  });
  const inputClass = (key: keyof FormState) => `input ${errors[key] ? "border-rose-400 focus:border-rose-500 focus:ring-rose-500/15" : ""}`;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const found = validate(form);
    setErrors(found);
    setSaved(false);
    if (Object.keys(found).length > 0) {
      setError("Fix the highlighted fields and try again.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (initial) {
        await updatePatient(initial.patient_id, toPayload(form));
        setSaved(true);
        onSaved?.();
        router.refresh();
        setBusy(false);
      } else {
        const created = await createPatient(toPayload(form));
        router.push(`/patients/${created.patient_id}`);
      }
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5" noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label htmlFor="full_name" className="label">Full name <span className="text-rose-500" aria-hidden>*</span></label>
          <input id="full_name" className={inputClass("full_name")} autoComplete="off" {...field("full_name")} />
          <FieldError id="full_name-error" message={errors.full_name} />
        </div>
        <div>
          <label htmlFor="date_of_birth" className="label">Date of birth</label>
          <input id="date_of_birth" type="date" className={inputClass("date_of_birth")} {...field("date_of_birth")} />
          <FieldError id="date_of_birth-error" message={errors.date_of_birth} />
        </div>
        <div>
          <label htmlFor="gender" className="label">Gender</label>
          <select id="gender" className="input" {...field("gender")}>
            <option value="">Not recorded</option>
            {GENDERS.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="phone" className="label">Phone</label>
          <input id="phone" type="tel" className="input" {...field("phone")} />
        </div>
        <div>
          <label htmlFor="email" className="label">Email</label>
          <input id="email" type="email" className={inputClass("email")} {...field("email")} />
          <FieldError id="email-error" message={errors.email} />
        </div>
        <div className="sm:col-span-2">
          <label htmlFor="address" className="label">Address</label>
          <input id="address" className="input" {...field("address")} />
        </div>
        <div>
          <label htmlFor="policy_number" className="label">Policy number</label>
          <input id="policy_number" className="input" {...field("policy_number")} />
        </div>
        <div>
          <label htmlFor="member_id" className="label">Insurance member ID <span className="text-rose-500" aria-hidden>*</span></label>
          <input id="member_id" className={inputClass("member_id")} {...field("member_id")} />
          <FieldError id="member_id-error" message={errors.member_id} />
          {!errors.member_id && <p className="mt-1.5 text-xs text-slate-500">Must match an existing member. One ID per patient.</p>}
        </div>
      </div>

      {error && (
        <p role="alert" className="flex items-start gap-2 rounded-xl bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700 animate-fade-up">
          <span aria-hidden>⚠</span>{error}
        </p>
      )}
      {saved && !error && (
        <p role="status" className="flex items-center gap-2 rounded-xl bg-emerald-50 px-3.5 py-2.5 text-sm font-medium text-emerald-700 animate-fade-up">
          <span aria-hidden>✓</span> Changes saved
        </p>
      )}

      <div className="flex justify-end gap-3">
        <button type="submit" className="btn-primary w-full sm:w-auto" disabled={busy}>
          {busy && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden />}
          {busy ? "Saving…" : initial ? "Save changes" : "Create patient"}
        </button>
      </div>
    </form>
  );
}
