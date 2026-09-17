"use client";

import { useEffect } from "react";

/** The guest's menu is read on a phone at the table, often outdoors — the same
 * forced light, high-contrast variant the POS uses, whatever the device says. */
export default function MenuLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    // Remember the owner's theme so leaving the kiosk route does not wipe it.
    const previous = document.documentElement.getAttribute("data-theme");
    document.documentElement.setAttribute("data-theme", "pos-light");
    return () => {
      if (previous) document.documentElement.setAttribute("data-theme", previous);
      else document.documentElement.removeAttribute("data-theme");
    };
  }, []);

  return <div className="min-h-screen">{children}</div>;
}
