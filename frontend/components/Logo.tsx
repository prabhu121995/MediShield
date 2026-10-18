// Shield mark used across the app. Decorative, so it is hidden from assistive tech.
export default function Logo({ className = "h-9 w-9" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="shield-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5b8cff" />
          <stop offset="1" stopColor="#2753e0" />
        </linearGradient>
      </defs>
      <path d="M16 2.5l10.5 3.8v8.2c0 6.6-4.4 12.3-10.5 14.9C9.9 26.8 5.5 21.1 5.5 14.5V6.3L16 2.5z" fill="url(#shield-grad)" />
      <path d="M11.5 15.8l3.1 3.1 6-6.2" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
