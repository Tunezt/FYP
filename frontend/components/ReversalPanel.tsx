"use client";

/** M15-T11 — the owner's way into a void or a refund.
 *
 * The till's own screen covers today, while the customer is still standing
 * there. This is the other case the roadmap names: a mistake found after the
 * shift closed, from the office, with no time pressure and the whole history to
 * search. Same endpoints, same manager-PIN guard, same `approvals` row (M15-T7)
 * — a void done from the dashboard must not be a different kind of void.
 *
 * The PIN is not proving who the owner is; they are already signed in. It stops
 * a dashboard left open on an unattended laptop from reversing a sale with one
 * click, and it puts a person rather than a session into the audit trail.
 */

import { useCallback, useEffect, useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { formatQty, formatRupiah } from "@/lib/format";
import { dayLabel, timeLabel } from "@/lib/dates";
import type { OrderRow, OrdersPage, Receipt, ReversalResult } from "@/lib/types";
import { EmptyState, Glass, Skeleton } from "@/components/ui";
import { IconChevronDown, IconReceipt, IconSearch } from "@/components/icons";
import { ApiError } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  completed: "",
  voided: "dibatalkan",
  refunded: "dikembalikan",
};

export function ReversalPanel({ tz, dayStart }: { tz?: string; dayStart?: number }) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const mutate = useOwnerMutation();

  useEffect(() => {
    const id = setTimeout(() => setDebounced(query), query ? 250 : 0);
    return () => clearTimeout(id);
  }, [query]);

  const orders = useOwnerData<OrdersPage>(
    `/api/orders?limit=20&q=${encodeURIComponent(debounced)}`
  );

  const [openId, setOpenId] = useState<string | null>(null);
  const [mode, setMode] = useState<"void" | "refund" | null>(null);
  const [restock, setRestock] = useState(true);
  const [pin, setPin] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // A null path means "fetch nothing", so the receipt loads only for the row the
  // owner actually opened — and goes through the same hook as every other read,
  // which is what keeps demo mode working here.
  const opened = useOwnerData<Receipt>(openId ? `/api/orders/${openId}/receipt` : null);
  const receipt = openId ? opened.data : null;

  const reset = useCallback(() => {
    setOpenId(null);
    setMode(null);
    setRestock(true);
    setPin("");
    setNote("");
    setError(null);
  }, []);

  function open(row: OrderRow) {
    reset();
    setOpenId(row.id);
    setDone(null);
  }

  async function submit() {
    if (!receipt || !mode) return;
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { manager_pin: pin, note: note.trim() || null };
      if (mode === "refund") body.restock = restock;
      const result = await mutate<ReversalResult>(`/api/orders/${receipt.order_id}/${mode}`, body);
      setDone(
        `#${receipt.number} ${result.status === "voided" ? "dibatalkan" : "dikembalikan"} — ${formatRupiah(receipt.total)}`
      );
      reset();
      orders.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Gagal memproses — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  const rows = orders.data?.rows ?? [];

  return (
    <section>
      <h2 className="section-title">Batalkan atau kembalikan transaksi</h2>
      <p className="ink-soft mt-1 text-sm">
        Untuk kesalahan yang baru ketahuan setelah shift ditutup. Kasir bisa membatalkan transaksi
        hari ini sendiri dari layar kasir. Perlu PIN pemilik atau manajer, dan setiap persetujuan
        tercatat di Keuangan.
      </p>

      {done && (
        <p className="notice notice-good mt-3">{done}</p>
      )}

      <Glass className="mt-4 px-5 pb-2 pt-4 md:px-6">
        <div className="relative">
          <IconSearch className="ink-faint pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2" />
          <input
            className="field pl-10"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Cari nomor struk, mis. A1B2C3D4"
            aria-label="Cari nomor struk"
          />
        </div>

        {orders.loading && !orders.data ? (
          <Skeleton className="my-3 h-32" />
        ) : rows.length === 0 ? (
          <EmptyState icon={<IconReceipt className="h-5 w-5" />} title="Tidak ada transaksi">
            {query ? "Nomor struk itu tidak ditemukan." : "Belum ada transaksi yang tercatat."}
          </EmptyState>
        ) : (
          <ul className="mt-2">
            {rows.map((row) => {
              const isOpen = openId === row.id;
              const label = STATUS_LABEL[row.status] ?? row.status;
              return (
                <li key={row.id} className="hairline-t first:border-t-0">
                  <button
                    onClick={() => (isOpen ? reset() : open(row))}
                    aria-expanded={isOpen}
                    className="list-row list-row-action border-b-0"
                  >
                    <span className="ink-faint w-20 shrink-0 text-xs">
                      {/* The owner's list spans the whole history, so a bare time
                          would be ambiguous: name the business day too (M15-T4). */}
                      {dayLabel(new Date(row.sold_at), tz, dayStart)}
                      <span className="block tabular-nums">{timeLabel(new Date(row.sold_at), tz)}</span>
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        #{row.number}
                        {label && <span className="ink-faint font-normal"> · {label}</span>}
                      </p>
                      <p className="ink-faint text-xs">
                        {row.line_count} item
                        {row.staff_name ? ` · ${row.staff_name}` : ""}
                        {row.customer_name ? ` · ${row.customer_name}` : ""}
                        {/* M15-T10: a slip somebody typed in reads differently
                            from one the till timestamped itself. */}
                        {row.entry_source === "manual_backdated" && " · dari nota kertas"}
                      </p>
                    </div>
                    <span className="shrink-0 text-sm font-semibold tabular-nums">
                      {formatRupiah(row.total)}
                    </span>
                    <IconChevronDown
                      className={`row-chevron h-4 w-4 shrink-0 ${isOpen ? "rotate-180" : ""}`}
                      aria-hidden
                    />
                  </button>

                  {isOpen && (
                    <div className="pb-4 sm:pl-[5.75rem]">
                      {!receipt ? (
                        <Skeleton className="h-24" />
                      ) : (
                        <>
                          <ul className="ink-soft surface-inset mb-3 space-y-1 rounded-xl px-3 py-2 text-sm">
                            {receipt.lines
                              .filter((l) => Number(l.quantity) > 0)
                              .map((l, i) => (
                                <li key={i} className="flex justify-between gap-3">
                                  <span className="min-w-0 truncate">
                                    {formatQty(l.quantity)}× {l.name}
                                    {l.variant && l.variant !== "Standar" ? ` (${l.variant})` : ""}
                                  </span>
                                  <span className="tabular-nums">{formatRupiah(l.line_total)}</span>
                                </li>
                              ))}
                          </ul>

                          {row.status !== "completed" ? (
                            <p className="ink-soft text-sm">
                              Transaksi ini sudah {label} — tidak bisa dibalik lagi.
                            </p>
                          ) : (
                            <div className="space-y-3">
                              <div className="grid gap-2 sm:grid-cols-2">
                                <button
                                  type="button"
                                  onClick={() => setMode("void")}
                                  aria-pressed={mode === "void"}
                                  className="choice-card"
                                >
                                  Batalkan
                                  <span className="ink-faint block text-xs font-normal">
                                    transaksinya tidak pernah terjadi
                                  </span>
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setMode("refund")}
                                  aria-pressed={mode === "refund"}
                                  className="choice-card"
                                >
                                  Kembalikan
                                  <span className="ink-faint block text-xs font-normal">
                                    uang dikembalikan ke pembeli
                                  </span>
                                </button>
                              </div>

                              {mode && (
                                <>
                                  {mode === "refund" && (
                                    <label className="flex cursor-pointer items-center justify-between gap-3 text-sm">
                                      <span>
                                        Barang kembali ke stok
                                        <span className="ink-faint block text-xs">
                                          matikan kalau barangnya sudah terpakai, tumpah, atau dibuang
                                        </span>
                                      </span>
                                      <input
                                        type="checkbox"
                                        checked={restock}
                                        onChange={(e) => setRestock(e.target.checked)}
                                      />
                                    </label>
                                  )}
                                  <div className="grid gap-2 sm:grid-cols-2">
                                    <label className="block">
                                      <span className="ink-soft mb-1.5 block text-[13px] font-medium">Alasan</span>
                                      <input
                                        className="field"
                                        value={note}
                                        onChange={(e) => setNote(e.target.value.slice(0, 200))}
                                        placeholder="cth. salah pencet menu"
                                      />
                                    </label>
                                    <label className="block">
                                      <span className="ink-soft mb-1.5 block text-[13px] font-medium">
                                        PIN pemilik / manajer
                                      </span>
                                      <input
                                        className="field tabular-nums"
                                        type="password"
                                        inputMode="numeric"
                                        value={pin}
                                        onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
                                        placeholder="••••"
                                      />
                                    </label>
                                  </div>
                                  <button
                                    onClick={submit}
                                    disabled={busy || pin.length < 4}
                                    className="btn-accent px-5 py-2.5 text-sm"
                                  >
                                    {busy
                                      ? "Memproses…"
                                      : mode === "void"
                                        ? `Batalkan #${receipt.number}`
                                        : `Kembalikan ${formatRupiah(receipt.total)}`}
                                  </button>
                                </>
                              )}
                            </div>
                          )}
                        </>
                      )}
                      {error && <p className="notice notice-bad mt-3">{error}</p>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Glass>
    </section>
  );
}
