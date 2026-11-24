"use client";

import { useEffect, useRef, useState } from "react";
import { SlideIndicator } from "@/components/motion";

export type IndexEntry = { id: string; label: string };

/** Which section of a long page is being read, and a way to jump to one.
 * The section in view is the last one whose heading has passed the upper
 * third of the screen; a jump scrolls smoothly and lands focus on the
 * section for keyboard and screen-reader users. */
export function useSectionSpy(entries: IndexEntry[]) {
  const [current, setCurrent] = useState(entries[0]?.id ?? "");
  // While a jump is scrolling, the observer must not drag the mark back
  // through every section on the way.
  const jumping = useRef<number | null>(null);

  useEffect(() => {
    let frame = 0;
    // Once the page can scroll no further, the last section is the one being
    // read, even if it is too short to reach that line.
    const measure = () => {
      frame = 0;
      if (jumping.current !== null) return;
      const line = window.innerHeight * 0.3;
      const scroller = document.scrollingElement ?? document.documentElement;
      const atEnd = scroller.scrollTop + window.innerHeight >= scroller.scrollHeight - 4;
      let next = entries[0]?.id ?? "";
      for (const e of entries) {
        const el = document.getElementById(e.id);
        if (el && el.getBoundingClientRect().top <= line) next = e.id;
      }
      if (atEnd && entries.length > 0) next = entries[entries.length - 1].id;
      setCurrent(next);
    };
    const schedule = () => {
      if (frame === 0) frame = window.requestAnimationFrame(measure);
    };
    measure();
    // Capture, so a scrolling panel is heard as well as the page itself.
    document.addEventListener("scroll", schedule, { capture: true, passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      if (frame !== 0) window.cancelAnimationFrame(frame);
      document.removeEventListener("scroll", schedule, { capture: true });
      window.removeEventListener("resize", schedule);
    };
  }, [entries]);

  function go(id: string) {
    const el = document.getElementById(id);
    if (!el) return;
    setCurrent(id);
    if (jumping.current !== null) window.clearTimeout(jumping.current);
    jumping.current = window.setTimeout(() => (jumping.current = null), 900);
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
    el.setAttribute("tabindex", "-1");
    el.focus({ preventScroll: true });
  }

  return { current, go };
}

/** A link like /settings#struk: the sections above it are still loading when
 * the browser first scrolls, so the target moves. Keep landing on it until the
 * page settles (about 3 s) or the reader takes over by scrolling. */
export function useHashLanding() {
  useEffect(() => {
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (!id) return;
    const stop = () => window.clearInterval(timer);
    const land = () => {
      const el = document.getElementById(id);
      if (!el) return;
      const top = el.getBoundingClientRect().top;
      const want = parseFloat(getComputedStyle(el).scrollMarginTop) || 0;
      if (Math.abs(top - want) > 4) window.scrollBy({ top: top - want, behavior: "auto" });
    };
    const timer = window.setInterval(land, 250);
    const quit = window.setTimeout(stop, 3000);
    const events = ["wheel", "touchstart", "keydown", "mousedown"] as const;
    for (const e of events) window.addEventListener(e, stop, { once: true, passive: true });
    land();
    return () => {
      stop();
      window.clearTimeout(quit);
      for (const e of events) window.removeEventListener(e, stop);
    };
  }, []);
}

/** A long page's contents as a row of chips that stays under the thumb on a
 * narrow screen. On a wide one the same list lives in the sidebar
 * (SettingsSubnav). It follows the reading: the section in view is the one
 * marked (one pill that travels), and a tap goes straight there. */
export function SectionIndex({ entries }: { entries: IndexEntry[] }) {
  const { current, go } = useSectionSpy(entries);
  const rowRef = useRef<HTMLDivElement>(null);

  // Keep the marked chip in view as the row scrolls past it.
  useEffect(() => {
    const chip = rowRef.current?.querySelector<HTMLElement>(`[data-id="${current}"]`);
    const row = rowRef.current;
    if (!chip || !row) return;
    const left = chip.offsetLeft - row.clientWidth / 2 + chip.clientWidth / 2;
    row.scrollTo({ left, behavior: "smooth" });
  }, [current]);

  return (
    <nav aria-label="Isi halaman">
      <div ref={rowRef} className="chip-row has-slide flex gap-1.5 overflow-x-auto">
        <SlideIndicator className="index-chip-mark" watch={current} />
        {entries.map((e) => (
          <a
            key={e.id}
            href={`#${e.id}`}
            data-id={e.id}
            data-slide-active={e.id === current}
            aria-current={e.id === current ? "location" : undefined}
            onClick={(ev) => {
              ev.preventDefault();
              go(e.id);
            }}
            className="index-chip"
          >
            {e.label}
          </a>
        ))}
      </div>
    </nav>
  );
}
