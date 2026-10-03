"use client";

import { useEffect, useState } from "react";
import { IconMoon, IconSun } from "@/components/icons";

/** The till keeps its own choice (a tablet on a counter and the owner's phone
 * rarely want the same thing), under its own key. */
export const POS_THEME_KEY = "pos-theme";

/** Sun/moon toggle. Light is the default; choosing dark writes
 * data-theme="dark" on <html> and persists to localStorage.
 *
 * scope "app" is the owner dashboard (read back before paint by the init
 * script in app/layout.tsx). scope "pos" is the till: its light is the
 * "pos-light" variant, and app/pos/layout.tsx applies the saved choice. */
export function ThemeToggle({ className = "", scope = "app" }: { className?: string; scope?: "app" | "pos" }) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.getAttribute("data-theme") === "dark");
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    const root = document.documentElement;
    if (next) root.setAttribute("data-theme", "dark");
    else if (scope === "pos") root.setAttribute("data-theme", "pos-light");
    else root.removeAttribute("data-theme");
    try {
      localStorage.setItem(scope === "pos" ? POS_THEME_KEY : "theme", next ? "dark" : "light");
    } catch {}
  }

  const label = dark ? "Mode terang" : "Mode gelap";

  return (
    <button
      onClick={toggle}
      title={label}
      aria-label={label}
      aria-pressed={dark}
      className={`icon-btn ${className}`}
    >
      {dark ? <IconSun className="h-[18px] w-[18px]" /> : <IconMoon className="h-[18px] w-[18px]" />}
    </button>
  );
}
