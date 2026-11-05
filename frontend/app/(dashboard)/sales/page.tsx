"use client";

import { useState } from "react";
import { useOwnerData } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import type { Business, OrdersPage, StaffMember, TrendPoint } from "@/lib/types";
import { ErrorState, Glass, Segmented, Skeleton } from "@/components/ui";
import { IconChevronLeft, IconChevronRight } from "@/components/icons";
import { TransactionHistory } from "@/components/TransactionHistory";
import { HistoryFilters } from "@/components/HistoryFilters";
import { ReversalPanel } from "@/components/ReversalPanel";
import { BackdatedSaleForm } from "@/components/BackdatedSaleForm";
import { Figure } from "@/components/motion";
import { TrendChart } from "@/components/charts";

const RANGES = [
  { value: "7", label: "7 hari" },
  { value: "30", label: "30 hari" },
  { value: "90", label: "90 hari" },
] as const;

export default function SalesPage() {
  const [range, setRange] = useState<"7" | "30" | "90">("30");
  const [page, setPage] = useState(1);
  // Filters live in the query, not in the page: filtering the 40 rows already
  // fetched would answer "who sold what last Tuesday" with whatever happens to
  // be on this page.
  const [staffId, setStaffId] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const filterQuery =
    (staffId ? `&staff_id=${staffId}` : "") +
    (since ? `&since=${since}` : "") +
    (until ? `&until=${until}` : "");
  const filtered = filterQuery !== "";
  const trend = useOwnerData<TrendPoint[]>(`/api/sales-trend?days=${range}`);
  // Receipts, not lines: one row per transaction, the way the owner's paper
  // slips and the till's own list are numbered.
  const sales = useOwnerData<OrdersPage>(
    `/api/orders?limit=40&offset=${(page - 1) * 40}${filterQuery}`
  );
  const staff = useOwnerData<StaffMember[]>("/auth/staff");
  // A filter change lands the owner on page 1 — page 4 of the old result is a
  // different set of rows, and usually an empty one.
  const onFilter = (fn: (v: string) => void) => (value: string) => {
    setPage(1);
    fn(value);
  };
  // Day buckets must match the backend's business day: its timezone AND its
  // day-start hour (M15-T4), or a 00:15 bill sits under the wrong header.
  const business = useOwnerData<Business>("/api/business");
  const tz = business.data?.timezone;
  const dayStart = business.data?.day_start_hour ?? 0;

  const totalPages = sales.data ? Math.max(1, Math.ceil(sales.data.total / 40)) : 1;
  const rangeTotal = (trend.data ?? []).reduce((sum, p) => sum + p.revenue, 0);
  const rangeTx = (trend.data ?? []).reduce((sum, p) => sum + p.transactions, 0);
  const points = trend.data ?? [];
  const best = points.reduce<TrendPoint | null>((top, p) => (!top || p.revenue > top.revenue ? p : top), null);

  return (
    <div className="animate-fade-up space-y-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="page-title">Penjualan</h1>
        <Segmented options={[...RANGES]} value={range} onChange={setRange} className="w-full sm:w-auto" />
      </header>

      <div className="grid gap-4 xl:grid-cols-12 xl:gap-5">
        {/* What the period came to */}
        <Glass className="flex flex-col p-5 md:p-6 xl:order-2 xl:col-span-4">
          <h2 className="ink-soft text-sm font-medium">
            Total {trend.data ? trend.data.length : range} hari terakhir
          </h2>
          {trend.data ? (
            <>
              <p className="figure mt-4 text-[2.25rem] md:text-[2.5rem]">
                <Figure value={rangeTotal} />
              </p>
              <dl className="mt-auto grid grid-cols-2 gap-x-4 gap-y-4 pt-6 text-sm xl:grid-cols-1 xl:gap-y-0">
                <div className="xl:flex xl:items-baseline xl:justify-between xl:gap-3 xl:py-2.5">
                  <dt className="ink-soft">Transaksi</dt>
                  <dd className="font-semibold tabular-nums">{rangeTx}</dd>
                </div>
                <div className="xl:hairline-t xl:flex xl:items-baseline xl:justify-between xl:gap-3 xl:py-2.5">
                  <dt className="ink-soft">Rata-rata per hari</dt>
                  <dd className="font-semibold tabular-nums">
                    {points.length > 0 ? formatRupiah(Math.round(rangeTotal / points.length)) : "—"}
                  </dd>
                </div>
                <div className="xl:hairline-t xl:flex xl:items-baseline xl:justify-between xl:gap-3 xl:py-2.5">
                  <dt className="ink-soft">Rata-rata per struk</dt>
                  <dd className="font-semibold tabular-nums">
                    {rangeTx > 0 ? formatRupiah(Math.round(rangeTotal / rangeTx)) : "—"}
                  </dd>
                </div>
                <div className="xl:hairline-t xl:flex xl:items-baseline xl:justify-between xl:gap-3 xl:py-2.5">
                  <dt className="ink-soft">Hari terbaik</dt>
                  {/* The day and its takings never break mid-figure: side by
                      side where there is room, the day above its amount where
                      there is not. */}
                  <dd className="font-semibold tabular-nums xl:text-right">
                    {best && best.revenue > 0 ? (
                      <>
                        <span className="whitespace-nowrap">
                          {new Date(best.date).toLocaleDateString("id-ID", { day: "numeric", month: "short" })}
                        </span>
                        <span className="ink-soft block whitespace-nowrap text-[13px] font-medium min-[1700px]:ml-2 min-[1700px]:inline min-[1700px]:text-sm">
                          {formatRupiah(best.revenue)}
                        </span>
                      </>
                    ) : (
                      "—"
                    )}
                  </dd>
                </div>
              </dl>
            </>
          ) : (
            <Skeleton className="mt-4 h-40" />
          )}
        </Glass>

        {/* Trend */}
        <Glass className="flex flex-col overflow-hidden pt-5 xl:order-1 xl:col-span-8">
          <div className="h-60 w-full shrink-0 md:h-72 xl:h-auto xl:min-h-[20rem] xl:flex-1">
            {trend.loading && !trend.data ? (
              <Skeleton className="mx-6 h-44" />
            ) : trend.error && !trend.data ? (
              <ErrorState onRetry={trend.reload} />
            ) : trend.data ? (
              <TrendChart points={trend.data} axis={false} />
            ) : (
              <Skeleton className="mx-6 h-44" />
            )}
          </div>
        </Glass>
      </div>

      <div className="grid items-start gap-x-8 gap-y-8 min-[1440px]:grid-cols-12">
      {/* Day-grouped history — one card per day */}
      <section className="min-w-0 min-[1440px]:col-span-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="section-title">
            Riwayat transaksi
            {filtered && sales.data && (
              <span className="ink-soft ml-2 text-sm font-medium tabular-nums">
                {sales.data.total} hasil
              </span>
            )}
          </h2>
        </div>
        <HistoryFilters
          className="mt-3"
          optionLabel="Kasir"
          options={(staff.data ?? []).map((member) => ({ value: member.id, label: member.name }))}
          value={staffId}
          onValue={onFilter(setStaffId)}
          since={since}
          until={until}
          onRange={(a, b) => {
            setPage(1);
            setSince(a);
            setUntil(b);
          }}
          onReset={() => {
            setPage(1);
            setStaffId("");
            setSince("");
            setUntil("");
          }}
        />
        <TransactionHistory
          orders={sales.data?.rows ?? []}
          loading={sales.loading}
          error={sales.error}
          onRetry={sales.reload}
          filtered={filtered}
          tz={tz}
          dayStart={dayStart}
        />
        {totalPages > 1 && (
          <div className="mt-5 flex items-center justify-between">
            <button
              className="btn-quiet gap-1 px-3.5 py-2 text-sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              <IconChevronLeft className="h-4 w-4" /> Lebih baru
            </button>
            <span className="ink-soft text-sm tabular-nums">
              {page} / {totalPages}
            </span>
            <button
              className="btn-quiet gap-1 px-3.5 py-2 text-sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Lebih lama <IconChevronRight className="h-4 w-4" />
            </button>
          </div>
        )}
      </section>

      {/* Corrections — occasional work: below the everyday list, or beside it
          once the screen is wide enough to keep both in view.
          M15-T10 — a sale that happened on paper, entered afterwards */}
      <div className="min-w-0 space-y-8 min-[1440px]:sticky min-[1440px]:top-8 min-[1440px]:col-span-5">
      <BackdatedSaleForm
        onRecorded={() => {
          trend.reload();
          sales.reload();
        }}
      />

      {/* M15-T11 — reverse a sale found after the shift closed */}
      <ReversalPanel tz={tz} dayStart={dayStart} />
      </div>
      </div>
    </div>
  );
}
