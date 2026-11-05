"use client";

import { useState } from "react";
import Link from "next/link";
import { useOwnerData } from "@/lib/hooks";
import { formatQty } from "@/lib/format";
import type { AlertRow, InventoryItem, Overview, PnlMonth, StaffMember, TrendPoint } from "@/lib/types";
import { AlertIcon, EmptyState, ErrorState, Glass, ItemIcon, RowChevron, Segmented, SeverityBadge, Skeleton } from "@/components/ui";
import {
  IconArrowDown,
  IconArrowUp,
  IconArrowUpRight,
  IconBell,
  IconChart,
  IconChevronRight,
  IconGear,
  IconLeaf,
  IconPlus,
} from "@/components/icons";
import { HelpTip } from "@/components/HelpTip";
import { OnboardingTour } from "@/components/OnboardingTour";
import { StatCard } from "@/components/StatCard";
import { Figure, MoonPhase } from "@/components/motion";
import { TrendChart } from "@/components/charts";

/** Reporting period for the "Tren penjualan" pane — the segmented control in
 * that pane's header, beside the chart it drives. */
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

function greeting(): string {
  const h = new Date().getHours();
  if (h < 11) return "Selamat pagi";
  if (h < 15) return "Selamat siang";
  if (h < 19) return "Selamat sore";
  return "Selamat malam";
}

const pane = (i: number) => ({ ["--i" as string]: i }) as React.CSSProperties;

export default function OverviewPage() {
  const [period, setPeriod] = useState<PeriodValue>("week");
  // All fetches fire in parallel on mount — no waterfall. Trend fetches twice
  // the visible window so "vs periode sebelumnya" is computable client-side.
  const overview = useOwnerData<Overview>("/api/overview");
  const asked = PERIODS.find((p) => p.value === period) ?? PERIODS[0];
  const trend = useOwnerData<TrendPoint[]>(`/api/sales-trend?days=${asked.days * 2}`);
  // The period the trend data on screen belongs to, read off the data itself
  // (it holds twice the period's days). While another period loads, the old
  // one stays up under its own name: a figure is never shown under the name
  // of a period it was not counted for.
  const periodDef =
    PERIODS.find((p) => trend.data && p.days * 2 === trend.data.length) ?? asked;
  const shownPeriod = periodDef.value;
  const window = periodDef.days;
  const trendStale = trend.loading && trend.data !== null;
  // The fortnight under today's figure: fixed, whatever period the chart shows.
  const fortnight = useOwnerData<TrendPoint[]>("/api/sales-trend?days=14");
  const items = useOwnerData<InventoryItem[]>("/api/items");
  // Same window as the Peringatan page, so the count in the heading matches it.
  const alerts = useOwnerData<AlertRow[]>("/api/alerts?limit=100");
  const pnl = useOwnerData<PnlMonth[]>("/api/pnl?months=6");
  const staff = useOwnerData<StaffMember[]>("/auth/staff");
  const ownerName = (staff.data ?? []).find((s) => s.role === "owner")?.name;

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

  const days = fortnight.data ?? [];

  const reloadAll = () => {
    overview.reload();
    trend.reload();
    fortnight.reload();
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
    <div className="space-y-6 xl:space-y-7">
      <OnboardingTour enabled={o !== null && !o.onboarding_completed} />

      <header className="pane-in flex flex-wrap items-end justify-between gap-x-8 gap-y-4" style={pane(0)}>
        <div className="min-w-0">
          <h1 className="page-title">
            {greeting()}
            {ownerName ? `, ${ownerName}` : ""}
          </h1>
          <OperationalSummary
            loading={items.loading || alerts.loading}
            lowStock={atRisk.length}
            openAlerts={unacked.length}
            alertsCapped={(alerts.data?.length ?? 0) >= 100}
          />
        </div>
        <p className="ink-soft flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="inline-flex items-center gap-2 tabular-nums">
            {/* Poernama is the full moon: tonight's real phase keeps the date company. */}
            <MoonPhase date={new Date()} className="text-[15px] text-[color:var(--ink)]" />
            {new Date().toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long" })}
          </span>
          <span
            className="inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[13px] font-medium"
            style={{ background: "var(--good-bg)", color: "var(--good)" }}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
            Asisten WhatsApp aktif
          </span>
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-6 xl:grid-cols-12 xl:gap-5">
        {/* Key figures — one surface, three statements */}
        <div className="pane-in md:col-span-6 xl:col-span-12" style={pane(1)} data-tour="today">
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
                value={<Figure value={o.today_revenue} />}
                deltaPct={todayDelta}
                deltaLabel="vs kemarin"
                spark={days.map((d) => d.revenue)}
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
                      <Figure value={atRisk.length} kind="plain" />{" "}
                      <span className="ink-soft font-sans text-base font-medium tracking-normal">item</span>
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
                  value={<Figure value={o.month_net} />}
                  deltaPct={profitDelta}
                  deltaLabel="vs bulan lalu"
                  spark={months.map((m) => m.net)}
                />
              </div>
            </div>
          ) : (
            <Skeleton className="h-[140px] rounded-3xl" />
          )}
        </div>

        {/* Sales trend */}
        <Glass className="pane-in flex flex-col overflow-hidden md:col-span-6 xl:col-span-8" style={pane(4)}>
          <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 px-5 pt-5 md:px-6 md:pt-6 lg:flex-nowrap">
            <div className="min-w-0 flex-1">
              <h2 className="section-title">Tren penjualan</h2>
              {trend.data ? (
                <div className="refetch mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1" data-stale={trendStale}>
                  <p className="figure text-[2rem] md:text-[2.375rem]">
                    <Figure value={visibleTotal} />
                  </p>
                  <Delta pct={trendDelta} label={`vs ${periodDef.label.toLowerCase()} sebelumnya`} />
                </div>
              ) : (
                <Skeleton className="mt-2 h-10 w-52" />
              )}
            </div>
            <Segmented options={[...PERIODS]} value={period} onChange={setPeriod} className="shrink-0 max-sm:w-full [&>button]:max-sm:px-1.5 [&>button]:max-sm:text-[13px]" />
          </div>
          <div
            className={`refetch ${chartReady || !trend.data ? "mt-2 h-56 w-full shrink-0 md:h-72 xl:h-auto xl:min-h-[18rem] xl:flex-1" : "w-full"}`}
            data-stale={trendStale}
          >
            {!trend.data && trend.loading && <Skeleton className="mx-6 mt-4 h-44" />}
            {!trend.data && !trend.loading && trend.error && <ErrorState onRetry={trend.reload} />}
            {trend.data && !chartReady && (
              <div className="surface-inset mx-5 mb-5 mt-4 flex flex-col gap-3 rounded-2xl px-4 py-4 sm:flex-row sm:items-center md:mx-6 md:mb-6">
                <span className="icon-seat ink-soft" aria-hidden>
                  <IconChart className="h-[18px] w-[18px]" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {shownPeriod === "today" ? "Tren muncul mulai rentang 2 hari" : "Riwayat penjualan belum cukup"}
                  </p>
                  <p className="ink-soft mt-0.5 text-[13px] leading-relaxed">
                    {shownPeriod === "today"
                      ? "Angka hari ini sudah tercatat di atas. Grafik butuh setidaknya 2 hari untuk dibandingkan."
                      : "Grafik muncul setelah ada penjualan di setidaknya 2 hari."}
                  </p>
                </div>
                {shownPeriod === "today" && (
                  <button
                    type="button"
                    onClick={() => setPeriod("week")}
                    className="btn-quiet shrink-0 px-4 py-2 text-sm"
                  >
                    Lihat Minggu Ini
                  </button>
                )}
              </div>
            )}
            {trend.data && chartReady && (
              <TrendChart points={visible} />
            )}
          </div>
        </Glass>

        {/* Low stock */}
        <Glass className="pane-in flex flex-col px-5 py-5 md:col-span-3 md:px-6 xl:col-span-4" style={pane(5)} data-tour="attention">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 className="section-title">Stok menipis</h2>
            {atRisk.length > 0 && <span className="pill-warn tabular-nums">{atRisk.length} item</span>}
          </div>
          {items.loading && !items.data ? (
            <Skeleton className="mt-2 h-48" />
          ) : atRisk.length === 0 ? (
            <div className="flex flex-1 items-center justify-center">
              <EmptyState icon={<IconLeaf className="h-5 w-5" />} title="Stok aman semua">
                Tidak ada barang di bawah batas minimum. Sistem cek ulang tiap malam.
              </EmptyState>
            </div>
          ) : (
            <>
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
              <Link href="/inventory" className="btn-quiet mt-auto w-full px-4 py-2.5 text-sm">
                Lihat semua stok
              </Link>
            </>
          )}
        </Glass>

        {/* Alerts */}
        <Glass className="pane-in flex flex-col px-5 py-5 md:col-span-3 md:px-6 xl:col-span-7" style={pane(6)}>
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 className="section-title">Peringatan</h2>
            {unacked.length > 0 && (
              <Link href="/alerts" className="text-[13px] font-semibold text-[color:var(--accent)] underline decoration-[color:var(--hairline-strong)] underline-offset-[3px] hover:decoration-[color:var(--ink)]">
                Lihat semua ({unacked.length}
                {(alerts.data?.length ?? 0) >= 100 ? "+" : ""})
              </Link>
            )}
          </div>
          {alerts.loading && !alerts.data ? (
            <Skeleton className="mt-2 h-40" />
          ) : unacked.length === 0 ? (
            <div className="flex flex-1 items-center justify-center">
              <EmptyState icon={<IconBell className="h-5 w-5" />} title="Tidak ada peringatan">
                Kalau ada penjualan yang aneh atau stok kritis, kabarnya muncul di sini dan di
                WhatsApp.
              </EmptyState>
            </div>
          ) : (
            <ul>
              {unacked.slice(0, 4).map((alert) => (
                <li key={alert.id} className="list-row">
                  <AlertIcon type={alert.type} severity={alert.severity} />
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

        {/* Quick actions */}
        <nav
          className="pane-in grid grid-cols-2 gap-4 md:col-span-6 md:grid-cols-4 xl:col-span-5 xl:grid-cols-2 xl:gap-5"
          style={pane(7)}
          aria-label="Aksi cepat"
        >
          <QuickAction href="/inventory" icon={<IconPlus className="h-[18px] w-[18px]" />} label="Tambah barang" hint="Stok dan menu" />
          <QuickAction href="/sales" icon={<IconChart className="h-[18px] w-[18px]" />} label="Lihat laporan" hint="Riwayat transaksi" />
          <QuickAction href="/settings" icon={<IconGear className="h-[18px] w-[18px]" />} label="Tautan kasir" hint="Tablet dan staf" />
          <QuickAction href="/alerts" icon={<IconBell className="h-[18px] w-[18px]" />} label="Peringatan" hint="Yang perlu ditindak" />
        </nav>
      </div>
    </div>
  );
}

function Delta({ pct, label, empty }: { pct: number | null; label: string; empty?: string }) {
  if (pct === null) return empty ? <p className="ink-faint text-[13px]">{empty}</p> : null;
  return (
    <p className="flex items-center gap-1.5 text-[13px]">
      <span
        className={`inline-flex items-center gap-0.5 font-semibold tabular-nums ${
          pct >= 0 ? "text-[color:var(--good)]" : "text-[color:var(--bad)]"
        }`}
      >
        {pct >= 0 ? <IconArrowUp className="h-3 w-3" /> : <IconArrowDown className="h-3 w-3" />}
        {Math.abs(pct).toFixed(1).replace(".", ",")}%
      </span>
      <span className="ink-faint">{label}</span>
    </p>
  );
}

function QuickAction({ href, icon, label, hint }: { href: string; icon: React.ReactNode; label: string; hint: string }) {
  return (
    <Link href={href} className="tile-link min-h-[7.25rem]">
      <span className="flex items-start justify-between">
        <span className="icon-seat">{icon}</span>
        <IconArrowUpRight className="tile-arrow h-[18px] w-[18px]" />
      </span>
      <span className="mt-4 block min-w-0">
        <span className="block truncate text-[15px] font-semibold">{label}</span>
        <span className="ink-faint block truncate text-xs">{hint}</span>
      </span>
    </Link>
  );
}

/** One sentence under the greeting: what needs the owner today, each part a
 * link to where it is handled. Counts come straight from the same data as the
 * panes below; zero reads as a calm fact, not an empty slot. */
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
  if (loading) return <Skeleton className="mt-3 h-5 w-72 rounded-lg" />;

  const link =
    "font-medium text-[color:var(--ink)] underline decoration-[color:var(--hairline-strong)] underline-offset-[3px] transition-colors hover:decoration-[color:var(--ink)]";

  return (
    <p className="ink-soft mt-3 flex flex-col gap-x-2 gap-y-1 text-[15px] sm:flex-row sm:flex-wrap sm:items-center">
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
