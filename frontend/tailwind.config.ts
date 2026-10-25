import type { Config } from "tailwindcss";

const forestPalette = { DEFAULT: "#165B45", dark: "#0F4232", soft: "#E4F0EB" };

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // 1. Warm Ivory / Cream canvas
        ivory: "#F7F4EC",
        // 2. Warm Paper elevated surface
        paper: "#FFFDF8",
        // 3. Pale Butter yellow accent & architectural backdrop
        butter: { DEFAULT: "#F6E7B2", soft: "#FBF3D5", line: "#E5CE82", ink: "#5C4308" },
        mist: "#FBF3D5",
        // 4. Warm Stone hairlines & muted fills
        stone: { DEFAULT: "#E3DDD1", dark: "#C9C1B2" },
        slate: { soft: "#EFECE3" },
        // 5. Deep Forest Ink text hierarchy
        ink: { DEFAULT: "#10231C", soft: "#3A4D45", faint: "#5C6E66" },
        // 6. Clinical Forest Green primary accent (aliased to teal & cobalt for app-wide consistency)
        forest: forestPalette,
        teal: forestPalette,
        cobalt: forestPalette,
        // Semantic clinical status tokens (always paired with text labels)
        moss: { DEFAULT: "#165B45", soft: "#E4F0EB", line: "#B2D6C7" },
        amber: { clinical: "#8A5007", soft: "#FBF1DC", line: "#E6CB94" },
        crimson: { clinical: "#A8221A", soft: "#FCEBE9", line: "#EBB9B4" },
      },
      fontFamily: {
        display: ["'Bricolage Grotesque Variable'", "'Avenir Next'", "'Segoe UI'", "system-ui", "sans-serif"],
        serif: ["'Bricolage Grotesque Variable'", "'Avenir Next'", "'Segoe UI'", "system-ui", "sans-serif"],
        sans: ["'Instrument Sans Variable'", "'Segoe UI'", "system-ui", "-apple-system", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderRadius: { DEFAULT: "6px", md: "6px", lg: "8px", xl: "12px", "2xl": "16px" },
      boxShadow: {
        card: "0 1px 0 rgba(16,35,28,.04)",
        float: "0 14px 34px -14px rgba(16,35,28,.16), 0 1px 0 rgba(16,35,28,.05)",
      },
      keyframes: {
        rise: { from: { opacity: "0", transform: "translateY(10px)" }, to: { opacity: "1", transform: "none" } },
      },
      animation: { rise: "rise 420ms cubic-bezier(.2,.7,.2,1) both" },
    },
  },
  plugins: [],
};
export default config;
