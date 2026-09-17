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
          "var(--font-inter)",
          "-apple-system",
          "BlinkMacSystemFont",
          "SF Pro Text",
          "Segoe UI",
          "sans-serif",
        ],
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
      // One radius scale for the whole product: cards 16, sheets 20,
      // buttons and fields 12, small controls 10.
      borderRadius: {
        lg: "0.5rem",
        xl: "0.625rem",
        "2xl": "0.75rem",
        "3xl": "1rem",
        "4xl": "1.25rem",
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
