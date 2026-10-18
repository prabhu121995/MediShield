import UploadPanel from "@/components/UploadPanel";

export default function UploadPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="animate-fade-up">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Upload documents</h1>
        <p className="mt-1 text-sm text-slate-500">
          Claim forms, discharge summaries, ID documents, policy amendments and prescriptions. Select one or many files.
          Each is processed in the background, and the Uploads panel shows its progress.
        </p>
      </div>
      <section className="card animate-fade-up p-5 [animation-delay:60ms] sm:p-7">
        <UploadPanel />
      </section>
    </div>
  );
}
