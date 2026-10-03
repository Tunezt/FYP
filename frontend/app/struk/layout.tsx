"use client";

import { useEffect } from "react";

/** A scanned receipt is read on a customer's phone (till-5b) — the same
 * forced light, high-contrast variant the POS and the QR menu use. */
export default function ReceiptLayout({ children }: { children: React.ReactNode }) {
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
