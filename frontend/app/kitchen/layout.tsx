"use client";

import { useEffect } from "react";

/** The kitchen screen hangs above a hot line under bright light — the same
 * forced light, high-contrast variant the POS uses. */
export default function KitchenLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "pos-light");
    return () => document.documentElement.removeAttribute("data-theme");
  }, []);

  return <div className="min-h-screen select-none">{children}</div>;
}
