"use client";

import { useEffect, useState } from "react";
import { flushSync } from "react-dom";
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

  function apply(next: boolean) {
    setDark(next);
    const root = document.documentElement;
    if (next) root.setAttribute("data-theme", "dark");
    else if (scope === "pos") root.setAttribute("data-theme", "pos-light");
    else root.removeAttribute("data-theme");
    try {
      localStorage.setItem(scope === "pos" ? POS_THEME_KEY : "theme", next ? "dark" : "light");
    } catch {}
  }

  /** The new theme opens as a circle from the switch that was pressed, like
   * a lamp being turned down (or up) from that point. Where the browser has
   * no View Transitions, or the user asked for less motion, or this is the
   * till, the theme simply changes. */
  function toggle(e: React.MouseEvent<HTMLButtonElement>) {
    const next = !dark;
    const root = document.documentElement;
    const doc = document as Document & {
      startViewTransition?: (update: () => void) => { finished: Promise<void> };
    };
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!doc.startViewTransition || calm || scope === "pos") {
      apply(next);
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
    root.style.setProperty("--vt-x", `${x}px`);
    root.style.setProperty("--vt-y", `${y}px`);
    root.style.setProperty("--vt-r", `${radius}px`);
    root.classList.add("theme-vt");
    const transition = doc.startViewTransition(() => flushSync(() => apply(next)));
    transition.finished.finally(() => root.classList.remove("theme-vt"));
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
      {/* Keyed, so the icon that comes in turns into place. */}
      <span key={String(dark)} className="icon-swap flex">
        {dark ? <IconSun className="h-[18px] w-[18px]" /> : <IconMoon className="h-[18px] w-[18px]" />}
      </span>
    </button>
  );
}
