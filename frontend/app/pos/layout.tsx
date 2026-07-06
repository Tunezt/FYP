"use client";

import { useEffect } from "react";

/** The POS runs at a shop counter, often in bright daylight — force the light
 * high-contrast glass variant no matter what the device's color scheme says. */
export default function PosLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "pos-light");
    return () => document.documentElement.removeAttribute("data-theme");
  }, []);

  return <div className="min-h-screen select-none">{children}</div>;
}
