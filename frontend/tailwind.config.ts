import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ivory: "#F7F6EF",
        paper: "#FFFEFB",
        stone: { DEFAULT: "#D8DED3", dark: "#BFC9BD" },
        ink: { DEFAULT: "#20342E", soft: "#4C6058", faint: "#65756E" },
        teal: { DEFAULT: "#103F35", dark: "#0B332B", soft: "#E1E8DC" },
        amber: { clinical: "#A25A0B", soft: "#FCF1E1", line: "#EBCB9C" },
        crimson: { clinical: "#A82420", soft: "#FBEAE8", line: "#E8B4B0" },
        slate: { soft: "#EEF1F4" },
      },
      fontFamily: {
        serif: ["'Iowan Old Style'", "'Palatino Linotype'", "Palatino", "Georgia", "serif"],
        sans: ["'Avenir Next'", "'Segoe UI'", "Inter", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: { card: "0 1px 0 rgba(19,36,58,.04), 0 1px 3px rgba(19,36,58,.06)" },
    },
  },
  plugins: [],
};
export default config;
