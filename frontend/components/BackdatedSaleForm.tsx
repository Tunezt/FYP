"use client";

/** M15-T10 — entering a sale that happened on paper.
 *
 * The offline queue (M14) covers a lost connection. It does not cover a lost
 * device: one tablet is one point of failure, and when it dies mid-service the
 * staff keep selling on paper. Those slips still have to reach the books at the
 * time they actually happened, or the day's takings, the shift reconciliation
 * and the stock are all wrong by however many cups were poured.
 *
 * Owner-only, because choosing a sale's timestamp is exactly the power that
 * would let someone move takings between days.
 */

import { useMemo, useState } from "react";
import { Select } from "@/components/Select";
import { DateRangePicker } from "@/components/DateRangePicker";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import { ApiError } from "@/lib/api";
import type { InventoryItem, OrderRow, StaffMember } from "@/lib/types";
import { EmptyState, Sheet, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconBox, IconClose, IconPlus } from "@/components/icons";

type Draft = { itemId: string; quantity: string };

const METHODS = [
  { value: "cash", label: "Tunai" },
  { value: "qris", label: "QRIS" },
  { value: "transfer", label: "Transfer" },
] as const;

/** `datetime-local` wants "YYYY-MM-DDTHH:mm" in *local* time, which is what the
 *  owner is reading off the slip. Default to yesterday at 7pm: a plausible
 *  trading hour, and never in the future, which the server refuses. */
function defaultWhen(): string {
  const d = new Date(Date.now() - 86_400_000);
  d.setHours(19, 0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function BackdatedSaleForm({ onRecorded }: { onRecorded?: () => void }) {
  const items = useOwnerData<InventoryItem[]>("/api/items");
  const staff = useOwnerData<StaffMember[]>("/auth/staff");
  const mutate = useOwnerMutation();

  const [open, setOpen] = useState(false);
  const [when, setWhen] = useState(defaultWhen);
  // Typed as HH.MM / HH:MM in 24-hour time — the native time input follows the
  // browser's locale and shows "07:00 PM" on an English Chrome.
  const [timeText, setTimeText] = useState(() => defaultWhen().slice(11, 16));
  const [staffId, setStaffId] = useState("");
  const [method, setMethod] = useState<(typeof METHODS)[number]["value"]>("cash");
  const [note, setNote] = useState("");
  const [lines, setLines] = useState<Draft[]>([{ itemId: "", quantity: "1" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const active = (staff.data ?? []).filter((s) => s.is_active);
  // Only things the café actually sells: /api/items is the whole inventory and
  // includes raw materials priced at zero, which are not a paper sale.
  const catalogue = (items.data ?? []).filter((i) => Number(i.sell_price) > 0);

  /** What the till would have charged. The server prices it again and is the
   *  authority; this is so the owner can check the slip before saving. */
  const estimate = useMemo(
    () =>
      lines.reduce((sum, l) => {
        const item = catalogue.find((i) => i.id === l.itemId);
        return sum + (item ? Number(item.sell_price) * (Number(l.quantity) || 0) : 0);
      }, 0),
    [lines, catalogue]
  );

  const timeValid = /^([01]\d|2[0-3]):[0-5]\d$/.test(timeText);
  const ready =
    when !== "" && timeValid && staffId !== "" && lines.some((l) => l.itemId && Number(l.quantity) > 0);

  function reset() {
    setWhen(defaultWhen());
    setTimeText(defaultWhen().slice(11, 16));
    setNote("");
    setLines([{ itemId: "", quantity: "1" }]);
    setError(null);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const row = await mutate<OrderRow>("/api/backdated-sales", {
        // `datetime-local` has no zone; it is the owner's wall clock, which is
        // the café's. Let the browser attach the offset rather than guessing.
        sold_at: new Date(when).toISOString(),
        staff_id: staffId,
        payment_method: method,
        note: note.trim() || null,
        lines: lines
          .filter((l) => l.itemId && Number(l.quantity) > 0)
          .map((l) => ({ item_id: l.itemId, quantity: l.quantity })),
      });
      setDone(`#${row.number} · ${formatRupiah(row.total)} tercatat`);
      setOpen(false);
      reset();
      onRecorded?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Gagal menyimpan — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 section-title">
          Catat penjualan dari nota kertas
          <HelpTip title="Kalau tablet mati">
            Kalau kasir sempat jualan pakai kertas — tablet mati, layar pecah, atau internet putus
            lama — catat tiap nota di sini dengan tanggal dan jamnya yang asli. Stok, pembukuan dan
            poin ikut bergerak persis seperti kalau dulu dicatat di kasir, dan barisnya ditandai
            “dari nota kertas” supaya kelihatan bedanya di daftar transaksi.
          </HelpTip>
        </h2>
        <button
          onClick={() => {
            reset();
            setDone(null);
            setStaffId(staffId || active[0]?.id || "");
            setOpen(true);
          }}
          className="btn-quiet shrink-0 px-3.5 py-2 text-sm"
        >
          <IconPlus className="h-4 w-4" /> Catat nota
        </button>
      </div>
      <p className="ink-soft mt-1 max-w-2xl text-sm">
        Untuk penjualan yang sempat dicatat di kertas. Masuk ke pembukuan dengan tanggal dan jam
        aslinya, dan ditandai <span className="font-medium text-[color:var(--ink)]">dari nota kertas</span> di daftar
        transaksi.
      </p>

      {done && (
        <p className="notice notice-good mt-3">{done}</p>
      )}

      <Sheet open={open} onClose={() => !busy && setOpen(false)} title="Penjualan dari nota kertas">
        {items.loading || staff.loading ? (
          <Skeleton className="h-64" />
        ) : catalogue.length === 0 || active.length === 0 ? (
          <EmptyState icon={<IconBox className="h-5 w-5" />} title="Belum bisa mencatat">
            Perlu minimal satu barang di Stok dan satu staf aktif di Pengaturan.
          </EmptyState>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-[1fr_6rem] gap-3">
              <div>
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">Tanggal asli</span>
                <DateRangePicker
                  single
                  variant="field"
                  since={when.slice(0, 10)}
                  until={when.slice(0, 10)}
                  onChange={(day) => day && setWhen(`${day}T${when.slice(11, 16)}`)}
                  className="w-full"
                />
              </div>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">Jam</span>
                <input
                  className="field text-center tabular-nums"
                  inputMode="numeric"
                  placeholder="19:00"
                  aria-invalid={timeText.length === 5 && !timeValid}
                  value={timeText}
                  onChange={(e) => {
                    const digits = e.target.value.replace(/\D/g, "").slice(0, 4);
                    const text = digits.length > 2 ? `${digits.slice(0, 2)}:${digits.slice(2)}` : digits;
                    setTimeText(text);
                    if (/^([01]\d|2[0-3]):[0-5]\d$/.test(text)) setWhen(`${when.slice(0, 10)}T${text}`);
                  }}
                />
              </label>
            </div>
            <div>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">Kasir yang melayani</span>
                <Select
                  variant="field"
                  allowEmpty={false}
                  ariaLabel="Kasir yang melayani"
                  options={active.map((s) => ({ value: s.id, label: s.name }))}
                  value={staffId}
                  onChange={setStaffId}
                />
              </label>
            </div>

            <div className="space-y-2">
              <span className="ink-soft block text-[13px] font-medium">Barang</span>
              {lines.map((line, i) => (
                <div key={i} className="flex gap-2">
                  <Select
                    variant="field"
                    className="flex-1"
                    placeholder="— pilih barang —"
                    ariaLabel="Barang"
                    options={catalogue.map((item) => ({
                      value: item.id,
                      label: `${item.name} · ${formatRupiah(item.sell_price)}`,
                    }))}
                    value={line.itemId}
                    onChange={(value) =>
                      setLines(lines.map((l, j) => (j === i ? { ...l, itemId: value } : l)))
                    }
                  />
                  <input
                    className="field w-16 shrink-0 text-center tabular-nums"
                    inputMode="decimal"
                    aria-label="Jumlah"
                    value={line.quantity}
                    onChange={(e) =>
                      setLines(
                        lines.map((l, j) =>
                          j === i ? { ...l, quantity: e.target.value.replace(/[^0-9.]/g, "") } : l
                        )
                      )
                    }
                  />
                  {lines.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setLines(lines.filter((_, j) => j !== i))}
                      className="icon-btn h-11 w-9 shrink-0 hover:text-[color:var(--bad)]"
                      aria-label="Hapus baris"
                      title="Hapus baris"
                    >
                      <IconClose className="h-4 w-4" />
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                onClick={() => setLines([...lines, { itemId: "", quantity: "1" }])}
                className="inline-flex items-center gap-1 rounded-lg py-1 text-[13px] font-semibold text-[color:var(--accent)] hover:underline"
              >
                <IconPlus className="h-3.5 w-3.5" /> Tambah barang
              </button>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">Dibayar dengan</span>
                <Select
                  variant="field"
                  allowEmpty={false}
                  ariaLabel="Dibayar dengan"
                  options={METHODS.map((m) => ({ value: m.value, label: m.label }))}
                  value={method}
                  onChange={(value) => setMethod(value as (typeof METHODS)[number]["value"])}
                />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">Catatan</span>
                <input
                  className="field"
                  value={note}
                  onChange={(e) => setNote(e.target.value.slice(0, 200))}
                  placeholder="cth. tablet mati, nota no. 12"
                />
              </label>
            </div>

            <p className="ink-soft surface-inset rounded-2xl px-4 py-3 text-sm">
              Perkiraan total <span className="font-semibold tabular-nums">{formatRupiah(estimate)}</span> —
              harga dan pajak dihitung ulang oleh sistem saat disimpan, sama seperti di kasir.
            </p>

            {error && <p className="notice notice-bad">{error}</p>}

            <button onClick={save} disabled={busy || !ready} className="btn-accent w-full py-3.5">
              {busy ? "Menyimpan…" : "Catat penjualan ini"}
            </button>
          </div>
        )}
      </Sheet>

    </section>
  );
}
