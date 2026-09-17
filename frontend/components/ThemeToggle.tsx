"use client";

import { useEffect, useState } from "react";
import { IconMoon, IconSun } from "@/components/icons";

/** Sun/moon toggle for the owner dashboard. Light is the default; choosing dark
 * writes data-theme="dark" on <html> and persists to localStorage (read back
 * before paint by the init script in app/layout.tsx). POS forces its own theme
 * and never renders this. */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.getAttribute("data-theme") === "dark");
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    if (next) {
      document.documentElement.setAttribute("data-theme", "dark");
      try {
        localStorage.setItem("theme", "dark");
      } catch {}
    } else {
      document.documentElement.removeAttribute("data-theme");
      try {
        localStorage.setItem("theme", "light");
      } catch {}
    }
  }

  const label = dark ? "Mode terang" : "Mode gelap";

  return (
    <button
      onClick={toggle}
      title={label}
      aria-label={label}
      className={`icon-btn ${className}`}
    >
      {dark ? <IconSun className="h-[18px] w-[18px]" /> : <IconMoon className="h-[18px] w-[18px]" />}
    </button>
  );
}
