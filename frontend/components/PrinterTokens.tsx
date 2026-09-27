"use client";

import { useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { CopyField, Plate } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";

type PrinterToken = { printer: "front" | "kitchen"; token: string; claim_path: string; result_path: string; expires_at: string };
type PrintDevice = {
  printer: "front" | "kitchen";
  device: string;
  state: string;
  detail: string | null;
  last_seen_at: string;
  silent: boolean;
  test_state: string | null;
  test_detail: string | null;
  test_at: string | null;
};
type Routing = { bar: number; kitchen: number; none: number; unmapped: { id: string; name: string }[] };

// What a test ticket proved, in the words the evidence supports.
const TEST_TEXT: Record<string, { text: string; tone: "good" | "warn" | "bad" | "quiet" }> = {
  waiting: { text: "Menunggu bridge mengambilnya…", tone: "quiet" },
  printed: { text: "Tercetak — printer mengonfirmasi", tone: "good" },
  delivered: { text: "Terkirim ke printer (printer tidak bisa memastikan kertas keluar)", tone: "warn" },
  uncertain: { text: "Belum pasti tercetak — cek kertasnya", tone: "warn" },
  failed: { text: "Gagal", tone: "bad" },
  not_picked_up: { text: "Tidak diambil bridge — pastikan bridge jalan di tablet", tone: "bad" },
};

const DEVICE_TEXT: Record<string, string> = {
  ready: "siap",
  reachable: "tersambung (status tidak dibaca)",
  paper_low: "kertas hampir habis",
  paper_out: "kertas habis",
  cover_open: "tutup printer terbuka",
  offline: "printer tidak terjangkau",
  error: "error",
  unknown: "status tidak diketahui",
};

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
  const devices = useOwnerData<PrintDevice[]>("/api/printers/devices");
  const routing = useOwnerData<Routing>("/api/printers/routing");
  const [testing, setTesting] = useState<string | null>(null);

  // The bridge answers a test ticket within a poll or two; watch for it.
  async function printTest(printer: PrinterToken["printer"]) {
    setTesting(printer);
    setError(null);
    try {
      await mutate(`/api/printers/${printer}/test`, {});
      for (let i = 0; i < 12; i++) {
        await new Promise((r) => setTimeout(r, 1200));
        devices.reload();
        const seen = (devices.data ?? []).find((d) => d.printer === printer);
        if (seen && seen.test_state && seen.test_state !== "waiting") break;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Tes cetak belum bisa dijalankan — coba lagi.");
    } finally {
      setTesting(null);
      devices.reload();
    }
  }

  async function assign(itemId: string, station: "bar" | "kitchen" | "none") {
    try {
      await mutate(`/api/items/${itemId}`, { prep_station: station }, "PATCH");
      routing.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Belum tersimpan — coba lagi.");
    }
  }
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
          Kasir tidak mengirim ke printer secara langsung. Setiap slip disimpan dulu di server, lalu program print
          bridge di jaringan kafe (laptop, HP Android dengan Termux, atau komputer kecil) mengambilnya dengan token di
          bawah dan mengirimnya ke printer. Kalau printer mati, slip tidak hilang dan penjualan tidak batal. Sebelum
          printer tersambung, kasir tetap bisa mencetak manual lewat browser.
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
              <div className="flex flex-wrap gap-2">
                <button onClick={() => void printTest(p.id)} disabled={testing !== null} className="btn-quiet px-4 py-2 text-sm">
                  {testing === p.id ? "Mencetak tes…" : "Cetak tes"}
                </button>
                <button onClick={() => issue(p.id)} disabled={busy !== null} className="btn-quiet px-4 py-2 text-sm">
                  {busy === p.id ? "Membuat…" : tokens[p.id] ? "Buat ulang token" : "Buat token perangkat"}
                </button>
              </div>
            </div>
            <BridgeSeen devices={(devices.data ?? []).filter((d) => d.printer === p.id)} loaded={devices.data !== null} />
            <TestResult devices={(devices.data ?? []).filter((d) => d.printer === p.id)} />
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
        <button onClick={devices.reload} className="btn-quiet px-3 py-1.5 text-xs">
          Periksa status bridge lagi
        </button>
        <Routing routing={routing.data} onAssign={assign} />
      </Plate>
    </section>
  );
}

function BridgeSeen({ devices, loaded }: { devices: PrintDevice[]; loaded: boolean }) {
  if (!loaded) return null;
  if (devices.length === 0) return <p className="ink-faint text-xs">Belum ada print bridge yang melapor untuk printer ini.</p>;
  return (
    <ul className="space-y-0.5">
      {devices.map((d) => (
        <li key={d.device} className="text-xs" style={{ color: d.silent || !["ready", "reachable"].includes(d.state) ? "var(--warn)" : "var(--ink-soft)" }}>
          Bridge {d.device}: {d.silent ? "tidak melapor sejak" : `${DEVICE_TEXT[d.state] ?? d.state} ·`}{" "}
          {new Date(d.last_seen_at).toLocaleString("id-ID", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
        </li>
      ))}
    </ul>
  );
}

/** What the last test ticket proved for this printer. */
function TestResult({ devices }: { devices: PrintDevice[] }) {
  const latest = devices.filter((d) => d.test_state).sort((a, b) => (b.test_at ?? "").localeCompare(a.test_at ?? ""))[0];
  if (!latest || !latest.test_state) return null;
  const shown = TEST_TEXT[latest.test_state] ?? { text: latest.test_state, tone: "quiet" as const };
  const color = { good: "var(--good)", warn: "var(--warn)", bad: "var(--bad)", quiet: "var(--ink-soft)" }[shown.tone];
  return (
    <p className="text-xs" style={{ color }}>
      Tes cetak: {shown.text}
      {/* The label already says what a confirmed print means; the detail only adds something when it went wrong. */}
      {latest.test_detail && latest.test_state !== "printed" ? ` — ${latest.test_detail}` : ""}
    </p>
  );
}

/** Which products go where. A product nobody has assigned still prints, on the
 *  Bar slip with a warning, so this list exists to be emptied rather than to
 *  hide a silently dropped item. */
function Routing({ routing, onAssign }: { routing: Routing | null; onAssign: (id: string, station: "bar" | "kitchen" | "none") => void }) {
  if (!routing) return null;
  return (
    <div className="space-y-2 border-t border-[color:var(--hairline)] pt-4">
      <div>
        <p className="text-sm font-semibold">Tujuan cetak produk</p>
        <p className="ink-soft text-xs">
          Bar {routing.bar} · Dapur {routing.kitchen} · tanpa persiapan {routing.none}. Tujuan diatur per produk (Stok → ubah), tidak pernah ditebak dari namanya.
        </p>
      </div>
      {routing.unmapped.length === 0 ? (
        <p className="text-xs" style={{ color: "var(--good)" }}>Semua produk sudah punya tujuan.</p>
      ) : (
        <div className="space-y-1">
          <p className="text-xs" style={{ color: "var(--warn)" }}>
            {routing.unmapped.length} produk belum punya tujuan. Sementara ini slipnya keluar di printer depan dengan tanda
            “TUJUAN BELUM DIATUR”, jadi tidak ada yang hilang — tapi dapur tidak akan menerimanya.
          </p>
          <ul className="space-y-1">
            {routing.unmapped.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm">{item.name}</span>
                <span className="flex gap-1">
                  {(["bar", "kitchen", "none"] as const).map((station) => (
                    <button key={station} onClick={() => onAssign(item.id, station)} className="btn-quiet px-2.5 py-1 text-xs">
                      {station === "bar" ? "Bar" : station === "kitchen" ? "Dapur" : "Tanpa persiapan"}
                    </button>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
