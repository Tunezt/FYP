"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { formatQty, formatRupiah } from "@/lib/format";
import { orderHeading, serviceDateLabel, taxLineLabel } from "@/lib/pos";
import { ORDER_TYPE_LABEL, type OrderType } from "@/lib/types";
import type { Receipt } from "@/components/pos/Receipts";

/** The receipt a customer opens by scanning the QR the till showed (till-5b,
 *  decision 4). No login: the unguessable code in the address is the key. The
 *  same content as the paper receipt, laid out for a phone. */
export default function ReceiptPage() {
  const { code } = useParams<{ code: string }>();
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Receipt>(`/public/struk/${encodeURIComponent(code)}`)
      .then(setReceipt)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.detail : "Struk belum bisa dimuat — periksa koneksi, lalu muat ulang."));
  }, [code]);

  if (error) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <div className="glass-card max-w-sm px-6 py-8 text-center">
          <p className="text-lg font-semibold">Struk tidak bisa dibuka</p>
          <p className="ink-soft mt-1 text-sm">{error}</p>
        </div>
      </main>
    );
  }
  if (!receipt) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <p className="ink-soft animate-pulse">Membuka struk…</p>
      </main>
    );
  }

  const r = receipt;
  const when = new Date(r.sold_at).toLocaleString("id-ID", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });
  const head = orderHeading(r);
  const contact = [r.business_phone, r.business_instagram ? `IG ${r.business_instagram}` : null].filter(Boolean).join(" · ");
  const method = (m: string) => ({ cash: "Tunai", qris: "QRIS", points: "Poin" } as Record<string, string>)[m] ?? m.toUpperCase();
  const extras: [string, string][] = [];
  if (Number(r.discount_total) > 0) extras.push(["Diskon", `− ${formatRupiah(r.discount_total)}`]);
  if (Number(r.promo_total) > 0) extras.push([`Promo${r.promo_names.length ? ` (${r.promo_names.join(", ")})` : ""}`, `− ${formatRupiah(r.promo_total)}`]);
  if (Number(r.voucher_total) > 0) extras.push([`Voucher ${r.voucher_code ?? ""}`.trim(), `− ${formatRupiah(r.voucher_total)}`]);
  if (Number(r.service_charge) > 0) extras.push(["Service", formatRupiah(r.service_charge)]);
  if (Number(r.delivery_fee) > 0) extras.push(["Ongkos kirim", formatRupiah(r.delivery_fee)]);
  if (Number(r.tax_total) > 0) extras.push([taxLineLabel(r.tax_label, r.tax_rate, r.tax_inclusive), formatRupiah(r.tax_total)]);
  if (Number(r.rounding) !== 0) extras.push(["Pembulatan", formatRupiah(r.rounding)]);

  return (
    <main className="min-h-screen px-4 py-8">
      <article className="glass-card mx-auto w-full max-w-sm px-5 pb-6 pt-6" aria-label={`Struk ${r.business_name}`}>
        <header className="text-center">
          <h1 className="text-[22px] font-semibold uppercase tracking-[0.04em]">{r.business_name}</h1>
          {r.business_address && <p className="ink-soft mt-1 text-sm">{r.business_address}</p>}
          {contact && <p className="ink-soft text-sm">{contact}</p>}
        </header>

        <div className="hairline-t mt-4 pt-4 text-center">
          <p className="text-[30px] font-semibold leading-none tracking-[-0.02em]">{head.main}</p>
          {head.sub && <p className="mt-1 font-medium">{head.sub}</p>}
          <p className="ink-soft mt-2 text-sm">
            {ORDER_TYPE_LABEL[r.order_type as OrderType] ?? r.order_type} · {when}
          </p>
          {r.status !== "completed" && (
            <p className="pill-bad mx-auto mt-2 w-fit">{r.status === "voided" ? "Dibatalkan" : "Dikembalikan"}</p>
          )}
        </div>

        <ul className="hairline-t mt-4 space-y-3 pt-4">
          {r.lines
            .filter((l) => Number(l.quantity) > 0)
            .map((l, i) => (
              <li key={i} className="flex justify-between gap-3 text-[15px]">
                <div className="min-w-0">
                  <p className="font-medium">
                    {formatQty(l.quantity)}× {l.name}
                    {l.size ? <span className="font-normal"> · {l.size}</span> : null}
                  </p>
                  {l.modifiers.map((m, j) => (
                    <p key={j} className="ink-soft text-[13px]">
                      + {m.name}
                      {Number(m.price_delta) > 0 ? ` (${formatRupiah(m.price_delta)})` : ""}
                    </p>
                  ))}
                  {l.notes && <p className="ink-soft text-[13px] italic">{l.notes}</p>}
                </div>
                <p className="shrink-0 tabular-nums">{formatRupiah(l.line_total)}</p>
              </li>
            ))}
        </ul>

        <dl className="hairline-t mt-4 space-y-1 pt-4 text-[15px]">
          {extras.length > 0 && <Row label="Subtotal" value={formatRupiah(r.subtotal)} />}
          {extras.map(([label, value]) => (
            <Row key={label} label={label} value={value} />
          ))}
          <div className="flex justify-between pt-1 text-[19px] font-semibold">
            <dt>Total</dt>
            <dd className="tabular-nums">{formatRupiah(r.total)}</dd>
          </div>
          {r.payments
            .filter((p) => Number(p.amount) > 0)
            .map((p, i) => (
              <div key={i}>
                <Row label={method(p.method)} value={formatRupiah(p.amount)} />
                {p.method === "cash" && p.tendered && Number(p.tendered) > Number(p.amount) && (
                  <>
                    <Row label="Diterima" value={formatRupiah(p.tendered)} />
                    <Row label="Kembali" value={formatRupiah(Number(p.tendered) - Number(p.amount))} />
                  </>
                )}
              </div>
            ))}
        </dl>

        <footer className="hairline-t mt-4 pt-4 text-center">
          <p className="text-sm">Terima kasih sudah mampir!</p>
          <p className="ink-faint mt-1 text-xs">
            Ref {r.number}
            {r.service_date ? ` · ${serviceDateLabel(r.service_date)}` : ""}
          </p>
        </footer>
      </article>
      <p className="ink-faint mx-auto mt-4 max-w-sm text-center text-xs">
        Simpan halaman ini, atau ambil tangkapan layar, sebagai bukti pembayaran.
      </p>
    </main>
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
