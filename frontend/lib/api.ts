// Browser client. Every call goes through the same-origin /api/bff proxy,
// so the session cookie is sent and the token is never exposed to scripts.

export type Decision = "APPROVE" | "REJECT" | "ESCALATE";
export type DocType =
  | "CLAIM_FORM"
  | "ID_DOCUMENT"
  | "DISCHARGE_SUMMARY"
  | "PRESCRIPTION"
  | "POLICY_AMENDMENT";

// In processing order: ID, claim form, discharge, prescription, policy amendment.
export const DOC_CATEGORIES: { value: DocType; label: string; folder: string }[] = [
  { value: "ID_DOCUMENT", label: "ID document", folder: "id_documents" },
  { value: "CLAIM_FORM", label: "Claim form", folder: "claim_forms" },
  { value: "DISCHARGE_SUMMARY", label: "Discharge summary", folder: "discharge_summaries" },
  { value: "PRESCRIPTION", label: "Prescription", folder: "prescriptions" },
  { value: "POLICY_AMENDMENT", label: "Policy amendment", folder: "policy_amendments" },
];

export interface CaseSummary {
  case_id: string;
  file_name: string;
  doc_type: string | null;
  status: string;
  decision: Decision | null;
  confidence: number | null;
  needs_review: boolean;
  patient_id?: string | null;
  bundle_id?: string | null;   // the claim this page belongs to; null for a standalone upload
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DashboardStats {
  total: number;               // claims (one claim holds several pages)
  documents?: number;          // pages across all claims
  today: number;
  documents_today?: number;
  processing: number;
  failed: number;
  approved: number;
  rejected: number;
  escalated: number;
  reviewed: number;
  decided: number;
  approval_rate: number | null;
  avg_confidence: number | null;
  patients: number;
  review_queue?: number;
  queue_counts?: Record<"review" | "approved" | "rejected", number>;
}

export interface Patient {
  patient_id: string;
  member_id: string | null;
  full_name: string;
  date_of_birth: string | null;
  gender: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  policy_number: string | null;
  created_by: string;
  created_at: string;
  updated_by: string | null;
  updated_at: string;
}

export type PatientInput = Omit<Patient, "patient_id" | "created_by" | "created_at" | "updated_by" | "updated_at">;

export interface AgentTrace {
  agent: string;
  status: string;
  duration_ms: number;
  note: string;
}

export interface CaseDetail extends CaseSummary {
  override: Record<string, unknown> | null;
  result: {
    file_path: string;
    error?: string;
    preprocessor?: Record<string, unknown>;
    classifier?: Record<string, unknown>;
    kyc?: Record<string, unknown>;
    claims?: Record<string, unknown>;
    policy?: Record<string, unknown>;
    fraud?: Record<string, unknown>;
    orchestrator?: Record<string, unknown>;
    trace?: AgentTrace[];
  };
}

export interface PolicyDoc {
  id: number;
  filename: string;
  chunks: number;
  ingested_at: string;
}

export class RequestError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/bff${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new RequestError(res.status, body.detail ?? `request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;     // a delete: no body
  return res.json() as Promise<T>;
}

export const documentSrc = (id: string) => `/api/bff/cases/${id}/document`;

export function uploadCase(file: File, docType?: DocType) {
  const form = new FormData();
  form.append("file", file);
  if (docType) form.append("doc_type", docType);
  return call<CaseDetail>("/cases", { method: "POST", body: form });
}

export function retryCase(id: string) {
  return call<CaseDetail>(`/cases/${id}/retry`, { method: "POST" });
}

export function getCase(id: string) {
  return call<CaseDetail>(`/cases/${id}`);
}

export function reviewCase(
  id: string,
  body: { doc_type?: DocType; decision?: Decision; note?: string },
) {
  return call<CaseDetail>(`/cases/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function uploadPatientCases(patientId: string, files: File[], docType?: DocType) {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  if (docType) form.append("doc_type", docType);
  return call<CaseSummary[]>(`/patients/${patientId}/cases`, { method: "POST", body: form });
}

export function createPatient(body: PatientInput) {
  return call<Patient>("/patients", jsonInit("POST", body));
}

export function updatePatient(id: string, body: PatientInput) {
  return call<Patient>(`/patients/${id}`, jsonInit("PUT", body));
}

// Admin only. Refused (409) while claims or pages are linked to the patient.
export function deletePatient(id: string) {
  return call<void>(`/patients/${id}`, { method: "DELETE" });
}

function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export type CodeVerdict = "covered" | "excluded" | "not_in_policy";

export type ClinicalFit = "supported" | "doubtful" | "not_assessed";

export interface PolicyCodeCheck {
  code: string;
  verdict: CodeVerdict;
  reason: string;
  clinical_fit: ClinicalFit;
  clinical_note: string;
  next_step: string;
  cross_check: "matched" | "no_clause_mentions_code";
  source: string | null;
  clause: string | null;
}

export interface PolicySource {
  source: string;
  excerpt: string;
}

export interface PolicyAnswer {
  answer: string;
  codes: PolicyCodeCheck[];
  diagnoses: string[];
  sources: PolicySource[];
  /** Set when a guardrail refused the question ("I don't know"). */
  guardrail?: "off_topic" | "injection" | null;
}

export function askPolicy(message: string, history: { role: "user" | "assistant"; content: string }[]) {
  return call<PolicyAnswer>("/policies/chat", jsonInit("POST", { message, history }));
}

export type ClaimStatus =
  | "EMPTY" | "QUEUED" | "PROCESSING" | "FAILED" | "NEEDS_REVIEW" | "REJECTED" | "APPROVED" | "REVIEWED"
  | "ON_HOLD" | "STOPPED";

/** Why a claim is ON_HOLD: page reading incomplete ("ingest") or an agent failed ("agents"). */
export interface ClaimHold {
  stage: "ingest" | "agents";
  step?: "classify" | "extract";   // ingest only: held after classifying every page, or after reading
  reasons: string[];
  pages?: { doc_id: string; file_name: string; reason: string }[];
  action?: "continue" | "stop";
  note?: string;
  reviewer?: string;
}

export interface AgentSummary {
  status: "ok" | "error" | "not_run";
  confidence: number | null;
  summary: string;
}

export interface ClaimDecision {
  decision: Decision;
  confidence: number;
  justification: string;
  reasons: string[];
  agent_summaries: Record<string, AgentSummary>;
  weights: Record<string, number>;
  needs_review: boolean;
}

export interface IngestedDoc {
  doc_id: string;
  file_name: string;
  doc_type: string;
  classifier_confidence: number;
  classifier_source: string;
  read_confidence: number;
  image_quality?: string;
  raw_text?: string;           // the preprocessor's transcription (vision model)
  blur_variance?: number;
  is_blurry?: boolean;
  fields: Record<string, unknown>;
  error?: string;
}

export interface KycCheck {
  check: string;
  passed: boolean | null;
  detail: string;
}

export interface FraudAnomaly {
  type: string;
  severity: number;
  detail: string;
  evidence: string[];
}

/** A page lacking required fields. Tracked for the reviewer, never part of the fraud score. */
export interface MissingFields {
  document: string;
  doc_type: string;
  fields: string[];
  detail: string;
}

export interface ClaimResult {
  error?: string;
  thread_id?: string;          // LangGraph checkpoint thread of the latest run
  awaiting_human?: boolean;    // the run is paused for a person (hold or human_review)
  awaiting?: "ingest_hold" | "human_review" | null;
  hold?: ClaimHold | null;
  resume?: { from?: string; thread_id?: string } | null;   // set by a stop: where resume picks up
  review?: ClaimOverride | null;
  plan?: { agents: string[]; document_counts: Record<string, number>; unknown_documents: string[]; missing_required: string[] };
  kyc?: { kyc_passed: boolean; needs_review?: boolean; review_reasons?: string[]; flags: string[]; checks: KycCheck[]; reference_date: string; confidence: number; summary: string; error?: string;
    member?: { member_id: string; full_name: string; policy_number: string } | null };
  claims?: { summary: string; confidence: number; error?: string;
    policy_brief?: { cpt_codes: string[]; icd10_codes: string[]; claim_amount?: number | null; service_date?: string | null; medications: string[] };
    claim?: { file_name: string; extracted_fields: Record<string, unknown> } | null };
  duplicate?: { is_duplicate: boolean; duplicate_of?: string | null; member_id?: string | null; service_date?: string | null;
    claim_amount?: number | null; checked: number; summary: string; error?: string };
  policy?: { covered: boolean; exclusions: string[]; policy_clause: string; plan: string; sources: string[]; notes: string[];
    cpt_codes_checked: string[]; confidence: number; error?: string };
  fraud?: { fraud_score: number; risk_level: string; tags: string[]; anomalies: FraudAnomaly[];
    missing_fields?: MissingFields[]; confidence: number; summary: string; error?: string };
  decision?: ClaimDecision;
  documents?: IngestedDoc[];
  trace?: AgentTrace[];
}

export interface ClaimOverride {
  decision: Decision;
  note: string;
  reviewer: string;
  reviewed_at: string;
  original_decision: Decision | null;
}

export interface AuditEvent {
  id: number;
  claim_id: string;
  doc_id: string | null;
  actor: string;
  event: string;
  status: string;
  detail: Record<string, unknown>;
  duration_ms: number | null;
  created_at: string;
}

export interface BundleSummary {
  status: ClaimStatus;
  decision: Decision | null;
  documents: number;
  processing: number;
  failed: number;
  needs_review: number;
}

export interface ClaimBundle {
  bundle_id: string;
  title: string;
  patient_id: string | null;
  created_by: string;
  created_at: string;
  updated_by: string | null;
  updated_at: string;
  status?: ClaimStatus | null;
  decision?: Decision | null;
  confidence?: number | null;
  needs_review?: boolean;
  result?: ClaimResult | null;
  override?: ClaimOverride | null;
  documents: CaseSummary[];
  summary: BundleSummary;
  // Lists only: why the claim is where it is (on hold, else the agents' decision reasons).
  reasons?: string[];
  justification?: string | null;
}

export interface QueueItem extends ClaimBundle {
  justification: string | null;
  reasons: string[];
  fraud_score: number | null;
}

export function reviewClaim(id: string, body: { decision?: Decision; note?: string; doc_types?: Record<string, DocType> }) {
  return call<ClaimBundle>(`/claim-bundles/${id}/review`, jsonInit("POST", body));
}

export function rerunClaim(id: string) {
  return call<ClaimBundle>(`/claim-bundles/${id}/rerun`, { method: "POST" });
}

// Human in the loop for a claim ON_HOLD after page reading.
export function resolveHold(id: string, action: "continue" | "stop", note?: string) {
  return call<ClaimBundle>(`/claim-bundles/${id}/hold`, jsonInit("POST", { action, note }));
}

// Stop a claim at once; a running one abandons its model call in flight.
export function stopClaim(id: string, note?: string) {
  return call<ClaimBundle & { stop: "STOPPED" }>(`/claim-bundles/${id}/stop`, jsonInit("POST", { note }));
}

// Resume a stopped claim from where it stopped (its last finished step).
export function resumeClaim(id: string) {
  return call<ClaimBundle & { resume: string }>(`/claim-bundles/${id}/resume`, jsonInit("POST", {}));
}

// Re-upload one page of the claim (an UNKNOWN or unreadable one), then the claim re-runs.
export function replaceDocument(id: string, docId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return call<ClaimBundle>(`/claim-bundles/${id}/documents/${docId}/replace`, { method: "POST", body: form });
}

// Admin only: delete a claim that is not running, with its pages. The audit log keeps it.
export function deleteClaim(id: string) {
  return call<void>(`/claim-bundles/${id}`, { method: "DELETE" });
}

// Re-upload every page of the claim, then it re-runs.
export function replaceAllDocuments(id: string, files: File[]) {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  return call<ClaimBundle>(`/claim-bundles/${id}/replace`, { method: "POST", body: form });
}

// Every file in one request; no categories, so the classifier decides each page's type.
export function submitClaim(input: { title?: string; files: File[]; patientId?: string }) {
  const form = new FormData();
  if (input.title) form.append("title", input.title);
  if (input.patientId) form.append("patient_id", input.patientId);
  input.files.forEach((f) => form.append("files", f));
  return call<ClaimBundle>("/claim-bundles", { method: "POST", body: form });
}

export interface BundleItem {
  file: File;
  /** The document type of the card the file was attached to. Empty means detect it. */
  category: DocType | "";
}

// One multi-part request: the files, plus one category per file, in the same order.
export function createClaimBundle(input: { title: string; items: BundleItem[]; patientId?: string }) {
  const form = new FormData();
  form.append("title", input.title);
  if (input.patientId) form.append("patient_id", input.patientId);
  input.items.forEach((item) => {
    form.append("files", item.file);
    form.append("categories", item.category);
  });
  return call<ClaimBundle>("/claim-bundles", { method: "POST", body: form });
}

export interface ClaimRequirements {
  required_documents: DocType[];
  max_files: number;
  max_pages: number;
  /** Words in a file name that point to a document type (rules: filename_hints). */
  filename_hints: Partial<Record<DocType, string[]>>;
  /** Regex for the patient reference (insurance member ID) in a file name, e.g. PT_\d+. */
  patient_ref_pattern: string;
}

export interface PatientRef {
  member_id: string;
  status: "registered" | "new" | "unknown";
  patient: { patient_id: string; full_name: string } | null;
  member: { full_name: string; policy_number: string | null } | null;
}

export function resolvePatientRef(memberId: string) {
  return call<PatientRef>(`/claim-bundles/patient-ref/${encodeURIComponent(memberId)}`);
}

export function uploadPolicy(file: File) {
  const form = new FormData();
  form.append("file", file);
  return call<{ filename: string; chunks: number }>("/policies", { method: "POST", body: form });
}
