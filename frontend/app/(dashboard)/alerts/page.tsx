"use client";

import { useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { daySubLabel, groupByDay } from "@/lib/dates";
import type { AlertRow, Business } from "@/lib/types";
import { DayHeader, EmptyState, Glass, SeverityBadge, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconBell, IconBox, IconCheck, IconTrendUp } from "@/components/icons";

export default function AlertsPage() {
  const alerts = useOwnerData<AlertRow[]>("/api/alerts?limit=100");
  const business = useOwnerData<Business>("/api/business");
  const tz = business.data?.timezone;
  const mutate = useOwnerMutation();
  const [acking, setAcking] = useState<string | null>(null);

  async function acknowledge(id: string) {
    setAcking(id);
    try {
      await mutate(`/api/alerts/${id}/ack`);
      alerts.reload();
    } finally {
      setAcking(null);
    }
  }

  const open = (alerts.data ?? []).filter((a) => !a.is_acknowledged);
  const done = (alerts.data ?? []).filter((a) => a.is_acknowledged);
  const openGroups = groupByDay(open, (a) => new Date(a.created_at), tz);

  return (
    <div className="animate-fade-up space-y-7">
      <header>
        <h1 className="flex items-center gap-2 text-[1.65rem] font-bold tracking-tight md:text-3xl">
          Peringatan
          <HelpTip title="Dari mana peringatan ini?">
            Tiap malam sistem membandingkan penjualan &amp; pengeluaran hari itu dengan rata-rata
            30 hari (anomali), dan mengecek stok yang bakal habis. Yang penting juga dikirim ke
            WhatsApp.
          </HelpTip>
        </h1>
        <p className="ink-soft mt-1 text-sm">
          Dihitung otomatis tiap malam — murni dari data kasir &amp; nota, bukan tebakan AI.
        </p>
      </header>

      {alerts.loading ? (
        <Skeleton className="h-64" />
      ) : open.length === 0 && done.length === 0 ? (
        <Glass>
          <EmptyState emoji="🔔" title="Belum ada peringatan">
            Kalau ada penjualan yang aneh atau stok yang menipis, kabarnya muncul di sini dan di
            WhatsApp.
          </EmptyState>
        </Glass>
      ) : (
        <>
          {open.length > 0 && (
            <section>
              <div className="mb-1 flex items-baseline gap-2">
                <h2 className="text-base font-bold">Perlu ditindak</h2>
                <span className="pill-warn">{open.length}</span>
              </div>
              {openGroups.map((group) => (
                <div key={group.key}>
                  <DayHeader label={group.label} sub={daySubLabel(group.date, tz)} />
                  <ul>
                    {group.rows.map((alert) => (
                      <li key={alert.id} className="list-row">
                        <span
                          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
                          style={{
                            background: alert.severity === "high" ? "var(--bad-bg)" : "var(--warn-bg)",
                            color: alert.severity === "high" ? "var(--bad)" : "var(--warn)",
                          }}
                          aria-hidden
                        >
                          {alert.type === "low_stock" ? (
                            <IconBox className="h-5 w-5" />
                          ) : (
                            <IconTrendUp className="h-5 w-5" />
                          )}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">{alert.message}</p>
                          <p className="ink-faint mt-0.5 text-xs">
                            {alert.type === "low_stock" ? "stok menipis" : "anomali penjualan"}
                          </p>
                        </div>
                        <SeverityBadge severity={alert.severity} />
                        <button
                          onClick={() => acknowledge(alert.id)}
                          disabled={acking === alert.id}
                          className="btn-quiet shrink-0 px-3 py-2 text-xs font-semibold"
                          title="Tandai sudah dibaca"
                        >
                          <IconCheck className="h-4 w-4" /> beres
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          )}

          {done.length > 0 && (
            <section>
              <h2 className="mb-1 text-base font-bold">Sudah dibaca</h2>
              <ul className="opacity-55">
                {done.slice(0, 20).map((alert) => (
                  <li key={alert.id} className="list-row">
                    <IconBell className="ink-faint h-4 w-4 shrink-0" />
                    <p className="min-w-0 flex-1 truncate text-sm">{alert.message}</p>
                    <span className="ink-faint shrink-0 text-xs">
                      {new Date(alert.created_at).toLocaleDateString("id-ID", {
                        day: "numeric",
                        month: "short",
                      })}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
