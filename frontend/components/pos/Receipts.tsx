"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatQty, formatRupiah } from "@/lib/format";
import { ORDER_TYPE_LABEL, type OrderType } from "@/lib/types";

export type Receipt = {
  order_id: string;
  number: string;
  business_name: string;
  staff_name: string | null;
  customer_name: string | null;
  points_earned: number;
  points_redeemed: number;
  status: string;
  order_type: string;
  table_label: string | null;
  delivery_address: string | null;
  delivery_fee: string;
  sold_at: string;
  lines: {
    name: string;
    variant: string | null;
    quantity: string;
    unit_price: string;
    line_total: string;
    modifiers: { name: string; price_delta: string }[];
    notes: string | null;
  }[];
  subtotal: string;
  discount_total: string;
  promo_total: string;
  promo_names: string[];
  voucher_total: string;
  voucher_code: string | null;
  service_charge: string;
  tax_total: string;
  tax_inclusive: boolean;
  rounding: string;
  total: string;
  payments: { method: string; amount: string }[];
  parent_number?: string | null;
};

/** One row of the recent-sales list (M15-T11): enough to recognise the sale
 *  across the counter without opening it. */
type OrderRow = {
  id: string;
  number: string;
  sold_at: string;
  status: string;
  order_type: string;
  total: string;
  line_count: number;
  staff_name: string | null;
  customer_name: string | null;
  table_label: string | null;
};

type OrdersPage = { total: number; rows: OrderRow[] };

type ReversalResult = {
  order_id: string;
  status: string;
  reversing_lines: { id: string; item_id: string; quantity: string; line_total: string; stock_after: string | null }[];
  reversing_payments: { id: string; method: string; amount: string; reference: string | null }[];
};


/** M15-T11 — the screen that was missing.
 *
 *  `POST /pos/orders/{id}/void` and `/refund` have existed and been tested since
 *  M3-T4, and M15-T7 gave them a manager role and an audit trail, but nothing in
 *  the app ever called them: a sale rung up wrong needed a developer. Three
 *  steps, in the order a cashier thinks in — find it, look at it, reverse it.
 *
 *  The list is today's business day only (the server decides, M15-T4). A mistake
 *  found after the shift closed is the owner's job on the dashboard. */
export function TransactionsView({
  token,
  onReversed,
}: {
  token: string | null;
  onReversed: () => void;
}) {
  const [rows, setRows] = useState<OrderRow[] | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Receipt | null>(null);
  const [mode, setMode] = useState<"void" | "refund" | null>(null);
  const [restock, setRestock] = useState(true);
  const [pin, setPin] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ result: ReversalResult; receipt: Receipt } | null>(null);
  const [printing, setPrinting] = useState<Receipt | null>(null);

  const load = useCallback(
    (q: string) => {
      api<OrdersPage>(`/pos/orders?q=${encodeURIComponent(q)}`, { token })
        .then((page) => setRows(page.rows))
        .catch((e: unknown) => {
          setRows([]);
          setError(e instanceof ApiError ? e.detail : "Tidak bisa memuat transaksi.");
        });
    },
    [token]
  );
  useEffect(() => {
    const id = setTimeout(() => load(query), query ? 250 : 0);
    return () => clearTimeout(id);
  }, [load, query]);

  async function openOrder(row: OrderRow) {
    setError(null);
    try {
      setOpen(await api<Receipt>(`/pos/orders/${row.id}/receipt`, { token }));
      setMode(null);
      setPin("");
      setNote("");
      setRestock(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Tidak bisa membuka struk.");
    }
  }

  async function submit() {
    if (!open || !mode) return;
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { manager_pin: pin, note: note.trim() || null };
      if (mode === "refund") body.restock = restock;
      const result = await api<ReversalResult>(`/pos/orders/${open.order_id}/${mode}`, { body, token });
      const receipt = await api<Receipt>(`/pos/orders/${open.order_id}/receipt`, { token });
      setDone({ result, receipt });
      setOpen(null);
      setMode(null);
      setPin("");
      onReversed();
      load(query);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Gagal memproses — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  const time = (iso: string) =>
    new Date(iso).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });

  return (
    <div>
      <div className="glass-card px-5 pb-6 pt-5 sm:px-6">
        <div>
          <h1 className="text-[21px] font-semibold tracking-[-0.02em]">Riwayat transaksi hari ini</h1>
          <p className="ink-soft mt-0.5 text-sm">
            Salah pencet setelah dibayar? Buka transaksinya, lalu batalkan atau kembalikan. Perlu PIN pemilik atau
            manajer. Pesanan yang belum dibayar dibatalkan dari Pesanan aktif.
          </p>
        </div>

        {error && <p className="mt-3 text-sm text-[color:var(--bad)]">{error}</p>}

        {/* 3 — what happened */}
        {done ? (
          <div className="mt-4">
            <div
              className="rounded-3xl px-5 py-4"
              style={{ background: "var(--good-bg)", color: "var(--good)" }}
            >
              <p className="text-lg font-bold">
                {done.result.status === "voided" ? "Transaksi dibatalkan" : "Uang dikembalikan"}
              </p>
              <p className="text-sm">
                #{done.receipt.number} · {formatRupiah(done.receipt.total)}
                {done.result.reversing_lines.some((l) => l.stock_after !== null)
                  ? " · stok sudah dikembalikan"
                  : " · stok tidak dikembalikan"}
              </p>
            </div>
            <div className="mt-4 flex gap-3">
              <button onClick={() => setPrinting(done.receipt)} className="btn-quiet flex-1 py-3">
                🖨 Cetak struk
              </button>
              <button
                onClick={() => {
                  setDone(null);
                  load(query);
                }}
                className="btn-accent flex-1 py-3"
              >
                Selesai
              </button>
            </div>
          </div>
        ) : open ? (
          /* 2 — look at it, then choose */
          <div className="mt-4">
            <button onClick={() => setOpen(null)} className="ink-soft text-sm">
              ← kembali ke daftar
            </button>
            <div className="surface-inset rounded-2xl mt-3 px-4 py-3">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-lg font-bold tracking-[-0.02em]">#{open.number}</p>
                <p className="text-lg font-bold tabular-nums">{formatRupiah(open.total)}</p>
              </div>
              <p className="ink-soft text-xs">
                {time(open.sold_at)}
                {open.staff_name ? ` · ${open.staff_name}` : ""}
                {open.customer_name ? ` · utk ${open.customer_name}` : ""}
              </p>
              <ul className="mt-2 space-y-0.5 text-sm">
                {open.lines
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
            </div>

            {open.status !== "completed" ? (
              <p className="surface-inset rounded-2xl mt-4 px-4 py-6 text-center text-sm">
                Transaksi ini {open.status === "voided" ? "sudah dibatalkan" : "sudah dikembalikan"} —
                tidak bisa dibalik lagi.
              </p>
            ) : (
              <>
                <div className="mt-4 grid grid-cols-2 gap-3">
                  <button
                    onClick={() => setMode("void")}
                    className={`rounded-2xl px-4 py-3 text-sm font-semibold ${mode === "void" ? "btn-accent" : "btn-quiet"}`}
                  >
                    Batalkan
                    <span className="ink-faint block text-[11px] font-normal">salah pencet, barang belum diambil</span>
                  </button>
                  <button
                    onClick={() => setMode("refund")}
                    className={`rounded-2xl px-4 py-3 text-sm font-semibold ${mode === "refund" ? "btn-accent" : "btn-quiet"}`}
                  >
                    Kembalikan
                    <span className="ink-faint block text-[11px] font-normal">uang dikembalikan ke pembeli</span>
                  </button>
                </div>

                {mode && (
                  <div className="mt-4 space-y-3">
                    {mode === "refund" && (
                      <label className="surface-inset rounded-2xl flex items-center justify-between gap-3 rounded-2xl px-4 py-3 text-sm">
                        <span>
                          Barang kembali ke stok
                          <span className="ink-faint block text-[11px]">
                            matikan kalau barangnya sudah terpakai, tumpah, atau dibuang
                          </span>
                        </span>
                        <input
                          type="checkbox"
                          checked={restock}
                          onChange={(e) => setRestock(e.target.checked)}
                          className="h-5 w-5"
                        />
                      </label>
                    )}
                    <label className="block">
                      <span className="ink-soft text-[13px] font-medium">Alasan</span>
                      <input
                        value={note}
                        onChange={(e) => setNote(e.target.value.slice(0, 200))}
                        className="field mt-1 w-full text-sm"
                        placeholder="cth. salah pencet menu"
                      />
                    </label>
                    <label className="block">
                      <span className="ink-soft text-[13px] font-medium">PIN pemilik / manajer</span>
                      <input
                        type="password"
                        inputMode="numeric"
                        value={pin}
                        onChange={(e) => setPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
                        className="field mt-1 w-full text-sm tabular-nums"
                        placeholder="••••"
                      />
                    </label>
                    <button
                      onClick={submit}
                      disabled={busy || pin.length < 4}
                      className="btn-accent w-full py-3.5 text-lg"
                    >
                      {busy
                        ? "Memproses…"
                        : mode === "void"
                          ? `Batalkan #${open.number}`
                          : `Kembalikan ${formatRupiah(open.total)}`}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        ) : (
          /* 1 — find it */
          <div className="mt-4">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="field w-full py-3 text-sm"
              placeholder="Cari nomor struk, mis. A1B2C3D4"
            />
            {rows === null ? (
              <p className="surface-inset rounded-2xl mt-4 px-4 py-6 text-center text-sm">Memuat…</p>
            ) : rows.length === 0 ? (
              <p className="surface-inset rounded-2xl mt-4 px-4 py-6 text-center text-sm">
                {query ? "Nomor struk itu tidak ada hari ini." : "Belum ada transaksi hari ini."}
              </p>
            ) : (
              <ul className="mt-4 space-y-2">
                {rows.map((row) => (
                  <li key={row.id}>
                    <button
                      onClick={() => openOrder(row)}
                      className="surface-inset rounded-2xl flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
                    >
                      <div className="min-w-0">
                        <p className="font-bold tracking-tight">
                          #{row.number}
                          {row.status !== "completed" && (
                            <span className="ink-faint font-normal">
                              {" "}
                              · {row.status === "voided" ? "dibatalkan" : "dikembalikan"}
                            </span>
                          )}
                        </p>
                        <p className="ink-soft text-xs">
                          {time(row.sold_at)} · {row.line_count} item
                          {row.staff_name ? ` · ${row.staff_name}` : ""}
                        </p>
                      </div>
                      <p className="shrink-0 font-semibold tabular-nums">{formatRupiah(row.total)}</p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
      {printing && <ReceiptSheet receipt={printing} onClose={() => setPrinting(null)} />}
    </div>
  );
}



export function ReceiptSheet({ receipt, onClose }: { receipt: Receipt; onClose: () => void }) {
  const when = new Date(receipt.sold_at).toLocaleString("id-ID", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
  const method = (m: string) => (m === "cash" ? "Tunai" : m.toUpperCase());
  return (
    <div className="sheet-scrim z-[60] items-center p-6 print:bg-transparent print:backdrop-blur-none" onClick={onClose}>
      <style>{`@media print { body * { visibility: hidden; } #receipt, #receipt * { visibility: visible; } #receipt { position: absolute; left: 0; top: 0; width: 80mm; box-shadow: none; border-radius: 0; } }`}</style>
      <div
        id="receipt"
        onClick={(e) => e.stopPropagation()}
        className="w-[320px] rounded-2xl bg-white px-5 py-6 font-mono text-[12px] leading-5 text-black shadow-pop"
      >
        <p className="text-center text-sm font-bold uppercase">{receipt.business_name}</p>
        <p className="text-center">{when}</p>
        <p className="text-center">
          #{receipt.number}
          {receipt.staff_name ? ` · ${receipt.staff_name}` : ""}
          {receipt.customer_name ? ` · utk ${receipt.customer_name}` : ""}
          {receipt.status !== "completed" ? ` · ${receipt.status === "voided" ? "DIBATALKAN" : "DIKEMBALIKAN"}` : ""}
        </p>
        <p className="text-center">
          {ORDER_TYPE_LABEL[receipt.order_type as OrderType] ?? receipt.order_type}
          {receipt.table_label ? ` · ${receipt.table_label}` : ""}
          {receipt.delivery_address ? ` · ${receipt.delivery_address}` : ""}
        </p>
        {receipt.parent_number && <p className="text-center">Tambahan untuk #{receipt.parent_number}</p>}
        <hr className="my-3 border-dashed border-black" />
        {receipt.lines.map((l, i) => (
          <div key={i} className="mb-2">
            <div className="flex justify-between gap-2">
              <span>
                {formatQty(l.quantity)}× {l.name}
                {l.variant && l.variant !== "Standar" ? ` (${l.variant})` : ""}
              </span>
              <span>{formatRupiah(l.line_total)}</span>
            </div>
            {l.modifiers.map((m, j) => (
              <div key={j} className="flex justify-between gap-2 pl-4 text-[11px]">
                <span>+ {m.name}</span>
                <span>{Number(m.price_delta) > 0 ? formatRupiah(m.price_delta) : ""}</span>
              </div>
            ))}
            {l.notes && <div className="pl-4 text-[11px] italic">{l.notes}</div>}
          </div>
        ))}
        <hr className="my-3 border-dashed border-black" />
        {(Number(receipt.discount_total) > 0 ||
          Number(receipt.promo_total) > 0 ||
          Number(receipt.voucher_total) > 0 ||
          Number(receipt.service_charge) > 0 ||
          Number(receipt.delivery_fee) > 0 ||
          Number(receipt.tax_total) > 0 ||
          Number(receipt.rounding) !== 0) && (
          <div className="space-y-0.5">
            <div className="flex justify-between">
              <span>Subtotal</span>
              <span>{formatRupiah(receipt.subtotal)}</span>
            </div>
            {Number(receipt.discount_total) > 0 && (
              <div className="flex justify-between">
                <span>Diskon</span>
                <span>-{formatRupiah(receipt.discount_total)}</span>
              </div>
            )}
            {Number(receipt.promo_total) > 0 && (
              <div className="flex justify-between">
                <span>Promo{receipt.promo_names.length ? ` (${receipt.promo_names.join(", ")})` : ""}</span>
                <span>-{formatRupiah(receipt.promo_total)}</span>
              </div>
            )}
            {Number(receipt.voucher_total) > 0 && (
              <div className="flex justify-between">
                <span>Voucher {receipt.voucher_code ?? ""}</span>
                <span>-{formatRupiah(receipt.voucher_total)}</span>
              </div>
            )}
            {Number(receipt.service_charge) > 0 && (
              <div className="flex justify-between">
                <span>Service</span>
                <span>{formatRupiah(receipt.service_charge)}</span>
              </div>
            )}
            {Number(receipt.delivery_fee) > 0 && (
              <div className="flex justify-between">
                <span>Ongkos kirim</span>
                <span>{formatRupiah(receipt.delivery_fee)}</span>
              </div>
            )}
            {Number(receipt.tax_total) > 0 && (
              <div className="flex justify-between">
                <span>{receipt.tax_inclusive ? "Pajak (termasuk)" : "Pajak"}</span>
                <span>{formatRupiah(receipt.tax_total)}</span>
              </div>
            )}
            {Number(receipt.rounding) !== 0 && (
              <div className="flex justify-between">
                <span>Pembulatan</span>
                <span>
                  {Number(receipt.rounding) < 0 ? "-" : ""}
                  {formatRupiah(Math.abs(Number(receipt.rounding)))}
                </span>
              </div>
            )}
          </div>
        )}
        <div className="flex justify-between font-bold">
          <span>TOTAL</span>
          <span>{formatRupiah(receipt.total)}</span>
        </div>
        {receipt.payments.map((p, i) => (
          <div key={i} className="flex justify-between">
            <span>{method(p.method)}</span>
            <span>{formatRupiah(p.amount)}</span>
          </div>
        ))}
        {(receipt.points_earned > 0 || receipt.points_redeemed > 0) && (
          <p className="mt-2 text-center text-[11px]">
            {receipt.points_redeemed > 0 ? `Poin dipakai: ${receipt.points_redeemed}` : ""}
            {receipt.points_redeemed > 0 && receipt.points_earned > 0 ? " · " : ""}
            {receipt.points_earned > 0 ? `Poin didapat: +${receipt.points_earned}` : ""}
          </p>
        )}
        <p className="mt-4 text-center">Terima kasih 🙏</p>
        <div className="mt-4 flex gap-2 print:hidden">
          <button onClick={() => window.print()} className="btn-accent flex-1 py-2 text-sm">
            Cetak
          </button>
          <button onClick={onClose} className="btn-quiet flex-1 py-2 text-sm">
            Tutup
          </button>
        </div>
      </div>
    </div>
  );
}

