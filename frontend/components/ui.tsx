"use client";

import { useState } from "react";

/* Shared primitives — glass is a material for elevated surfaces; quieter
 * tinted flats ("plate") carry secondary content so the page has texture
 * variety instead of wall-to-wall frosted cards. */

type SurfaceProps = React.HTMLAttributes<HTMLElement> & { children: React.ReactNode };

/** Hero surface — frosted glass. Budget: ONE per page. */
export function Glass({ children, className = "", ...rest }: SurfaceProps) {
  return (
    <section className={`glass-card ${className}`} {...rest}>
      {children}
    </section>
  );
}

/** Quiet secondary surface — flat warm white, no blur. */
export function Plate({ children, className = "", ...rest }: SurfaceProps) {
  return (
    <section className={`plate ${className}`} {...rest}>
      {children}
    </section>
  );
}

/** Day header for chronological lists: "Hari ini · Selasa        Rp 360.000" */
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
      <p className="text-sm font-bold">
        {label}
        {sub && <span className="ink-faint ml-2 text-xs font-medium">{sub}</span>}
      </p>
      {meta && <p className="ink-soft text-xs font-semibold tabular-nums">{meta}</p>}
    </div>
  );
}

/** Small warm identity tile for item rows (no product photos in the data —
 * initials on a soft tint carry recognition instead). */
const TILE_TONES = [
  "bg-accent-100 text-accent-800 dark:bg-accent-900 dark:text-accent-200",
  "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
];

export function Tile({ label, className = "" }: { label: string; className?: string }) {
  const initials = label
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
  let hash = 0;
  for (const ch of label) hash = (hash * 31 + ch.charCodeAt(0)) & 0xffff;
  const tone = TILE_TONES[hash % TILE_TONES.length];
  return (
    <span
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-xs font-bold ${tone} ${className}`}
      aria-hidden
    >
      {initials}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const label = severity === "high" ? "penting" : severity === "medium" ? "sedang" : "info";
  const cls = severity === "high" ? "pill-bad" : severity === "medium" ? "pill-warn" : "pill-good";
  return <span className={cls}>{label}</span>;
}

export function EmptyState({
  emoji,
  title,
  children,
}: {
  emoji: string;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="px-6 py-12 text-center">
      <p className="text-3xl">{emoji}</p>
      <p className="mt-3 font-semibold">{title}</p>
      {children && <p className="ink-soft mx-auto mt-1 max-w-sm text-sm">{children}</p>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-2xl bg-[color:var(--hairline)] ${className}`} />;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div
      className="inline-flex rounded-2xl p-1"
      style={{ background: "var(--hairline)" }}
      role="tablist"
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          role="tab"
          aria-selected={opt.value === value}
          onClick={() => onChange(opt.value)}
          className={`rounded-xl px-3.5 py-1.5 text-sm font-medium transition-all duration-200 ${
            opt.value === value ? "glass-card glass-strong shadow-key" : "ink-soft hover:opacity-80"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/** Bottom sheet on mobile, centered card on desktop — the iOS pattern. */
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
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-black/30 backdrop-blur-sm sm:items-center"
      onClick={onClose}
    >
      <div
        className="glass-card glass-strong max-h-[88vh] w-full max-w-lg animate-fade-up overflow-y-auto rounded-b-none rounded-t-4xl px-6 pb-8 pt-5 sm:rounded-4xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={title}
      >
        <div className="mx-auto mb-4 h-1.5 w-10 rounded-full bg-[color:var(--ink-faint)] opacity-40 sm:hidden" />
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-bold">{title}</h3>
          <button onClick={onClose} className="ink-soft rounded-full px-3 py-1 text-sm">
            tutup
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function CopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        void navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      }}
      className="field flex w-full items-center justify-between gap-2 text-left"
    >
      <span className="truncate text-sm">{value}</span>
      <span className="shrink-0 text-xs font-semibold text-accent-500">
        {copied ? "tersalin ✓" : "salin"}
      </span>
    </button>
  );
}
