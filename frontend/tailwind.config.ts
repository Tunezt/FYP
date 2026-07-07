import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "media",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "var(--font-inter)",
          "-apple-system",
          "BlinkMacSystemFont",
          "SF Pro Display",
          "SF Pro Text",
          "Segoe UI",
          "sans-serif",
        ],
      },
      colors: {
        // Forest-green accent ramp (owner-approved reference, 2026-07-07;
        // supersedes the original blue-violet — see docs/progress.md).
        accent: {
          50: "#f0f7f2",
          100: "#dcefe3",
          200: "#bbdfc9",
          300: "#8ec7a6",
          400: "#5aa87e",
          500: "#3a9161",
          600: "#2b7a4e", // primary UI accent + light-mode chart series 1
          700: "#236743",
          800: "#1e5338",
          900: "#19442f",
        },
        terra: {
          // money-out / second chart series (validated vs the green)
          400: "#d99a6c",
          500: "#c2703d",
          600: "#a85c2f",
        },
      },
      backgroundImage: {
        "accent-gradient": "linear-gradient(135deg, #35895a 0%, #2b7a4e 55%, #1e5338 100%)",
        "accent-gradient-soft":
          "linear-gradient(135deg, rgba(43,122,78,0.10) 0%, rgba(43,122,78,0.05) 100%)",
      },
      boxShadow: {
        glass: "0 1px 2px rgba(24,32,26,0.04), 0 10px 30px rgba(24,32,26,0.07)",
        pop: "0 2px 6px rgba(24,32,26,0.06), 0 16px 44px rgba(24,32,26,0.14)",
        key: "0 1px 0 rgba(255,255,255,0.85) inset, 0 2px 6px rgba(24,32,26,0.08)",
      },
      borderRadius: {
        "4xl": "2rem",
      },
      animation: {
        "fade-up": "fadeUp 0.45s cubic-bezier(0.22, 1, 0.36, 1) both",
        "scale-in": "scaleIn 0.28s cubic-bezier(0.22, 1, 0.36, 1) both",
        shimmer: "shimmer 1.6s linear infinite",
      },
      keyframes: {
        fadeUp: {
          from: { opacity: "0", transform: "translateY(14px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        scaleIn: {
          from: { opacity: "0", transform: "scale(0.96)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        shimmer: {
          from: { backgroundPosition: "200% 0" },
          to: { backgroundPosition: "-200% 0" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
