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
  step?: string;               // the step running on this page: classify | preprocess | extract
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

export const PAGE_STEPS = ["classify", "preprocess", "extract"] as const;
const LABELS: Record<string, string> = {
  classify: "Classify", preprocess: "Preprocess", extract: "Preprocess + extract", ingest_hold: "Hold (person)",
  plan: "Plan", kyc: "KYC", claims: "Claims", duplicate_check: "Duplicate check", policy: "Policy", fraud: "Fraud", decide: "Decision",
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

  // ---- pages: every page is classified first, then each is preprocessed (image -> text), then extracted ----
  const pages: PageProgress[] = documents.map((d) => {
    const steps: Record<string, PageStep> = {};
    for (const s of PAGE_STEPS) {
      const done = completed(s, d.case_id).at(-1);
      const go = begun(s, d.case_id).at(-1);
      if (done) steps[s] = { state: done.status === "error" ? "error" : "done", durationMs: done.duration_ms ?? 0, detail: done.detail };
      else if (go && running) steps[s] = { state: "running", startedAt: go.created_at };
      else steps[s] = { state: "waiting" };
    }
    // Preprocess runs inside the extract step (one card, "Preprocess + extract") and writes its
    // own rows, so the card can say which half is running. Older runs have none.
    if (!begun("preprocess", d.case_id).length && !completed("preprocess", d.case_id).length) {
      steps.preprocess = { ...steps.extract, durationMs: undefined };
    }
    const runningStep = (["classify", "extract"] as const).find((s) => steps[s].state === "running");
    const extracted = steps.extract.state === "done" || steps.extract.state === "error";
    const docType = (steps.classify.detail?.doc_type ?? steps.extract.detail?.doc_type) as string | undefined;
    const failed = PAGE_STEPS.some((s) => steps[s].state === "error");
    return {
      doc_id: d.case_id, file_name: d.file_name, steps, doc_type: docType,
      state: extracted ? (failed ? "error" : "done") : runningStep ? "running" : "waiting",
      step: runningStep,
      blurry: Boolean(steps.classify.detail?.is_blurry),
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
    const timed = pages.some((p) => p.steps[key].durationMs != null);      // older runs: preprocess is untimed
    const durationMs = timed ? pages.reduce((sum, p) => sum + (p.steps[key].durationMs ?? 0), 0) : undefined;
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

  const cls = pageStage("classify");
  const ext = pageStage("extract");
  const allRead = pages.length > 0 && pages.every((p) => p.state === "done" || p.state === "error");

  // ---- the hold (a page UNKNOWN, blurry or failed after classify, or unreadable after extract) ----
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

  const stage = (key: string, ready: boolean, applies: boolean | null, skipNote = "not applicable"): Stage => {
    const e = completed(key).at(-1);
    if (e) return { key, label: LABELS[key], state: e.status === "error" ? "error" : "done", note: fmtMs(e.duration_ms ?? 0),
                    durationMs: e.duration_ms ?? 0, detail: e.detail };
    if (blocked) return { key, label: LABELS[key], state: "waiting", note: "on hold" };
    if (applies === false) return { key, label: LABELS[key], state: "skipped", note: skipNote };
    const go = begun(key).at(-1);
    if (running && (go || ready)) return { key, label: LABELS[key], state: "running", note: "running…", startedAt: go?.created_at ?? lastTime };
    return { key, label: LABELS[key], state: "waiting", note: "waiting" };
  };

  const plan = stage("plan", allRead && (!heldAt || Boolean(holdDone)), null);
  const planDone = plan.state === "done";
  // The duplicate checkpoint runs before any agent; a duplicate skips them all and goes to decide.
  const duplicate = stage("duplicate_check", planDone, planned ? planned.length > 0 : null);
  const isDuplicate = completed("duplicate_check").at(-1)?.detail?.is_duplicate === true;
  const DUP = "skipped: duplicate claim";
  const agent = (applies: boolean | null) => (isDuplicate ? false : applies);
  const checked = finished(duplicate);
  const kyc = stage("kyc", checked, agent(planned ? planned.includes("kyc") : null), DUP);
  const claims = stage("claims", checked, agent(planned ? planned.includes("claims") : null), DUP);
  const policy = stage("policy", finished(claims), agent(planned ? planned.includes("claims") && hasClaimForm : null), DUP);
  const fraud = stage("fraud", checked && [kyc, claims, policy].every(finished), agent(planned ? planned.length > 0 : null), DUP);
  const decide = stage("decide", planDone && (isDuplicate || finished(fraud)), null);

  const decided = completed("decide").at(-1)?.detail?.decision as string | undefined;
  const reviewDone = completed("human_review").at(-1);
  const human: Stage = reviewDone || reviewed
    ? { key: "human_review", label: LABELS.human_review, state: "done", note: String(reviewDone?.detail?.decision ?? "decided"), detail: reviewDone?.detail }
    : awaitingHuman || decided === "ESCALATE"
      ? { key: "human_review", label: LABELS.human_review, state: "hold", note: "paused at checkpoint, waiting for a person", startedAt: completed("decide").at(-1)?.created_at }
      : decided ? { key: "human_review", label: LABELS.human_review, state: "skipped", note: "not needed" }
        : { key: "human_review", label: LABELS.human_review, state: "waiting", note: "only if escalated" };

  const heldAfterClassify = (hold?.detail?.step ?? heldAt?.detail?.step) !== "extract";
  const stages = heldAfterClassify
    ? [cls, ...(hold ? [hold] : []), ext, plan, duplicate, kyc, claims, policy, fraud, decide, human]
    : [cls, ext, ...(hold ? [hold] : []), plan, duplicate, kyc, claims, policy, fraud, decide, human];
  return { running, current: stages.find((s) => s.state === "running") ?? null, stages, pages };
}

export function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
}
