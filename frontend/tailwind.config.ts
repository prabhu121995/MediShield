import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#dce8ff",
          500: "#3b6cf6",
          600: "#2753e0",
          700: "#1f43b8",
        },
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.96)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        shake: {
          "0%, 100%": { transform: "translateX(0)" },
          "20%, 60%": { transform: "translateX(-6px)" },
          "40%, 80%": { transform: "translateX(6px)" },
        },
        shimmer: {
          from: { backgroundPosition: "-200% 0" },
          to: { backgroundPosition: "200% 0" },
        },
        blob: {
          "0%, 100%": { transform: "translate(0,0) scale(1)" },
          "33%": { transform: "translate(30px,-40px) scale(1.08)" },
          "66%": { transform: "translate(-20px,20px) scale(0.95)" },
        },
        "pulse-dot": {
          "0%": { boxShadow: "0 0 0 0 rgba(245,158,11,0.55)" },
          "70%": { boxShadow: "0 0 0 8px rgba(245,158,11,0)" },
          "100%": { boxShadow: "0 0 0 0 rgba(245,158,11,0)" },
        },
        progress: {
          from: { width: "0%" },
          to: { width: "100%" },
        },
        scan: {
          "0%": { transform: "translateY(0)", opacity: "0" },
          "10%, 90%": { opacity: "1" },
          "100%": { transform: "translateY(40px)", opacity: "0" },
        },
        blink: {
          "0%, 90%, 100%": { transform: "scaleY(1)" },
          "94%": { transform: "scaleY(0.1)" },
        },
        "antenna-ping": {
          "0%, 100%": { opacity: "0.45", transform: "scale(0.9)" },
          "50%": { opacity: "1", transform: "scale(1.25)" },
        },
        ripple: {
          "0%": { transform: "scale(0.85)", opacity: "0.6" },
          "100%": { transform: "scale(1.6)", opacity: "0" },
        },
        // A short dance, then a rest: hop, wiggle left and right, settle.
        dance: {
          "0%, 55%, 100%": { transform: "translateY(0) rotate(0)" },
          "60%": { transform: "translateY(-8px) rotate(-8deg)" },
          "66%": { transform: "translateY(0) rotate(8deg)" },
          "72%": { transform: "translateY(-5px) rotate(-6deg)" },
          "78%": { transform: "translateY(0) rotate(5deg)" },
          "84%": { transform: "translateY(-2px) rotate(-2deg)" },
          "90%": { transform: "translateY(0) rotate(0)" },
        },
        wave: {
          "0%, 50%, 100%": { transform: "rotate(0)" },
          "10%, 30%": { transform: "rotate(16deg)" },
          "20%, 40%": { transform: "rotate(-10deg)" },
        },
        "bubble-in": {
          "0%": { opacity: "0", transform: "translateY(8px) scale(0.7)" },
          "60%": { opacity: "1", transform: "translateY(-2px) scale(1.04)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.35s ease-out both",
        "fade-up": "fade-up 0.45s cubic-bezier(0.16,1,0.3,1) both",
        "scale-in": "scale-in 0.3s cubic-bezier(0.16,1,0.3,1) both",
        shake: "shake 0.5s ease-in-out",
        shimmer: "shimmer 1.6s linear infinite",
        blob: "blob 14s ease-in-out infinite",
        "pulse-dot": "pulse-dot 1.8s ease-out infinite",
        progress: "progress 2.5s ease-in-out infinite",
        scan: "scan 2.8s ease-in-out infinite",
        blink: "blink 4s ease-in-out infinite",
        "antenna-ping": "antenna-ping 1.6s ease-in-out infinite",
        ripple: "ripple 2.4s ease-out infinite",
        dance: "dance 3s ease-in-out infinite",
        wave: "wave 2s ease-in-out infinite",
        "bubble-in": "bubble-in 0.5s cubic-bezier(0.16,1,0.3,1) 0.4s both",
      },
    },
  },
  plugins: [],
};

export default config;
