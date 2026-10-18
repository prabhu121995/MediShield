import type { CSSProperties } from "react";

const SHIELD =
  "M16 2.5l10.5 3.8v8.2c0 6.6-4.4 12.3-10.5 14.9C9.9 26.8 5.5 21.1 5.5 14.5V6.3L16 2.5z";

// Scale elements around their own centre, not the SVG origin.
const centred: CSSProperties = { transformBox: "fill-box", transformOrigin: "center" };

// Animated shield with a bot inside. Decorative, so it is hidden from assistive tech.
// Motion is disabled globally under prefers-reduced-motion (see globals.css).
export default function LoginHero({ className = "h-20 w-20" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="login-shield-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5b8cff" />
          <stop offset="1" stopColor="#2753e0" />
        </linearGradient>
        <linearGradient id="login-scan-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0.6" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <clipPath id="login-shield-clip">
          <path d={SHIELD} />
        </clipPath>
      </defs>

      <path d={SHIELD} fill="url(#login-shield-grad)" />

      {/* Scan line sweeping down the shield */}
      <g clipPath="url(#login-shield-clip)">
        <rect x="0" y="-8" width="32" height="8" fill="url(#login-scan-grad)" className="animate-scan" />
      </g>

      {/* Antenna */}
      <line x1="16" y1="11" x2="16" y2="8.6" stroke="#fff" strokeWidth="1.2" strokeLinecap="round" />
      <circle cx="16" cy="7.8" r="1.1" fill="#fff" className="animate-antenna-ping" style={centred} />

      {/* Head */}
      <rect x="10.5" y="11" width="11" height="9" rx="2.5" fill="#fff" />
      <rect x="13.2" y="14" width="1.9" height="2.6" rx="0.95" fill="#2753e0" className="animate-blink" style={centred} />
      <rect x="16.9" y="14" width="1.9" height="2.6" rx="0.95" fill="#2753e0" className="animate-blink" style={centred} />
      <path d="M13.8 18h4.4" stroke="#2753e0" strokeWidth="1" strokeLinecap="round" />
    </svg>
  );
}
