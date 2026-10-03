"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { IconClose, IconPrinter } from "@/components/icons";
import { clockTime } from "@/lib/pos";
import { PrintDocument, type PrintDoc } from "@/components/pos/PrintDocument";

// The print queue at the till (prt-4). It says only what is known: a job is
// waiting, being sent, failed, uncertain (a device took it and never answered,
// or said the paper may or may not exist), delivered (the printer accepted it
// but could not confirm), or printed (the printer confirmed it, or a person
// did). Recovery is deliberate: a failed job can be retried; an uncertain one
// needs a marked reprint or a person's confirmation, because paper may already
// exist. A job waiting too long is held (prt-8): no printer takes it until a
// person lets it through (marked TERLAMBAT) or says it is not needed.

export type PrintStatus = "pending" | "held" | "sending" | "uncertain" | "delivered" | "printed" | "failed" | "cancelled";

export type PrintJob = {
  id: string;
  order_id: string;
  printer: "front" | "kitchen";
  kind: string;
  kind_label: string;
  copy_kind: "original" | "reprint";
  status: PrintStatus;
  order_label: string;
  table_label: string | null;
  attempts: number;
  claimed_by: string | null;
  error: string | null;
  confirmed_by_person: boolean;
  evidence: "printer_status" | "bytes_delivered" | null;
  released: boolean;
  withdrawn_by_person: boolean;
  created_at: string;
  claimed_at: string | null;
  printed_at: string | null;
  reprint_of: string | null;
};

export const PRINTER_LABEL = { front: "Printer depan", kitchen: "Printer dapur" } as const;

export const PRINT_STATUS: Record<PrintStatus, { text: string; cls: string }> = {
  pending: { text: "Menunggu printer", cls: "pill-quiet" },
  held: { text: "Tertahan", cls: "pill-warn" },
  sending: { text: "Sedang dikirim", cls: "pill-quiet" },
  uncertain: { text: "Belum pasti tercetak", cls: "pill-warn" },
  delivered: { text: "Terkirim ke printer", cls: "pill-quiet" },
  printed: { text: "Tercetak", cls: "pill-good" },
  failed: { text: "Gagal cetak", cls: "pill-bad" },
  cancelled: { text: "Ditarik", cls: "pill-quiet" },
};

// What a print bridge last said about its printer (prt-8).
export type PrintDevice = {
  printer: "front" | "kitchen";
  device: string;
  state: "ready" | "reachable" | "paper_low" | "paper_out" | "cover_open" | "offline" | "error" | "unknown";
  detail: string | null;
  version: string | null;
  last_seen_at: string;
  silent: boolean;
};

const DEVICE_STATE: Record<PrintDevice["state"], { text: string; tone: "good" | "warn" | "bad" | "quiet" }> = {
  ready: { text: "Siap", tone: "good" },
  reachable: { text: "Tersambung (status tidak dibaca)", tone: "quiet" },
  paper_low: { text: "Kertas hampir habis", tone: "warn" },
  paper_out: { text: "Kertas habis", tone: "bad" },
  cover_open: { text: "Tutup printer terbuka", tone: "bad" },
  offline: { text: "Tidak terjangkau", tone: "bad" },
  error: { text: "Error", tone: "bad" },
  unknown: { text: "Tidak diketahui", tone: "warn" },
};

export function deviceTrouble(d: PrintDevice): boolean {
  return d.silent || DEVICE_STATE[d.state].tone === "bad";
}

/** What to say about a printer whose bridge has stopped reporting, or whose
 *  printer it cannot reach. `silent` is decided by the server from the last
 *  heartbeat it received: a bridge that has been killed, frozen or unplugged
 *  cannot tell us itself, so nothing here depends on it doing so. */
export function bridgeAlert(d: PrintDevice): string | null {
  if (d.silent) {
    return `${PRINTER_LABEL[d.printer]}: bridge di tablet tidak melapor sejak ${clockTime(d.last_seen_at)}. Slip tidak akan keluar sampai bridge dijalankan lagi.`;
  }
  if (DEVICE_STATE[d.state].tone === "bad") {
    return `${PRINTER_LABEL[d.printer]}: ${DEVICE_STATE[d.state].text.toLowerCase()}${d.detail ? ` (${d.detail})` : ""}.`;
  }
  return null;
}

/** Per printer: the bridges still reporting; if none is, only the one heard
 *  from last, so a bridge that was replaced or renamed does not nag forever. */
function currentDevices(devices: PrintDevice[]): PrintDevice[] {
  return (["front", "kitchen"] as const).flatMap((printer) => {
    const mine = devices.filter((d) => d.printer === printer);
    const live = mine.filter((d) => !d.silent);
    if (live.length) return live;
    const last = [...mine].sort((a, b) => b.last_seen_at.localeCompare(a.last_seen_at))[0];
    return last ? [last] : [];
  });
}

export function usePrintQueue(token: string | null) {
  const [open, setOpen] = useState<PrintJob[] | null>(null);
  const [devices, setDevices] = useState<PrintDevice[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [jobs, seen] = await Promise.all([
        api<PrintJob[]>("/pos/print-jobs?scope=open", { token }),
        api<PrintDevice[]>("/pos/printers", { token }).catch(() => null),
      ]);
      setOpen(jobs);
      if (seen) setDevices(currentDevices(seen));
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Antrean cetak tidak bisa dimuat");
    }
  }, [token]);
  useEffect(() => {
    void load();
    const id = setInterval(() => document.visibilityState === "visible" && void load(), 6000);
    return () => clearInterval(id);
  }, [load]);
  const attention =
    (open ?? []).filter((j) => j.status === "failed" || j.status === "uncertain" || j.status === "held").length +
    devices.filter(deviceTrouble).length;
  const waiting = (open ?? []).filter((j) => j.status === "pending" || j.status === "sending").length;
  const alerts = devices.map(bridgeAlert).filter((a): a is string => a !== null);
  return { open, devices, error, load, attention, waiting, alerts };
}

/** One job's recovery buttons, shared by the queue and an order's detail. */
export function PrintJobActions({
  job,
  token,
  onChanged,
  onBrowserPrint,
  onPreview,
  compact = false,
}: {
  job: Pick<PrintJob, "id" | "status" | "printer">;
  token: string | null;
  onChanged: () => void;
  onBrowserPrint: (jobId: string) => void;
  onPreview?: (jobId: string) => void; // till-13: see the slip as it prints
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function act(path: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`/pos/print-jobs/${job.id}/${path}`, { token, body: {} });
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Belum tersimpan — coba lagi.");
      onChanged();
    } finally {
      setBusy(false);
    }
  }
  const btn = `btn-quiet ${compact ? "px-2.5 py-1.5 text-xs" : "px-3 py-2 text-sm"}`;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {onPreview && (
        <button onClick={() => onPreview(job.id)} disabled={busy} className={btn}>
          Lihat slip
        </button>
      )}
      {job.status === "held" && (
        <button onClick={() => act("release")} disabled={busy} className={btn} title="Dicetak dengan tanda TERLAMBAT">
          Cetak sekarang (terlambat)
        </button>
      )}
      {job.status === "failed" && (
        <button onClick={() => act("retry")} disabled={busy} className={btn}>
          Coba lagi
        </button>
      )}
      {(job.status === "pending" || job.status === "held" || job.status === "failed") && (
        <button
          onClick={() => onBrowserPrint(job.id)}
          disabled={busy}
          className={btn}
          title={job.printer === "kitchen" ? "Cadangan manual: dicetak di printer tablet ini, lalu diantar ke dapur" : "Cadangan manual: dialog cetak browser"}
        >
          Cetak manual
        </button>
      )}
      {job.status === "uncertain" && (
        <button onClick={() => act("confirm")} disabled={busy} className={btn}>
          Kertas sudah ada
        </button>
      )}
      {(job.status === "uncertain" || job.status === "printed" || job.status === "delivered" || job.status === "failed") && (
        <button onClick={() => act("reprint")} disabled={busy} className={btn}>
          Cetak ulang
        </button>
      )}
      {(job.status === "held" || job.status === "failed") && (
        <button onClick={() => act("withdraw")} disabled={busy} className={btn}>
          Tidak perlu dicetak
        </button>
      )}
      {error && <span className="text-xs text-[color:var(--bad)]">{error}</span>}
    </div>
  );
}

export function PrintQueueSheet({
  token,
  queue,
  onClose,
  onBrowserPrint,
  onPreview,
}: {
  token: string | null;
  queue: ReturnType<typeof usePrintQueue>;
  onClose: () => void;
  onBrowserPrint: (jobId: string) => void;
  onPreview?: (jobId: string) => void;
}) {
  const [tab, setTab] = useState<"open" | "recent">("open");
  const [recent, setRecent] = useState<PrintJob[] | null>(null);
  useEffect(() => {
    if (tab !== "recent") return;
    api<PrintJob[]>("/pos/print-jobs?scope=recent", { token }).then(setRecent).catch(() => setRecent([]));
  }, [tab, token, queue.open]);
  const rows = tab === "open" ? queue.open : recent;

  return (
    <div className="sheet-scrim z-[56]" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Antrean cetak" className="sheet-panel sm:max-w-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3 px-5 pb-2 pt-4">
          <div>
            <h2 className="text-[19px] font-semibold tracking-[-0.015em]">Antrean cetak</h2>
            <p className="ink-soft text-[13px]">
              Struk dan slip bar ke printer depan, slip dapur ke printer dapur. &ldquo;Tercetak&rdquo; hanya kalau printer melapor atau kamu konfirmasi.
            </p>
          </div>
          <button onClick={onClose} aria-label="Tutup" className="icon-btn ink-soft -mr-2 h-10 w-10 shrink-0 rounded-full">
            <IconClose className="h-5 w-5" />
          </button>
        </div>
        <div className="px-5">
          <div className="segmented flex w-full" role="tablist">
            <button role="tab" aria-selected={tab === "open"} onClick={() => setTab("open")} className="segmented-item min-h-[2.5rem] flex-1">
              Perlu perhatian <span className="tabular-nums">{queue.open?.length ?? 0}</span>
            </button>
            <button role="tab" aria-selected={tab === "recent"} onClick={() => setTab("recent")} className="segmented-item min-h-[2.5rem] flex-1">
              12 jam terakhir
            </button>
          </div>
          {queue.error && <p className="notice notice-warn mt-2 text-sm">{queue.error} — status di bawah mungkin tidak terbaru.</p>}
          <PrinterDevices devices={queue.devices} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5 pt-3">
          {rows === null ? (
            <p className="ink-soft py-8 text-center text-sm">Memuat…</p>
          ) : rows.length === 0 ? (
            <p className="ink-soft py-8 text-center text-sm">{tab === "open" ? "Tidak ada yang menunggu. Semua slip sudah tercetak atau ditarik." : "Belum ada cetakan."}</p>
          ) : (
            <ul className="hairline-t">
              {rows.map((j) => (
                <li key={j.id} className="hairline-b py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-[15px] font-semibold">
                        {j.table_label ? `${j.table_label} · ` : ""}
                        {j.order_label}
                      </p>
                      <p className="ink-soft text-[13px]">
                        {j.kind_label}
                        {j.copy_kind === "reprint" ? " · cetak ulang" : ""} · {PRINTER_LABEL[j.printer]} · {clockTime(j.created_at)}
                        {j.attempts > 1 ? ` · percobaan ke-${j.attempts}` : ""}
                      </p>
                      {j.error && (
                        <p className="text-[13px]" style={{ color: j.status === "pending" ? "var(--ink-soft)" : "var(--bad)" }}>
                          {j.error}
                        </p>
                      )}
                      {j.status === "held" && (
                        <p className="text-[13px]" style={{ color: "var(--warn)" }}>
                          Menunggu lebih dari 15 menit. Printer tidak mencetaknya sendiri supaya pesanan lama tidak keluar seperti pesanan baru.
                        </p>
                      )}
                      {j.status === "delivered" && (
                        <p className="ink-soft text-[13px]">Printer menerima slip ini, tapi tidak bisa memastikan kertasnya keluar.</p>
                      )}
                      {j.released && j.status !== "printed" && <p className="ink-soft text-[13px]">Akan dicetak dengan tanda TERLAMBAT.</p>}
                      {j.status === "uncertain" && !j.error && (
                        <p className="text-[13px]" style={{ color: "var(--warn)" }}>
                          Printer mengambil slip ini tapi tidak melapor. Cek kertasnya dulu sebelum mencetak ulang.
                        </p>
                      )}
                    </div>
                    <span className={`${PRINT_STATUS[j.status].cls} shrink-0`}>
                      {PRINT_STATUS[j.status].text}
                      {j.status === "printed" && j.confirmed_by_person ? " (dikonfirmasi)" : ""}
                    </span>
                  </div>
                  <div className="mt-2">
                    <PrintJobActions job={j} token={token} onChanged={() => void queue.load()} onBrowserPrint={onBrowserPrint} onPreview={onPreview} compact />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

/** One line per printer from what its bridge last reported. Nothing is shown
 *  until a bridge has reported at all, so a till without one looks as before. */
function PrinterDevices({ devices }: { devices: PrintDevice[] }) {
  if (devices.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1">
      {devices.map((d) => {
        const st = DEVICE_STATE[d.state];
        const color = d.silent ? "var(--warn)" : { good: "var(--good)", warn: "var(--warn)", bad: "var(--bad)", quiet: "var(--ink-soft)" }[st.tone];
        return (
          <li key={`${d.printer}:${d.device}`} className="flex flex-wrap items-baseline justify-between gap-x-3 text-[13px]">
            <span>
              <span className="font-semibold">{PRINTER_LABEL[d.printer]}</span>
              <span className="ink-faint"> · {d.device}</span>
            </span>
            <span style={{ color }}>
              {d.silent ? `Tidak melapor sejak ${clockTime(d.last_seen_at)}` : st.text}
              {!d.silent && d.detail && st.tone !== "good" && st.text !== d.detail ? ` — ${d.detail}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Manual fallback through the browser's print dialog. The job is taken, the
 *  slip is shown and printed, and then the person is asked. A closed dialog is
 *  not proof of paper, so nothing is marked printed without that answer. */
export function BrowserPrintSheet({
  jobId,
  token,
  onDone,
  previewOnly = false,
}: {
  jobId: string;
  token: string | null;
  onDone: () => void;
  previewOnly?: boolean; // till-13: look at any slip, printed or not, without taking it
}) {
  const [doc, setDoc] = useState<PrintDoc | null>(null);
  const [meta, setMeta] = useState<{ printer: "front" | "kitchen"; kind_label?: string; order_label?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asked, setAsked] = useState(false);
  const [busy, setBusy] = useState(false);
  // Preview first; the job is only taken when the dialog is actually opened.
  useEffect(() => {
    api<{ document: PrintDoc; printer: "front" | "kitchen"; kind_label?: string; order_label?: string }>(`/pos/print-jobs/${jobId}`, { token })
      .then((j) => {
        setDoc(j.document);
        setMeta({ printer: j.printer, kind_label: j.kind_label, order_label: j.order_label });
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.detail : "Slip tidak bisa diambil"));
  }, [jobId, token]);

  async function openDialog() {
    setBusy(true);
    setError(null);
    try {
      await api(`/pos/print-jobs/${jobId}/browser`, { token, body: {} });
      window.print();
      setAsked(true);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Slip tidak bisa diambil — coba lagi");
    } finally {
      setBusy(false);
    }
  }

  async function answer(ok: boolean) {
    setBusy(true);
    try {
      await api(`/pos/print-jobs/${jobId}/browser-result`, { token, body: { ok } });
      onDone();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Belum tersimpan — coba lagi");
      setBusy(false);
    }
  }

  return (
    <div className="sheet-scrim z-[65] items-center p-4 print:bg-transparent" onClick={() => !asked && onDone()}>
      <style>{`@media print { body * { visibility: hidden; } #browser-slip, #browser-slip * { visibility: visible; } #browser-slip { position: absolute; left: 0; top: 0; width: 80mm; } }`}</style>
      <div className="sheet-panel max-h-[92dvh] w-full overflow-y-auto sm:max-w-md print:shadow-none" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 pt-4 print:hidden">
          <p className="flex items-center gap-2 text-[17px] font-semibold">
            <IconPrinter className="h-5 w-5" /> {previewOnly ? (meta?.kind_label ?? "Lihat slip") : "Cetak manual lewat browser"}
          </p>
          <p className="ink-soft text-[13px]">
            {previewOnly
              ? `Seperti yang keluar di ${meta?.printer === "kitchen" ? "printer dapur" : "printer depan"}, kertas 80 mm.`
              : meta?.printer === "kitchen"
                ? "Slip dapur. Dicetak di printer yang tersambung ke tablet ini (biasanya printer depan) — antar kertasnya ke dapur."
                : "Cadangan kalau printer belum tersambung otomatis. Pilih printer depan di dialog cetak."}
          </p>
        </div>
        {error && <p className="notice notice-bad mx-5 mt-3 print:hidden">{error}</p>}
        {doc && (
          <div className="flex justify-center bg-[color:var(--surface-inset)] px-3 py-4 print:bg-white print:p-0">
            <PrintDocument doc={doc} id="browser-slip" />
          </div>
        )}
        <div className="px-5 pb-5 pt-3 print:hidden">
          {previewOnly ? (
            <button onClick={onDone} className="btn-quiet w-full py-3">
              Tutup
            </button>
          ) : !asked ? (
            <div className="flex gap-2">
              <button onClick={onDone} className="btn-quiet px-4 py-3" disabled={busy}>
                Tutup
              </button>
              <button onClick={() => void openDialog()} disabled={!doc || busy} className="btn-accent flex-1 py-3">
                Buka dialog cetak
              </button>
            </div>
          ) : (
            <>
              <p className="text-[15px] font-semibold">Apakah kertasnya keluar dengan benar?</p>
              <div className="mt-2 flex gap-2">
                <button onClick={() => answer(false)} disabled={busy} className="btn-quiet flex-1 py-3">
                  Tidak keluar
                </button>
                <button onClick={() => answer(true)} disabled={busy} className="btn-accent flex-1 py-3">
                  Ya, sudah tercetak
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
