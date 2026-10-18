// Live progress of a claim, derived from its audit log. Every agent writes an
// "agent_completed" row when it finishes, so the rows since the latest "claim_started"
// tell which agents are done, which one is running, and which are still waiting.
import type { AuditEvent, CaseSummary } from "./api";

export type StageState = "waiting" | "running" | "done" | "error" | "skipped";

export interface Stage {
  key: string;
  label: string;
  state: StageState;
  note: string;
  startedAt?: string;          // when the running stage began (end of the previous step)
  detail?: Record<string, unknown>;
}

export interface PageProgress {
  doc_id: string;
  file_name: string;
  state: StageState;
  step?: string;               // the step running on this page: preprocess | classify | extract
  doc_type?: string;
  blurry?: boolean;
}

export interface Progress {
  running: boolean;
  current: Stage | null;
  stages: Stage[];
  pages: PageProgress[];
}

const LABELS: Record<string, string> = {
  pages: "Preprocess · classify · extract", plan: "Plan", kyc: "KYC", claims: "Claims", policy: "Policy",
  fraud: "Fraud", decide: "Decision", human_review: "Human review",
};
const PAGE_STEPS = ["preprocess", "classify", "extract"] as const;
const STEP_LABEL: Record<string, string> = { preprocess: "image → text", classify: "classifying", extract: "extracting fields" };

export function claimProgress(events: AuditEvent[], documents: CaseSummary[], running: boolean, awaitingHuman = false,
                              reviewed = false): Progress {
  const start = events.map((e) => e.event).lastIndexOf("claim_started");
  const run = start >= 0 ? events.slice(start) : [];
  const done = (agent: string) => run.filter((e) => e.event === "agent_completed" && e.actor === agent);
  const last = (agent: string) => done(agent).at(-1);
  const lastTime = run.at(-1)?.created_at;

  // Each page passes preprocess -> classify -> extract, one page at a time, in upload order.
  let runningAssigned = false;
  const pages: PageProgress[] = documents.map((d) => {
    const steps = run.filter((e) => e.event === "agent_completed" && e.doc_id === d.case_id);
    const extracted = steps.find((e) => e.actor === "extract");
    const typed = steps.find((e) => e.actor === "classify") ?? extracted;
    const failed = steps.some((e) => e.status === "error") || Boolean(extracted?.detail?.error);
    const read = steps.find((e) => e.actor === "preprocess");
    if (extracted) {
      return { doc_id: d.case_id, file_name: d.file_name, state: failed ? "error" : "done",
               doc_type: (typed?.detail?.doc_type ?? extracted.detail?.doc_type) as string,
               blurry: Boolean(read?.detail?.is_blurry || typed?.detail?.is_blurry) };
    }
    if (running && start >= 0 && !runningAssigned) {
      runningAssigned = true;
      const next = PAGE_STEPS.find((s) => !steps.some((e) => e.actor === s)) ?? "extract";
      return { doc_id: d.case_id, file_name: d.file_name, state: "running", step: next,
               doc_type: typed?.detail?.doc_type as string | undefined };
    }
    return { doc_id: d.case_id, file_name: d.file_name, state: "waiting" };
  });

  const planned = (last("plan")?.detail?.agents as string[] | undefined) ?? null;
  const hasClaimForm = pages.some((p) => p.doc_type === "CLAIM_FORM");
  const allRead = pages.length > 0 && pages.every((p) => p.state === "done" || p.state === "error");
  const finished = (s: Stage) => ["done", "error", "skipped"].includes(s.state);

  const stage = (key: string, ready: boolean, applies: boolean | null): Stage => {
    const e = last(key);
    if (e) return { key, label: LABELS[key], state: e.status === "error" ? "error" : "done", note: `${e.duration_ms ?? 0} ms`, detail: e.detail };
    if (applies === false) return { key, label: LABELS[key], state: "skipped", note: "not applicable" };
    if (running && ready) return { key, label: LABELS[key], state: "running", note: "running…", startedAt: lastTime };
    return { key, label: LABELS[key], state: "waiting", note: "waiting" };
  };

  const readCount = pages.filter((p) => p.state === "done" || p.state === "error").length;
  const current = pages.find((p) => p.state === "running");
  const pagesStage: Stage = allRead
    ? { key: "pages", label: LABELS.pages, state: "done", note: `${readCount}/${pages.length} pages` }
    : { key: "pages", label: LABELS.pages, state: running && start >= 0 ? "running" : "waiting",
        note: `page ${Math.min(readCount + 1, pages.length)} of ${pages.length}${current?.step ? ` · ${STEP_LABEL[current.step]}` : ""}`,
        startedAt: lastTime };

  const plan = stage("plan", allRead, null);
  const planDone = plan.state === "done";
  const kyc = stage("kyc", planDone, planned ? planned.includes("kyc") : null);
  const claims = stage("claims", planDone, planned ? planned.includes("claims") : null);
  const policy = stage("policy", finished(claims), planned ? planned.includes("claims") && hasClaimForm : null);
  const fraud = stage("fraud", planDone && [kyc, claims, policy].every(finished), planned ? planned.length > 0 : null);
  const decide = stage("decide", planDone && finished(fraud), null);

  const decided = last("decide")?.detail?.decision as string | undefined;
  const reviewDone = last("human_review");
  const human: Stage = reviewDone || reviewed
    ? { key: "human_review", label: LABELS.human_review, state: "done", note: String(reviewDone?.detail?.decision ?? "decided"), detail: reviewDone?.detail }
    : awaitingHuman || decided === "ESCALATE"
      ? { key: "human_review", label: LABELS.human_review, state: "running", note: "paused at checkpoint, waiting for a person", startedAt: last("decide")?.created_at }
      : decided ? { key: "human_review", label: LABELS.human_review, state: "skipped", note: "not needed" }
        : { key: "human_review", label: LABELS.human_review, state: "waiting", note: "only if escalated" };

  const stages = [pagesStage, plan, kyc, claims, policy, fraud, decide, human];
  return { running, current: stages.find((s) => s.state === "running") ?? null, stages, pages };
}
