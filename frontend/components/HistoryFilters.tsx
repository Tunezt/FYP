"use client";

/** Filter bar for the history lists — one dropdown and a date range.
 *
 * The same control on every list, because "which days, and whose/what kind"
 * is the same question everywhere: cashier on Penjualan, category on
 * Keuangan, severity on Peringatan.
 *
 * Deliberately three plain controls rather than a calendar popover: this runs
 * on a counter tablet, often one-handed, and the native date input is the one
 * control every device already knows how to show well. Both dates are
 * inclusive and counted in business days — the same boundary as the day
 * headers and the metrics (M15-T4) — and the backend does the filtering, so
 * the answer is not "whatever is on this page".
 *
 * "Reset" appears only once something is filtered, so the quiet state stays
 * quiet. */
export type FilterOption = { value: string; label: string };

export function HistoryFilters({
  options,
  optionLabel,
  value,
  onValue,
  allLabel = "Semua",
  since,
  onSince,
  until,
  onUntil,
  onReset,
  className = "",
}: {
  options?: FilterOption[];
  optionLabel?: string;
  value?: string;
  onValue?: (v: string) => void;
  allLabel?: string;
  since: string;
  onSince: (v: string) => void;
  until: string;
  onUntil: (v: string) => void;
  onReset: () => void;
  className?: string;
}) {
  const active = Boolean(value || since || until);
  const field =
    "h-10 rounded-xl border border-[color:var(--hairline)] bg-[color:var(--surface)] px-3 text-sm " +
    "transition-colors hover:border-[color:var(--ink-faint)] focus:border-[color:var(--accent)] focus:outline-none";

  return (
    <div className={`flex flex-wrap items-end gap-3 ${className}`}>
      {options && options.length > 0 && onValue && (
        <label className="flex flex-col gap-1">
          <span className="ink-faint text-xs font-semibold">{optionLabel}</span>
          <select
            value={value ?? ""}
            onChange={(e) => onValue(e.target.value)}
            className={`${field} cursor-pointer pr-8`}
          >
            <option value="">{allLabel}</option>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      )}

      <label className="flex flex-col gap-1">
        <span className="ink-faint text-xs font-semibold">Dari tanggal</span>
        <input
          type="date"
          value={since}
          max={until || undefined}
          onChange={(e) => onSince(e.target.value)}
          className={`${field} cursor-pointer`}
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="ink-faint text-xs font-semibold">Sampai tanggal</span>
        <input
          type="date"
          value={until}
          min={since || undefined}
          onChange={(e) => onUntil(e.target.value)}
          className={`${field} cursor-pointer`}
        />
      </label>

      {active && (
        <button type="button" onClick={onReset} className="btn-quiet h-10 px-4 text-sm">
          Reset
        </button>
      )}
    </div>
  );
}
