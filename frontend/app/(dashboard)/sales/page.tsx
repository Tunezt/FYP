"use client";

import { useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useOwnerData } from "@/lib/hooks";
import { formatQty, formatRupiah } from "@/lib/format";
import type { Page, SaleRow, TrendPoint } from "@/lib/types";
import { EmptyState, Glass, SectionTitle, Segmented, Skeleton } from "@/components/ui";

const RANGES = [
  { value: "7", label: "7 hari" },
  { value: "30", label: "30 hari" },
  { value: "90", label: "90 hari" },
] as const;

export default function SalesPage() {
  const [range, setRange] = useState<"7" | "30" | "90">("30");
  const [page, setPage] = useState(1);
  const trend = useOwnerData<TrendPoint[]>(`/api/sales-trend?days=${range}`);
  const sales = useOwnerData<Page<SaleRow>>(`/api/sales?page=${page}&page_size=25`);

  const totalPages = sales.data ? Math.max(1, Math.ceil(sales.data.total / sales.data.page_size)) : 1;
  const rangeTotal = (trend.data ?? []).reduce((sum, p) => sum + p.revenue, 0);

  return (
    <div className="animate-fade-up space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight md:text-3xl">Penjualan</h1>
          {trend.data && (
            <p className="ink-soft mt-1 text-sm">
              {formatRupiah(rangeTotal)} dalam {range} hari terakhir
            </p>
          )}
        </div>
        <Segmented options={[...RANGES]} value={range} onChange={setRange} />
      </header>

      <Glass className="overflow-hidden pt-5">
        <div className="h-56 w-full">
          {trend.data ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trend.data} margin={{ top: 8, left: 8, right: 8, bottom: 4 }}>
                <defs>
                  <linearGradient id="rev2" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#5e5ce6" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="date"
                  tickFormatter={(d: string) => d.slice(8)}
                  tick={{ fontSize: 11, fill: "var(--ink-faint)" }}
                  axisLine={false}
                  tickLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis hide />
                <Tooltip
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="glass-card glass-strong px-3 py-2 text-xs">
                        <p className="ink-soft">{(payload[0].payload as TrendPoint).date}</p>
                        <p className="font-bold">{formatRupiah(payload[0].value as number)}</p>
                        <p className="ink-soft">
                          {(payload[0].payload as TrendPoint).transactions} transaksi
                        </p>
                      </div>
                    ) : null
                  }
                />
                <Area type="monotone" dataKey="revenue" stroke="#5e5ce6" strokeWidth={2.5} fill="url(#rev2)" />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <Skeleton className="mx-6 h-44" />
          )}
        </div>
      </Glass>

      <section>
        <SectionTitle>Riwayat transaksi</SectionTitle>
        {sales.loading ? (
          <Skeleton className="h-64" />
        ) : !sales.data || sales.data.rows.length === 0 ? (
          <Glass>
            <EmptyState emoji="🧾" title="Belum ada transaksi">
              Transaksi dari layar kasir akan muncul di sini begitu staf mencatat penjualan
              pertama.
            </EmptyState>
          </Glass>
        ) : (
          <>
            <ul className="space-y-1.5">
              {sales.data.rows.map((sale) => (
                <li
                  key={sale.id}
                  className="flex items-center justify-between gap-4 rounded-2xl px-4 py-3 transition-colors hover:bg-[color:var(--glass)]"
                  style={{ border: "1px solid var(--hairline)" }}
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium">
                      {formatQty(sale.quantity)}× {sale.item_name}
                    </p>
                    <p className="ink-faint text-xs">
                      {new Date(sale.sold_at).toLocaleString("id-ID", {
                        day: "numeric",
                        month: "short",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}{" "}
                      · {sale.staff_name}
                    </p>
                  </div>
                  <span className="shrink-0 font-semibold tabular-nums">
                    {formatRupiah(sale.total_price)}
                  </span>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex items-center justify-between">
              <button
                className="btn-quiet px-4 py-2 text-sm disabled:opacity-40"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                ← Sebelumnya
              </button>
              <span className="ink-soft text-sm tabular-nums">
                {page} / {totalPages}
              </span>
              <button
                className="btn-quiet px-4 py-2 text-sm disabled:opacity-40"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Berikutnya →
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
