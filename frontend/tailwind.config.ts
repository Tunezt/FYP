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
        accent: {
          50: "#eef1ff",
          100: "#dfe4ff",
          200: "#c5ccff",
          300: "#a2a9fe",
          400: "#7f7ffa",
          500: "#5e5ce6", // iOS indigo — primary accent
          600: "#4f46d6",
          700: "#4238b8",
          800: "#373094",
          900: "#302e75",
        },
        violet: {
          500: "#8b5cf6",
        },
      },
      backgroundImage: {
        "accent-gradient": "linear-gradient(135deg, #5e5ce6 0%, #8b5cf6 55%, #a855f7 100%)",
        "accent-gradient-soft":
          "linear-gradient(135deg, rgba(94,92,230,0.14) 0%, rgba(139,92,246,0.10) 100%)",
      },
      boxShadow: {
        glass: "0 1px 1px rgba(255,255,255,0.6) inset, 0 8px 32px rgba(30,30,60,0.12)",
        "glass-dark": "0 1px 0 rgba(255,255,255,0.08) inset, 0 8px 32px rgba(0,0,0,0.45)",
        pop: "0 2px 8px rgba(30,30,60,0.08), 0 12px 40px rgba(30,30,60,0.16)",
        key: "0 1px 0 rgba(255,255,255,0.9) inset, 0 2px 6px rgba(30,30,60,0.10)",
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
