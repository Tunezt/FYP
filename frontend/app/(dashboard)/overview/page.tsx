"use client";

import { useState } from "react";
import Link from "next/link";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useOwnerData } from "@/lib/hooks";
import { formatCompactRupiah, formatQty, formatRupiah } from "@/lib/format";
import type { AlertRow, InventoryItem, Overview, PnlMonth, TrendPoint } from "@/lib/types";
import { EmptyState, ErrorState, Glass, ItemIcon, RowChevron, Segmented, SeverityBadge, Skeleton } from "@/components/ui";
import { StatCard } from "@/components/StatCard";
import {
  IconArrowDown,
  IconArrowUp,
  IconBell,
  IconChart,
  IconChevronRight,
  IconGear,
  IconLeaf,
  IconPlus,
} from "@/components/icons";
import { HelpTip } from "@/components/HelpTip";
import { OnboardingTour } from "@/components/OnboardingTour";
/** Page-wide reporting period — the segmented control in the header. Drives
 * the "Tren penjualan" section. */
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
  // Same window as the Peringatan page, so the count in the heading matches it.
  const alerts = useOwnerData<AlertRow[]>("/api/alerts?limit=100");
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
    <div className="animate-fade-up space-y-6">
      <OnboardingTour enabled={o !== null && !o.onboarding_completed} />

      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <p className="ink-faint flex flex-wrap items-center gap-x-2 text-[13px]">
            <span>
              {new Date().toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long" })}
            </span>
            <span aria-hidden>·</span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--good)]" aria-hidden />
              Asisten WhatsApp aktif
            </span>
          </p>
          <h1 className="page-title mt-1">Ringkasan usaha</h1>
          <OperationalSummary
            loading={items.loading || alerts.loading}
            lowStock={atRisk.length}
            openAlerts={unacked.length}
            alertsCapped={(alerts.data?.length ?? 0) >= 100}
          />
        </div>
        <Segmented
          options={[...PERIODS]}
          value={period}
          onChange={setPeriod}
          className="w-full sm:w-auto"
        />
      </header>

      {/* Key figures — one surface, three statements */}
      <div data-tour="today">
        {o ? (
          <div className="glass-card grid divide-y divide-[color:var(--hairline)] lg:grid-cols-3 lg:divide-x lg:divide-y-0">
            <StatCard
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
                    {atRisk.length}{" "}
                    <span className="ink-soft text-base font-medium tracking-normal">item</span>
                  </>
                )
              }
              footer={
                <Link
                  href="/inventory"
                  className="inline-flex items-center gap-0.5 text-[13px] font-semibold text-[color:var(--accent)] hover:underline"
                >
                  Lihat detail <IconChevronRight className="h-3.5 w-3.5" />
                </Link>
              }
            />
            <div data-tour="month" className="min-w-0">
              <StatCard
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
          </div>
        ) : (
          <Skeleton className="h-[132px] rounded-3xl" />
        )}
      </div>

      {/* Sales trend */}
      <Glass className="overflow-hidden">
        <div className="px-5 pt-5 md:px-6">
          <h2 className="section-title">Tren penjualan</h2>
          {trend.data && !trend.loading ? (
            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <p className="text-[2rem] font-semibold leading-tight tabular-nums tracking-[-0.03em] md:text-[2.25rem]">
                {formatRupiah(visibleTotal)}
              </p>
              {trendDelta !== null && (
                <p className="flex items-center gap-1 text-[13px]">
                  <span
                    className={`inline-flex items-center gap-0.5 font-semibold tabular-nums ${
                      trendDelta >= 0 ? "text-[color:var(--good)]" : "text-[color:var(--bad)]"
                    }`}
                  >
                    {trendDelta >= 0 ? <IconArrowUp className="h-3 w-3" /> : <IconArrowDown className="h-3 w-3" />}
                    {Math.abs(trendDelta).toFixed(1).replace(".", ",")}%
                  </span>
                  <span className="ink-faint">vs {periodDef.label.toLowerCase()} sebelumnya</span>
                </p>
              )}
            </div>
          ) : (
            <Skeleton className="mt-2 h-10 w-52" />
          )}
        </div>
        <div className={chartReady || trend.loading ? "h-52 w-full md:h-64" : "w-full"}>
          {trend.loading && <Skeleton className="mx-6 mt-4 h-40" />}
          {!trend.loading && trend.error && <ErrorState onRetry={trend.reload} />}
          {!trend.loading && !trend.error && trend.data && !chartReady && (
            <div className="surface-inset mx-5 mb-5 mt-4 flex flex-col gap-3 rounded-2xl px-4 py-4 sm:flex-row sm:items-center md:mx-6">
              <span
                className="ink-faint flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[color:var(--surface)]"
                style={{ boxShadow: "0 0 0 1px var(--border)" }}
                aria-hidden
              >
                <IconChart className="h-[18px] w-[18px]" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">
                  {period === "today" ? "Tren muncul mulai rentang 2 hari" : "Riwayat penjualan belum cukup"}
                </p>
                <p className="ink-soft mt-0.5 text-[13px] leading-relaxed">
                  {period === "today"
                    ? "Angka hari ini sudah tercatat di atas. Grafik butuh setidaknya 2 hari untuk dibandingkan."
                    : "Grafik muncul setelah ada penjualan di setidaknya 2 hari."}
                </p>
              </div>
              {period === "today" && (
                <button
                  type="button"
                  onClick={() => setPeriod("week")}
                  className="btn-quiet shrink-0 px-3.5 py-2 text-sm"
                >
                  Lihat Minggu Ini
                </button>
              )}
            </div>
          )}
          {!trend.loading && !trend.error && trend.data && chartReady && (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={visible} margin={{ top: 20, left: 8, right: 20, bottom: 8 }}>
                <defs>
                  <linearGradient id="rev-hero" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.14} />
                    <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
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
                  padding={{ left: 16, right: 16 }}
                  minTickGap={40}
                  tickMargin={8}
                />
                <YAxis
                  tickFormatter={(v: number) => formatCompactRupiah(v)}
                  tick={{ fontSize: 11, fill: "var(--ink-faint)" }}
                  axisLine={false}
                  tickLine={false}
                  width={46}
                />
                <Tooltip
                  cursor={{ stroke: "var(--hairline-strong)", strokeWidth: 1 }}
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="rounded-xl bg-[color:var(--surface-float)] px-3 py-2 text-xs shadow-pop">
                        <p className="ink-soft">
                          {new Date((payload[0].payload as TrendPoint).date).toLocaleDateString(
                            "id-ID",
                            { weekday: "short", day: "numeric", month: "short" }
                          )}
                        </p>
                        <p className="text-sm font-semibold tabular-nums">
                          {formatRupiah(payload[0].value as number)}
                        </p>
                        <p className="ink-faint">
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
                  strokeWidth={1.75}
                  fill="url(#rev-hero)"
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </Glass>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        {/* Low stock */}
        <Glass className="px-5 py-4 md:px-6" data-tour="attention">
          <div className="mb-1 flex items-baseline justify-between">
            <h2 className="section-title">Stok menipis</h2>
            <Link href="/inventory" className="text-[13px] font-semibold text-[color:var(--accent)] hover:underline">
              Lihat semua
            </Link>
          </div>
          {items.loading ? (
            <Skeleton className="mt-3 h-40" />
          ) : atRisk.length === 0 ? (
            <EmptyState icon={<IconLeaf className="h-5 w-5" />} title="Stok aman semua">
              Tidak ada barang di bawah batas minimum. Sistem cek ulang tiap malam.
            </EmptyState>
          ) : (
            <ul>
              {atRisk.slice(0, 5).map((item) => {
                const pill = stockPill(item);
                return (
                  <li key={item.id}>
                    <Link href="/inventory" className="list-row list-row-action">
                      <ItemIcon name={item.name} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{item.name}</p>
                        <p className="ink-faint truncate text-xs">
                          {item.days_remaining !== null
                            ? `±${item.days_remaining} hari lagi di laju sekarang`
                            : "jarang terjual"}
                        </p>
                      </div>
                      <div className="shrink-0 text-right">
                        <span className={pill.cls}>{pill.text}</span>
                        <p className="ink-faint mt-1 text-[11px] tabular-nums">
                          Minimum {formatQty(item.reorder_threshold)} {item.unit}
                        </p>
                      </div>
                      <RowChevron />
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Glass>

        {/* Alerts */}
        <Glass className="px-5 py-4 md:px-6">
          <div className="mb-1 flex items-baseline justify-between">
            <h2 className="section-title">Peringatan</h2>
            {unacked.length > 0 && (
              <Link href="/alerts" className="text-[13px] font-semibold text-[color:var(--accent)] hover:underline">
                Lihat semua
              </Link>
            )}
          </div>
          {alerts.loading ? (
            <Skeleton className="mt-3 h-40" />
          ) : unacked.length === 0 ? (
            <EmptyState icon={<IconBell className="h-5 w-5" />} title="Tidak ada peringatan">
              Kalau ada penjualan yang aneh atau stok kritis, kabarnya muncul di sini dan di
              WhatsApp.
            </EmptyState>
          ) : (
            <ul>
              {unacked.slice(0, 4).map((alert) => (
                <li key={alert.id} className="list-row">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium leading-snug">{alert.message}</p>
                    <p className="ink-faint mt-0.5 text-xs">
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
        </Glass>
      </div>

      {/* Quick actions */}
      <nav className="grid grid-cols-2 gap-2.5 sm:grid-cols-4" aria-label="Aksi cepat">
        <QuickAction href="/inventory" icon={<IconPlus className="h-[18px] w-[18px]" />} label="Tambah barang" />
        <QuickAction href="/sales" icon={<IconChart className="h-[18px] w-[18px]" />} label="Lihat laporan" />
        <QuickAction href="/settings" icon={<IconGear className="h-[18px] w-[18px]" />} label="Tautan kasir" />
        <QuickAction href="/alerts" icon={<IconBell className="h-[18px] w-[18px]" />} label="Peringatan" />
      </nav>
    </div>
  );
}

function QuickAction({ href, icon, label }: { href: string; icon: React.ReactNode; label: string }) {
  return (
    <Link href={href} className="btn-quiet justify-start gap-2.5 px-4 py-3 text-sm">
      <span className="text-[color:var(--accent)]">{icon}</span>
      <span className="truncate">{label}</span>
    </Link>
  );
}

/** One sentence under the page title: what needs the owner today, each part a
 * link to where it is handled. Counts come straight from the same data as the
 * cards below; zero reads as a calm fact, not an empty slot. */
function OperationalSummary({
  loading,
  lowStock,
  openAlerts,
  alertsCapped,
}: {
  loading: boolean;
  lowStock: number;
  openAlerts: number;
  alertsCapped: boolean;
}) {
  if (loading) return <Skeleton className="mt-2 h-5 w-72 rounded-lg" />;

  const link =
    "font-medium text-[color:var(--ink)] underline decoration-[color:var(--hairline-strong)] underline-offset-[3px] transition-colors hover:decoration-[color:var(--ink)]";

  return (
    <p className="ink-soft mt-1.5 flex flex-col gap-x-2 gap-y-1 text-[15px] sm:flex-row sm:flex-wrap sm:items-center">
      <span className="inline-flex items-center gap-2">
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: lowStock > 0 ? "var(--warn)" : "var(--good)" }}
          aria-hidden
        />
        {lowStock > 0 ? (
          <Link href="/inventory" className={link}>
            {lowStock} barang stok menipis
          </Link>
        ) : (
          <span>Stok aman</span>
        )}
      </span>
      <span aria-hidden className="ink-faint hidden sm:inline">·</span>
      <span className="inline-flex items-center gap-2">
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: openAlerts > 0 ? "var(--bad)" : "var(--good)" }}
          aria-hidden
        />
        {openAlerts > 0 ? (
          <Link href="/alerts" className={link}>
            {openAlerts}
            {alertsCapped ? "+" : ""} peringatan perlu ditindak
          </Link>
        ) : (
          <span>Tidak ada peringatan baru</span>
        )}
      </span>
    </p>
  );
}
