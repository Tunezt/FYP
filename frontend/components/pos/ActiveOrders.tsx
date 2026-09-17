"use client";

import { useEffect, useMemo, useState } from "react";
import { IconAlert, IconChevronRight, IconClose, IconNote, IconPrinter } from "@/components/icons";
import { formatRupiah } from "@/lib/format";
import {
  PREP_LABEL,
  SERVICE_LABEL,
  SOURCE_LABEL,
  clockTime,
  lineSummary,
  minutesSince,
  waitLabel,
  type ActiveOrder,
} from "@/lib/pos";

type Filter = "all" | "unpaid" | "preparing" | "ready";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "Semua" },
  { id: "unpaid", label: "Belum dibayar" },
  { id: "preparing", label: "Disiapkan" },
  { id: "ready", label: "Siap diambil" },
];

const matches = (o: ActiveOrder, f: Filter) =>
  f === "all" ||
  (f === "unpaid" && o.payment === "unpaid") ||
  (f === "preparing" && o.payment === "paid" && (o.prep === "new" || o.prep === "preparing")) ||
  (f === "ready" && o.payment === "paid" && o.prep === "ready");

export type ActiveActions = {
  pay: (o: ActiveOrder) => void;
  resume: (o: ActiveOrder) => void;
  cancel: (o: ActiveOrder, reason: string) => Promise<boolean>;
  reprice: (o: ActiveOrder) => Promise<void>;
  addTo: (o: ActiveOrder) => void;
  handover: (o: ActiveOrder) => Promise<void>;
  print: (o: ActiveOrder) => void;
};

/** "Pesanan aktif" (svc-6): everything the counter still owes someone, unpaid
 *  and paid-but-not-handed-over, with payment and preparation as two separate
 *  facts. Oldest first, because the customer who has waited longest is next. */
export function ActiveOrders({
  orders,
  stale,
  error,
  busyId,
  actions,
  onRetry,
}: {
  orders: ActiveOrder[] | null;
  stale: boolean;
  error: string | null;
  busyId: string | null;
  actions: ActiveActions;
  onRetry: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 30000);
    return () => clearInterval(id);
  }, []);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: 0, unpaid: 0, preparing: 0, ready: 0 };
    for (const o of orders ?? []) for (const f of FILTERS) if (matches(o, f.id)) c[f.id] += 1;
    return c;
  }, [orders]);
  const shown = (orders ?? []).filter((o) => matches(o, filter));
  const current = (orders ?? []).find((o) => o.id === selected) ?? null;

  return (
    <div className="grid min-h-0 gap-5 lg:grid-cols-[minmax(0,1fr)_400px]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="segmented max-w-full overflow-x-auto" role="tablist" aria-label="Saring pesanan">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                role="tab"
                aria-selected={filter === f.id}
                onClick={() => setFilter(f.id)}
                className="segmented-item flex min-h-[2.5rem] items-center gap-1.5 whitespace-nowrap"
              >
                {f.label}
                <span className={`tabular-nums ${filter === f.id ? "font-semibold" : "ink-faint"}`}>{counts[f.id]}</span>
              </button>
            ))}
          </div>
          <SyncStatus stale={stale} error={error} onRetry={onRetry} />
        </div>

        {orders === null ? (
          error ? (
            <div className="glass-card mt-4 px-6 py-10 text-center">
              <p className="text-[17px] font-semibold">Pesanan belum bisa dimuat</p>
              <p className="ink-soft mt-1 text-sm">{error}</p>
              <button onClick={onRetry} className="btn-quiet mt-4 px-5 py-2.5 text-sm">
                Coba lagi
              </button>
            </div>
          ) : (
            <ul className="mt-4 space-y-2" aria-busy>
              {Array.from({ length: 4 }).map((_, i) => (
                <li key={i} className="glass-card h-[5.5rem] animate-pulse" />
              ))}
            </ul>
          )
        ) : shown.length === 0 ? (
          <div className="mt-4 rounded-3xl px-6 py-12 text-center" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
            <p className="text-[17px] font-semibold">
              {filter === "all" ? "Tidak ada pesanan aktif" : `Tidak ada yang ${FILTERS.find((f) => f.id === filter)!.label.toLowerCase()}`}
            </p>
            <p className="ink-soft mt-1 text-sm">
              {filter === "all"
                ? "Pesanan yang disimpan, pesanan QR, dan pesanan yang sedang dibuat muncul di sini."
                : "Ganti saringan untuk melihat pesanan lain."}
            </p>
          </div>
        ) : (
          <ul className="glass-card mt-4 overflow-hidden p-0">
            {shown.map((o, i) => (
              <li key={o.id} className={i > 0 ? "hairline-t" : ""}>
                <OrderRow order={o} active={selected === o.id} onOpen={() => setSelected(o.id)} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Detail: a side panel on a wide till, a sheet on a tablet */}
      <aside className="hidden lg:block">
        <div className="glass-card sticky top-[5.5rem] max-h-[calc(100dvh-7rem)] overflow-y-auto p-0">
          {current ? (
            <OrderDetail order={current} busy={busyId === current.id} actions={actions} onClose={() => setSelected(null)} />
          ) : (
            <div className="px-6 py-16 text-center">
              <p className="text-[15px] font-medium">Pilih pesanan</p>
              <p className="ink-soft mt-1 text-sm">Rincian dan tindakan berikutnya muncul di sini.</p>
            </div>
          )}
        </div>
      </aside>
      {current && (
        <div className="sheet-scrim lg:hidden" onClick={() => setSelected(null)}>
          <div className="sheet-panel sm:max-w-lg" onClick={(e) => e.stopPropagation()}>
            <div className="overflow-y-auto">
              <OrderDetail order={current} busy={busyId === current.id} actions={actions} onClose={() => setSelected(null)} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function SyncStatus({ stale, error, onRetry }: { stale: boolean; error: string | null; onRetry: () => void }) {
  if (!error && !stale) {
    return (
      <span className="ink-faint flex items-center gap-1.5 text-[13px]" role="status">
        <span className="h-2 w-2 rounded-full" style={{ background: "var(--good)" }} /> Tersambung
      </span>
    );
  }
  return (
    <span className="flex items-center gap-2 text-[13px] font-medium" role="status" style={{ color: "var(--warn)" }}>
      <span className="h-2 w-2 rounded-full" style={{ background: "var(--warn)" }} />
      {error ? "Koneksi terputus · data mungkin tidak terbaru" : "Memperbarui…"}
      {error && (
        <button onClick={onRetry} className="rounded-lg px-2 py-1 text-[13px] underline underline-offset-2">
          Coba lagi
        </button>
      )}
    </span>
  );
}

function PaymentBadge({ o }: { o: ActiveOrder }) {
  return o.payment === "unpaid" ? <span className="pill-warn">Belum dibayar</span> : <span className="pill-good">Lunas</span>;
}

function PrepBadge({ o }: { o: ActiveOrder }) {
  if (o.payment !== "paid" || !o.prep) return <span className="pill-quiet">Belum ke dapur</span>;
  return o.prep === "ready" ? <span className="pill-good">{PREP_LABEL.ready}</span> : <span className="pill-quiet">{PREP_LABEL[o.prep]}</span>;
}

function OrderRow({ order: o, active, onOpen }: { order: ActiveOrder; active: boolean; onOpen: () => void }) {
  const waited = minutesSince(o.placed_at);
  return (
    <button
      onClick={onOpen}
      aria-current={active}
      className={`list-row-action flex w-full items-center gap-4 px-4 py-3.5 text-left sm:px-5 ${active ? "bg-[color:var(--row-hover)]" : ""}`}
    >
      <div className="w-[5.25rem] shrink-0">
        <p className="text-[19px] font-semibold tabular-nums tracking-[-0.02em]">{o.code}</p>
        <p className="ink-faint text-xs">{SOURCE_LABEL[o.source]}</p>
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-medium">
          {o.guest_name || SERVICE_LABEL[o.order_type] || o.order_type}
          {o.guest_name ? <span className="ink-soft font-normal"> · {SERVICE_LABEL[o.order_type] ?? o.order_type}</span> : null}
          {o.table_label ? <span className="ink-soft font-normal"> · {o.table_label}</span> : null}
        </p>
        <p className="ink-soft truncate text-[13px]">{lineSummary(o.lines)}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <PaymentBadge o={o} />
          <PrepBadge o={o} />
          {o.parent_code && <span className="pill-quiet">Tambahan untuk {o.parent_code}</span>}
          {o.price_changes.length > 0 && <span className="pill-warn">Harga berubah</span>}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <p className="text-[15px] font-semibold tabular-nums">{formatRupiah(o.total)}</p>
        <p className="ink-faint text-xs tabular-nums">
          {clockTime(o.placed_at)} · {waitLabel(waited)}
        </p>
      </div>
      <span className="ink-faint row-chevron hidden sm:block">
        <IconChevronRight className="h-4 w-4" />
      </span>
    </button>
  );
}

function OrderDetail({
  order: o,
  busy,
  actions,
  onClose,
}: {
  order: ActiveOrder;
  busy: boolean;
  actions: ActiveActions;
  onClose: () => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");
  useEffect(() => {
    setCancelling(false);
    setReason("");
  }, [o.id]);
  const unpaid = o.payment === "unpaid";

  return (
    <div className="px-5 pb-5 pt-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[28px] font-semibold leading-none tabular-nums tracking-[-0.025em]">{o.code}</p>
          <p className="ink-soft mt-1.5 text-[13px]">
            {SOURCE_LABEL[o.source]} · {SERVICE_LABEL[o.order_type] ?? o.order_type}
            {o.table_label ? ` · ${o.table_label}` : ""}
            {o.guest_name ? ` · ${o.guest_name}` : ""}
          </p>
          <p className="ink-faint text-[13px] tabular-nums">
            Diterima {clockTime(o.placed_at)} · {waitLabel(minutesSince(o.placed_at))} lalu
            {o.paid_at ? ` · dibayar ${clockTime(o.paid_at)}` : ""}
          </p>
        </div>
        <button onClick={onClose} aria-label="Tutup rincian" className="icon-btn ink-soft -mr-2 h-10 w-10 shrink-0 rounded-full">
          <IconClose className="h-5 w-5" />
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <PaymentBadge o={o} />
        <PrepBadge o={o} />
        {o.parent_code && <span className="pill-quiet">Tambahan untuk {o.parent_code}</span>}
      </div>

      {o.price_changes.length > 0 && (
        <div className="notice notice-warn mt-4 text-sm" role="alert">
          <p className="flex items-center gap-1.5 font-semibold">
            <IconAlert className="h-4 w-4" /> Harga berubah sejak dipesan
          </p>
          <ul className="mt-1 space-y-0.5">
            {o.price_changes.map((ch, i) => (
              <li key={i} className="tabular-nums">
                {ch.name}: {ch.now === null ? "sudah tidak tersedia" : `${formatRupiah(ch.was ?? 0)} → ${formatRupiah(ch.now)}`}
              </li>
            ))}
          </ul>
          <p className="mt-1">Konfirmasi ke pelanggan, lalu perbarui sebelum dibayar.</p>
          {o.price_changes.every((ch) => ch.now !== null) ? (
            <button onClick={() => actions.reprice(o)} disabled={busy} className="btn-quiet mt-2 px-4 py-2 text-sm">
              Perbarui ke harga sekarang
            </button>
          ) : (
            <button onClick={() => actions.resume(o)} disabled={busy} className="btn-quiet mt-2 px-4 py-2 text-sm">
              Ubah pesanan
            </button>
          )}
        </div>
      )}

      <ul className="hairline-t mt-4">
        {o.lines.map((l, i) => (
          <li key={i} className="hairline-b flex items-start gap-3 py-2.5">
            <span className="w-8 shrink-0 text-[15px] font-semibold tabular-nums">{Number(l.quantity)}×</span>
            <div className="min-w-0 flex-1">
              <p className={`text-[15px] font-medium leading-snug ${l.done && o.prep === "preparing" ? "ink-soft line-through decoration-1" : ""}`}>
                {l.name}
                {l.size ? <span className="font-normal"> · {l.size}</span> : null}
              </p>
              {l.modifiers.length > 0 && <p className="ink-soft text-[13px]">{l.modifiers.join(", ")}</p>}
              {l.notes && (
                <p className="flex items-center gap-1 text-[13px]" style={{ color: "var(--warn)" }}>
                  <IconNote className="h-3.5 w-3.5 shrink-0" /> {l.notes}
                </p>
              )}
            </div>
            {l.line_total && <span className="shrink-0 text-sm tabular-nums">{formatRupiah(l.line_total)}</span>}
          </li>
        ))}
      </ul>
      {o.note && <p className="ink-soft mt-2 text-sm">Catatan: {o.note}</p>}
      <div className="mt-3 flex items-baseline justify-between">
        <span className="ink-soft text-sm">{o.is_estimate ? "Perkiraan total" : "Total dibayar"}</span>
        <span className="text-[22px] font-semibold tabular-nums">{formatRupiah(o.total)}</span>
      </div>

      <div className="mt-4 space-y-2">
        {unpaid ? (
          cancelling ? (
            <div className="surface-inset rounded-2xl p-3">
              <label className="text-sm font-medium" htmlFor={`cancel-${o.id}`}>
                Alasan batal
              </label>
              <input
                id={`cancel-${o.id}`}
                autoFocus
                value={reason}
                onChange={(e) => setReason(e.target.value.slice(0, 200))}
                className="field mt-1.5 text-sm"
                placeholder="mis. pelanggan tidak jadi"
              />
              <p className="ink-soft mt-1.5 text-xs">Belum dibayar, jadi tidak ada stok atau uang yang dikembalikan.</p>
              <div className="mt-2 flex gap-2">
                <button onClick={() => setCancelling(false)} className="btn-quiet flex-1 py-2.5 text-sm">
                  Kembali
                </button>
                <button
                  onClick={async () => {
                    if (await actions.cancel(o, reason.trim())) setCancelling(false);
                  }}
                  disabled={busy}
                  className="btn-danger flex-1 py-2.5 text-sm"
                >
                  Batalkan pesanan
                </button>
              </div>
            </div>
          ) : (
            <>
              <button onClick={() => actions.pay(o)} disabled={busy || o.price_changes.length > 0} className="btn-accent w-full py-3.5 text-base">
                Bayar {formatRupiah(o.total)}
              </button>
              <div className="flex gap-2">
                <button onClick={() => actions.resume(o)} disabled={busy} className="btn-quiet flex-1 py-2.5 text-sm">
                  Ubah pesanan
                </button>
                <button onClick={() => setCancelling(true)} disabled={busy} className="btn-quiet flex-1 py-2.5 text-sm">
                  Batalkan
                </button>
              </div>
            </>
          )
        ) : (
          <>
            {o.prep === "ready" && (
              <button onClick={() => actions.handover(o)} disabled={busy} className="btn-accent w-full py-3.5 text-base">
                Tandai sudah diserahkan
              </button>
            )}
            <div className="flex gap-2">
              <button onClick={() => actions.addTo(o)} disabled={busy} className="btn-quiet flex-1 py-2.5 text-sm">
                Tambah pesanan
              </button>
              <button onClick={() => actions.print(o)} disabled={busy} className="btn-quiet flex-1 py-2.5 text-sm">
                <IconPrinter className="h-4 w-4" /> Struk
              </button>
            </div>
            {o.prep !== "ready" && (
              <p className="ink-soft text-center text-xs">
                Dapur sedang {o.prep === "preparing" ? "menyiapkan" : "menerima"} pesanan ini. Salah pesanan? Batalkan lewat Riwayat transaksi.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
