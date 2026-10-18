// Shown while a page's data loads. The shape matches the dashboard, so nothing jumps when the content arrives.
export default function Loading() {
  return (
    <div className="space-y-6" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading</span>
      <div className="space-y-2">
        <div className="skeleton h-8 w-48" />
        <div className="skeleton h-4 w-72" />
      </div>
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
          <div key={i} className="card space-y-4 p-4 sm:p-5">
            <div className="flex justify-between">
              <div className="skeleton h-3 w-20" />
              <div className="skeleton h-9 w-9 rounded-xl" />
            </div>
            <div className="skeleton h-8 w-16" />
            <div className="skeleton h-3 w-28" />
          </div>
        ))}
      </div>
      <div className="card overflow-hidden">
        <div className="flex gap-3 border-b border-slate-200 px-4 py-3">
          {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-8 w-24" />)}
        </div>
        <div className="divide-y divide-slate-100">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="flex items-center gap-4 px-5 py-4">
              <div className="skeleton h-4 flex-1" />
              <div className="skeleton hidden h-4 w-24 sm:block" />
              <div className="skeleton h-6 w-20 rounded-full" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
