"use client";

import { DateRangePicker } from "@/components/DateRangePicker";
import { Select, type SelectOption } from "@/components/Select";

/** Filter bar for the history lists — one dropdown and one date range.
 *
 * The same control on every list, because "which days, and whose/what kind" is
 * the same question everywhere: cashier on Penjualan, jenis on Keuangan,
 * tingkat on Peringatan.
 *
 * Both controls are ours rather than the browser's: the native select opens the
 * OS listbox and the native date input shows the browser's locale order, which
 * is how a warm Indonesian dashboard ends up with `mm/dd/yyyy` in it. Dates are
 * inclusive and counted in business days — the same boundary as the day headers
 * and the metrics (M15-T4) — and the filtering happens in SQL, so a count
 * describes the whole result rather than the page on screen.
 *
 * "Reset" appears only once something is filtered, so the quiet state stays
 * quiet. */
export function HistoryFilters({
  options,
  optionLabel,
  value,
  onValue,
  allLabel = "Semua",
  since,
  until,
  onRange,
  onReset,
  className = "",
}: {
  options?: SelectOption[];
  optionLabel?: string;
  value?: string;
  onValue?: (v: string) => void;
  allLabel?: string;
  since: string;
  until: string;
  onRange: (since: string, until: string) => void;
  onReset: () => void;
  className?: string;
}) {
  const active = Boolean(value || since || until);

  return (
    <div className={`flex flex-wrap items-end gap-2.5 ${className}`}>
      {options && options.length > 0 && onValue && (
        <label className="flex flex-col gap-1.5">
          <span className="ink-faint text-xs font-semibold">{optionLabel}</span>
          <Select
            options={options}
            value={value ?? ""}
            onChange={onValue}
            placeholder={allLabel}
            ariaLabel={optionLabel}
            className="w-44"
          />
        </label>
      )}

      <label className="flex flex-col gap-1.5">
        <span className="ink-faint text-xs font-semibold">Tanggal</span>
        <DateRangePicker since={since} until={until} onChange={onRange} className="w-52" />
      </label>

      {active && (
        <button type="button" onClick={onReset} className="btn-quiet h-10 px-4 py-0 text-sm">
          Reset
        </button>
      )}
    </div>
  );
}
