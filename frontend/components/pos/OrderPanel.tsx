"use client";

import { IconClose, IconLock, IconNote, IconTag } from "@/components/icons";
import { formatRupiah } from "@/lib/format";
import { SERVICE_HINT, SERVICE_LABEL, taxLineLabel } from "@/lib/pos";
import { FormHint } from "@/components/FormHint";

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
  tax_label?: string;
  tax_rate?: string;
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
  onNote,
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
  onNote: (uid: string) => void;
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
  // till-2: the main button says what it is still waiting for.
  const sendMissing = sendFirst && !tableLabel.trim() ? "Isi nomor meja dulu" : null;
  // till-4: no order type is chosen for the cashier.
  const noType = orderType === "";
  // till-9: an ojol order is matched to its driver by the app's order code.
  const ojol = orderType === "pickup";
  const payMissing = empty
    ? "Ketuk menu untuk menambahkan item"
    : noType
      ? "Pilih jenis pesanan"
      : ojol && !externalRef.trim()
        ? "Isi kode pesanan ojol"
        : null;

  // A subtotal equal to the total (e.g. tax included in the price) says
  // nothing; the printed receipt leaves it out too (till-8).
  const parts: [string, string][] = quote
    ? ([
        Number(quote.subtotal) !== Number(quote.total) ? ["Subtotal", formatRupiah(quote.subtotal)] : null,
        Number(quote.discount_total) > 0 ? ["Diskon", `− ${formatRupiah(quote.discount_total)}`] : null,
        Number(quote.promo_total) > 0 ? ["Promo", `− ${formatRupiah(quote.promo_total)}`] : null,
        Number(quote.service_charge) > 0 ? ["Service", formatRupiah(quote.service_charge)] : null,
        Number(quote.delivery_fee) > 0 ? ["Ongkos kirim", formatRupiah(quote.delivery_fee)] : null,
        Number(quote.tax_total) > 0 ? [taxLineLabel(quote.tax_label, quote.tax_rate, quote.tax_inclusive), formatRupiah(quote.tax_total)] : null,
        Number(quote.rounding) !== 0 ? ["Pembulatan", formatRupiah(quote.rounding)] : null,
      ].filter(Boolean) as [string, string][])
    : [];

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
      {/* polish-3: every row of chrome here is a row of the order the cashier
          cannot see. On the café's tablet the panel is about 600 px tall, so the
          title and its count share one line, the breakdown is one line, and the
          actions sit beside the main button instead of under it. */}
      <header className="flex items-center gap-2 px-5 pb-2 pt-3">
        <div className="flex min-w-0 flex-1 items-baseline gap-2">
          <h2 className="shrink-0 truncate text-[18px] font-semibold tracking-[-0.015em]">{title}</h2>
          <p className="ink-soft min-w-0 truncate text-[13px]">{subtitle}</p>
        </div>
        {!(empty && context.kind === "new") && (
          <button onClick={onClear} disabled={busy} className="ink-soft -mr-1.5 inline-flex h-9 shrink-0 items-center rounded-lg px-2.5 text-[13px] font-medium transition-colors hover:bg-[color:var(--row-hover)] hover:text-[color:var(--ink)] active:bg-[color:var(--row-press)]">
            {context.kind === "new" ? "Kosongkan" : "Tutup"}
          </button>
        )}
        {onClose && (
          <button onClick={onClose} aria-label="Tutup ringkasan" className="icon-btn ink-soft h-10 w-10 shrink-0 rounded-full">
            <IconClose className="h-5 w-5" />
          </button>
        )}
      </header>

      <div className="px-5">
        {onOrderType && (
          /* till-9: three separate, outlined choices under a named group, not a
             grey strip that reads as a label. Unchosen, the name says so in red. */
          <fieldset>
            <legend className="mb-1.5 flex items-baseline gap-1.5 text-[13px] font-semibold">
              Jenis pesanan
              {noType ? (
                <span className="text-[12px] font-medium text-[color:var(--bad)]">· wajib dipilih</span>
              ) : (
                SERVICE_HINT[orderType] && <span className="ink-faint text-[12px] font-normal">· {SERVICE_HINT[orderType]}</span>
              )}
            </legend>
            <div className="grid grid-cols-3 gap-2">
              {orderTypes.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => onOrderType(t)}
                  aria-pressed={orderType === t}
                  title={SERVICE_HINT[t]}
                  className="choice-card flex min-h-[2.75rem] items-center justify-center px-1.5 py-1.5 text-center text-[14px] font-semibold leading-tight"
                >
                  {SERVICE_LABEL[t] ?? t}
                </button>
              ))}
            </div>
          </fieldset>
        )}
        <div className={`flex gap-2 ${onOrderType ? "mt-2" : ""}`}>
          {bill && (
            <input
              value={tableLabel}
              onChange={(e) => onTableLabel(e.target.value.slice(0, 20))}
              className="field min-h-[2.75rem] w-28 py-2 text-sm font-semibold"
              placeholder="No. meja"
              aria-label="Nomor meja"
              aria-required="true"
            />
          )}
          {ojol && (
            <input
              value={externalRef}
              onChange={(e) => onExternalRef(e.target.value.slice(0, 40))}
              className="field min-h-[2.75rem] w-40 py-2 text-sm font-semibold"
              placeholder="Kode pesanan ojol"
              aria-label="Kode pesanan ojol"
              aria-required="true"
              title="Nomor pesanan dari aplikasi GoFood / GrabFood / ShopeeFood"
            />
          )}
          <input
            value={guestName}
            onChange={(e) => onGuestName(e.target.value.slice(0, 60))}
            className="field min-h-[2.75rem] min-w-0 flex-1 py-2 text-sm"
            placeholder={ojol ? "Nama (opsional)" : "Nama pelanggan (opsional)"}
            aria-label="Nama pelanggan"
          />
        </div>
      </div>

      <div className="mt-2.5 min-h-0 flex-1 overflow-y-auto overscroll-contain px-5">
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
                        <button onClick={() => onCancelSent(l.uid)} disabled={busy} className="ink-soft -mr-2 inline-flex min-h-[2.75rem] items-center rounded-lg px-2 text-xs underline-offset-2 hover:underline">
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
                    <li key={l.uid} className="hairline-b py-2.5">
                      <div className="flex items-start gap-3">
                        <button onClick={() => onEdit(l.uid)} className="min-w-0 flex-1 rounded-lg text-left" aria-label={`Ubah ${l.title}`}>
                          <p className="text-[15px] font-semibold leading-snug">
                            {l.title}
                            {l.guest && <span className="pill-warn ml-1.5 align-middle text-[11px]">{l.guest}</span>}
                          </p>
                          {l.modifiers.length > 0 && <p className="ink-soft text-[13px] leading-snug">{l.modifiers.join(", ")}</p>}
                          {/* The note in full: it is what the kitchen will read. */}
                          {l.notes && (
                            <p className="mt-0.5 text-[13px] font-medium leading-snug" style={{ color: "var(--warn)" }}>
                              “{l.notes}”
                            </p>
                          )}
                          {l.qty > 1 && (
                            <p className="ink-faint mt-0.5 text-xs tabular-nums">
                              {l.qty} × {formatRupiah(l.unitPrice)}
                            </p>
                          )}
                        </button>
                        <p className="shrink-0 text-right text-[15px] font-semibold tabular-nums">
                          {formatRupiah(l.unitPrice * l.qty - l.discount)}
                          {l.discount > 0 && (
                            <span className="ink-faint block text-xs font-normal line-through">{formatRupiah(l.unitPrice * l.qty)}</span>
                          )}
                        </p>
                      </div>
                      {/* The line's own controls, all the same height: what can be
                          said about it on the left, how many on the right. */}
                      <div className="mt-1.5 flex items-center gap-2">
                        <button
                          onClick={() => onNote(l.uid)}
                          data-set={l.notes ? "note" : undefined}
                          className="line-action"
                          aria-label={l.notes ? `Ubah catatan ${l.title}: ${l.notes}` : `Tambah catatan untuk ${l.title}`}
                        >
                          <IconNote className="h-4 w-4 shrink-0" />
                          <span className="truncate">{l.notes ? "Ubah catatan" : "Catatan"}</span>
                        </button>
                        {onDiscount && (
                          <button
                            onClick={() => onDiscount(l.uid)}
                            data-set={l.discount > 0 ? "discount" : undefined}
                            className="line-action shrink-0 tabular-nums"
                            aria-label={l.discount > 0 ? `Ubah diskon ${l.title}` : `Beri diskon untuk ${l.title}`}
                          >
                            <IconTag className="h-4 w-4 shrink-0" />
                            {l.discount > 0 ? `− ${formatRupiah(l.discount)}` : "Diskon"}
                          </button>
                        )}
                        <div className="surface-inset ml-auto flex shrink-0 items-center rounded-xl p-0.5" role="group" aria-label={`Jumlah ${l.title}`}>
                          <button
                            onClick={() => onQty(l.uid, -1)}
                            aria-label={l.qty === 1 ? `Hapus ${l.title}` : `Kurangi ${l.title}`}
                            className="h-10 w-10 rounded-[10px] text-lg font-medium transition-colors hover:bg-[color:var(--surface)] active:bg-[color:var(--row-press)]"
                          >
                            −
                          </button>
                          <span className="w-7 text-center text-sm font-semibold tabular-nums">{l.qty}</span>
                          <button
                            onClick={() => onQty(l.uid, +1)}
                            disabled={l.qty >= l.maxQty}
                            aria-label={`Tambah ${l.title}`}
                            className="h-10 w-10 rounded-[10px] text-lg font-medium transition-colors hover:bg-[color:var(--surface)] active:bg-[color:var(--row-press)] disabled:opacity-30"
                          >
                            +
                          </button>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>

      <footer className="hairline-t px-5 pb-4 pt-2.5">
        {/* What the total is made of, as one quiet line: label and amount
            pairs, wrapping to a second line only when there are many. */}
        {!empty && quote && parts.length > 0 && (
          <p className="ink-faint mb-1 text-[12px] leading-snug tabular-nums">
            {parts.map(([label, value], i) => (
              <span key={label}>
                {i > 0 && <span aria-hidden> · </span>}
                <span className="whitespace-nowrap">
                  {label} {value}
                </span>
              </span>
            ))}
          </p>
        )}
        <div className="flex items-baseline justify-between gap-3">
          <span className="ink-soft text-sm">{bill && !empty ? "Total tagihan" : "Total"}</span>
          <span className={`text-[24px] font-semibold leading-none tabular-nums tracking-[-0.02em] transition-opacity ${quoting ? "opacity-60" : ""}`}>{formatRupiah(total)}</span>
        </div>
        {error && (
          <p role="alert" className="notice notice-bad mt-2 text-sm">
            {error}
          </p>
        )}
        <div className="mt-2.5 flex gap-2">
          {sendFirst ? (
            <>
              <button onClick={onPay} disabled={busy} className="btn-quiet min-h-[3rem] shrink-0 px-3.5 py-2.5 text-sm">
                Bayar sekarang
              </button>
              <button onClick={onSend!} disabled={busy || !!sendMissing} className="btn-accent min-h-[3rem] min-w-0 flex-1 px-3 py-2.5 text-[15px]">
                Kirim ke dapur/bar · {unsentCount}
              </button>
            </>
          ) : (
            <>
              {onHold && lines.length > 0 && !bill && (
                <button onClick={onHold} disabled={busy || noType} className="btn-quiet min-h-[3rem] shrink-0 px-3.5 py-2.5 text-sm">
                  {context.kind === "open" ? "Simpan" : "Simpan dulu"}
                </button>
              )}
              <button onClick={onPay} disabled={!!payMissing || busy} className="btn-accent min-h-[3rem] min-w-0 flex-1 px-3 py-2.5 text-base">
                Bayar {formatRupiah(total)}
              </button>
            </>
          )}
        </div>
        {sendFirst ? (
          sendMissing ? (
            <FormHint missing={sendMissing} />
          ) : (
            <p className="ink-faint mt-1.5 text-center text-xs">Slip dan nota meja dicetak. Dibayar saat meja selesai.</p>
          )
        ) : (
          <FormHint missing={payMissing} />
        )}
      </footer>
    </section>
  );
}
