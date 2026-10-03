"use client";

import { useEffect } from "react";
import { POS_THEME_KEY } from "@/components/ThemeToggle";

/** The POS runs at a shop counter, often in bright daylight — light and
 * high-contrast unless the cashier has chosen dark on this tablet (an evening
 * shift, a dim bar). The device's own colour scheme is never consulted, and
 * the owner's dashboard choice is left alone. */
export default function PosLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const root = document.documentElement;
    // Remember the owner's theme so leaving the kiosk route does not wipe it.
    const previous = root.getAttribute("data-theme");
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(POS_THEME_KEY);
    } catch {}
    root.setAttribute("data-theme", saved === "dark" ? "dark" : "pos-light");
    root.setAttribute("data-surface", "pos");
    return () => {
      root.removeAttribute("data-surface");
      if (previous) root.setAttribute("data-theme", previous);
      else root.removeAttribute("data-theme");
    };
  }, []);

  return <div className="min-h-screen select-none">{children}</div>;
}
