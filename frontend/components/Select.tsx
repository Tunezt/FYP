"use client";

import { useEffect, useId, useRef, useState } from "react";
import { IconCheck, IconChevronDown } from "@/components/icons";

/** A dropdown that belongs to this interface.
 *
 * The native `<select>` renders the OS widget — a blue Windows listbox in the
 * middle of a warm-light café dashboard — and cannot be styled. This is the
 * standard listbox pattern instead: a button that owns the value, a floating
 * panel of options, full keyboard support (Up/Down/Home/End to move, Enter or
 * Space to take, Escape to leave, typing jumps to a label).
 *
 * The panel is `position: fixed` and measured from the trigger, so it is never
 * clipped by a card's own overflow, and it flips above the trigger when the
 * viewport runs out below. */
export type SelectOption = { value: string; label: string };

export function Select({
  options,
  value,
  onChange,
  placeholder = "Semua",
  className = "",
  ariaLabel,
  variant = "control",
  allowEmpty = true,
}: {
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  ariaLabel?: string;
  /** "control" is the compact filter-bar size; "field" matches .field inside forms. */
  variant?: "control" | "field";
  /** A filter offers "Semua"; a form field that must have an answer does not. */
  allowEmpty?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState<{ left: number; top: number; width: number; above: boolean } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const listId = useId();

  const all: SelectOption[] = allowEmpty ? [{ value: "", label: placeholder }, ...options] : options;
  const selectedIndex = Math.max(0, all.findIndex((o) => o.value === value));
  const current = all[selectedIndex] ?? all[0];

  const place = () => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const panelHeight = Math.min(all.length * 40 + 16, 280);
    const below = window.innerHeight - r.bottom;
    const above = below < panelHeight + 16 && r.top > below;
    setPos({
      left: r.left,
      top: above ? r.top - panelHeight - 8 : r.bottom + 8,
      width: r.width,
      above,
    });
  };

  const openPanel = () => {
    place();
    setActive(selectedIndex);
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      if (
        !panelRef.current?.contains(e.target as Node) &&
        !triggerRef.current?.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onMove = () => place();
    document.addEventListener("mousedown", onDocDown);
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    return () => {
      document.removeEventListener("mousedown", onDocDown);
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, all.length]);

  const take = (index: number) => {
    const option = all[index];
    if (!option) return;
    onChange(option.value);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openPanel();
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(all.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(all.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      take(active);
    } else if (e.key.length === 1) {
      // Type-ahead: "sa" jumps to Sari, the way a native select does.
      const now = Date.now();
      typed.current = {
        text: now - typed.current.at > 900 ? e.key : typed.current.text + e.key,
        at: now,
      };
      const needle = typed.current.text.toLowerCase();
      const hit = all.findIndex((o) => o.label.toLowerCase().startsWith(needle));
      if (hit >= 0) setActive(hit);
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        onClick={() => (open ? setOpen(false) : openPanel())}
        onKeyDown={onKeyDown}
        className={`${
          variant === "field" ? "field flex cursor-pointer items-center" : "control-field flex items-center"
        } justify-between gap-2 pr-3 text-left ${open ? "control-field-open" : ""} ${className}`}
      >
        <span className={`truncate ${value ? "" : "ink-soft"}`}>{current.label}</span>
        <IconChevronDown
          className={`h-4 w-4 shrink-0 text-[color:var(--ink-faint)] transition-transform duration-200 ${
            open ? "rotate-180" : ""
          }`}
          aria-hidden
        />
      </button>

      {open && pos && (
        <div
          ref={panelRef}
          id={listId}
          role="listbox"
          aria-activedescendant={`${listId}-${active}`}
          tabIndex={-1}
          onKeyDown={onKeyDown}
          className={`popover-panel ${pos.above ? "origin-bottom" : "origin-top"}`}
          style={{ left: pos.left, top: pos.top, minWidth: Math.max(pos.width, 168) }}
        >
          {all.map((option, index) => {
            const isSelected = option.value === value;
            return (
              <div
                key={option.value || "__all"}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={isSelected}
                onMouseEnter={() => setActive(index)}
                onClick={() => take(index)}
                className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl px-3 py-2 text-sm transition-colors ${
                  index === active ? "bg-[color:var(--row-hover)]" : ""
                } ${isSelected ? "font-semibold" : ""}`}
              >
                <span className="truncate">{option.label}</span>
                {isSelected && (
                  <IconCheck className="h-4 w-4 shrink-0 text-[color:var(--accent)]" aria-hidden />
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
