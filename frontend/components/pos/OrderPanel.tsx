"use client";

import { IconClose, IconLock, IconNote } from "@/components/icons";
import { formatRupiah } from "@/lib/format";
import { SERVICE_LABEL } from "@/lib/pos";

export type PanelLine = {
  uid: string;
  title: string;          // "Americano · Large"
  modifiers: string[];
  notes: string;
  qty: number;
  unitPrice: number;
  discount: number;
  maxQty: number;
  guest: string | null;   // bill-2: "QR · Andi" when a guest added it from the menu
};

/** A line of an open bill that already went to the Bar/Dapur (bill-1): shown,
 *  locked, and only taken off with a reason. */
export type SentPanelLine = {
  uid: string;
  title: string;
  modifiers: string[];
  notes: string;
  qty: number;
  lineTotal: number;
  batch: number;
};

export type PanelQuote = {
  subtotal: string;
  discount_total: string;
  promo_total: string;
  service_charge: string;
  delivery_fee: string;
  tax_total: string;
  tax_inclusive: boolean;
  rounding: string;
  total: string;
} | null;

/** What the panel is building (svc-6, bill-1): a fresh order, or an unpaid
 *  order reopened from "Pesanan aktif" — for dine-in, the table's open bill. */
export type PanelContext =
  | { kind: "new" }
  | { kind: "open"; title: string; source: "pos" | "menu"; dirty: boolean };

/** The order being built, always in view on a wide till and in the drawer on a
 *  narrow one. A dine-in table's bill is *sent* to the Bar/Dapur as it grows and
 *  paid once at the end; everything else is paid at the counter. */
export function OrderPanel({
  context,
  lines,
  sentLines,
  quote,
  quoting,
  orderType,
  orderTypes,
  onOrderType,
  guestName,
  onGuestName,
  tableLabel,
  onTableLabel,
  externalRef,
  onExternalRef,
  onQty,
  onEdit,
  onDiscount,
  onCancelSent,
  onClear,
  onHold,
  onSend,
  onPay,
  onClose,
  error,
  busy,
}: {
  context: PanelContext;
  lines: PanelLine[];
  sentLines: SentPanelLine[];
  quote: PanelQuote;
  quoting: boolean;
  orderType: string;
  orderTypes: string[];
  onOrderType: ((t: string) => void) | null;
  guestName: string;
  onGuestName: (v: string) => void;
  tableLabel: string;
  onTableLabel: (v: string) => void;
  externalRef: string;
  onExternalRef: (v: string) => void;
  onQty: (uid: string, delta: number) => void;
  onEdit: (uid: string) => void;
  onDiscount: ((uid: string) => void) | null;
  onCancelSent: (uid: string) => void;
  onClear: () => void;
  onHold: (() => void) | null;
  onSend: (() => void) | null;
  onPay: () => void;
  onClose?: () => void;
  error: string | null;
  busy: boolean;
}) {
  const unsentCount = lines.reduce((n, l) => n + l.qty, 0);
  const sentCount = sentLines.reduce((n, l) => n + l.qty, 0);
  const gross = lines.reduce((s, l) => s + l.unitPrice * l.qty - l.discount, 0) + sentLines.reduce((s, l) => s + l.lineTotal, 0);
  const total = quote ? Number(quote.total) : gross;
  const empty = lines.length === 0 && sentLines.length === 0;
  const bill = orderType === "dine_in";
  const sendFirst = onSend !== null && lines.length > 0;

  const title = context.kind === "new" ? "Pesanan baru" : context.title;
  const subtitle =
    context.kind === "new"
      ? empty
        ? bill
          ? "Pilih menu, isi meja, lalu kirim ke dapur/bar"
          : "Pilih menu di sebelah kiri"
        : `${unsentCount} item`
      : [
          bill ? "Tagihan meja" : context.source === "menu" ? "Dari QR" : "Disimpan di kasir",
          "belum dibayar",
          sentCount ? `${sentCount} sudah dikirim` : null,
          context.dirty ? "ada perubahan" : null,
        ]
          .filter(Boolean)
          .join(" · ");

  return (
    <section aria-label="Ringkasan pesanan" className="flex h-full min-h-0 flex-col">
      <header className="flex items-start justify-between gap-3 px-5 pb-3 pt-4">
        <div className="min-w-0">
          <h2 className="truncate text-[19px] font-semibold tracking-[-0.015em]">{title}</h2>
          <p className="ink-soft mt-0.5 truncate text-[13px]">{subtitle}</p>
        </div>
        {onClose && (
          <button onClick={onClose} aria-label="Tutup ringkasan" className="icon-btn ink-soft h-10 w-10 shrink-0 rounded-full">
            <IconClose className="h-5 w-5" />
          </button>
        )}
      </header>

      <div className="px-5">
        {onOrderType && (
          <div className={`segmented grid w-full gap-[3px] ${orderTypes.length === 4 ? "grid-cols-2" : "grid-cols-3"}`} role="group" aria-label="Jenis pesanan">
            {orderTypes.map((t) => (
              <button
                key={t}
                onClick={() => onOrderType(t)}
                aria-pressed={orderType === t}
                className="segmented-item min-h-[2.5rem] whitespace-nowrap px-2 text-[13px]"
              >
                {SERVICE_LABEL[t] ?? t}
              </button>
            ))}
          </div>
        )}
        <div className={`flex gap-2 ${onOrderType ? "mt-2" : ""}`}>
          {bill && (
            <input
              value={tableLabel}
              onChange={(e) => onTableLabel(e.target.value.slice(0, 20))}
              className="field w-28 py-2 text-sm font-semibold"
              placeholder="Meja"
              aria-label="Nomor meja"
            />
          )}
          <input
            value={guestName}
            onChange={(e) => onGuestName(e.target.value.slice(0, 60))}
            className="field min-w-0 flex-1 py-2 text-sm"
            placeholder="Nama pelanggan (opsional)"
            aria-label="Nama pelanggan"
          />
          {!bill && (
            <input
              value={externalRef}
              onChange={(e) => onExternalRef(e.target.value.slice(0, 40))}
              className="field w-32 py-2 text-sm"
              placeholder="Kode driver"
              aria-label="Kode driver (opsional)"
              title="Nomor pesanan dari aplikasi ojol, kalau ada"
            />
          )}
        </div>
      </div>

      <div className="mt-3 min-h-0 flex-1 overflow-y-auto px-5">
        {empty ? (
          <div className="flex h-full min-h-[8rem] flex-col items-center justify-center text-center">
            <p className="text-[15px] font-medium">Belum ada item</p>
            <p className="ink-soft mt-1 max-w-[15rem] text-sm">Ketuk menu untuk menambahkan. Ukuran dan pilihan wajib ditanyakan dulu.</p>
          </div>
        ) : (
          <>
            {sentLines.length > 0 && (
              <>
                <p className="ink-faint pb-1 text-[12px] font-semibold uppercase tracking-[0.04em]">Sudah dikirim</p>
                <ul className="hairline-t">
                  {sentLines.map((l) => (
                    <li key={l.uid} className="hairline-b flex items-start gap-3 py-2.5">
                      <span className="w-7 shrink-0 text-[15px] font-semibold tabular-nums">{l.qty}×</span>
                      <div className="min-w-0 flex-1">
                        <p className="text-[15px] font-medium leading-snug">{l.title}</p>
                        {l.modifiers.length > 0 && <p className="ink-soft text-[13px] leading-snug">{l.modifiers.join(", ")}</p>}
                        {l.notes && (
                          <p className="mt-0.5 flex items-center gap-1 text-[13px] leading-snug" style={{ color: "var(--warn)" }}>
                            <IconNote className="h-3.5 w-3.5 shrink-0" /> {l.notes}
                          </p>
                        )}
                        <p className="ink-faint mt-0.5 flex items-center gap-1 text-xs">
                          <IconLock className="h-3 w-3" /> {l.batch > 1 ? `Tambahan ${l.batch - 1}` : "Kiriman pertama"}
                        </p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <p className="text-sm tabular-nums">{formatRupiah(l.lineTotal)}</p>
                        <button onClick={() => onCancelSent(l.uid)} disabled={busy} className="ink-soft rounded-lg px-1.5 py-1 text-xs underline-offset-2 hover:underline">
                          batalkan
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {lines.length > 0 && (
              <>
                {sentLines.length > 0 && (
                  <p className="pb-1 pt-4 text-[12px] font-semibold uppercase tracking-[0.04em]" style={{ color: "var(--warn)" }}>
                    Belum dikirim
                  </p>
                )}
                <ul className="hairline-t">
                  {lines.map((l) => (
                    <li key={l.uid} className="hairline-b flex items-start gap-3 py-3">
                      <button onClick={() => onEdit(l.uid)} className="min-w-0 flex-1 rounded-lg text-left" aria-label={`Ubah ${l.title}`}>
                        <p className="text-[15px] font-semibold leading-snug">
                          {l.title}
                          {l.guest && <span className="pill-warn ml-1.5 align-middle text-[11px]">{l.guest}</span>}
                        </p>
                        {l.modifiers.length > 0 && <p className="ink-soft text-[13px] leading-snug">{l.modifiers.join(", ")}</p>}
                        {l.notes && (
                          <p className="mt-0.5 flex items-center gap-1 text-[13px] leading-snug" style={{ color: "var(--warn)" }}>
                            <IconNote className="h-3.5 w-3.5 shrink-0" /> {l.notes}
                          </p>
                        )}
                        <p className="ink-faint mt-0.5 text-xs tabular-nums">
                          {formatRupiah(l.unitPrice)}
                          {l.discount > 0 ? ` · diskon ${formatRupiah(l.discount)}` : ""}
                        </p>
                      </button>
                      <div className="flex shrink-0 flex-col items-end gap-1.5">
                        <p className="text-[15px] font-semibold tabular-nums">{formatRupiah(l.unitPrice * l.qty - l.discount)}</p>
                        <div className="surface-inset flex items-center rounded-xl p-0.5" role="group" aria-label={`Jumlah ${l.title}`}>
                          <button
                            onClick={() => onQty(l.uid, -1)}
                            aria-label={l.qty === 1 ? `Hapus ${l.title}` : `Kurangi ${l.title}`}
                            className="h-9 w-9 rounded-[10px] text-lg font-medium transition-colors hover:bg-[color:var(--surface)]"
                          >
                            −
                          </button>
                          <span className="w-7 text-center text-sm font-semibold tabular-nums">{l.qty}</span>
                          <button
                            onClick={() => onQty(l.uid, +1)}
                            disabled={l.qty >= l.maxQty}
                            aria-label={`Tambah ${l.title}`}
                            className="h-9 w-9 rounded-[10px] text-lg font-medium transition-colors hover:bg-[color:var(--surface)] disabled:opacity-30"
                          >
                            +
                          </button>
                        </div>
                        {onDiscount && (
                          <button onClick={() => onDiscount(l.uid)} className="ink-faint text-xs underline-offset-2 hover:underline">
                            {l.discount > 0 ? "ubah diskon" : "diskon"}
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>

      <footer className="hairline-t px-5 pb-5 pt-3">
        {!empty && quote && (
          <dl className="mb-2 space-y-0.5 text-[13px]">
            {(Number(quote.discount_total) > 0 || Number(quote.promo_total) > 0 || Number(quote.service_charge) > 0 ||
              Number(quote.tax_total) > 0 || Number(quote.rounding) !== 0 || Number(quote.delivery_fee) > 0) && (
              <Row label="Subtotal" value={formatRupiah(quote.subtotal)} />
            )}
            {Number(quote.discount_total) > 0 && <Row label="Diskon" value={`− ${formatRupiah(quote.discount_total)}`} />}
            {Number(quote.promo_total) > 0 && <Row label="Promo" value={`− ${formatRupiah(quote.promo_total)}`} />}
            {Number(quote.service_charge) > 0 && <Row label="Service" value={formatRupiah(quote.service_charge)} />}
            {Number(quote.delivery_fee) > 0 && <Row label="Ongkos kirim" value={formatRupiah(quote.delivery_fee)} />}
            {Number(quote.tax_total) > 0 && <Row label={quote.tax_inclusive ? "Pajak (termasuk)" : "Pajak"} value={formatRupiah(quote.tax_total)} />}
            {Number(quote.rounding) !== 0 && <Row label="Pembulatan" value={formatRupiah(quote.rounding)} />}
          </dl>
        )}
        <div className="flex items-baseline justify-between">
          <span className="ink-soft text-sm">{bill && !empty ? "Total tagihan" : "Total"}</span>
          <span className={`text-[26px] font-semibold tabular-nums tracking-[-0.02em] transition-opacity ${quoting ? "opacity-60" : ""}`}>{formatRupiah(total)}</span>
        </div>
        {error && (
          <p role="alert" className="notice notice-bad mt-2 text-sm">
            {error}
          </p>
        )}
        {sendFirst ? (
          <>
            <button onClick={onSend!} disabled={busy} className="btn-accent mt-3 w-full py-3.5 text-base">
              Kirim ke dapur/bar · {unsentCount} item
            </button>
            <p className="ink-faint mt-1.5 text-center text-xs">Slip bar/dapur dan nota meja dicetak. Dibayar nanti, saat meja selesai.</p>
          </>
        ) : (
          <button onClick={onPay} disabled={empty || busy} className="btn-accent mt-3 w-full py-3.5 text-base">
            Bayar {formatRupiah(total)}
          </button>
        )}
        <div className="mt-2 flex gap-2">
          {sendFirst && (
            <button onClick={onPay} disabled={busy} className="btn-quiet flex-1 py-2.5 text-sm">
              Bayar sekarang
            </button>
          )}
          {onHold && lines.length > 0 && (
            <button onClick={onHold} disabled={busy} className="btn-quiet flex-1 py-2.5 text-sm">
              {bill ? "Simpan, belum kirim" : context.kind === "open" ? "Simpan perubahan" : "Simpan, bayar nanti"}
            </button>
          )}
          <button onClick={onClear} disabled={busy || (empty && context.kind === "new")} className="btn-quiet px-4 py-2.5 text-sm">
            {context.kind === "new" ? "Kosongkan" : "Tutup"}
          </button>
        </div>
      </footer>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="ink-soft">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}
