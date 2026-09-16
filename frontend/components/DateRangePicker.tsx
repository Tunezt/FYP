"use client";

import { useEffect, useRef, useState } from "react";
import { IconCalendar, IconChevronLeft, IconChevronRight } from "@/components/icons";

/** A date range picked on one calendar, with the span drawn as one band.
 *
 * The native date input shows `mm/dd/yyyy` in the browser's own locale and
 * opens the OS calendar — American order in an Indonesian café, and two
 * separate widgets for what is really one question ("which days?").
 *
 * One click sets the start, the second sets the end; clicking again starts
 * over. Between them the days are tinted and the ends are capped, so the
 * range reads as a single continuous band rather than two circled dates.
 * Today is always marked, even in another month, so "how far back is this?"
 * never needs counting.
 *
 * Values are `YYYY-MM-DD` strings in the business's own days — no timezone
 * conversion happens here, because the backend already means business days. */

const DAY_NAMES = ["Min", "Sen", "Sel", "Rab", "Kam", "Jum", "Sab"];
const MONTHS = [
  "Januari", "Februari", "Maret", "April", "Mei", "Juni",
  "Juli", "Agustus", "September", "Oktober", "November", "Desember",
];

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parse = (s: string) => {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
};
const short = (s: string) => {
  const d = parse(s);
  return `${d.getDate()} ${MONTHS[d.getMonth()].slice(0, 3)}`;
};

export function DateRangePicker({
  since,
  until,
  onChange,
  className = "",
  single = false,
  variant = "control",
  placeholder,
}: {
  since: string;
  until: string;
  onChange: (since: string, until: string) => void;
  className?: string;
  /** One day rather than a span — form fields like "berlaku sampai". */
  single?: boolean;
  variant?: "control" | "field";
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => {
    const base = since ? parse(since) : new Date();
    return new Date(base.getFullYear(), base.getMonth(), 1);
  });
  const [pending, setPending] = useState<string | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number; above: boolean } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const today = iso(new Date());
  const PANEL_W = 316;
  const PANEL_H = 388;

  const place = () => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const below = window.innerHeight - r.bottom;
    const above = below < PANEL_H + 16 && r.top > below;
    setPos({
      left: Math.max(12, Math.min(r.left, window.innerWidth - PANEL_W - 12)),
      top: above ? r.top - PANEL_H - 8 : r.bottom + 8,
      above,
    });
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (
        !panelRef.current?.contains(e.target as Node) &&
        !triggerRef.current?.contains(e.target as Node)
      ) {
        setOpen(false);
        setPending(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        setPending(null);
        triggerRef.current?.focus();
      }
    };
    const onMove = () => place();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [open]);

  const pick = (day: string) => {
    if (single) {
      onChange(day, day);
      setOpen(false);
      return;
    }
    if (!pending) {
      setPending(day);
      onChange(day, "");
      return;
    }
    // Second click closes the range, in whichever order they were clicked.
    const [a, b] = pending <= day ? [pending, day] : [day, pending];
    setPending(null);
    onChange(a, b);
    setOpen(false);
  };

  const preset = (days: number) => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - (days - 1));
    setPending(null);
    setMonth(new Date(start.getFullYear(), start.getMonth(), 1));
    onChange(iso(start), iso(end));
    setOpen(false);
  };

  const empty = placeholder ?? (single ? "Pilih tanggal" : "Semua tanggal");
  const label = single
    ? since
      ? `${parse(since).getDate()} ${MONTHS[parse(since).getMonth()]} ${parse(since).getFullYear()}`
      : empty
    : since && until
      ? `${short(since)} – ${short(until)}`
      : since
        ? `${short(since)} – …`
        : empty;

  // Leading blanks so the 1st lands under its weekday.
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells: (string | null)[] = Array(first.getDay()).fill(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(iso(new Date(month.getFullYear(), month.getMonth(), d)));

  const rangeStart = since;
  const rangeEnd = until || (pending ? pending : "");
  const inRange = (day: string) => rangeStart && rangeEnd && day > rangeStart && day < rangeEnd;
  const isStart = (day: string) => day === rangeStart;
  const isEnd = (day: string) => day === rangeEnd && rangeEnd !== rangeStart;
  const isOnly = (day: string) => day === rangeStart && (!rangeEnd || rangeEnd === rangeStart);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => {
          if (open) {
            setOpen(false);
          } else {
            place();
            setOpen(true);
          }
        }}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={`${
          variant === "field" ? "field cursor-pointer" : "control-field"
        } flex items-center gap-2 text-left ${open ? "control-field-open" : ""} ${className}`}
      >
        <IconCalendar className="h-4 w-4 shrink-0 text-[color:var(--ink-faint)]" aria-hidden />
        <span className={`truncate ${since ? "" : "ink-soft"}`}>{label}</span>
      </button>

      {open && pos && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Pilih rentang tanggal"
          className="popover-panel p-3"
          style={{ left: pos.left, top: pos.top, width: PANEL_W }}
        >
          <div className="mb-2 flex items-center justify-between">
            <button
              type="button"
              aria-label="Bulan sebelumnya"
              onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
              className="icon-btn"
            >
              <IconChevronLeft className="h-4 w-4" aria-hidden />
            </button>
            <p className="text-sm font-bold">
              {MONTHS[month.getMonth()]} {month.getFullYear()}
            </p>
            <button
              type="button"
              aria-label="Bulan berikutnya"
              onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
              className="icon-btn"
            >
              <IconChevronRight className="h-4 w-4" aria-hidden />
            </button>
          </div>

          <div className="grid grid-cols-7 gap-y-0.5">
            {DAY_NAMES.map((d) => (
              <span key={d} className="ink-faint pb-1 text-center text-[0.68rem] font-semibold">
                {d}
              </span>
            ))}
            {cells.map((day, i) =>
              day === null ? (
                <span key={`b${i}`} />
              ) : (
                <span
                  key={day}
                  className={`cal-cell ${inRange(day) ? "cal-cell-in" : ""} ${
                    isStart(day) && rangeEnd && !isOnly(day) ? "cal-cell-start" : ""
                  } ${isEnd(day) ? "cal-cell-end" : ""}`}
                >
                  <button
                    type="button"
                    onClick={() => pick(day)}
                    aria-current={day === today ? "date" : undefined}
                    aria-pressed={isStart(day) || isEnd(day)}
                    className={`cal-day ${isStart(day) || isEnd(day) ? "cal-day-on" : ""} ${
                      day === today && !isStart(day) && !isEnd(day) ? "cal-day-today" : ""
                    }`}
                  >
                    {parse(day).getDate()}
                  </button>
                </span>
              )
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t pt-3" style={{ borderColor: "var(--hairline)" }}>
            <button type="button" className="chip-btn" onClick={() => preset(1)}>
              Hari ini
            </button>
            {!single && (
              <>
                <button type="button" className="chip-btn" onClick={() => preset(7)}>
                  7 hari
                </button>
                <button type="button" className="chip-btn" onClick={() => preset(30)}>
                  30 hari
                </button>
              </>
            )}
            {(since || until) && (
              <button
                type="button"
                className="chip-btn ml-auto"
                onClick={() => {
                  setPending(null);
                  onChange("", "");
                  setOpen(false);
                }}
              >
                Hapus
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
