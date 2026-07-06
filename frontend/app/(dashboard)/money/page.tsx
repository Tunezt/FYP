"use client";

import { useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { useOwnerData } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import type { ExpenseRow, Page, PnlMonth, ReceiptRow } from "@/lib/types";
import { EmptyState, Glass, Plate, SectionTitle, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconReceipt } from "@/components/icons";

const MONTH_LABEL = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

function monthName(ym: string): string {
  const m = Number(ym.slice(5)) - 1;
  return MONTH_LABEL[m] ?? ym;
}

export default function MoneyPage() {
  const [expensePage, setExpensePage] = useState(1);
  const pnl = useOwnerData<PnlMonth[]>("/api/pnl?months=6");
  const expenses = useOwnerData<Page<ExpenseRow>>(`/api/expenses?page=${expensePage}&page_size=15`);
  const receipts = useOwnerData<Page<ReceiptRow>>("/api/receipts?page=1&page_size=8");

  const current = pnl.data?.[pnl.data.length - 1];
  const expenseTotalPages = expenses.data
    ? Math.max(1, Math.ceil(expenses.data.total / expenses.data.page_size))
    : 1;

  return (
    <div className="animate-fade-up space-y-8">
      <header>
        <h1 className="text-2xl font-bold tracking-tight md:text-3xl">Keuangan</h1>
        {current && (
          <p className="ink-soft mt-1 text-sm">
            Bulan ini: {formatRupiah(current.revenue)} masuk · {formatRupiah(current.expenses)}{" "}
            keluar
          </p>
        )}
      </header>

      {/* P&L chart */}
      <Glass className="px-6 pb-4 pt-5">
        <SectionTitle
          hint={
            <HelpTip title="Untung / rugi per bulan">
              Batang ungu = penjualan; batang abu = pengeluaran tercatat (manual + hasil foto
              nota). Angka di bawah = selisihnya. Semakin lengkap nota difoto, semakin akurat.
            </HelpTip>
          }
        >
          Untung / rugi 6 bulan
        </SectionTitle>
        <div className="h-56 w-full">
          {pnl.data ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={pnl.data} margin={{ top: 8, left: 4, right: 4 }} barGap={3}>
                <XAxis
                  dataKey="month"
                  tickFormatter={monthName}
                  tick={{ fontSize: 12, fill: "var(--ink-faint)" }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: "var(--hairline)" }}
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="glass-card glass-strong px-3 py-2 text-xs">
                        <p className="ink-soft">{(payload[0].payload as PnlMonth).month}</p>
                        <p className="font-semibold text-accent-500">
                          masuk {formatRupiah((payload[0].payload as PnlMonth).revenue)}
                        </p>
                        <p className="ink-soft">
                          keluar {formatRupiah((payload[0].payload as PnlMonth).expenses)}
                        </p>
                        <p className="font-bold">
                          net {formatRupiah((payload[0].payload as PnlMonth).net)}
                        </p>
                      </div>
                    ) : null
                  }
                />
                <Bar dataKey="revenue" fill="#5e5ce6" radius={[6, 6, 0, 0]} maxBarSize={34} />
                <Bar dataKey="expenses" fill="var(--ink-faint)" radius={[6, 6, 0, 0]} maxBarSize={34} opacity={0.5} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Skeleton className="h-44" />
          )}
        </div>
        {pnl.data && (
          <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
            {pnl.data.map((m) => (
              <p key={m.month} className="text-center">
                <span
                  className={`text-xs font-bold tabular-nums ${m.net >= 0 ? "text-emerald-600" : "text-red-500"}`}
                >
                  {m.net >= 0 ? "+" : "−"}
                  {new Intl.NumberFormat("id-ID", { notation: "compact" }).format(Math.abs(m.net))}
                </span>
              </p>
            ))}
          </div>
        )}
      </Glass>

      <div className="grid gap-6 lg:grid-cols-5">
        {/* Expenses list */}
        <section className="lg:col-span-3">
          <SectionTitle>Pengeluaran</SectionTitle>
          {expenses.loading ? (
            <Skeleton className="h-56" />
          ) : !expenses.data || expenses.data.rows.length === 0 ? (
            <Glass>
              <EmptyState emoji="🗒️" title="Belum ada pengeluaran">
                Foto nota belanja ke asisten WhatsApp, atau ketik saja &ldquo;tadi beli gas
                88rb&rdquo; — semua tercatat di sini.
              </EmptyState>
            </Glass>
          ) : (
            <>
              <ul className="space-y-1.5">
                {expenses.data.rows.map((expense) => (
                  <li
                    key={expense.id}
                    className="flex items-center justify-between gap-4 rounded-2xl px-4 py-3"
                    style={{ border: "1px solid var(--hairline)" }}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {expense.description || expense.category || "Pengeluaran"}
                      </p>
                      <p className="ink-faint text-xs">
                        {new Date(expense.occurred_at).toLocaleDateString("id-ID", {
                          day: "numeric",
                          month: "short",
                        })}
                        {" · "}
                        {expense.category ?? "lainnya"}
                        {expense.source === "receipt" && " · dari foto nota 🧾"}
                      </p>
                    </div>
                    <span className="shrink-0 font-semibold tabular-nums">
                      − {formatRupiah(expense.amount)}
                    </span>
                  </li>
                ))}
              </ul>
              {expenseTotalPages > 1 && (
                <div className="mt-3 flex items-center justify-between">
                  <button
                    className="btn-quiet px-3 py-1.5 text-sm disabled:opacity-40"
                    disabled={expensePage <= 1}
                    onClick={() => setExpensePage((p) => p - 1)}
                  >
                    ←
                  </button>
                  <span className="ink-soft text-sm tabular-nums">
                    {expensePage} / {expenseTotalPages}
                  </span>
                  <button
                    className="btn-quiet px-3 py-1.5 text-sm disabled:opacity-40"
                    disabled={expensePage >= expenseTotalPages}
                    onClick={() => setExpensePage((p) => p + 1)}
                  >
                    →
                  </button>
                </div>
              )}
            </>
          )}
        </section>

        {/* Receipts */}
        <section className="lg:col-span-2">
          <SectionTitle>Nota terakhir</SectionTitle>
          {receipts.loading ? (
            <Skeleton className="h-56" />
          ) : !receipts.data || receipts.data.rows.length === 0 ? (
            <Plate className="px-2 py-2">
              <EmptyState emoji="📸" title="Belum ada nota">
                Nota yang difoto lewat WhatsApp tampil di sini lengkap dengan hasil bacaannya.
              </EmptyState>
            </Plate>
          ) : (
            <ul className="space-y-1.5">
              {receipts.data.rows.map((receipt) => (
                <li
                  key={receipt.id}
                  className="flex items-center gap-3 rounded-2xl px-4 py-3"
                  style={{ border: "1px solid var(--hairline)" }}
                >
                  {receipt.image_signed_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={receipt.image_signed_url}
                      alt=""
                      className="h-11 w-11 rounded-xl object-cover"
                    />
                  ) : (
                    <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-gradient-soft">
                      <IconReceipt className="ink-soft h-5 w-5" />
                    </span>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {receipt.supplier || "Tanpa nama supplier"}
                    </p>
                    <p className="ink-faint text-xs">
                      {receipt.item_count} item
                      {receipt.occurred_at &&
                        ` · ${new Date(receipt.occurred_at).toLocaleDateString("id-ID", { day: "numeric", month: "short" })}`}
                    </p>
                  </div>
                  {receipt.total_amount && (
                    <span className="shrink-0 text-sm font-semibold tabular-nums">
                      {formatRupiah(receipt.total_amount)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
