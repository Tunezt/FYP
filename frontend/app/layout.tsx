import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: "Warung Pintar",
  description:
    "AI business management for small F&B and retail — run your whole business from WhatsApp.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f2f3f8" },
    { media: "(prefers-color-scheme: dark)", color: "#0c0c14" },
  ],
};

// Applies a saved dark preference before first paint so there's no flash of the
// (default) light theme on reload. Light is the default; only "dark" opts in.
const themeInitScript = `(function(){try{if(localStorage.getItem("theme")==="dark"){document.documentElement.setAttribute("data-theme","dark");}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="font-sans">{children}</body>
    </html>
  );
}
