"use client";

import { useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { daySubLabel, groupByDay } from "@/lib/dates";
import type { AlertRow, Business } from "@/lib/types";
import { AlertIcon, DayGroup, EmptyState, ErrorState, Glass, SeverityBadge, Skeleton } from "@/components/ui";
import { HistoryFilters } from "@/components/HistoryFilters";
import { HelpTip } from "@/components/HelpTip";
import { IconBell, IconCheck } from "@/components/icons";

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
  // "beres" answers at once: the row folds away while the server is told,
  // and comes back with a note if the server says no.
  const [leaving, setLeaving] = useState<Set<string>>(new Set());
  const [ackError, setAckError] = useState<string | null>(null);

  async function acknowledge(id: string) {
    setAckError(null);
    setLeaving((s) => new Set(s).add(id));
    const folded = new Promise((r) => window.setTimeout(r, 340));
    try {
      await Promise.all([mutate(`/api/alerts/${id}/ack`), folded]);
      alerts.reload();
    } catch {
      setLeaving((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
      setAckError("Peringatan itu belum tertandai. Coba tekan beres lagi.");
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

      {ackError && (
        <p className="notice notice-bad" role="alert">
          {ackError}
        </p>
      )}

      {alerts.loading && !alerts.data ? (
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
        <div
          className={
            open.length > 0 && done.length > 0
              ? "grid items-start gap-x-6 gap-y-7 min-[1440px]:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]"
              : "max-w-5xl"
          }
        >
          {open.length > 0 && (
            <section className="min-w-0">
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
                      <li
                        key={alert.id}
                        className="list-row flex-wrap sm:flex-nowrap"
                        data-leaving={leaving.has(alert.id) || undefined}
                        style={{ ["--row-inset" as string]: "4.25rem" }}
                      >
                        <AlertIcon type={alert.type} severity={alert.severity} />
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
                          disabled={leaving.has(alert.id)}
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
            <section className="min-w-0">
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
        </div>
      )}
    </div>
  );
}
