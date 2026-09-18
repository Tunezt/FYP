"use client";

import { useState } from "react";
import { useOwnerMutation } from "@/lib/hooks";
import { CopyField, Plate } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";

type PrinterToken = { printer: "front" | "kitchen"; token: string; claim_path: string; result_path: string; expires_at: string };

const PRINTERS: { id: PrinterToken["printer"]; name: string; what: string }[] = [
  { id: "front", name: "Printer depan", what: "struk pelanggan dan slip bar, sebagai kertas terpisah" },
  { id: "kitchen", name: "Printer dapur", what: "slip dapur saja" },
];

/** prt-4: the owner issues one device token per printer. Honest about what it
 *  is for: a printer, or a small bridge next to it, pulls its jobs with this
 *  token. Which hardware does that is recorded in docs/printing.md; this card
 *  claims no compatibility. */
export function PrinterTokens() {
  const mutate = useOwnerMutation();
  const [tokens, setTokens] = useState<Partial<Record<PrinterToken["printer"], PrinterToken>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function issue(printer: PrinterToken["printer"]) {
    setBusy(printer);
    setError(null);
    try {
      const t = await mutate<PrinterToken>(`/api/printers/${printer}/token`, {});
      setTokens((prev) => ({ ...prev, [printer]: t }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Token belum bisa dibuat — coba lagi ya.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <h2 className="mb-2 flex items-center gap-2 section-title">
        Printer
        <HelpTip title="Printer">
          Kasir tidak mengirim ke printer secara langsung. Setiap slip disimpan dulu di server, lalu printer (atau alat
          kecil di sebelahnya) mengambilnya dengan token di bawah. Kalau printer mati, slip tidak hilang dan penjualan
          tidak batal. Sebelum printer tersambung, kasir tetap bisa mencetak manual lewat browser.
        </HelpTip>
      </h2>
      <Plate className="space-y-4 px-6 py-5">
        {PRINTERS.map((p) => (
          <div key={p.id} className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold">{p.name}</p>
                <p className="ink-soft text-xs">Mencetak {p.what}.</p>
              </div>
              <button onClick={() => issue(p.id)} disabled={busy !== null} className="btn-quiet px-4 py-2 text-sm">
                {busy === p.id ? "Membuat…" : tokens[p.id] ? "Buat ulang token" : "Buat token perangkat"}
              </button>
            </div>
            {tokens[p.id] && (
              <div className="space-y-1">
                <CopyField value={tokens[p.id]!.token} />
                <p className="ink-faint text-xs">
                  Pasang di perangkat printer {p.name.toLowerCase()} saja. Berlaku sampai{" "}
                  {new Date(tokens[p.id]!.expires_at).toLocaleDateString("id-ID", { day: "numeric", month: "long", year: "numeric" })}; berhenti
                  berlaku kalau semua perangkat kasir diputuskan.
                </p>
              </div>
            )}
          </div>
        ))}
        {error && <p className="notice notice-bad">{error}</p>}
      </Plate>
    </section>
  );
}
