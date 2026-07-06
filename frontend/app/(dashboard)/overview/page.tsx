"use client";

import Link from "next/link";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { useOwnerData } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import type { AlertRow, InventoryItem, Overview, TrendPoint } from "@/lib/types";
import { Glass, Plate, SectionTitle, SeverityBadge, Skeleton, EmptyState } from "@/components/ui";
import { IconArrowDown, IconArrowUp, IconChat } from "@/components/icons";
import { HelpTip } from "@/components/HelpTip";

export default function OverviewPage() {
  // Four independent fetches fire in parallel on mount — no waterfall.
  const overview = useOwnerData<Overview>("/api/overview");
  const trend = useOwnerData<TrendPoint[]>("/api/sales-trend?days=30");
  const items = useOwnerData<InventoryItem[]>("/api/items");
  const alerts = useOwnerData<AlertRow[]>("/api/alerts?limit=5");

  const o = overview.data;
  const delta = o && o.yesterday_revenue > 0 ? ((o.today_revenue - o.yesterday_revenue) / o.yesterday_revenue) * 100 : null;
  const atRisk = (items.data ?? []).filter(
    (i) => i.below_reorder_threshold || (i.days_remaining !== null && i.days_remaining <= 3)
  );
  const unacked = (alerts.data ?? []).filter((a) => !a.is_acknowledged);

  return (
    <div className="animate-fade-up space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="ink-faint text-sm">
            {new Date().toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long" })}
          </p>
          <h1 className="text-2xl font-bold tracking-tight md:text-3xl">
            {o ? o.business_name : "…"}
          </h1>
        </div>
        <p className="ink-soft flex items-center gap-2 text-sm">
          <IconChat className="h-4 w-4 text-emerald-500" />
          Asisten WhatsApp aktif
        </p>
      </header>

      {/* Today — the one number the owner came for */}
      <Glass className="overflow-hidden">
        <div className="px-6 pb-2 pt-6 md:px-8">
          <p className="ink-soft text-sm font-medium">Penjualan hari ini</p>
          {o ? (
            <div className="mt-1 flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <span className="text-4xl font-bold tabular-nums tracking-tight md:text-5xl">
                {formatRupiah(o.today_revenue)}
              </span>
              <span className="ink-soft text-sm">{o.today_transactions} transaksi</span>
              {delta !== null && (
                <span
                  className={`flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    delta >= 0 ? "bg-emerald-500/15 text-emerald-600" : "bg-red-500/15 text-red-500"
                  }`}
                >
                  {delta >= 0 ? <IconArrowUp className="h-3 w-3" /> : <IconArrowDown className="h-3 w-3" />}
                  {Math.abs(delta).toFixed(0)}% vs kemarin
                </span>
              )}
            </div>
          ) : (
            <Skeleton className="mt-2 h-12 w-64" />
          )}
        </div>
        <div className="h-40 w-full md:h-48">
          {trend.data ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trend.data} margin={{ top: 12, left: 0, right: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#5e5ce6" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="date" hide />
                <Tooltip
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="glass-card glass-strong px-3 py-2 text-xs">
                        <p className="ink-soft">{(payload[0].payload as TrendPoint).date}</p>
                        <p className="font-bold">{formatRupiah(payload[0].value as number)}</p>
                      </div>
                    ) : null
                  }
                />
                <Area
                  type="monotone"
                  dataKey="revenue"
                  stroke="#5e5ce6"
                  strokeWidth={2.5}
                  fill="url(#rev)"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <Skeleton className="mx-6 h-32" />
          )}
        </div>
      </Glass>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* This month — quiet plate, rows not cards */}
        <Plate className="px-6 py-5">
          <SectionTitle
            hint={
              <HelpTip title="Laba bulan ini">
                Masuk = semua penjualan dari kasir. Keluar = pengeluaran yang tercatat (manual +
                dari foto nota). Selisihnya perkiraan laba — belum termasuk biaya yang tidak
                dicatat.
              </HelpTip>
            }
          >
            Bulan ini
          </SectionTitle>
          {o ? (
            <dl className="space-y-2.5">
              <div className="flex items-baseline justify-between">
                <dt className="ink-soft text-sm">Masuk</dt>
                <dd className="font-semibold tabular-nums">{formatRupiah(o.month_revenue)}</dd>
              </div>
              <div className="flex items-baseline justify-between">
                <dt className="ink-soft text-sm">Keluar</dt>
                <dd className="font-semibold tabular-nums">− {formatRupiah(o.month_expenses)}</dd>
              </div>
              <div className="hairline-b" />
              <div className="flex items-baseline justify-between pt-1">
                <dt className="text-sm font-semibold">Perkiraan laba</dt>
                <dd
                  className={`text-lg font-bold tabular-nums ${o.month_net >= 0 ? "text-emerald-600" : "text-red-500"}`}
                >
                  {formatRupiah(o.month_net)}
                </dd>
              </div>
            </dl>
          ) : (
            <Skeleton className="h-24" />
          )}
          <Link href="/money" className="mt-4 inline-block text-sm font-semibold text-accent-500">
            Lihat keuangan →
          </Link>
        </Plate>

        {/* Needs attention */}
        <Glass className="px-6 py-5">
          <SectionTitle
            hint={
              <HelpTip title="Perlu perhatian">
                Barang yang stoknya di bawah batas minimum, atau yang habis dalam ±3 hari kalau
                laju penjualan tetap. Dihitung otomatis dari data kasir.
              </HelpTip>
            }
            action={
              unacked.length > 0 && (
                <Link href="/alerts" className="text-sm font-semibold text-accent-500">
                  {unacked.length} peringatan →
                </Link>
              )
            }
          >
            Perlu perhatian
          </SectionTitle>
          {items.loading ? (
            <Skeleton className="h-24" />
          ) : atRisk.length === 0 && unacked.length === 0 ? (
            <EmptyState emoji="🌿" title="Semua aman">
              Stok cukup dan tidak ada kejadian aneh. Cek lagi besok!
            </EmptyState>
          ) : (
            <ul className="space-y-2.5">
              {atRisk.slice(0, 4).map((item) => (
                <li key={item.id} className="flex items-center justify-between gap-3">
                  <span className="truncate text-sm font-medium">{item.name}</span>
                  <span className="ink-soft shrink-0 text-xs tabular-nums">
                    {item.days_remaining !== null
                      ? `±${item.days_remaining} hari lagi`
                      : `sisa ${Number(item.current_stock)} ${item.unit}`}
                  </span>
                </li>
              ))}
              {unacked.slice(0, 2).map((alert) => (
                <li key={alert.id} className="flex items-center justify-between gap-3">
                  <span className="truncate text-sm">{alert.message}</span>
                  <SeverityBadge severity={alert.severity} />
                </li>
              ))}
            </ul>
          )}
        </Glass>
      </div>
    </div>
  );
}
