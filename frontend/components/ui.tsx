"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { categorize, type ItemCategory } from "@/lib/itemCategory";
import { Wordmark } from "@/components/Wordmark";
import {
  IconAlert,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconClose,
  IconCatBakery,
  IconCatCigarette,
  IconCatCleaning,
  IconCatCoffee,
  IconCatEgg,
  IconCatFlour,
  IconCatGas,
  IconCatMilk,
  IconCatNoodle,
  IconCatOil,
  IconCatProduce,
  IconCatRice,
  IconCatSauce,
  IconCatSnack,
  IconCatSugar,
  IconCatTea,
  IconCatWater,
} from "@/components/icons";

/* Shared primitives. One material system (see globals.css): porcelain cards on
 * a porcelain ground, an ink shell for navigation, opaque floating layers. */

type SurfaceProps = React.HTMLAttributes<HTMLElement> & { children: React.ReactNode };

/** The standard card — paper with a soft lift. */
export function Glass({ children, className = "", ...rest }: SurfaceProps) {
  return (
    <section className={`glass-card ${className}`} {...rest}>
      {children}
    </section>
  );
}

/** A quieter card — the same paper, ringed, without the lift. */
export function Plate({ children, className = "", ...rest }: SurfaceProps) {
  return (
    <section className={`plate ${className}`} {...rest}>
      {children}
    </section>
  );
}

/** Renders children into <body>, so no transformed, filtered or clipped
 * ancestor can trap a fixed-position layer. Mount-gated for SSR. */
export function Portal({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted ? createPortal(children, document.body) : null;
}

/** Static day header — the first row of a day's group card. */
export function DayHeader({
  label,
  sub,
  meta,
}: {
  label: string;
  sub?: string | null;
  meta?: React.ReactNode;
}) {
  return (
    <div className="day-header">
      <p className="min-w-0 flex-1 truncate text-sm">
        <span className="font-semibold">{label}</span>
        {sub && <span className="ink-faint ml-2">{sub}</span>}
      </p>
      {meta && <p className="ink-soft shrink-0 text-[13px] font-medium tabular-nums">{meta}</p>}
    </div>
  );
}

/** The trailing chevron on an interactive row. Pairs with `.list-row-action`.
 * Decorative: the row is the button, screen readers get its label. */
export function RowChevron({ className = "" }: { className?: string }) {
  return <IconChevronRight className={`row-chevron h-4 w-4 shrink-0 ${className}`} aria-hidden />;
}

/** A day header that opens and closes its group.
 *
 * History lists are long and the owner nearly always wants the last day or two,
 * so older days stay folded. A folded day is a single tidy line that still
 * answers "how much did we take?"; the whole header is the hit target, and the
 * trailing chevron sits in the same column as the rows' own chevrons. */
export function DayHeaderToggle({
  label,
  sub,
  meta,
  open,
  onToggle,
  count,
  controls,
}: {
  label: string;
  sub?: string | null;
  meta?: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  count?: number;
  controls?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={controls}
      className="day-header day-header-toggle"
    >
      <span className="min-w-0 flex-1 truncate text-sm">
        <span className="font-semibold">{label}</span>
        {sub && <span className="ink-faint ml-2">{sub}</span>}
      </span>
      {count !== undefined && !meta && (
        <span className="ink-faint shrink-0 text-[13px] tabular-nums">{count}</span>
      )}
      {meta && <span className="ink-soft shrink-0 text-[13px] font-medium tabular-nums">{meta}</span>}
      <IconChevronDown
        className={`row-chevron h-4 w-4 shrink-0 ${open ? "rotate-180" : ""}`}
        aria-hidden
      />
    </button>
  );
}

/** A day (or any group) as one card: its header row, then its rows. */
export function DayGroup({
  label,
  sub,
  meta,
  count,
  open,
  onToggle,
  children,
}: {
  label: string;
  sub?: string | null;
  meta?: React.ReactNode;
  count?: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <div className="group-card">
      <DayHeaderToggle
        label={label}
        sub={sub}
        meta={meta}
        count={count}
        open={open}
        onToggle={onToggle}
        controls={id}
      />
      <ul id={id} className="group-body" hidden={!open}>
        {children}
      </ul>
    </div>
  );
}

/** Identity tile for item rows with no photo: initials on the inset material.
 * Deliberately monochrome — a list's colour belongs to status, not decoration. */
export function Tile({ label, className = "" }: { label: string; className?: string }) {
  const initials = label
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <span
      className={`surface-inset ink-soft flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-xs font-semibold tracking-wide ${className}`}
      style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}
      aria-hidden
    >
      {initials}
    </span>
  );
}

const CATEGORY_ICONS: Record<ItemCategory, (p: { className?: string }) => React.ReactNode> = {
  oil: IconCatOil,
  rice: IconCatRice,
  noodle: IconCatNoodle,
  sugar: IconCatSugar,
  milk: IconCatMilk,
  coffee: IconCatCoffee,
  tea: IconCatTea,
  egg: IconCatEgg,
  gas: IconCatGas,
  cleaning: IconCatCleaning,
  cigarette: IconCatCigarette,
  flour: IconCatFlour,
  water: IconCatWater,
  sauce: IconCatSauce,
  snack: IconCatSnack,
  produce: IconCatProduce,
  bakery: IconCatBakery,
};

/** Item identity mark. Three-tier fallback so a row is never blank:
 *  1. matched category  → product photo (public/items/<category>.png)
 *  2. photo failed load  → the drawn category icon on the inset material
 *  3. no category match   → initials Tile. */
export function ItemIcon({ name, className = "" }: { name: string; className?: string }) {
  const category = categorize(name);
  const [photoFailed, setPhotoFailed] = useState(false);

  if (!category) return <Tile label={name} className={className} />;

  if (photoFailed) {
    const Icon = CATEGORY_ICONS[category];
    return (
      <span
        className={`surface-inset ink-soft flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${className}`}
        style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}
        aria-hidden
      >
        <Icon className="h-5 w-5" />
      </span>
    );
  }

  return (
    <span
      className={`surface-inset relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl ${className}`}
      aria-hidden
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`/items/${category}.png`}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover"
        onError={() => setPhotoFailed(true)}
      />
      <span
        className="pointer-events-none absolute inset-0 rounded-[inherit]"
        style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}
      />
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const label = severity === "high" ? "penting" : severity === "medium" ? "sedang" : "info";
  const cls = severity === "high" ? "pill-bad" : severity === "medium" ? "pill-warn" : "pill-quiet";
  return <span className={cls}>{label}</span>;
}

/** Empty state: a drawn glyph on the inset material, a title, one line of
 * guidance, and optionally the action that fills it. */
export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-6 py-10 text-center">
      <span
        className="surface-inset ink-faint flex h-11 w-11 items-center justify-center rounded-2xl"
        style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}
        aria-hidden
      >
        {icon}
      </span>
      <p className="mt-3 text-[15px] font-semibold">{title}</p>
      {children && <p className="ink-soft mt-1 max-w-sm text-sm leading-relaxed">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-2xl bg-[color:var(--row-press)] ${className}`} />;
}

/** Shown when a data fetch fails — distinct from the loading skeleton so a dead
 * connection never looks like an endless load. Offers an explicit retry. */
export function ErrorState({
  onRetry,
  title = "Gagal memuat data",
  children,
}: {
  onRetry: () => void;
  title?: string;
  children?: React.ReactNode;
}) {
  return (
    <EmptyState
      icon={<IconAlert className="h-5 w-5" />}
      title={title}
      action={
        <button onClick={onRetry} className="btn-quiet px-4 py-2 text-sm">
          Coba lagi
        </button>
      }
    >
      {children ?? "Sambungan ke server sedang bermasalah. Coba lagi sebentar ya."}
    </EmptyState>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  className = "",
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={`segmented ${className}`} role="tablist">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          role="tab"
          aria-selected={opt.value === value}
          onClick={() => onChange(opt.value)}
          className="segmented-item flex-1 whitespace-nowrap"
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/** Bottom sheet on phones, centred panel on larger screens.
 *
 * Portaled to <body> (a page's entrance animation or a card's overflow can
 * never trap or clip it), with a pinned header, a body that scrolls on its own,
 * Escape to close, focus moved in on open and returned on close, and the page
 * behind held still. */
export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    // Wait a frame: the panel is portaled in after mount.
    const raf = requestAnimationFrame(() => panelRef.current?.focus({ preventScroll: true }));
    const onKey = (e: KeyboardEvent) => {
      // An open popover inside the sheet takes Escape first.
      if (e.key === "Escape" && !document.querySelector(".popover-panel")) onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.({ preventScroll: true });
    };
  }, [open]);

  if (!open) return null;
  return (
    <Portal>
      <div className="sheet-scrim" onClick={onClose}>
        <div
          ref={panelRef}
          className="sheet-panel relative sm:max-w-lg"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
          style={{ outline: "none" }}
        >
          <span
            className="absolute left-1/2 top-2 h-1 w-9 -translate-x-1/2 rounded-full bg-[color:var(--hairline-strong)] sm:hidden"
            aria-hidden
          />
          <div className="flex shrink-0 items-center gap-3 px-5 pb-3 pt-6 sm:px-6 sm:pt-5">
            <h3
              id={titleId}
              className="min-w-0 flex-1 truncate text-[17px] font-semibold tracking-[-0.012em]"
            >
              {title}
            </h3>
            <button
              type="button"
              onClick={onClose}
              aria-label="Tutup"
              className="icon-btn rounded-full bg-[color:var(--row-hover)]"
            >
              <IconClose className="h-4 w-4" />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-1 sm:px-6 sm:pb-6">
            {children}
          </div>
        </div>
      </div>
    </Portal>
  );
}

export function CopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      }}
      className="field flex w-full items-center justify-between gap-3 text-left"
    >
      <span className="truncate text-[13px] tabular-nums">{value}</span>
      <span className="flex shrink-0 items-center gap-1 text-xs font-semibold text-[color:var(--accent)]">
        {copied ? (
          <>
            <IconCheck className="h-3.5 w-3.5" /> tersalin
          </>
        ) : (
          "salin"
        )}
      </span>
    </button>
  );
}

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** 24-hour time typed as digits ("1930" becomes 19:30). The native time input
 * follows the browser's locale and shows "07:30 PM" on an English Chrome, in
 * an app whose every other time reads 19.30. `onChange` only ever receives a
 * complete, valid "HH:MM" — or "" when the field is cleared. */
export function TimeField({
  value,
  onChange,
  className = "",
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string;
  ariaLabel?: string;
}) {
  const [text, setText] = useState(value);
  useEffect(() => {
    if (TIME_RE.test(value) || value === "") setText(value.slice(0, 5));
  }, [value]);
  const invalid = text.length === 5 && !TIME_RE.test(text);
  return (
    <input
      className={`field text-center tabular-nums ${className}`}
      inputMode="numeric"
      placeholder="00:00"
      aria-label={ariaLabel}
      aria-invalid={invalid}
      value={text}
      onChange={(e) => {
        const digits = e.target.value.replace(/\D/g, "").slice(0, 4);
        const next = digits.length > 2 ? `${digits.slice(0, 2)}:${digits.slice(2)}` : digits;
        setText(next);
        if (TIME_RE.test(next)) onChange(next);
        else if (next === "") onChange("");
      }}
    />
  );
}

/** The brand mark: the Poernama wordmark at one of three set widths. It takes
 * the surrounding text colour, so it is ink on porcelain and porcelain on the
 * ink shell without separate artwork. */
export function BrandMark({
  size = "md",
  lit = false,
  className = "",
}: {
  size?: "sm" | "md" | "lg";
  /** The illuminated sign treatment — only on the charcoal shell. */
  lit?: boolean;
  className?: string;
}) {
  const width = size === "sm" ? "w-[176px]" : size === "lg" ? "w-[232px]" : "w-[184px]";
  return <Wordmark className={`${width} ${lit ? "wordmark-lit" : ""} ${className}`} />;
}
