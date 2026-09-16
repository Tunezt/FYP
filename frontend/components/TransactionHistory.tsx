"use client";

import { useState } from "react";
import { useOwnerData } from "@/lib/hooks";
import { daySubLabel, groupByDay, timeLabel } from "@/lib/dates";
import { formatQty, formatRupiah } from "@/lib/format";
import type { OrderRow, Receipt } from "@/lib/types";
import { DayHeaderToggle, EmptyState, ErrorState, Glass, ItemIcon, Skeleton } from "@/components/ui";
import { IconChevronDown } from "@/components/icons";

/** Riwayat transaksi — one row per receipt, opening to what was ordered.
 *
 * A sale is a receipt, not a line: "2× Croissant" on its own cannot be matched
 * against the paper slip in the owner's hand, and three lines of one bill read
 * as three sales. Each row is therefore one transaction — number, time,
 * cashier, item count, total — and clicking it fetches that receipt and shows
 * the items underneath.
 *
 * The lines are fetched only when a row is opened (the list is 40 rows; forty
 * receipts up front would be forty requests for something nobody asked to see)
 * and the endpoint is the same one the till printed from, so the owner is
 * never reading a different document from the cashier. */

function statusChip(order: OrderRow) {
  if (order.status === "voided") return { text: "dibatalkan", cls: "pill-bad" };
  if (order.status === "refunded") return { text: "dikembalikan", cls: "pill-warn" };
  return null;
}

function TransactionRow({ order, tz }: { order: OrderRow; tz?: string }) {
  const [open, setOpen] = useState(false);
  // `null` path = no request: the receipt is loaded the first time it is opened.
  const receipt = useOwnerData<Receipt>(open ? `/api/orders/${order.id}/receipt` : null);
  const chip = statusChip(order);

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="list-row list-row-action"
      >
        <span className="ink-faint w-11 shrink-0 text-xs tabular-nums">
          {timeLabel(new Date(order.sold_at), tz)}
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold">
            <span className="tabular-nums">#{order.number}</span>
            {chip && <span className={chip.cls}>{chip.text}</span>}
            {order.entry_source === "manual_backdated" && (
              <span className="pill-quiet">dari nota kertas</span>
            )}
          </p>
          <p className="ink-faint truncate text-xs">
            {order.line_count} item
            {order.staff_name ? ` · oleh ${order.staff_name}` : ""}
            {order.customer_name ? ` · ${order.customer_name}` : ""}
          </p>
        </div>
        <span
          className={`shrink-0 text-sm font-semibold tabular-nums ${
            order.status === "voided" ? "ink-faint line-through" : ""
          }`}
        >
          {formatRupiah(order.total)}
        </span>
        <IconChevronDown
          className={`row-chevron h-4 w-4 shrink-0 transition-transform duration-200 ${
            open ? "rotate-180" : ""
          }`}
          aria-hidden
        />
      </button>

      {open && (
        <div className="receipt-lines">
          {receipt.loading && <Skeleton className="h-16" />}
          {!receipt.loading && receipt.error && (
            <p className="ink-soft py-2 text-xs">
              Gagal memuat isi struk.{" "}
              <button onClick={receipt.reload} className="font-semibold underline">
                Coba lagi
              </button>
            </p>
          )}
          {!receipt.loading && receipt.data && (
            <ul>
              {receipt.data.lines.map((line, i) => (
                <li key={`${line.name}-${i}`} className="flex items-center gap-3 py-1.5">
                  <ItemIcon name={line.name} className="h-7 w-7 rounded-lg" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">
                      {formatQty(line.quantity)}× {line.name}
                      {line.variant ? ` · ${line.variant}` : ""}
                    </p>
                    {line.modifiers.length > 0 && (
                      <p className="ink-faint truncate text-xs">
                        {line.modifiers.map((m) => m.name).join(", ")}
                      </p>
                    )}
                    {line.notes && <p className="ink-faint truncate text-xs">“{line.notes}”</p>}
                  </div>
                  <span className="ink-soft shrink-0 text-xs tabular-nums">
                    {formatRupiah(line.line_total)}
                  </span>
                </li>
              ))}
              <li className="mt-1 flex items-center justify-between border-t pt-2 text-sm font-semibold"
                  style={{ borderColor: "var(--hairline)" }}>
                <span>Total</span>
                <span className="tabular-nums">{formatRupiah(receipt.data.total)}</span>
              </li>
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

export function TransactionHistory({
  orders,
  loading,
  error,
  onRetry,
  filtered,
  tz,
  dayStart,
}: {
  orders: OrderRow[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  filtered: boolean;
  tz?: string;
  dayStart: number;
}) {
  const [openDays, setOpenDays] = useState<Record<string, boolean>>({});
  const groups = groupByDay(orders, (o) => new Date(o.sold_at), tz, dayStart);
  const openByDefault = (label: string) => label === "Hari ini" || label === "Kemarin";
  const isOpen = (key: string, label: string) => openDays[key] ?? openByDefault(label);
  const toggle = (key: string, label: string) =>
    setOpenDays((o) => ({ ...o, [key]: !(o[key] ?? openByDefault(label)) }));

  if (loading) return <Skeleton className="mt-3 h-64" />;
  if (error && orders.length === 0) {
    return (
      <Glass className="mt-3">
        <ErrorState onRetry={onRetry} />
      </Glass>
    );
  }
  if (orders.length === 0) {
    return (
      <Glass className="mt-3">
        <EmptyState
          emoji="🧾"
          title={filtered ? "Tidak ada transaksi yang cocok" : "Belum ada transaksi"}
        >
          {filtered
            ? "Coba ubah tanggal atau pilih kasir lain."
            : "Transaksi dari layar kasir akan muncul di sini begitu staf mencatat penjualan pertama."}
        </EmptyState>
      </Glass>
    );
  }

  return (
    <>
      {groups.map((group) => {
        // Voided bills stay in the list (nothing is deleted) but must not be
        // counted into the day's takings.
        const dayTotal = group.rows.reduce(
          (sum, o) => sum + (o.status === "voided" ? 0 : Number(o.total)),
          0
        );
        return (
          <div key={group.key}>
            <DayHeaderToggle
              label={group.label}
              sub={daySubLabel(group.date, tz, dayStart)}
              meta={`${group.rows.length} transaksi · ${formatRupiah(dayTotal)}`}
              open={isOpen(group.key, group.label)}
              onToggle={() => toggle(group.key, group.label)}
              count={group.rows.length}
            />
            <ul hidden={!isOpen(group.key, group.label)}>
              {group.rows.map((order) => (
                <TransactionRow key={order.id} order={order} tz={tz} />
              ))}
            </ul>
          </div>
        );
      })}
    </>
  );
}
