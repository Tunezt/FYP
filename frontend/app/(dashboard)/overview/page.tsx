"use client";

import { useState } from "react";
import Link from "next/link";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useOwnerData } from "@/lib/hooks";
import { formatCompactRupiah, formatQty, formatRupiah } from "@/lib/format";
import type { AlertRow, InventoryItem, Overview, PnlMonth, TrendPoint } from "@/lib/types";
import { EmptyState, ErrorState, Glass, ItemIcon, Plate, Segmented, SeverityBadge, Skeleton } from "@/components/ui";
import { StatCard } from "@/components/StatCard";
import {
  IconBell,
  IconBox,
  IconChart,
  IconChat,
  IconChevronRight,
  IconGear,
  IconPlus,
  IconTrendUp,
  IconWallet,
} from "@/components/icons";
import { HelpTip } from "@/components/HelpTip";
import { OnboardingTour } from "@/components/OnboardingTour";
/** Page-wide reporting period — top-right segmented control, matching the
 * reference's Hari Ini/Minggu Ini/Bulan Ini/Tahun Ini tabs. Drives the "Tren
 * penjualan" hero section (its own inner range pill is folded into this). */
const PERIODS = [
  { value: "today", label: "Hari Ini", days: 1 },
  { value: "week", label: "Minggu Ini", days: 7 },
  { value: "month", label: "Bulan Ini", days: 30 },
  { value: "year", label: "Tahun Ini", days: 365 },
] as const;
type PeriodValue = (typeof PERIODS)[number]["value"];

function stockPill(item: InventoryItem): { text: string; cls: string } {
  const stock = Number(item.current_stock);
  if (stock <= 0) return { text: `0 ${item.unit}`, cls: "pill-bad" };
  return { text: `${formatQty(stock)} ${item.unit}`, cls: "pill-warn" };
}

export default function OverviewPage() {
  const [period, setPeriod] = useState<PeriodValue>("today");
  const periodDef = PERIODS.find((p) => p.value === period) ?? PERIODS[0];
  const window = periodDef.days;
  // All fetches fire in parallel on mount — no waterfall. Trend fetches twice
  // the visible window so "vs periode sebelumnya" is computable client-side.
  const overview = useOwnerData<Overview>("/api/overview");
  const trend = useOwnerData<TrendPoint[]>(`/api/sales-trend?days=${window * 2}`);
  const items = useOwnerData<InventoryItem[]>("/api/items");
  const alerts = useOwnerData<AlertRow[]>("/api/alerts?limit=6");
  const pnl = useOwnerData<PnlMonth[]>("/api/pnl?months=6");

  const o = overview.data;
  const todayDelta =
    o && o.yesterday_revenue > 0
      ? ((o.today_revenue - o.yesterday_revenue) / o.yesterday_revenue) * 100
      : null;

  const visible = (trend.data ?? []).slice(-window);
  const previous = (trend.data ?? []).slice(0, window);
  const visibleTotal = visible.reduce((s, p) => s + p.revenue, 0);
  const previousTotal = previous.reduce((s, p) => s + p.revenue, 0);
  const trendDelta = previousTotal > 0 ? ((visibleTotal - previousTotal) / previousTotal) * 100 : null;
  // A single day can't draw an area path — show the number without a chart.
  const chartReady = visible.length >= 2;

  const months = pnl.data ?? [];
  const thisMonth = months[months.length - 1];
  const lastMonth = months[months.length - 2];
  const profitDelta =
    thisMonth && lastMonth && Math.abs(lastMonth.net) > 0
      ? ((thisMonth.net - lastMonth.net) / Math.abs(lastMonth.net)) * 100
      : null;

  const atRisk = (items.data ?? [])
    .filter((i) => i.below_reorder_threshold || (i.days_remaining !== null && i.days_remaining <= 3))
    .sort((a, b) => Number(a.current_stock) - Number(b.current_stock));
  const unacked = (alerts.data ?? []).filter((a) => !a.is_acknowledged);

  const reloadAll = () => {
    overview.reload();
    trend.reload();
    items.reload();
    alerts.reload();
    pnl.reload();
  };

  // Primary fetch failed with nothing to show — a dead connection, not a load.
  if (overview.error && !overview.data) {
    return (
      <div className="animate-fade-up">
        <Glass>
          <ErrorState onRetry={reloadAll} />
        </Glass>
      </div>
    );
  }

  return (
    <div className="animate-fade-up space-y-7">
      <OnboardingTour enabled={o !== null && !o.onboarding_completed} />

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="ink-faint text-sm">
            {new Date().toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long" })}
          </p>
          <h1 className="text-[1.65rem] font-bold tracking-tight md:text-3xl">
            {o ? o.business_name : "…"}
          </h1>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Segmented options={[...PERIODS]} value={period} onChange={setPeriod} />
          <p className="ink-soft flex items-center gap-2 text-xs">
            <IconChat className="h-3.5 w-3.5 text-[color:var(--good)]" />
            Asisten WhatsApp aktif
          </p>
        </div>
      </header>

      {/* Stat cards */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" data-tour="today">
        {o ? (
          <>
            <StatCard
              icon={<IconChart className="h-5 w-5" />}
              label="Penjualan hari ini"
              help={
                <HelpTip title="Penjualan hari ini">
                  Semua transaksi dari layar kasir sejak tengah malam. Pembandingnya total
                  penjualan kemarin seharian, jadi di pagi hari wajar masih terlihat turun.
                </HelpTip>
              }
              value={formatRupiah(o.today_revenue)}
              deltaPct={todayDelta}
              deltaLabel="vs kemarin"
              spark={(trend.data ?? []).slice(-14).map((p) => p.revenue)}
            />
            <StatCard
              icon={<IconBox className="h-5 w-5" />}
              label="Stok menipis"
              help={
                <HelpTip title="Stok menipis">
                  Barang di bawah batas minimum, atau yang habis dalam ±3 hari kalau laju
                  penjualan 14 hari terakhir bertahan.
                </HelpTip>
              }
              value={
                items.loading ? "…" : (
                  <>
                    {atRisk.length} <span className="ink-soft text-base font-medium">item</span>
                  </>
                )
              }
              footer={
                <Link
                  href="/inventory"
                  className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-[color:var(--accent)]"
                >
                  Lihat detail <IconChevronRight className="h-3.5 w-3.5" />
                </Link>
              }
            />
            <div data-tour="month" className="sm:col-span-2 xl:col-span-1">
              <StatCard
                icon={<IconWallet className="h-5 w-5" />}
                label="Untung bulan ini"
                help={
                  <HelpTip title="Untung bulan ini">
                    Semua penjualan dikurangi pengeluaran yang tercatat (manual + dari foto
                    nota). Makin rajin catat, makin akurat.
                  </HelpTip>
                }
                value={formatRupiah(o.month_net)}
                deltaPct={profitDelta}
                deltaLabel="vs bulan lalu"
                spark={months.map((m) => m.net)}
              />
            </div>
          </>
        ) : (
          <>
            <Skeleton className="h-[172px]" />
            <Skeleton className="h-[172px]" />
            <Skeleton className="h-[172px]" />
          </>
        )}
      </div>

      {/* HERO — sales trend */}
      <Glass className="overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-3 px-6 pt-6 md:px-7">
          <div>
            <h2 className="text-base font-bold">Tren penjualan</h2>
            {trend.data ? (
              <>
                <p className="mt-1 text-3xl font-bold tabular-nums tracking-tight md:text-4xl">
                  {formatRupiah(visibleTotal)}
                </p>
                {trendDelta !== null && (
                  <p
                    className={`mt-1 flex items-center gap-1 text-xs font-semibold ${
                      trendDelta >= 0 ? "text-[color:var(--good)]" : "text-[color:var(--bad)]"
                    }`}
                  >
                    <IconTrendUp className="h-3.5 w-3.5" />
                    {trendDelta >= 0 ? "+" : "−"}
                    {Math.abs(trendDelta).toFixed(1)}%
                    <span className="ink-faint font-medium">vs {periodDef.label.toLowerCase()} sebelumnya</span>
                  </p>
                )}
              </>
            ) : (
              <Skeleton className="mt-2 h-10 w-52" />
            )}
          </div>
          <span className="ink-faint mt-1 text-xs font-semibold uppercase tracking-wide">
            {periodDef.label}
          </span>
        </div>
        <div className="h-52 w-full md:h-60">
          {trend.data && !chartReady && (
            <div className="flex h-full items-center justify-center px-6 text-center">
              <p className="ink-faint text-sm">
                Grafik butuh setidaknya 2 hari data — pilih Minggu Ini ke atas untuk melihat tren.
              </p>
            </div>
          )}
          {trend.data && chartReady && (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={visible} margin={{ top: 16, left: 0, right: 4, bottom: 0 }}>
                <defs>
                  <linearGradient id="rev-hero" x1="0" y1="0" x2="0" y2="1">
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
                  minTickGap={40}
                />
                <YAxis
                  tickFormatter={(v: number) => formatCompactRupiah(v)}
                  tick={{ fontSize: 11, fill: "var(--ink-faint)" }}
                  axisLine={false}
                  tickLine={false}
                  width={46}
                />
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
                  fill="url(#rev-hero)"
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--glass-strong)" }}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </Glass>

      {/* Low stock — its own full-width card, matching the reference */}
      <Plate className="px-6 py-5" data-tour="attention">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-base font-bold">Stok menipis</h2>
          <Link href="/inventory" className="text-sm font-semibold text-[color:var(--accent)]">
            Lihat semua
          </Link>
        </div>
        {items.loading ? (
          <Skeleton className="h-40" />
        ) : atRisk.length === 0 ? (
          <EmptyState emoji="🌿" title="Stok aman semua">
            Tidak ada barang di bawah batas minimum. Sistem cek ulang tiap malam.
          </EmptyState>
        ) : (
          <ul>
            {atRisk.slice(0, 5).map((item) => {
              const pill = stockPill(item);
              return (
                <li key={item.id} className="list-row">
                  <ItemIcon name={item.name} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">{item.name}</p>
                    <p className="ink-faint text-xs">
                      {item.days_remaining !== null
                        ? `±${item.days_remaining} hari lagi di laju sekarang`
                        : "jarang terjual"}
                    </p>
                  </div>
                  <div className="text-right">
                    <span className={pill.cls}>{pill.text}</span>
                    <p className="ink-faint mt-1 text-[11px]">
                      Minimum {formatQty(item.reorder_threshold)} {item.unit}
                    </p>
                  </div>
                  <Link href="/inventory" aria-label={`Lihat ${item.name}`} className="ink-faint">
                    <IconChevronRight className="h-4 w-4" />
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </Plate>

      {/* Alerts — same full-width card treatment, stacked below */}
      <Plate className="px-6 py-5">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-base font-bold">Peringatan</h2>
          {unacked.length > 0 && (
            <Link href="/alerts" className="text-sm font-semibold text-[color:var(--accent)]">
              Lihat semua
            </Link>
          )}
        </div>
        {alerts.loading ? (
          <Skeleton className="h-40" />
        ) : unacked.length === 0 ? (
          <EmptyState emoji="🔔" title="Tidak ada peringatan">
            Kalau ada penjualan yang aneh atau stok kritis, kabarnya muncul di sini dan di
            WhatsApp.
          </EmptyState>
        ) : (
          <ul>
            {unacked.slice(0, 4).map((alert) => (
              <li key={alert.id} className="list-row">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{alert.message}</p>
                  <p className="ink-faint text-xs">
                    {new Date(alert.created_at).toLocaleDateString("id-ID", {
                      day: "numeric",
                      month: "short",
                    })}
                  </p>
                </div>
                <SeverityBadge severity={alert.severity} />
              </li>
            ))}
          </ul>
        )}
      </Plate>

      {/* Quick actions */}
      <Plate className="flex flex-wrap items-stretch justify-between gap-1 divide-x divide-[color:var(--hairline)] px-1 py-1">
        <QuickAction href="/inventory" icon={<IconPlus className="h-5 w-5" />} label="Tambah barang" />
        <QuickAction href="/sales" icon={<IconChart className="h-5 w-5" />} label="Lihat laporan" />
        <QuickAction href="/settings" icon={<IconGear className="h-5 w-5" />} label="Tautan kasir" />
        <QuickAction href="/alerts" icon={<IconBell className="h-5 w-5" />} label="Peringatan" />
      </Plate>
    </div>
  );
}

function QuickAction({ href, icon, label }: { href: string; icon: React.ReactNode; label: string }) {
  return (
    <Link
      href={href}
      className="flex min-w-[120px] flex-1 flex-col items-center gap-1.5 rounded-2xl px-4 py-3 text-center transition-colors hover:bg-[color:var(--accent-soft)]"
    >
      <span className="text-[color:var(--accent)]">{icon}</span>
      <span className="text-xs font-semibold">{label}</span>
    </Link>
  );
}
