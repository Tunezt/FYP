"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { IconClose, IconPrinter } from "@/components/icons";
import { clockTime } from "@/lib/pos";
import { PrintDocument, type PrintDoc } from "@/components/pos/PrintDocument";

// The print queue at the till (prt-4). It says only what is known: a job is
// waiting, being sent, failed, uncertain (a device took it and never answered)
// or printed (a device said so, or a person confirmed it). Recovery is
// deliberate: a failed job can be retried; an uncertain one needs a marked
// reprint or a person's confirmation, because paper may already exist.

export type PrintJob = {
  id: string;
  order_id: string;
  printer: "front" | "kitchen";
  kind: string;
  kind_label: string;
  copy_kind: "original" | "reprint";
  status: "pending" | "sending" | "uncertain" | "printed" | "failed" | "cancelled";
  order_label: string;
  table_label: string | null;
  attempts: number;
  claimed_by: string | null;
  error: string | null;
  confirmed_by_person: boolean;
  created_at: string;
  claimed_at: string | null;
  printed_at: string | null;
  reprint_of: string | null;
};

export const PRINTER_LABEL = { front: "Printer depan", kitchen: "Printer dapur" } as const;

export const PRINT_STATUS: Record<PrintJob["status"], { text: string; cls: string }> = {
  pending: { text: "Menunggu printer", cls: "pill-quiet" },
  sending: { text: "Sedang dikirim", cls: "pill-quiet" },
  uncertain: { text: "Belum pasti tercetak", cls: "pill-warn" },
  printed: { text: "Tercetak", cls: "pill-good" },
  failed: { text: "Gagal cetak", cls: "pill-bad" },
  cancelled: { text: "Ditarik", cls: "pill-quiet" },
};

export function usePrintQueue(token: string | null) {
  const [open, setOpen] = useState<PrintJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setOpen(await api<PrintJob[]>("/pos/print-jobs?scope=open", { token }));
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
  const attention = (open ?? []).filter((j) => j.status === "failed" || j.status === "uncertain").length;
  const waiting = (open ?? []).filter((j) => j.status === "pending" || j.status === "sending").length;
  return { open, error, load, attention, waiting };
}

/** One job's recovery buttons, shared by the queue and an order's detail. */
export function PrintJobActions({
  job,
  token,
  onChanged,
  onBrowserPrint,
  compact = false,
}: {
  job: Pick<PrintJob, "id" | "status" | "printer">;
  token: string | null;
  onChanged: () => void;
  onBrowserPrint: (jobId: string) => void;
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
      {job.status === "failed" && (
        <button onClick={() => act("retry")} disabled={busy} className={btn}>
          Coba lagi
        </button>
      )}
      {(job.status === "pending" || job.status === "failed") && job.printer === "front" && (
        <button onClick={() => onBrowserPrint(job.id)} disabled={busy} className={btn} title="Cadangan manual: dialog cetak browser">
          Cetak manual
        </button>
      )}
      {job.status === "uncertain" && (
        <button onClick={() => act("confirm")} disabled={busy} className={btn}>
          Kertas sudah ada
        </button>
      )}
      {(job.status === "uncertain" || job.status === "printed" || job.status === "failed") && (
        <button onClick={() => act("reprint")} disabled={busy} className={btn}>
          Cetak ulang
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
}: {
  token: string | null;
  queue: ReturnType<typeof usePrintQueue>;
  onClose: () => void;
  onBrowserPrint: (jobId: string) => void;
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
                      {j.error && <p className="text-[13px] text-[color:var(--bad)]">{j.error}</p>}
                      {j.status === "uncertain" && (
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
                    <PrintJobActions job={j} token={token} onChanged={() => void queue.load()} onBrowserPrint={onBrowserPrint} compact />
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

/** Manual fallback through the browser's print dialog. The job is taken, the
 *  slip is shown and printed, and then the person is asked. A closed dialog is
 *  not proof of paper, so nothing is marked printed without that answer. */
export function BrowserPrintSheet({ jobId, token, onDone }: { jobId: string; token: string | null; onDone: () => void }) {
  const [doc, setDoc] = useState<PrintDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asked, setAsked] = useState(false);
  const [busy, setBusy] = useState(false);
  // Preview first; the job is only taken when the dialog is actually opened.
  useEffect(() => {
    api<{ document: PrintDoc }>(`/pos/print-jobs/${jobId}`, { token })
      .then((j) => setDoc(j.document))
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
            <IconPrinter className="h-5 w-5" /> Cetak manual lewat browser
          </p>
          <p className="ink-soft text-[13px]">Cadangan kalau printer belum tersambung otomatis. Pilih printer depan di dialog cetak.</p>
        </div>
        {error && <p className="notice notice-bad mx-5 mt-3 print:hidden">{error}</p>}
        {doc && (
          <div className="flex justify-center bg-[color:var(--surface-inset)] px-3 py-4 print:bg-white print:p-0">
            <PrintDocument doc={doc} id="browser-slip" />
          </div>
        )}
        <div className="px-5 pb-5 pt-3 print:hidden">
          {!asked ? (
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
