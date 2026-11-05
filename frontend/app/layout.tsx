import type { Metadata, Viewport } from "next";
import { Inter, Outfit } from "next/font/google";
import "./globals.css";

// Outfit is the voice of the product: geometric and light, lighter still as
// it gets bigger. Inter stays loaded for one job only: numbers that must line
// up in a column (Outfit has no tabular figures).
const outfit = Outfit({ subsets: ["latin"], variable: "--font-display", display: "swap" });
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap", axes: ["opsz"] });

export const metadata: Metadata = {
  title: "Poernama",
  description: "Sistem Poernama",
  // Meta Business domain verification for the Poernama portfolio: must sit in
  // the server-rendered <head>, so it lives here and not in client code.
  other: { "facebook-domain-verification": "w6inzuyav8bdljzf4r98z0tvii4wfv" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f0f0ed" },
    { media: "(prefers-color-scheme: dark)", color: "#19191b" },
  ],
};

// Applies a saved dark preference before first paint so there's no flash of the
// (default) light theme on reload. Light is the default; only "dark" opts in.
// Because it mutates <html> before hydration, <html> carries suppressHydrationWarning
// (one level deep only — mismatches in children are still reported).
const themeInitScript = `(function(){try{if(localStorage.getItem("theme")==="dark"){document.documentElement.setAttribute("data-theme","dark");}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="id" className={`${inter.variable} ${outfit.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="font-sans">{children}</body>
    </html>
  );
}
