"use client";

import { useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { daySubLabel, groupByDay } from "@/lib/dates";
import type { AlertRow, Business } from "@/lib/types";
import { DayGroup, EmptyState, ErrorState, Glass, SeverityBadge, Skeleton } from "@/components/ui";
import { HistoryFilters } from "@/components/HistoryFilters";
import { HelpTip } from "@/components/HelpTip";
import { IconBell, IconBox, IconCheck, IconTrendUp } from "@/components/icons";

const ALERT_LABEL: Record<string, string> = {
  anomaly: "anomali penjualan",
  low_stock: "stok menipis",
  margin_drop: "margin turun",
  stockout_risk: "stok habis sebelum kiriman",
  void_rate: "pembatalan kasir",
  supplier_price: "harga supplier berubah",
};

const SEVERITIES = [
  { value: "high", label: "Penting" },
  { value: "medium", label: "Sedang" },
  { value: "low", label: "Ringan" },
];

export default function AlertsPage() {
  const [severity, setSeverity] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const filterQuery =
    (severity ? `&severity=${severity}` : "") +
    (since ? `&since=${since}` : "") +
    (until ? `&until=${until}` : "");
  const filtered = filterQuery !== "";
  const [openDays, setOpenDays] = useState<Record<string, boolean>>({});
  const alerts = useOwnerData<AlertRow[]>(`/api/alerts?limit=100${filterQuery}`);
  const business = useOwnerData<Business>("/api/business");
  const tz = business.data?.timezone;
  const dayStart = business.data?.day_start_hour ?? 0;
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
  const openGroups = groupByDay(open, (a) => new Date(a.created_at), tz, dayStart);
  const openByDefault = (label: string) => label === "Hari ini" || label === "Kemarin";
  const dayOpen = (key: string, label: string) => openDays[key] ?? openByDefault(label);
  const toggleDay = (key: string, label: string) =>
    setOpenDays((o) => ({ ...o, [key]: !(o[key] ?? openByDefault(label)) }));

  return (
    <div className="animate-fade-up space-y-7">
      <header>
        <h1 className="flex items-center gap-2 page-title">
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

      <HistoryFilters
        optionLabel="Tingkat"
        options={SEVERITIES}
        value={severity}
        onValue={setSeverity}
        since={since}
        until={until}
        onRange={(a, b) => {
          setSince(a);
          setUntil(b);
        }}
        onReset={() => {
          setSeverity("");
          setSince("");
          setUntil("");
        }}
      />

      {alerts.loading ? (
        <Skeleton className="h-64" />
      ) : alerts.error && !alerts.data ? (
        <Glass>
          <ErrorState onRetry={alerts.reload} />
        </Glass>
      ) : open.length === 0 && done.length === 0 ? (
        <Glass>
          <EmptyState
            icon={<IconBell className="h-5 w-5" />}
            title={filtered ? "Tidak ada peringatan yang cocok" : "Belum ada peringatan"}
          >
            {filtered
              ? "Coba ubah tanggal atau pilih tingkat lain."
              : "Kalau ada penjualan yang aneh atau stok yang menipis, kabarnya muncul di sini dan di WhatsApp."}
          </EmptyState>
        </Glass>
      ) : (
        <>
          {open.length > 0 && (
            <section>
              <div className="mb-3 flex items-baseline gap-2">
                <h2 className="section-title">Perlu ditindak</h2>
                <span className="pill-warn tabular-nums">{open.length}</span>
              </div>
              {openGroups.map((group) => (
                <DayGroup
                  key={group.key}
                  label={group.label}
                  sub={daySubLabel(group.date, tz, dayStart)}
                  meta={`${group.rows.length} peringatan`}
                  open={dayOpen(group.key, group.label)}
                  onToggle={() => toggleDay(group.key, group.label)}
                  count={group.rows.length}
                >
                    {group.rows.map((alert) => (
                      <li key={alert.id} className="list-row flex-wrap sm:flex-nowrap" style={{ ["--row-inset" as string]: "4.25rem" }}>
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
                            {ALERT_LABEL[alert.type] ?? alert.type}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2 max-sm:w-full max-sm:justify-between max-sm:pl-[3.25rem]">
                        <SeverityBadge severity={alert.severity} />
                        <button
                          onClick={() => acknowledge(alert.id)}
                          disabled={acking === alert.id}
                          className="btn-quiet shrink-0 px-3 py-2 text-xs font-semibold"
                          title="Tandai sudah dibaca"
                        >
                          <IconCheck className="h-4 w-4" /> beres
                        </button>
                        </div>
                      </li>
                    ))}
                </DayGroup>
              ))}
            </section>
          )}

          {done.length > 0 && (
            <section>
              <h2 className="mb-3 section-title">Sudah dibaca</h2>
              <ul className="group-card group-body">
                {done.slice(0, 20).map((alert) => (
                  <li key={alert.id} className="list-row" style={{ ["--row-inset" as string]: "2.75rem" }}>
                    <IconCheck className="ink-faint h-4 w-4 shrink-0" />
                    <p className="ink-soft min-w-0 flex-1 truncate text-sm">{alert.message}</p>
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
