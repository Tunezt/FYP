"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import NumberFlow from "@number-flow/react";

/* Motion primitives. The product's motion says four things and nothing else:
 *   - where you are moved        (SlideIndicator: one pill that travels)
 *   - this number just changed   (Figure: digits roll to their new value)
 *   - this surface is lit        (GlassLight: a pane catches the pointer's light)
 *   - you went through a door    (travel: the sign and its ink pane move with you)
 * Everything honours prefers-reduced-motion: the state still changes, it just
 * arrives instead of travelling. */

function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const RUPIAH = { style: "currency", currency: "IDR", maximumFractionDigits: 0 } as const;
const PLAIN = { maximumFractionDigits: 0 } as const;
const ROLL = { duration: 850, easing: "cubic-bezier(0.16, 1, 0.3, 1)" };
const FADE = { duration: 380, easing: "ease-out" };
// At a counter the number has to read true at a glance: a short turn, no more.
const ROLL_QUICK = { duration: 320, easing: "cubic-bezier(0.22, 1, 0.36, 1)" };
const FADE_QUICK = { duration: 180, easing: "ease-out" };

/** A figure whose digits roll, like the counter on a till. On first
 * appearance it rolls up from zero; when the data behind it changes (another
 * period, a reload) each digit turns from the old figure to the new one, so
 * the eye sees which way it went. Screen readers get the final value only
 * (the element labels itself). */
export function Figure({
  value,
  kind = "rupiah",
  quick = false,
}: {
  value: number;
  kind?: "rupiah" | "plain";
  /** For the till and the order bar: the figure settles almost at once. */
  quick?: boolean;
}) {
  // A quick figure is simply there when it mounts; only changes turn.
  const [shown, setShown] = useState(quick && Number.isFinite(value) ? value : 0);
  useEffect(() => {
    setShown(Number.isFinite(value) ? value : 0);
  }, [value]);
  return (
    <NumberFlow
      value={shown}
      locales="id-ID"
      format={kind === "rupiah" ? RUPIAH : PLAIN}
      transformTiming={quick ? ROLL_QUICK : ROLL}
      spinTiming={quick ? ROLL_QUICK : ROLL}
      opacityTiming={quick ? FADE_QUICK : FADE}
      willChange
    />
  );
}

/** The travelling pill. Drop it as the first child of a positioned container
 * that carries `has-slide`; it finds the descendant marked
 * `data-slide-active="true"` and moves under it. The active item itself goes
 * transparent (see .has-slide in globals.css), so there is one pill that
 * moves rather than two that swap. `watch` is whatever decides which item is
 * active. */
export function SlideIndicator({ className = "", watch }: { className?: string; watch: unknown }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  // The first placement must not animate in from the corner.
  const [settled, setSettled] = useState(false);

  useLayoutEffect(() => {
    const parent = ref.current?.parentElement;
    if (!parent) return;
    const measure = () => {
      const el = parent.querySelector<HTMLElement>('[data-slide-active="true"]');
      setBox(el ? { x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight } : null);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(parent);
    for (const child of Array.from(parent.children)) observer.observe(child);
    return () => observer.disconnect();
  }, [watch]);

  const placed = box !== null;
  useEffect(() => {
    if (!placed) {
      setSettled(false);
      return;
    }
    const id = requestAnimationFrame(() => setSettled(true));
    return () => cancelAnimationFrame(id);
  }, [placed]);

  return (
    <span
      ref={ref}
      aria-hidden
      className={`slide-indicator ${className}`}
      data-settled={settled}
      style={
        box
          ? { transform: `translate3d(${box.x}px, ${box.y}px, 0)`, width: box.w, height: box.h, opacity: 1 }
          : { opacity: 0 }
      }
    />
  );
}

/** Surfaces catch light. While the pointer is over a pane, the pane gets a
 * soft highlight and a brighter rim that follow it (--lx / --ly, read by
 * globals.css). One listener for the whole page, throttled to a frame, and
 * only on devices that actually have a hovering pointer. */
export function GlassLight() {
  useEffect(() => {
    if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches || reducedMotion()) return;
    const SELECTOR = ".glass-card, .group-card, .tile-link, .plate, .ink-card";
    let raf = 0;
    let lit: HTMLElement | null = null;
    let x = 0;
    let y = 0;
    let target: Element | null = null;

    const paint = () => {
      raf = 0;
      const pane = target?.closest<HTMLElement>(SELECTOR) ?? null;
      if (lit && lit !== pane) lit.removeAttribute("data-lit");
      if (pane) {
        const rect = pane.getBoundingClientRect();
        pane.style.setProperty("--lx", `${x - rect.left}px`);
        pane.style.setProperty("--ly", `${y - rect.top}px`);
        pane.setAttribute("data-lit", "");
      }
      lit = pane;
    };
    const onMove = (e: PointerEvent) => {
      x = e.clientX;
      y = e.clientY;
      target = e.target as Element | null;
      if (!raf) raf = requestAnimationFrame(paint);
    };
    const onLeave = () => {
      lit?.removeAttribute("data-lit");
      lit = null;
    };
    document.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      if (raf) cancelAnimationFrame(raf);
      onLeave();
    };
  }, []);
  return null;
}

/** A refusal: the element shakes its head once. Safe to call repeatedly. */
export function shake(el: HTMLElement | null) {
  if (!el || reducedMotion()) return;
  el.classList.remove("shake");
  void el.offsetWidth; // restart the animation
  el.classList.add("shake");
}

/* ── Travel ─────────────────────────────────────────────────────────────────
 * Logging in is walking through the café's door: the ink pane that carries
 * the sign on the login page becomes the sidebar, and the sign glides to its
 * place at the top of it (see the `travel` rules in globals.css). Logging out
 * plays it back. Built on View Transitions: where the browser has none, or
 * the visitor asked for less motion, the page simply changes. */

let arrive: (() => void) | null = null;

/** Start a navigation that should carry the sign with it. `go` performs the
 * route change; the transition holds the old page until the destination
 * calls `travelArrived()` (or a short patience runs out). */
export function travel(go: () => void) {
  const doc = document as Document & {
    startViewTransition?: (update: () => Promise<void>) => { finished: Promise<void> };
  };
  if (!doc.startViewTransition || reducedMotion()) {
    go();
    return;
  }
  const root = document.documentElement;
  root.classList.add("travel");
  const transition = doc.startViewTransition(
    () =>
      new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, 1600);
        arrive = () => {
          window.clearTimeout(timer);
          arrive = null;
          // A beat for the destination's first effects to settle. Not a
          // frame: the browser holds rendering until this resolves.
          window.setTimeout(resolve, 40);
        };
        go();
      })
  );
  transition.finished.catch(() => {}).finally(() => root.classList.remove("travel"));
}

/** Called by a page once it is on screen, to release a pending `travel`. */
export function travelArrived() {
  arrive?.();
}

/* ── The moon ───────────────────────────────────────────────────────────────
 * "Poernama" is the full moon. Two small marks come from it: the product's
 * own waiting mark (a moon that waxes), and tonight's real phase beside the
 * date. Both are plain geometry: a disc and the terminator's ellipse. */

const SYNODIC = 29.530588853;
/** A known new moon: 6 January 2000, 18:14 UTC. */
const NEW_MOON = Date.UTC(2000, 0, 6, 18, 14);

/** Where the moon is in its cycle: 0 new, 0.5 full, back to 1. */
export function moonAge(date: Date): number {
  const days = (date.getTime() - NEW_MOON) / 86_400_000;
  return (((days % SYNODIC) + SYNODIC) % SYNODIC) / SYNODIC;
}

/** The lit part of a moon at `phase` (0..1) as one closed path in a 24-box. */
function litPath(phase: number): string {
  const r = 9;
  const cx = 12;
  const top = `${cx},${12 - r}`;
  const bottom = `${cx},${12 + r}`;
  const waxing = phase <= 0.5;
  // 0 at new, 1 at full, back to 0.
  const lit = waxing ? phase * 2 : (1 - phase) * 2;
  // The terminator is half an ellipse whose width runs r → 0 → r.
  const rx = Math.abs(1 - lit * 2) * r;
  const limb = `A ${r} ${r} 0 0 ${waxing ? 1 : 0} ${bottom}`;
  const bulge = lit > 0.5 ? (waxing ? 1 : 0) : waxing ? 0 : 1;
  const terminator = `A ${rx.toFixed(3)} ${r} 0 0 ${bulge} ${top}`;
  return `M ${top} ${limb} ${terminator} Z`;
}

const PHASE_NAMES = [
  "bulan baru",
  "sabit muda",
  "paruh awal",
  "cembung awal",
  "purnama",
  "cembung akhir",
  "paruh akhir",
  "sabit tua",
];

/** Tonight's moon, drawn as it is. The sentence it carries (as a tooltip and
 * for screen readers) says the phase and how far the next full moon is. */
export function MoonPhase({ date, className = "" }: { date: Date; className?: string }) {
  const phase = moonAge(date);
  const name = PHASE_NAMES[Math.round(phase * 8) % 8];
  const toFull = Math.round((((0.5 - phase + 1) % 1) * SYNODIC));
  const isFull = name === "purnama";
  const label = isFull
    ? "Malam ini purnama"
    : `Bulan malam ini: ${name}. Purnama ${toFull <= 1 ? "besok" : `${toFull} hari lagi`}.`;
  return (
    <span className={`moon-phase ${className}`} role="img" aria-label={label} title={label} data-full={isFull}>
      <svg viewBox="0 0 24 24" aria-hidden>
        <circle cx="12" cy="12" r="9" className="moon-disc" />
        <path d={litPath(phase)} className="moon-lit" />
      </svg>
    </span>
  );
}

/** The waiting mark: a moon that waxes to full and wanes again. Sits where a
 * spinner would (a busy button, the first load). Takes the text colour. */
export function MoonSpinner({ className = "" }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 24 24" className={`moon-spinner ${className}`} aria-hidden>
      <defs>
        <clipPath id={id}>
          <circle cx="12" cy="12" r="9" />
        </clipPath>
      </defs>
      <circle cx="12" cy="12" r="9" className="moon-disc" />
      <g clipPath={`url(#${id})`}>
        <circle cx="12" cy="12" r="9" className="moon-shade" />
      </g>
    </svg>
  );
}
