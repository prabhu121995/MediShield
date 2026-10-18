"use client";

// Upload queue shared by the whole app. An upload is saved on the server at once,
// then processed in the background. This provider polls each case until it
// finishes, so navigating between pages or reloading does not lose progress.
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { DocType, getCase, retryCase, uploadCase, uploadPatientCases } from "@/lib/api";

export type JobState = "uploading" | "processing" | "done" | "failed";

export interface Job {
  key: string;
  name: string;
  caseId?: string;
  state: JobState;
  decision?: string | null;
  error?: string;
}

export interface EnqueueOptions {
  category?: DocType;
  /** Attach the files to a patient. They go up in one multi-part request. */
  patientId?: string;
}

interface UploadContextValue {
  jobs: Job[];
  enqueue: (files: File[], options?: EnqueueOptions) => void;
  retry: (key: string) => void;
  dismiss: (key: string) => void;
  clearFinished: () => void;
}

const STORAGE_KEY = "ms_upload_jobs";
const POLL_MS = 1500;
const UploadContext = createContext<UploadContextValue | null>(null);

const uid = () => Math.random().toString(36).slice(2, 10);

function persist(jobs: Job[]) {
  try {
    const keep = jobs.filter((j) => j.caseId).slice(0, 25).map((j) => ({ key: j.key, name: j.name, caseId: j.caseId }));
    localStorage.setItem(STORAGE_KEY, JSON.stringify(keep));
  } catch {
    /* private mode or storage disabled: progress still works in-memory */
  }
}

export function UploadProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const loaded = useRef(false);

  const update = useCallback((key: string, patch: Partial<Job>) => {
    setJobs((prev) => prev.map((j) => (j.key === key ? { ...j, ...patch } : j)));
  }, []);

  // Restore jobs saved before a reload, so their progress resumes.
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    try {
      const saved: { key: string; name: string; caseId: string }[] = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
      setJobs(saved.map((s) => ({ ...s, state: "processing" as JobState })));
    } catch {
      /* ignore corrupt storage */
    }
  }, []);

  useEffect(() => persist(jobs), [jobs]);

  // Poll every job that is still running.
  useEffect(() => {
    const active = jobs.filter((j) => j.caseId && (j.state === "processing" || j.state === "uploading"));
    if (active.length === 0) return;
    const timer = setInterval(async () => {
      for (const job of active) {
        try {
          const c = await getCase(job.caseId!);
          if (c.status === "PROCESSING") continue;
          if (c.status === "FAILED") {
            update(job.key, { state: "failed", error: String(c.result.error ?? "processing failed") });
          } else {
            update(job.key, { state: "done", decision: c.decision });
            router.refresh();
          }
        } catch {
          /* transient network error: keep polling */
        }
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [jobs, update, router]);

  const enqueue = useCallback((files: File[], options: EnqueueOptions = {}) => {
    const fresh: Job[] = files.map((f) => ({ key: uid(), name: f.name, state: "uploading" }));
    setJobs((prev) => [...fresh, ...prev]);

    if (options.patientId) {
      // One request for the whole batch; the server returns the cases in the same order.
      uploadPatientCases(options.patientId, files, options.category)
        .then((cases) => fresh.forEach((job, i) => update(job.key, { caseId: cases[i].case_id, state: "processing" })))
        .catch((err: Error) => fresh.forEach((job) => update(job.key, { state: "failed", error: err.message })));
      return;
    }

    fresh.forEach(async (job, i) => {
      try {
        const created = await uploadCase(files[i], options.category);
        update(job.key, { caseId: created.case_id, state: "processing" });
      } catch (err) {
        update(job.key, { state: "failed", error: (err as Error).message });
      }
    });
  }, [update]);

  const retry = useCallback(async (key: string) => {
    const job = jobs.find((j) => j.key === key);
    if (!job?.caseId) return;
    update(key, { state: "processing", error: undefined });
    try {
      await retryCase(job.caseId);
    } catch (err) {
      update(key, { state: "failed", error: (err as Error).message });
    }
  }, [jobs, update]);

  const dismiss = useCallback((key: string) => setJobs((prev) => prev.filter((j) => j.key !== key)), []);
  const clearFinished = useCallback(() => setJobs((prev) => prev.filter((j) => j.state === "uploading" || j.state === "processing")), []);

  const value = useMemo(() => ({ jobs, enqueue, retry, dismiss, clearFinished }), [jobs, enqueue, retry, dismiss, clearFinished]);
  return <UploadContext.Provider value={value}>{children}</UploadContext.Provider>;
}

export function useUploads() {
  const ctx = useContext(UploadContext);
  if (!ctx) throw new Error("useUploads must be used inside UploadProvider");
  return ctx;
}
