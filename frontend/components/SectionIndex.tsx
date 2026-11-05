"use client";

import { useEffect, useRef, useState } from "react";
import { SlideIndicator } from "@/components/motion";

export type IndexEntry = { id: string; label: string };

/** Where a long page keeps its contents: a rail beside the sections on a
 * wide screen, a row of chips that stays under the thumb on a narrow one.
 * Either way it follows the reading: the section in view is the one marked
 * (one pill that travels, as in the sidebar), and a tap goes straight there. */
export function SectionIndex({ entries, variant }: { entries: IndexEntry[]; variant: "rail" | "chips" }) {
  const [current, setCurrent] = useState(entries[0]?.id ?? "");
  const rowRef = useRef<HTMLDivElement>(null);
  // While a jump is scrolling, the observer must not drag the mark back
  // through every section on the way.
  const jumping = useRef<number | null>(null);

  useEffect(() => {
    let frame = 0;
    // The section being read is the last one whose heading has passed the
    // upper third of the screen; once the page can scroll no further, the
    // last section is, even if it is too short to reach that line.
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

  // Keep the marked chip in view as the row scrolls past it.
  useEffect(() => {
    if (variant !== "chips") return;
    const chip = rowRef.current?.querySelector<HTMLElement>(`[data-id="${current}"]`);
    const row = rowRef.current;
    if (!chip || !row) return;
    const left = chip.offsetLeft - row.clientWidth / 2 + chip.clientWidth / 2;
    row.scrollTo({ left, behavior: "smooth" });
  }, [current, variant]);

  function go(id: string) {
    const el = document.getElementById(id);
    if (!el) return;
    setCurrent(id);
    if (jumping.current !== null) window.clearTimeout(jumping.current);
    jumping.current = window.setTimeout(() => (jumping.current = null), 900);
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
    // Land on the section for keyboard and screen-reader users too.
    el.setAttribute("tabindex", "-1");
    el.focus({ preventScroll: true });
  }

  if (variant === "rail")
    return (
      <nav aria-label="Isi halaman" className="index-rail has-slide flex flex-col">
        <SlideIndicator className="index-mark" watch={current} />
        {entries.map((e) => (
          <a
            key={e.id}
            href={`#${e.id}`}
            data-slide-active={e.id === current}
            aria-current={e.id === current ? "location" : undefined}
            onClick={(ev) => {
              ev.preventDefault();
              go(e.id);
            }}
            className="index-link"
          >
            {e.label}
          </a>
        ))}
      </nav>
    );

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
