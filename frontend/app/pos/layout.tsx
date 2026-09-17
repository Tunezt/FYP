"use client";

import { useEffect } from "react";

/** The POS runs at a shop counter, often in bright daylight — force the light
 * high-contrast variant no matter what the device's color scheme says. */
export default function PosLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    // Remember the owner's theme so leaving the kiosk route does not wipe it.
    const previous = document.documentElement.getAttribute("data-theme");
    document.documentElement.setAttribute("data-theme", "pos-light");
    return () => {
      if (previous) document.documentElement.setAttribute("data-theme", previous);
      else document.documentElement.removeAttribute("data-theme");
    };
  }, []);

  return <div className="min-h-screen select-none">{children}</div>;
}
