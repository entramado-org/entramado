import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#070a12",
          900: "#0a0e1a",
          800: "#111726",
          700: "#1a2236",
          600: "#273047",
        },
        accent: {
          DEFAULT: "#38e1c4",
          glow: "#5ef2d8",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "pulse-glow": { "0%,100%": { opacity: "0.6" }, "50%": { opacity: "1" } },
        "slide-in-r": { from: { transform: "translateX(20px)", opacity: "0" }, to: { transform: "translateX(0)", opacity: "1" } },
      },
      animation: {
        "fade-in": "fade-in 0.6s ease-out both",
        "pulse-glow": "pulse-glow 3s ease-in-out infinite",
        "slide-in-r": "slide-in-r 240ms cubic-bezier(.2,.7,.2,1) both",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
export default config;
