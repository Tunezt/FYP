"use client";

import { useState } from "react";

/* Shared primitives — glass is a material for elevated surfaces; quieter
 * tinted flats ("plate") carry secondary content so the page has texture
 * variety instead of wall-to-wall frosted cards. */

export function Glass({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <section className={`glass-card ${className}`}>{children}</section>;
}

export function Plate({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-3xl bg-accent-gradient-soft ${className}`}
      style={{ border: "1px solid var(--hairline)" }}
    >
      {children}
    </section>
  );
}

export function SectionTitle({
  children,
  hint,
  action,
}: {
  children: React.ReactNode;
  hint?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-widest ink-soft">
        {children}
        {hint}
      </h2>
      {action}
    </div>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  high: "bg-red-500/15 text-red-600 dark:text-red-400",
  medium: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  low: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const label = severity === "high" ? "penting" : severity === "medium" ? "sedang" : "info";
  return (
    <span
      className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.low}`}
    >
      {label}
    </span>
  );
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
