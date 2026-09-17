import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

// Variable Inter with its optical-size axis: text sizes get the open text cut,
// headings and big figures automatically get the tighter display cut.
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap", axes: ["opsz"] });

export const metadata: Metadata = {
  title: "Poernama",
  description: "Sistem Poernama",
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
    <html lang="id" className={inter.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="font-sans">{children}</body>
    </html>
  );
}
