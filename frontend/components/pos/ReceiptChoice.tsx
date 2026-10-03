"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { api, ApiError } from "@/lib/api";
import { formatRupiah } from "@/lib/format";
import { IconCheck, IconPrinter } from "@/components/icons";

type Choice = "paper" | "qr" | "whatsapp" | "none";
type ChoiceOut = { choice: Choice; receipt_code: string | null; web_path: string | null; whatsapp_link: string | null };

/** After payment the cashier asks the customer how they want the receipt
 *  (till-5b, decision 4): on paper, as a QR to scan, by WhatsApp, or not at
 *  all. The Bar/Dapur slips have already gone to the printers; nothing here
 *  holds up the kitchen. `title` and `amount` say which sale it is; `change`
 *  is the money to hand back, kept in view while the customer decides. */
export function ReceiptChoiceSheet({
  orderId,
  token,
  title,
  amount,
  change,
  whatsappLive,
  onDone,
}: {
  orderId: string;
  token: string | null;
  title: string;
  amount: string;
  change: number;
  whatsappLive: boolean;
  onDone: (message: string | null) => void;
}) {
  const [busy, setBusy] = useState<Choice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<{ kind: "qr" | "whatsapp"; url: string; image: string } | null>(null);

  async function choose(choice: Choice) {
    setBusy(choice);
    setError(null);
    try {
      const out = await api<ChoiceOut>(`/pos/orders/${orderId}/receipt-choice`, { token, body: { choice } });
      if (choice === "paper") return onDone("Struk masuk antrean cetak");
      if (choice === "none") return onDone(null);
      const url = choice === "qr" ? `${window.location.origin}${out.web_path}` : out.whatsapp_link ?? "";
      const image = await QRCode.toDataURL(url, { width: 560, margin: 1, errorCorrectionLevel: "M" });
      setShown({ kind: choice, url, image });
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Belum tersimpan — periksa koneksi, lalu coba lagi.");
    } finally {
      setBusy(null);
    }
  }

  // Escape is "Tidak perlu" only once nothing is on screen for the customer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && shown && onDone(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, onDone]);

  return (
    <div className="sheet-scrim z-[75] items-center p-4" role="dialog" aria-modal="true" aria-labelledby="receipt-choice-title">
      <div className="glass-card w-full max-w-lg px-6 pb-6 pt-5">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-white" style={{ background: "var(--good)" }}>
            <IconCheck className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <p className="ink-soft text-[13px] font-medium">
              {title} · lunas {formatRupiah(amount)}
            </p>
            {change > 0 ? (
              <p className="text-[26px] font-semibold tabular-nums tracking-[-0.02em]">Kembalian {formatRupiah(change)}</p>
            ) : (
              <p className="text-[22px] font-semibold tracking-[-0.02em]">Pembayaran diterima</p>
            )}
          </div>
        </div>

        {shown ? (
          <div className="mt-5 text-center">
            <p className="text-[17px] font-semibold">
              {shown.kind === "qr" ? "Minta pelanggan memindai kode ini" : "Pindai, lalu tekan kirim di WhatsApp"}
            </p>
            <p className="ink-soft mt-0.5 text-sm">
              {shown.kind === "qr"
                ? "Struknya terbuka di HP pelanggan — tanpa aplikasi, tanpa nomor HP."
                : "WhatsApp terbuka dengan pesan STRUK sudah terisi; struk dibalas otomatis."}
            </p>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={shown.image} alt="Kode QR struk" className="mx-auto mt-4 h-64 w-64 rounded-2xl bg-white p-3" />
            <button onClick={() => onDone(shown.kind === "qr" ? "Struk QR ditampilkan" : "Struk WhatsApp ditampilkan")} className="btn-accent mt-5 w-full py-3.5 text-base">
              Selesai
            </button>
          </div>
        ) : (
          <>
            <p id="receipt-choice-title" className="mt-5 text-[17px] font-semibold">
              Struknya mau bagaimana?
            </p>
            <p className="ink-soft text-sm">Slip dapur dan bar sudah dicetak. Tanyakan ke pelanggan.</p>
            <div className="mt-4 grid grid-cols-2 gap-2.5">
              <ChoiceButton label="Kertas" sub="dicetak sekarang" busy={busy === "paper"} disabled={busy !== null} onClick={() => void choose("paper")}>
                <IconPrinter className="h-5 w-5" />
              </ChoiceButton>
              <ChoiceButton label="QR" sub="pelanggan memindai" busy={busy === "qr"} disabled={busy !== null} onClick={() => void choose("qr")} />
              <ChoiceButton
                label="WhatsApp"
                sub={whatsappLive ? "dikirim ke WA pelanggan" : "Belum aktif"}
                busy={busy === "whatsapp"}
                disabled={busy !== null || !whatsappLive}
                onClick={() => void choose("whatsapp")}
              />
              <ChoiceButton label="Tidak perlu" sub="tanpa struk" busy={busy === "none"} disabled={busy !== null} onClick={() => void choose("none")} />
            </div>
            {error && (
              <p role="alert" className="notice notice-bad mt-3">
                {error}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function ChoiceButton({
  label,
  sub,
  busy,
  disabled,
  onClick,
  children,
}: {
  label: string;
  sub: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
  children?: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="choice-card flex min-h-[5rem] flex-col items-start justify-center gap-0.5 px-4 py-3 text-left disabled:cursor-not-allowed disabled:opacity-45"
    >
      <span className="flex items-center gap-2 text-[17px] font-semibold">
        {children}
        {busy ? "Menyimpan…" : label}
      </span>
      <span className="ink-soft text-[13px]">{sub}</span>
    </button>
  );
}
