import type { Config } from "tailwindcss";

// e-ink / engineering-instrument system. Colour only where state needs it.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: { DEFAULT: "#F4F4EF", raised: "#FAFAF6", sunk: "#ECECE6" },
        ink: { DEFAULT: "#11110F", 2: "#66665F", 3: "#9B9B93" },
        hairline: { DEFAULT: "#D7D7CF", strong: "#B9B9B0" },
        allow: { DEFAULT: "#3E6B50", bg: "#E9EFE9" },
        deny: { DEFAULT: "#B4322A", bg: "#F5E6E3" },
        approval: { DEFAULT: "#9A6B12", bg: "#F5EEDC" },
        hold: { DEFAULT: "#4A4A8A", bg: "#E8E8F2" },
      },
      fontFamily: {
        sans: ["Geist", "Inter", "IBM Plex Sans", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "IBM Plex Mono", "JetBrains Mono", "Consolas", "ui-monospace", "monospace"],
        dot: ["Geist Mono", "IBM Plex Mono", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", { lineHeight: "14px", letterSpacing: "0.08em" }],
        xs: ["11px", { lineHeight: "16px" }],
        sm: ["12.5px", { lineHeight: "18px" }],
        base: ["13.5px", { lineHeight: "20px" }],
      },
      borderRadius: { none: "0", sm: "2px", DEFAULT: "3px", md: "4px", lg: "6px" },
      boxShadow: { none: "none", hairline: "0 0 0 1px #D7D7CF" },
      keyframes: {
        pulse2: { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
        dash: { to: { strokeDashoffset: "-24" } },
      },
      animation: { pulse2: "pulse2 1.6s ease-in-out infinite", dash: "dash 1.2s linear infinite" },
    },
  },
  plugins: [],
} satisfies Config;
