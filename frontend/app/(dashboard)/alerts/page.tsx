"use client";

import { useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import type { AlertRow } from "@/lib/types";
import { EmptyState, Glass, SectionTitle, SeverityBadge, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconCheck } from "@/components/icons";

export default function AlertsPage() {
  const alerts = useOwnerData<AlertRow[]>("/api/alerts?limit=100");
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

  return (
    <div className="animate-fade-up space-y-8">
      <header>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight md:text-3xl">
          Peringatan
          <HelpTip title="Dari mana peringatan ini?">
            Tiap malam sistem membandingkan penjualan &amp; pengeluaran hari itu dengan rata-rata
            30 hari (anomali), dan mengecek stok yang bakal habis. Peringatan penting juga
            dikirim ke WhatsApp.
          </HelpTip>
        </h1>
        <p className="ink-soft mt-1 text-sm">
          Dihitung otomatis tiap malam — bukan tebakan AI, murni dari data kasir &amp; nota.
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
              <SectionTitle>Perlu ditindak ({open.length})</SectionTitle>
              <ul className="space-y-2">
                {open.map((alert) => (
                  <li key={alert.id}>
                    <Glass className="flex items-center gap-4 px-5 py-4">
                      <span className="text-xl" aria-hidden>
                        {alert.type === "low_stock" ? "📦" : "📈"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">{alert.message}</p>
                        <p className="ink-faint mt-0.5 text-xs">
                          {alert.type === "low_stock" ? "stok menipis" : "anomali"}
                          {" · "}
                          {new Date(alert.created_at).toLocaleDateString("id-ID", {
                            day: "numeric",
                            month: "long",
                          })}
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
                    </Glass>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {done.length > 0 && (
            <section>
              <SectionTitle>Sudah dibaca</SectionTitle>
              <ul className="space-y-1.5 opacity-60">
                {done.slice(0, 20).map((alert) => (
                  <li
                    key={alert.id}
                    className="flex items-center justify-between gap-4 rounded-2xl px-4 py-3"
                    style={{ border: "1px solid var(--hairline)" }}
                  >
                    <p className="min-w-0 truncate text-sm">{alert.message}</p>
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
