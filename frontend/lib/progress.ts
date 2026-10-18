// Live progress of a claim, derived from its audit log. Every agent writes an "agent_started"
// row when it begins and an "agent_completed" row when it ends, so the rows since the latest
// "claim_started" tell which agents are done, which one is running (and since when), and
// which are still waiting.
import type { AuditEvent, CaseSummary } from "./api";

export type StageState = "waiting" | "running" | "done" | "error" | "skipped" | "hold";

export interface Stage {
  key: string;
  label: string;
  state: StageState;
  note: string;
  startedAt?: string;          // when the running stage began
  durationMs?: number;         // total time the stage took (all pages for a page stage)
  detail?: Record<string, unknown>;
}

export interface PageStep {
  state: StageState;
  startedAt?: string;
  durationMs?: number;
  detail?: Record<string, unknown>;
}

export interface PageProgress {
  doc_id: string;
  file_name: string;
  state: StageState;
  step?: string;               // the step running on this page: preprocess | classify | extract
  doc_type?: string;
  blurry?: boolean;
  steps: Record<string, PageStep>;
}

export interface Progress {
  running: boolean;
  current: Stage | null;
  stages: Stage[];
  pages: PageProgress[];
}

export const PAGE_STEPS = ["preprocess", "classify", "extract"] as const;
const LABELS: Record<string, string> = {
  preprocess: "Preprocess", classify: "Classify", extract: "Extract", ingest_hold: "Hold (person)",
  plan: "Plan", kyc: "KYC", claims: "Claims", policy: "Policy", fraud: "Fraud", decide: "Decision",
  human_review: "Human review",
};

export function claimProgress(events: AuditEvent[], documents: CaseSummary[], running: boolean, awaitingHuman = false,
                              reviewed = false, onHold = false): Progress {
  const start = events.map((e) => e.event).lastIndexOf("claim_started");
  const run = start >= 0 ? events.slice(start) : [];
  const completed = (agent: string, doc?: string) =>
    run.filter((e) => e.event === "agent_completed" && e.actor === agent && (doc === undefined || e.doc_id === doc));
  const begun = (agent: string, doc?: string) =>
    run.filter((e) => e.event === "agent_started" && e.actor === agent && (doc === undefined || e.doc_id === doc));
  const lastTime = run.at(-1)?.created_at;

  // ---- pages: each passes preprocess -> classify -> extract, one page at a time ----
  const pages: PageProgress[] = documents.map((d) => {
    const steps: Record<string, PageStep> = {};
    for (const s of PAGE_STEPS) {
      const done = completed(s, d.case_id).at(-1);
      const go = begun(s, d.case_id).at(-1);
      if (done) steps[s] = { state: done.status === "error" ? "error" : "done", durationMs: done.duration_ms ?? 0, detail: done.detail };
      else if (go && running) steps[s] = { state: "running", startedAt: go.created_at };
      else steps[s] = { state: "waiting" };
    }
    const runningStep = PAGE_STEPS.find((s) => steps[s].state === "running");
    const extracted = steps.extract.state === "done" || steps.extract.state === "error";
    const docType = (steps.classify.detail?.doc_type ?? steps.extract.detail?.doc_type) as string | undefined;
    const failed = PAGE_STEPS.some((s) => steps[s].state === "error");
    return {
      doc_id: d.case_id, file_name: d.file_name, steps, doc_type: docType,
      state: extracted ? (failed ? "error" : "done") : runningStep ? "running" : "waiting",
      step: runningStep,
      blurry: Boolean(steps.preprocess.detail?.is_blurry || steps.classify.detail?.is_blurry),
    };
  });

  // Older runs have no agent_started rows: the first unfinished page is the running one.
  if (running && start >= 0 && !pages.some((p) => p.state === "running") && !run.some((e) => e.event === "agent_started")) {
    const p = pages.find((x) => x.state === "waiting");
    if (p) {
      p.state = "running";
      p.step = PAGE_STEPS.find((s) => p.steps[s].state === "waiting");
      if (p.step) p.steps[p.step] = { state: "running", startedAt: lastTime };
    }
  }

  const pageStage = (key: (typeof PAGE_STEPS)[number]): Stage => {
    const n = pages.length;
    const finished = pages.filter((p) => ["done", "error"].includes(p.steps[key].state)).length;
    const errors = pages.filter((p) => p.steps[key].state === "error").length;
    const live = pages.find((p) => p.steps[key].state === "running");
    const durationMs = pages.reduce((sum, p) => sum + (p.steps[key].durationMs ?? 0), 0);
    if (n > 0 && finished === n) {
      return { key, label: LABELS[key], state: errors ? "error" : "done", durationMs,
               note: errors ? `${finished}/${n} pages · ${errors} failed` : `${finished}/${n} pages` };
    }
    if (live) {
      return { key, label: LABELS[key], state: "running", startedAt: live.steps[key].startedAt, durationMs,
               note: `page ${pages.indexOf(live) + 1} of ${n} · ${live.file_name}` };
    }
    return { key, label: LABELS[key], state: "waiting", durationMs, note: finished ? `${finished}/${n} pages` : "waiting" };
  };

  const pre = pageStage("preprocess");
  const cls = pageStage("classify");
  const ext = pageStage("extract");
  const allRead = pages.length > 0 && pages.every((p) => p.state === "done" || p.state === "error");

  // ---- the hold after page reading (only when a page failed or is UNKNOWN) ----
  const holdDone = completed("ingest_hold").at(-1);
  const heldAt = run.filter((e) => e.event === "claim_on_hold" && e.detail?.stage === "ingest").at(-1);
  const hold: Stage | null = holdDone
    ? { key: "ingest_hold", label: LABELS.ingest_hold, state: "done", note: `person chose ${holdDone.detail?.action}`, detail: holdDone.detail }
    : heldAt && onHold
      ? { key: "ingest_hold", label: LABELS.ingest_hold, state: "hold", note: "waiting for a person: continue, stop or re-upload", startedAt: heldAt.created_at, detail: heldAt.detail }
      : null;

  // ---- the claim agents ----
  const planned = (completed("plan").at(-1)?.detail?.agents as string[] | undefined) ?? null;
  const hasClaimForm = pages.some((p) => p.doc_type === "CLAIM_FORM");
  const finished = (s: Stage) => ["done", "error", "skipped"].includes(s.state);
  const blocked = Boolean(hold && hold.state === "hold") || (holdDone?.detail?.action === "stop");

  const stage = (key: string, ready: boolean, applies: boolean | null): Stage => {
    const e = completed(key).at(-1);
    if (e) return { key, label: LABELS[key], state: e.status === "error" ? "error" : "done", note: fmtMs(e.duration_ms ?? 0),
                    durationMs: e.duration_ms ?? 0, detail: e.detail };
    if (blocked) return { key, label: LABELS[key], state: "waiting", note: "on hold" };
    if (applies === false) return { key, label: LABELS[key], state: "skipped", note: "not applicable" };
    const go = begun(key).at(-1);
    if (running && (go || ready)) return { key, label: LABELS[key], state: "running", note: "running…", startedAt: go?.created_at ?? lastTime };
    return { key, label: LABELS[key], state: "waiting", note: "waiting" };
  };

  const plan = stage("plan", allRead && (!heldAt || Boolean(holdDone)), null);
  const planDone = plan.state === "done";
  const kyc = stage("kyc", planDone, planned ? planned.includes("kyc") : null);
  const claims = stage("claims", planDone, planned ? planned.includes("claims") : null);
  const policy = stage("policy", finished(claims), planned ? planned.includes("claims") && hasClaimForm : null);
  const fraud = stage("fraud", planDone && [kyc, claims, policy].every(finished), planned ? planned.length > 0 : null);
  const decide = stage("decide", planDone && finished(fraud), null);

  const decided = completed("decide").at(-1)?.detail?.decision as string | undefined;
  const reviewDone = completed("human_review").at(-1);
  const human: Stage = reviewDone || reviewed
    ? { key: "human_review", label: LABELS.human_review, state: "done", note: String(reviewDone?.detail?.decision ?? "decided"), detail: reviewDone?.detail }
    : awaitingHuman || decided === "ESCALATE"
      ? { key: "human_review", label: LABELS.human_review, state: "hold", note: "paused at checkpoint, waiting for a person", startedAt: completed("decide").at(-1)?.created_at }
      : decided ? { key: "human_review", label: LABELS.human_review, state: "skipped", note: "not needed" }
        : { key: "human_review", label: LABELS.human_review, state: "waiting", note: "only if escalated" };

  const stages = [pre, cls, ext, ...(hold ? [hold] : []), plan, kyc, claims, policy, fraud, decide, human];
  return { running, current: stages.find((s) => s.state === "running") ?? null, stages, pages };
}

export function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
}
