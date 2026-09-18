"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { IconAlert, IconChevronRight, IconClose, IconLock, IconNote } from "@/components/icons";
import { formatRupiah } from "@/lib/format";
import {
  isTableBill,
  needsSending,
  orderHeading,
  serviceDateLabel,
  SERVICE_LABEL,
  SOURCE_LABEL,
  clockTime,
  lineSummary,
  minutesSince,
  waitLabel,
  type ActiveLine,
  type ActiveOrder,
} from "@/lib/pos";

type Filter = "all" | "send" | "tables" | "counter";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "Semua" },
  { id: "send", label: "Perlu dikirim" },
  { id: "tables", label: "Meja" },
  { id: "counter", label: "Bawa pulang" },
];

const matches = (o: ActiveOrder, f: Filter) =>
  f === "all" || (f === "send" && needsSending(o)) || (f === "tables" && isTableBill(o)) || (f === "counter" && !isTableBill(o));

export type ActiveActions = {
  pay: (o: ActiveOrder) => void;
  resume: (o: ActiveOrder) => void;
  send: (o: ActiveOrder) => Promise<void>;
  cancel: (o: ActiveOrder, reason: string) => Promise<boolean>;
  reprice: (o: ActiveOrder) => Promise<void>;
};

/** "Pesanan aktif" (svc-6, bill-3): every order that is not paid yet — the
 *  tables' open bills, held takeaway orders, and QR orders. An order leaves
 *  this list when it is paid (or cancelled). What needs the cashier first is
 *  anything not yet sent to the Bar/Dapur, so it is marked and filterable. */
export function ActiveOrders({
  orders,
  stale,
  error,
  busyId,
  actions,
  onRetry,
  printSlot,
}: {
  orders: ActiveOrder[] | null;
  stale: boolean;
  error: string | null;
  busyId: string | null;
  actions: ActiveActions;
  onRetry: () => void;
  /** prt-4: the order's paper, with recovery. */
  printSlot?: (o: ActiveOrder) => ReactNode;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 30000);
    return () => clearInterval(id);
  }, []);

  const open = useMemo(() => (orders ?? []).filter((o) => o.payment === "unpaid"), [orders]);
  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: 0, send: 0, tables: 0, counter: 0 };
    for (const o of open) for (const f of FILTERS) if (matches(o, f.id)) c[f.id] += 1;
    return c;
  }, [open]);
  const shown = open.filter((o) => matches(o, filter));
  const current = open.find((o) => o.id === selected) ?? null;

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
                <span
                  className={`tabular-nums ${filter === f.id ? "font-semibold" : "ink-faint"}`}
                  style={f.id === "send" && counts.send > 0 && filter !== f.id ? { color: "var(--warn)" } : undefined}
                >
                  {counts[f.id]}
                </span>
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
              {filter === "all" ? "Tidak ada pesanan yang belum dibayar" : filter === "send" ? "Semua sudah dikirim" : `Tidak ada pesanan ${FILTERS.find((f) => f.id === filter)!.label.toLowerCase()}`}
            </p>
            <p className="ink-soft mt-1 text-sm">
              {filter === "all" ? "Tagihan meja, pesanan yang disimpan, dan pesanan QR muncul di sini sampai dibayar." : "Ganti saringan untuk melihat pesanan lain."}
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
            <OrderDetail order={current} busy={busyId === current.id} actions={actions} onClose={() => setSelected(null)} printSlot={printSlot} />
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
              <OrderDetail order={current} busy={busyId === current.id} actions={actions} onClose={() => setSelected(null)} printSlot={printSlot} />
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

function StateBadges({ o }: { o: ActiveOrder }) {
  const fromGuest = o.lines.some((l) => l.from_guest && !l.sent_batch);
  const items = o.lines.filter((l) => !l.sent_batch).reduce((n, l) => n + Number(l.quantity), 0);
  return (
    <>
      {needsSending(o) ? (
        <span className="pill-warn">
          {fromGuest ? "Pesanan QR · " : ""}Perlu dikirim · {items} item
        </span>
      ) : isTableBill(o) ? (
        <span className="pill-quiet">Terkirim{o.sent_batches > 1 ? ` · ${o.sent_batches}×` : ""}</span>
      ) : null}
      <span className="pill-quiet">Belum dibayar</span>
      {o.price_changes.length > 0 && <span className="pill-warn">Harga berubah</span>}
    </>
  );
}

function OrderRow({ order: o, active, onOpen }: { order: ActiveOrder; active: boolean; onOpen: () => void }) {
  const waited = minutesSince(o.placed_at);
  const head = orderHeading(o);
  return (
    <button
      onClick={onOpen}
      aria-current={active}
      className={`list-row-action flex w-full items-center gap-4 px-4 py-3.5 text-left sm:px-5 ${active ? "bg-[color:var(--row-hover)]" : ""}`}
    >
      <div className="w-[8.75rem] shrink-0">
        <p className="whitespace-nowrap text-[17px] font-semibold leading-tight tabular-nums tracking-[-0.015em]">{head.main}</p>
        {head.sub && <p className="ink-soft text-[13px] font-medium tabular-nums">{head.sub}</p>}
        <p className="mt-0.5 flex items-center gap-1">
          <span className="pill-quiet">{SOURCE_LABEL[o.source]}</span>
          {o.previous_day && <span className="pill-warn">{serviceDateLabel(o.service_date)}</span>}
        </p>
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-medium">
          {o.guest_name || SERVICE_LABEL[o.order_type] || o.order_type}
          {o.guest_name ? <span className="ink-soft font-normal"> · {SERVICE_LABEL[o.order_type] ?? o.order_type}</span> : null}
        </p>
        <p className="ink-soft truncate text-[13px]">{lineSummary(o.lines)}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <StateBadges o={o} />
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

function LineRow({ l }: { l: ActiveLine }) {
  return (
    <li className="hairline-b flex items-start gap-3 py-2.5">
      <span className="w-8 shrink-0 text-[15px] font-semibold tabular-nums">{Number(l.quantity)}×</span>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-medium leading-snug">
          {l.name}
          {l.size ? <span className="font-normal"> · {l.size}</span> : null}
          {l.from_guest && !l.sent_batch && <span className="pill-warn ml-1.5 align-middle text-[11px]">QR{l.guest_name ? ` · ${l.guest_name}` : ""}</span>}
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
  );
}

function OrderDetail({
  order: o,
  busy,
  actions,
  onClose,
  printSlot,
}: {
  order: ActiveOrder;
  busy: boolean;
  actions: ActiveActions;
  onClose: () => void;
  printSlot?: (o: ActiveOrder) => ReactNode;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");
  useEffect(() => {
    setCancelling(false);
    setReason("");
  }, [o.id]);
  const head = orderHeading(o);
  const unsent = o.lines.filter((l) => !l.sent_batch);
  const sent = o.lines.filter((l) => l.sent_batch);
  const batches = Array.from(new Set(sent.map((l) => l.sent_batch as number))).sort((a, b) => a - b);
  const table = isTableBill(o);
  const reasonNeeded = o.sent_batches > 0;

  return (
    <div className="px-5 pb-5 pt-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[28px] font-semibold leading-none tabular-nums tracking-[-0.025em]">{head.main}</p>
          {head.sub && <p className="mt-1 text-[15px] font-medium tabular-nums">{head.sub}</p>}
          <p className="ink-soft mt-1.5 text-[13px]">
            {SOURCE_LABEL[o.source]} · {SERVICE_LABEL[o.order_type] ?? o.order_type}
            {o.guest_name ? ` · ${o.guest_name}` : ""}
            {o.previous_day ? ` · dari ${serviceDateLabel(o.service_date)}` : ""}
          </p>
          <p className="ink-faint text-[13px] tabular-nums">
            Dibuka {clockTime(o.placed_at)} · {waitLabel(minutesSince(o.placed_at))} lalu
          </p>
        </div>
        <button onClick={onClose} aria-label="Tutup rincian" className="icon-btn ink-soft -mr-2 h-10 w-10 shrink-0 rounded-full">
          <IconClose className="h-5 w-5" />
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <StateBadges o={o} />
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

      {unsent.length > 0 && (
        <>
          <p className="mt-4 pb-1 text-[12px] font-semibold uppercase tracking-[0.04em]" style={table ? { color: "var(--warn)" } : undefined}>
            {table ? "Belum dikirim" : "Pesanan"}
          </p>
          <ul className="hairline-t">
            {unsent.map((l, i) => (
              <LineRow key={l.uid ?? `u${i}`} l={l} />
            ))}
          </ul>
        </>
      )}
      {batches.map((b) => (
        <div key={b}>
          <p className="ink-faint mt-4 flex items-center gap-1 pb-1 text-[12px] font-semibold uppercase tracking-[0.04em]">
            <IconLock className="h-3 w-3" /> {b === 1 ? "Kiriman pertama" : `Tambahan ${b - 1}`}
          </p>
          <ul className="hairline-t">
            {sent
              .filter((l) => l.sent_batch === b)
              .map((l, i) => (
                <LineRow key={l.uid ?? `s${b}-${i}`} l={l} />
              ))}
          </ul>
        </div>
      ))}
      {o.cancelled_lines.length > 0 && (
        <div className="mt-4">
          <p className="ink-faint pb-1 text-[12px] font-semibold uppercase tracking-[0.04em]">Dibatalkan</p>
          <ul className="space-y-1">
            {o.cancelled_lines.map((c, i) => (
              <li key={i} className="ink-soft text-[13px]">
                <span className="line-through decoration-1">
                  {Number(c.quantity)}× {c.name}
                  {c.size ? ` · ${c.size}` : ""}
                </span>{" "}
                — {c.reason} · {clockTime(c.at)}
              </li>
            ))}
          </ul>
        </div>
      )}
      {o.note && <p className="ink-soft mt-2 text-sm">Catatan: {o.note}</p>}
      {printSlot && o.print_jobs.length > 0 && printSlot(o)}
      <div className="mt-3 flex items-baseline justify-between">
        <span className="ink-soft text-sm">{table ? "Total tagihan sementara" : "Perkiraan total"}</span>
        <span className="text-[22px] font-semibold tabular-nums">{formatRupiah(o.total)}</span>
      </div>

      <div className="mt-4 space-y-2">
        {cancelling ? (
          <div className="surface-inset rounded-2xl p-3">
            <label className="text-sm font-medium" htmlFor={`cancel-${o.id}`}>
              Alasan batal{reasonNeeded ? "" : " (opsional)"}
            </label>
            <input
              id={`cancel-${o.id}`}
              autoFocus
              value={reason}
              onChange={(e) => setReason(e.target.value.slice(0, 200))}
              className="field mt-1.5 text-sm"
              placeholder="mis. tamu tidak jadi"
            />
            <p className="ink-soft mt-1.5 text-xs">
              {reasonNeeded
                ? "Sebagian sudah dikirim: bar/dapur mendapat slip BATAL dengan alasan ini. Belum dibayar, jadi tidak ada stok atau uang yang dikembalikan."
                : "Belum dibayar, jadi tidak ada stok atau uang yang dikembalikan."}
            </p>
            <div className="mt-2 flex gap-2">
              <button onClick={() => setCancelling(false)} className="btn-quiet flex-1 py-2.5 text-sm">
                Kembali
              </button>
              <button
                onClick={async () => {
                  if (await actions.cancel(o, reason.trim())) setCancelling(false);
                }}
                disabled={busy || (reasonNeeded && !reason.trim())}
                className="btn-danger flex-1 py-2.5 text-sm"
              >
                Batalkan pesanan
              </button>
            </div>
          </div>
        ) : (
          <>
            {needsSending(o) ? (
              <>
                <button onClick={() => void actions.send(o)} disabled={busy} className="btn-accent w-full py-3.5 text-base">
                  Kirim ke dapur/bar · {unsent.reduce((n, l) => n + Number(l.quantity), 0)} item
                </button>
                <button onClick={() => actions.pay(o)} disabled={busy || o.price_changes.length > 0} className="btn-quiet w-full py-3 text-sm">
                  Bayar {formatRupiah(o.total)}
                </button>
              </>
            ) : (
              <button onClick={() => actions.pay(o)} disabled={busy || o.price_changes.length > 0} className="btn-accent w-full py-3.5 text-base">
                Bayar {formatRupiah(o.total)}
              </button>
            )}
            <div className="flex gap-2">
              <button onClick={() => actions.resume(o)} disabled={busy} className="btn-quiet flex-1 py-2.5 text-sm">
                {table ? "Tambah / ubah" : "Ubah pesanan"}
              </button>
              <button onClick={() => setCancelling(true)} disabled={busy} className="btn-quiet flex-1 py-2.5 text-sm">
                Batalkan
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
