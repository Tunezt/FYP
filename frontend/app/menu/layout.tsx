"use client";

import { useEffect } from "react";

/** The guest's menu is read on a phone at the table, often outdoors — the same
 * forced light, high-contrast variant the POS uses, whatever the device says. */
export default function MenuLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "pos-light");
    return () => document.documentElement.removeAttribute("data-theme");
  }, []);

  return <div className="min-h-screen">{children}</div>;
}
