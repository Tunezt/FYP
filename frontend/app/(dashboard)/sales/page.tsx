"use client";

import { useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useOwnerData } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import type { Business, OrdersPage, StaffMember, TrendPoint } from "@/lib/types";
import { ErrorState, Glass, Segmented, Skeleton } from "@/components/ui";
import { IconChevronLeft, IconChevronRight } from "@/components/icons";
import { TransactionHistory } from "@/components/TransactionHistory";
import { HistoryFilters } from "@/components/HistoryFilters";
import { ReversalPanel } from "@/components/ReversalPanel";
import { BackdatedSaleForm } from "@/components/BackdatedSaleForm";

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


  return (
    <div className="animate-fade-up space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Penjualan</h1>
          {trend.data && !trend.loading && (
            <p className="ink-soft mt-1 text-sm">
              <span className="font-semibold tabular-nums">{formatRupiah(rangeTotal)}</span> dari{" "}
              {rangeTx} transaksi · {range} hari terakhir
            </p>
          )}
        </div>
        <Segmented options={[...RANGES]} value={range} onChange={setRange} className="w-full sm:w-auto" />
      </header>

      {/* Trend */}
      <Glass className="overflow-hidden pt-4">
        <div className="h-56 w-full md:h-64">
          {trend.loading ? (
            <Skeleton className="mx-6 h-44" />
          ) : trend.error ? (
            <ErrorState onRetry={trend.reload} />
          ) : trend.data ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trend.data} margin={{ top: 12, left: 16, right: 16, bottom: 8 }}>
                <defs>
                  <linearGradient id="rev-sales" x1="0" y1="0" x2="0" y2="1">
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
                  minTickGap={44}
                  tickMargin={8}
                />
                <YAxis hide />
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
                  strokeWidth={1.75}
                  fill="url(#rev-sales)"
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <Skeleton className="mx-6 h-44" />
          )}
        </div>
      </Glass>

      {/* Day-grouped history — one card per day */}
      <section>
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

      {/* Corrections — occasional work, below the everyday list.
          M15-T10 — a sale that happened on paper, entered afterwards */}
      <BackdatedSaleForm
        onRecorded={() => {
          trend.reload();
          sales.reload();
        }}
      />

      {/* M15-T11 — reverse a sale found after the shift closed */}
      <ReversalPanel tz={tz} dayStart={dayStart} />
    </div>
  );
}
