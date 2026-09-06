"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { useOwnerData } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import { dayLabel, daySubLabel, groupByDay, timeLabel } from "@/lib/dates";
import type {
  ApprovalRow,
  Business,
  CashMovementRow,
  ExpenseRow,
  Page,
  PnlMonth,
  ReceiptRow,
  ShiftRow,
} from "@/lib/types";
import { DayHeader, EmptyState, ErrorState, Glass, Plate, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconReceipt } from "@/components/icons";

const MONTH_LABEL = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

function monthName(ym: string): string {
  const m = Number(ym.slice(5)) - 1;
  return MONTH_LABEL[m] ?? ym;
}

const compact = new Intl.NumberFormat("id-ID", { notation: "compact" });

const CASH_KIND_LABEL: Record<CashMovementRow["kind"], string> = {
  cash_in: "Kas masuk",
  petty_cash: "Kas kecil",
  supplier_payment: "Bayar pemasok",
  bank_drop: "Setor bank",
};

const APPROVAL_LABEL: Record<string, string> = { discount: "diskon", void: "batal", refund: "refund" };
const ROLE_LABEL: Record<string, string> = { owner: "pemilik", manager: "manajer", staff: "staf" };

export default function MoneyPage() {
  const [expensePage, setExpensePage] = useState(1);
  const pnl = useOwnerData<PnlMonth[]>("/api/pnl?months=6");
  const expenses = useOwnerData<Page<ExpenseRow>>(`/api/expenses?page=${expensePage}&page_size=20`);
  const receipts = useOwnerData<Page<ReceiptRow>>("/api/receipts?page=1&page_size=6");
  const shifts = useOwnerData<ShiftRow[]>("/api/shifts?limit=12");
  const cash = useOwnerData<CashMovementRow[]>("/api/cash-movements?limit=200");
  const approvals = useOwnerData<ApprovalRow[]>("/api/approvals?limit=30");
  const business = useOwnerData<Business>("/api/business");
  const tz = business.data?.timezone;
  const dayStart = business.data?.day_start_hour ?? 0;

  const current = pnl.data?.[pnl.data.length - 1];
  const expenseTotalPages = expenses.data
    ? Math.max(1, Math.ceil(expenses.data.total / expenses.data.page_size))
    : 1;
  const expenseGroups = groupByDay(expenses.data?.rows ?? [], (e) => new Date(e.occurred_at), tz, dayStart);

  return (
    <div className="animate-fade-up space-y-7">
      <header>
        <h1 className="text-[1.65rem] font-bold tracking-tight md:text-3xl">Keuangan</h1>
        {current && (
          <p className="ink-soft mt-1 text-sm">
            Bulan ini <span className="font-semibold tabular-nums">{formatRupiah(current.revenue)}</span> masuk
            {" · "}
            <span className="font-semibold tabular-nums">{formatRupiah(current.expenses)}</span> keluar
          </p>
        )}
      </header>

      {/* HERO — P&L */}
      <Glass className="px-6 pb-5 pt-6 md:px-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-base font-bold">
            Untung / rugi 6 bulan
            <HelpTip title="Untung / rugi per bulan">
              Hijau = penjualan; oranye = pengeluaran tercatat (manual + hasil foto nota). Angka
              di bawah tiap bulan = selisihnya. Makin lengkap nota difoto, makin akurat.
            </HelpTip>
          </h2>
          {/* Legend — identity never by color alone (labels beside marks) */}
          <div className="flex items-center gap-4 text-xs font-medium">
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: "var(--chart-1)" }} />
              Masuk
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: "var(--chart-2)" }} />
              Keluar
            </span>
          </div>
        </div>
        <div className="mt-2 h-56 w-full">
          {pnl.data ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={pnl.data} margin={{ top: 8, left: 4, right: 4 }} barGap={2}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis
                  dataKey="month"
                  tickFormatter={monthName}
                  tick={{ fontSize: 12, fill: "var(--ink-faint)" }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: "var(--chart-grid)" }}
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="plate px-3 py-2 text-xs shadow-pop">
                        <p className="ink-soft">{(payload[0].payload as PnlMonth).month}</p>
                        <p className="font-semibold tabular-nums">
                          masuk {formatRupiah((payload[0].payload as PnlMonth).revenue)}
                        </p>
                        <p className="ink-soft tabular-nums">
                          keluar {formatRupiah((payload[0].payload as PnlMonth).expenses)}
                        </p>
                        <p className="font-bold tabular-nums">
                          net {formatRupiah((payload[0].payload as PnlMonth).net)}
                        </p>
                      </div>
                    ) : null
                  }
                />
                <Bar dataKey="revenue" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
                <Bar dataKey="expenses" fill="var(--chart-2)" radius={[4, 4, 0, 0]} maxBarSize={28} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Skeleton className="h-44" />
          )}
        </div>
        {pnl.data && (
          <div className="mt-2 grid grid-cols-6 gap-1">
            {pnl.data.map((m) => (
              <p key={m.month} className="text-center">
                <span
                  className={`text-xs font-bold tabular-nums ${
                    m.net >= 0 ? "text-[color:var(--good)]" : "text-[color:var(--bad)]"
                  }`}
                >
                  {m.net >= 0 ? "+" : "−"}
                  {compact.format(Math.abs(m.net))}
                </span>
              </p>
            ))}
          </div>
        )}
      </Glass>


      {/* Till report (M7-T3) — every closed shift with the sum its count was
          checked against, and the cash that moved through it. */}
      <section>
        <h2 className="flex items-center gap-2 text-base font-bold">
          Shift &amp; kas laci
          <HelpTip title="Kas seharusnya">
            Modal awal + penjualan tunai − refund tunai + kas masuk − kas keluar. Selisihnya
            (uang dihitung − kas seharusnya) langsung masuk pembukuan, jadi laporan untung/rugi
            ikut menghitung kas yang hilang atau lebih.
          </HelpTip>
        </h2>
        {shifts.loading ? (
          <Skeleton className="mt-3 h-40" />
        ) : shifts.error && !shifts.data ? (
          <Plate className="mt-3">
            <ErrorState onRetry={shifts.reload} />
          </Plate>
        ) : !shifts.data || shifts.data.length === 0 ? (
          <Plate className="mt-3">
            <EmptyState emoji="🧾" title="Belum ada shift">
              Kasir membuka shift dari kios POS dengan modal awal, lalu menghitung uang laci saat
              tutup. Hasilnya muncul di sini.
            </EmptyState>
          </Plate>
        ) : (
          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {shifts.data.map((shift) => {
              const variance = Number(shift.variance ?? 0);
              const open = shift.status === "open";
              const movements = (cash.data ?? []).filter((m) => m.shift_id === shift.id);
              return (
                <Plate key={shift.id} className="px-5 py-4">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="truncate text-sm font-semibold">{shift.staff_name}</p>
                    <p className="ink-faint shrink-0 text-xs">
                      {dayLabel(new Date(shift.opened_at), tz, dayStart)} · {timeLabel(new Date(shift.opened_at), tz)}
                      {shift.closed_at ? `–${timeLabel(new Date(shift.closed_at), tz)}` : ""}
                    </p>
                  </div>
                  <dl className="mt-3 space-y-1 text-sm">
                    <div className="flex justify-between">
                      <dt className="ink-soft">Modal awal</dt>
                      <dd className="tabular-nums">{formatRupiah(shift.opening_float)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="ink-soft">Penjualan tunai</dt>
                      <dd className="tabular-nums">+ {formatRupiah(shift.cash_sales)}</dd>
                    </div>
                    {Number(shift.cash_refunds) > 0 && (
                      <div className="flex justify-between">
                        <dt className="ink-soft">Refund tunai</dt>
                        <dd className="tabular-nums">− {formatRupiah(shift.cash_refunds)}</dd>
                      </div>
                    )}
                    {Number(shift.cash_in) > 0 && (
                      <div className="flex justify-between">
                        <dt className="ink-soft">Kas masuk</dt>
                        <dd className="tabular-nums">+ {formatRupiah(shift.cash_in)}</dd>
                      </div>
                    )}
                    {Number(shift.cash_out) > 0 && (
                      <div className="flex justify-between">
                        <dt className="ink-soft">Kas keluar</dt>
                        <dd className="tabular-nums">− {formatRupiah(shift.cash_out)}</dd>
                      </div>
                    )}
                    <div className="hairline-t flex justify-between pt-1">
                      <dt className="font-medium">Kas seharusnya</dt>
                      <dd className="font-semibold tabular-nums">{formatRupiah(shift.expected_cash ?? 0)}</dd>
                    </div>
                    {open ? (
                      <p className="ink-faint pt-1 text-xs">Masih buka — belum dihitung.</p>
                    ) : (
                      <>
                        <div className="flex justify-between">
                          <dt className="ink-soft">Uang dihitung</dt>
                          <dd className="tabular-nums">{formatRupiah(shift.counted_cash ?? 0)}</dd>
                        </div>
                        <div className="flex justify-between">
                          <dt className="font-medium">Selisih</dt>
                          <dd
                            className={`font-bold tabular-nums ${
                              variance < 0 ? "text-red-600" : variance > 0 ? "text-amber-600" : "ink-soft"
                            }`}
                          >
                            {variance === 0
                              ? "pas"
                              : `${variance > 0 ? "+ " : "− "}${formatRupiah(Math.abs(variance))}`}
                          </dd>
                        </div>
                      </>
                    )}
                  </dl>
                  {shift.notes && <p className="ink-faint mt-2 text-xs italic">“{shift.notes}”</p>}
                  {movements.length > 0 && (
                    <ul className="hairline-t mt-3 space-y-1 pt-2">
                      {movements.map((m) => (
                        <li key={m.id} className="flex justify-between gap-2 text-xs">
                          <span className="ink-soft truncate">
                            {CASH_KIND_LABEL[m.kind]}
                            {m.supplier_name ? ` · ${m.supplier_name}` : ""} — {m.reason}
                          </span>
                          <span className="shrink-0 tabular-nums">
                            {m.direction === "in" ? "+ " : "− "}
                            {formatRupiah(m.amount)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Plate>
              );
            })}
          </div>
        )}
      </section>

      {/* Manager overrides (M15-T7) — who authorised what, and for how much */}
      <section>
        <h2 className="flex items-center gap-2 text-base font-bold">
          Otorisasi manajer
          <HelpTip title="Kenapa ini dicatat">
            Batal transaksi, refund dan diskon perlu PIN pemilik atau manajer. Setiap persetujuan
            dicatat di sini: siapa yang menyetujui, peran dia saat itu, siapa yang minta, dan berapa
            nilainya. Kalau ada yang tidak kamu kenali, tanyakan hari itu juga.
          </HelpTip>
        </h2>
        {approvals.loading ? (
          <Skeleton className="mt-3 h-32" />
        ) : (approvals.data ?? []).length === 0 ? (
          <Plate className="mt-3">
            <EmptyState emoji="🔐" title="Belum ada otorisasi">
              Belum ada transaksi yang dibatalkan, direfund, atau didiskon dengan PIN. Kalau nanti
              ada, semuanya muncul di sini.
            </EmptyState>
          </Plate>
        ) : (
          <ul className="mt-3">
            {(approvals.data ?? []).map((a) => (
              <li key={a.id} className="list-row">
                <span
                  className="shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold"
                  style={{
                    background: a.action === "discount" ? "var(--warn-bg)" : "var(--bad-bg)",
                    color: a.action === "discount" ? "var(--warn)" : "var(--bad)",
                  }}
                >
                  {APPROVAL_LABEL[a.action]}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">
                    {a.approver_name}
                    <span className="ink-faint font-normal"> ({ROLE_LABEL[a.approver_role] ?? a.approver_role})</span>
                  </p>
                  <p className="ink-faint truncate text-xs">
                    {a.requested_by_name ? `diminta ${a.requested_by_name} · ` : ""}
                    {dayLabel(new Date(a.created_at), tz, dayStart)} {timeLabel(new Date(a.created_at), tz)}
                    {a.note ? ` · “${a.note}”` : ""}
                  </p>
                </div>
                <span className="shrink-0 text-sm font-semibold tabular-nums">
                  {a.amount ? formatRupiah(a.amount) : "—"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid gap-8 lg:grid-cols-5">
        {/* Expenses — day-grouped open rows */}
        <section className="lg:col-span-3">
          <h2 className="text-base font-bold">Pengeluaran</h2>
          {expenses.loading ? (
            <Skeleton className="mt-3 h-56" />
          ) : expenses.error && !expenses.data ? (
            <Plate className="mt-3">
              <ErrorState onRetry={expenses.reload} />
            </Plate>
          ) : !expenses.data || expenses.data.rows.length === 0 ? (
            <Plate className="mt-3">
              <EmptyState emoji="🗒️" title="Belum ada pengeluaran">
                Foto nota belanja ke asisten WhatsApp, atau ketik saja &ldquo;tadi beli gas
                88rb&rdquo; — semua tercatat di sini.
              </EmptyState>
            </Plate>
          ) : (
            <>
              {expenseGroups.map((group) => {
                const dayTotal = group.rows.reduce((s, r) => s + Number(r.amount), 0);
                return (
                  <div key={group.key}>
                    <DayHeader
                      label={group.label}
                      sub={daySubLabel(group.date, tz, dayStart)}
                      meta={`− ${formatRupiah(dayTotal)}`}
                    />
                    <ul>
                      {group.rows.map((expense) => (
                        <li key={expense.id} className="list-row">
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium">
                              {expense.description || expense.category || "Pengeluaran"}
                            </p>
                            <p className="ink-faint text-xs">
                              {expense.category ?? "lainnya"}
                              {expense.source === "receipt" && " · dari foto nota 🧾"}
                            </p>
                          </div>
                          <span className="shrink-0 text-sm font-semibold tabular-nums">
                            − {formatRupiah(expense.amount)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
              {expenseTotalPages > 1 && (
                <div className="mt-4 flex items-center justify-between">
                  <button
                    className="btn-quiet px-3 py-1.5 text-sm disabled:opacity-40"
                    disabled={expensePage <= 1}
                    onClick={() => setExpensePage((p) => p - 1)}
                  >
                    ← Lebih baru
                  </button>
                  <span className="ink-soft text-sm tabular-nums">
                    {expensePage} / {expenseTotalPages}
                  </span>
                  <button
                    className="btn-quiet px-3 py-1.5 text-sm disabled:opacity-40"
                    disabled={expensePage >= expenseTotalPages}
                    onClick={() => setExpensePage((p) => p + 1)}
                  >
                    Lebih lama →
                  </button>
                </div>
              )}
            </>
          )}
        </section>

        {/* Receipts */}
        <section className="lg:col-span-2">
          <h2 className="text-base font-bold">Nota terakhir</h2>
          {receipts.loading ? (
            <Skeleton className="mt-3 h-56" />
          ) : receipts.error && !receipts.data ? (
            <Plate className="mt-3">
              <ErrorState onRetry={receipts.reload} />
            </Plate>
          ) : !receipts.data || receipts.data.rows.length === 0 ? (
            <Plate className="mt-3">
              <EmptyState emoji="📸" title="Belum ada nota">
                Nota yang difoto lewat WhatsApp tampil di sini lengkap dengan hasil bacaannya.
              </EmptyState>
            </Plate>
          ) : (
            <ul className="mt-1">
              {receipts.data.rows.map((receipt) => (
                <li key={receipt.id} className="list-row">
                  {receipt.image_signed_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={receipt.image_signed_url}
                      alt=""
                      className="h-10 w-10 shrink-0 rounded-xl object-cover"
                    />
                  ) : (
                    <span
                      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
                      style={{ background: "var(--accent-soft)" }}
                    >
                      <IconReceipt className="h-5 w-5 text-[color:var(--accent)]" />
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
