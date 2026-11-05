import type { Config } from "tailwindcss";

const config: Config = {
  // The app's own theme switch (data-theme on <html>), not the OS setting:
  // `dark:` must agree with the tokens in globals.css.
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "var(--font-display)",
          "var(--font-inter)",
          "-apple-system",
          "BlinkMacSystemFont",
          "SF Pro Text",
          "Segoe UI",
          "sans-serif",
        ],
        // Titles and the big figures (see .page-title / .figure in globals.css).
        display: ["var(--font-display)", "var(--font-inter)", "-apple-system", "sans-serif"],
      },
      // Inter is variable: "bold" sits a little under 700 so headings and
      // figures stay firm without turning heavy.
      fontWeight: {
        bold: "660",
      },
      colors: {
        // Forest-green ramp (owner-approved identity). Prefer the CSS tokens
        // (--accent, --accent-fill) in components: they follow the theme.
        accent: {
          50: "#f0f6f2",
          100: "#dcebe2",
          200: "#b9d6c5",
          300: "#8cbba1",
          400: "#5c9c79",
          500: "#3a805b",
          600: "#2a6b48",
          700: "#225a3c",
          800: "#1d4a33",
          900: "#193d2b",
        },
        terra: {
          400: "#d99a6c",
          500: "#b8663a",
          600: "#9d5530",
        },
      },
      // One radius scale for the whole product: panes 24, sheets 28,
      // fields and inner blocks 16, tiles 12. Buttons, chips and segmented
      // controls are full pills.
      borderRadius: {
        lg: "0.625rem",
        xl: "0.75rem",
        "2xl": "1rem",
        "3xl": "1.5rem",
        "4xl": "1.75rem",
      },
      boxShadow: {
        glass: "var(--shadow-card)",
        pop: "var(--shadow-float)",
        key: "var(--shadow-raised)",
      },
      animation: {
        "scale-in": "scaleIn 0.22s cubic-bezier(0.22, 1, 0.36, 1) backwards",
        shimmer: "shimmer 1.6s linear infinite",
      },
      keyframes: {
        scaleIn: {
          from: { opacity: "0", transform: "scale(0.97)" },
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
