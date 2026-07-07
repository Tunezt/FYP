"use client";

import { useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useOwnerData } from "@/lib/hooks";
import { formatQty, formatRupiah } from "@/lib/format";
import { daySubLabel, groupByDay, timeLabel } from "@/lib/dates";
import type { Business, Page, SaleRow, TrendPoint } from "@/lib/types";
import { DayHeader, EmptyState, Glass, Segmented, Skeleton, Tile } from "@/components/ui";

const RANGES = [
  { value: "7", label: "7 hari" },
  { value: "30", label: "30 hari" },
  { value: "90", label: "90 hari" },
] as const;

export default function SalesPage() {
  const [range, setRange] = useState<"7" | "30" | "90">("30");
  const [page, setPage] = useState(1);
  const trend = useOwnerData<TrendPoint[]>(`/api/sales-trend?days=${range}`);
  const sales = useOwnerData<Page<SaleRow>>(`/api/sales?page=${page}&page_size=40`);
  // Day buckets must match the backend's business-timezone day boundaries.
  const business = useOwnerData<Business>("/api/business");
  const tz = business.data?.timezone;

  const totalPages = sales.data ? Math.max(1, Math.ceil(sales.data.total / sales.data.page_size)) : 1;
  const rangeTotal = (trend.data ?? []).reduce((sum, p) => sum + p.revenue, 0);
  const rangeTx = (trend.data ?? []).reduce((sum, p) => sum + p.transactions, 0);

  const groups = groupByDay(sales.data?.rows ?? [], (s) => new Date(s.sold_at), tz);

  return (
    <div className="animate-fade-up space-y-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[1.65rem] font-bold tracking-tight md:text-3xl">Penjualan</h1>
          {trend.data && (
            <p className="ink-soft mt-1 text-sm">
              <span className="font-semibold tabular-nums">{formatRupiah(rangeTotal)}</span> dari{" "}
              {rangeTx} transaksi · {range} hari terakhir
            </p>
          )}
        </div>
        <Segmented options={[...RANGES]} value={range} onChange={setRange} />
      </header>

      {/* HERO — trend */}
      <Glass className="overflow-hidden pt-5">
        <div className="h-56 w-full">
          {trend.data ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trend.data} margin={{ top: 8, left: 8, right: 8, bottom: 4 }}>
                <defs>
                  <linearGradient id="rev-sales" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.24} />
                    <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis
                  dataKey="date"
                  tickFormatter={(d: string) =>
                    new Date(d).toLocaleDateString("id-ID", { day: "numeric", month: "short" })
                  }
                  tick={{ fontSize: 11, fill: "var(--ink-faint)" }}
                  axisLine={false}
                  tickLine={false}
                  interval="preserveStartEnd"
                  minTickGap={44}
                />
                <YAxis hide />
                <Tooltip
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="plate px-3 py-2 text-xs shadow-pop">
                        <p className="ink-soft">
                          {new Date((payload[0].payload as TrendPoint).date).toLocaleDateString(
                            "id-ID",
                            { weekday: "short", day: "numeric", month: "short" }
                          )}
                        </p>
                        <p className="font-bold tabular-nums">
                          {formatRupiah(payload[0].value as number)}
                        </p>
                        <p className="ink-soft">
                          {(payload[0].payload as TrendPoint).transactions} transaksi
                        </p>
                      </div>
                    ) : null
                  }
                />
                <Area
                  type="monotone"
                  dataKey="revenue"
                  stroke="var(--chart-1)"
                  strokeWidth={2}
                  fill="url(#rev-sales)"
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--glass-strong)" }}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <Skeleton className="mx-6 h-44" />
          )}
        </div>
      </Glass>

      {/* Day-grouped history — open rows on the page background */}
      <section>
        <h2 className="text-base font-bold">Riwayat transaksi</h2>
        {sales.loading ? (
          <Skeleton className="mt-3 h-64" />
        ) : !sales.data || sales.data.rows.length === 0 ? (
          <Glass className="mt-3">
            <EmptyState emoji="🧾" title="Belum ada transaksi">
              Transaksi dari layar kasir akan muncul di sini begitu staf mencatat penjualan
              pertama.
            </EmptyState>
          </Glass>
        ) : (
          <>
            {groups.map((group) => {
              const dayTotal = group.rows.reduce((s, r) => s + Number(r.total_price), 0);
              return (
                <div key={group.key}>
                  <DayHeader
                    label={group.label}
                    sub={daySubLabel(group.date, tz)}
                    meta={`${group.rows.length} transaksi · ${formatRupiah(dayTotal)}`}
                  />
                  <ul>
                    {group.rows.map((sale) => (
                      <li key={sale.id} className="list-row">
                        <span className="ink-faint w-11 shrink-0 text-xs tabular-nums">
                          {timeLabel(new Date(sale.sold_at), tz)}
                        </span>
                        <Tile label={sale.item_name} className="h-9 w-9 rounded-lg text-[10px]" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">
                            {formatQty(sale.quantity)}× {sale.item_name}
                          </p>
                          <p className="ink-faint text-xs">oleh {sale.staff_name}</p>
                        </div>
                        <span className="shrink-0 text-sm font-semibold tabular-nums">
                          {formatRupiah(sale.total_price)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
            {totalPages > 1 && (
              <div className="mt-5 flex items-center justify-between">
                <button
                  className="btn-quiet px-4 py-2 text-sm disabled:opacity-40"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  ← Lebih baru
                </button>
                <span className="ink-soft text-sm tabular-nums">
                  {page} / {totalPages}
                </span>
                <button
                  className="btn-quiet px-4 py-2 text-sm disabled:opacity-40"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Lebih lama →
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
