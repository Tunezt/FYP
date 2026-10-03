"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import { readPhoto, todayIso, type Photo } from "@/lib/photo";
import type { InventoryItem } from "@/lib/types";
import { Sheet } from "@/components/ui";
import { Select } from "@/components/Select";
import { FormHint, missingText } from "@/components/FormHint";
import { IconAlert, IconCamera, IconCheck, IconClose } from "@/components/icons";

/* Recording money that left the café, from the dashboard (till-1). Until the
 * café's WhatsApp number is live this is the only way to keep a nota: a typed
 * expense, or a photo that goes through the same reader and the same
 * draft-then-confirm as the WhatsApp assistant. */

export const EXPENSE_CATEGORIES: { value: string; label: string }[] = [
  { value: "bahan baku", label: "Bahan baku" },
  { value: "operasional", label: "Operasional" },
  { value: "gaji", label: "Gaji & upah" },
  { value: "sewa", label: "Sewa" },
  { value: "listrik", label: "Listrik, air & gas" },
  { value: "pemasaran", label: "Pemasaran" },
  { value: "lainnya", label: "Lainnya" },
];

const digits = (v: string) => v.replace(/[^0-9]/g, "");
const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : fallback);

/** A photo slot: pick, preview, remove. */
function PhotoPicker({ photo, onPhoto, label }: { photo: Photo | null; onPhoto: (p: Photo | null) => void; label: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <div>
      {photo ? (
        <div className="surface-inset flex items-center gap-3 rounded-2xl p-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={photo.previewUrl} alt="Foto nota" className="h-14 w-14 rounded-xl object-cover" />
          <span className="ink-soft min-w-0 flex-1 text-sm">Foto terlampir · {Math.max(1, Math.round(photo.bytes / 1024))} KB</span>
          <button type="button" onClick={() => onPhoto(null)} className="icon-btn ink-soft h-11 w-11 rounded-full" aria-label="Hapus foto">
            <IconClose className="h-5 w-5" />
          </button>
        </div>
      ) : (
        <button type="button" onClick={() => input.current?.click()} className="btn-quiet w-full py-3 text-sm">
          <IconCamera className="h-[18px] w-[18px]" /> {label}
        </button>
      )}
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        tabIndex={-1}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          try {
            setError(null);
            onPhoto(await readPhoto(file));
          } catch (err) {
            setError(errText(err, "Foto tidak bisa dibuka."));
          }
        }}
      />
      {error && <p className="mt-1.5 text-[13px] text-[color:var(--bad)]">{error}</p>}
    </div>
  );
}

export function AddExpenseSheet({
  open,
  onClose,
  onSaved,
  initialPhoto = null,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (message: string) => void;
  initialPhoto?: Photo | null;
}) {
  const mutate = useOwnerMutation();
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState("");
  const [day, setDay] = useState(todayIso());
  const [note, setNote] = useState("");
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setAmount("");
    setCategory("");
    setDay(todayIso());
    setNote("");
    setPhoto(initialPhoto);
    setError(null);
  }, [open, initialPhoto]);

  const value = Number(amount || 0);
  const missing = missingText("Isi", [!(value > 0) && "jumlah", !category && "jenis pengeluaran"]);
  const future = day > todayIso();

  async function save() {
    if (missing || future || busy) return;
    setBusy(true);
    setError(null);
    try {
      await mutate("/api/expenses", {
        amount: value,
        category,
        occurred_on: day || null,
        description: note.trim() || null,
        ...(photo ? { image_base64: photo.base64, mime_type: photo.mime } : {}),
      });
      onSaved(`Pengeluaran ${formatRupiah(value)} dicatat`);
      onClose();
    } catch (e) {
      setError(errText(e, "Gagal menyimpan — coba lagi."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open={open} onClose={() => !busy && onClose()} title="Tambah pengeluaran">
      <div className="space-y-4">
        <label className="block">
          <span className="ink-soft mb-1.5 block text-[13px] font-medium">Jumlah (Rp)</span>
          <input
            autoFocus
            inputMode="numeric"
            className="field py-3 text-2xl font-semibold tabular-nums"
            placeholder="0" aria-required="true"
            value={amount ? Number(amount).toLocaleString("id-ID") : ""}
            onChange={(e) => setAmount(digits(e.target.value).slice(0, 10))}
          />
        </label>
        <div>
          <span className="ink-soft mb-1.5 block text-[13px] font-medium">Jenis</span>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Jenis pengeluaran">
            {EXPENSE_CATEGORIES.map((c) => (
              <button
                key={c.value}
                type="button"
                onClick={() => setCategory(c.value)}
                aria-pressed={category === c.value}
                className="choice-chip min-h-[2.75rem]"
              >
                {category === c.value && <IconCheck className="h-3.5 w-3.5" />}
                {c.label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="ink-soft mb-1.5 block text-[13px] font-medium">Tanggal</span>
            <input
              type="date"
              className="field"
              value={day}
              max={todayIso()}
              onChange={(e) => setDay(e.target.value)}
              aria-invalid={future || undefined}
            />
          </label>
          <label className="block">
            <span className="ink-soft mb-1.5 block text-[13px] font-medium">Catatan (opsional)</span>
            <input className="field" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} placeholder="mis. isi ulang gas" />
          </label>
        </div>
        <PhotoPicker photo={photo} onPhoto={setPhoto} label="Lampirkan foto nota (opsional)" />
        {error && <p className="notice notice-bad">{error}</p>}
        <div>
          <button onClick={save} disabled={!!missing || future || busy} className="btn-accent w-full py-3.5">
            {busy ? "Menyimpan…" : value > 0 ? `Simpan ${formatRupiah(value)}` : "Simpan pengeluaran"}
          </button>
          <FormHint missing={future ? "Tanggal tidak boleh setelah hari ini" : missing} />
        </div>
      </div>
    </Sheet>
  );
}

// ── Foto nota: read, check, confirm ─────────────────────────────────────────

type DraftLine = { name_read: string; quantity: string; unit_read: string; unit_price: string; line_total: string; item_id?: string; reason?: string; candidates?: string[] };
type Draft = {
  supplier_text: string;
  supplier_name: string | null;
  date: string;
  total_amount: string;
  matched: (DraftLine & { item_id: string; item_name: string })[];
  questions: (DraftLine & { reason: string; candidates: string[] })[];
  confidence: string | null;
  ambiguities: string[];
};
type ScanOut = { image_path: string; parsed: Record<string, unknown>; draft: Draft };
type ConfirmOut = { goods_receipt_number: number | null; stocked: string[]; skipped: string[]; expense_amount: string };

type EditLine = {
  key: string;
  name: string;
  quantity: string;
  unit: string;
  unitPrice: string;
  lineTotal: string;
  target: string; // item id, or "" = cost only, nothing into stock
  reason: string | null;
};

const SKIP = "__cost_only__";
const num = (s: string) => {
  const n = Number(String(s).replace(",", "."));
  return Number.isFinite(n) ? n : 0;
};
const whole = (s: string) => String(Math.round(num(s)));

function toLines(d: Draft): EditLine[] {
  let k = 0;
  const base = (l: DraftLine) => ({
    key: `l${++k}`,
    name: l.name_read,
    quantity: String(Number(l.quantity)),
    unit: l.unit_read,
    unitPrice: whole(l.unit_price),
    lineTotal: whole(l.line_total),
  });
  return [
    ...d.matched.map((m) => ({ ...base(m), target: m.item_id, reason: null })),
    ...d.questions.map((q) => ({ ...base(q), target: q.item_id ?? "", reason: q.reason })),
  ];
}

/** The whole photo flow: a button that opens the camera, the reading state,
 *  the check sheet, and the result. `onManual` hands the photo to the typed
 *  form when the reader cannot help. */
export function NotaScanButton({ onSaved, onManual }: { onSaved: (message: string) => void; onManual: (photo: Photo | null) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const mutate = useOwnerMutation();
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [phase, setPhase] = useState<"idle" | "reading" | "check" | "failed">("idle");
  const [scan, setScan] = useState<ScanOut | null>(null);
  const [lines, setLines] = useState<EditLine[]>([]);
  const [supplier, setSupplier] = useState("");
  const [notaDate, setNotaDate] = useState("");
  const [total, setTotal] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const items = useOwnerData<InventoryItem[]>(phase === "check" ? "/api/items" : null);

  async function read(p: Photo) {
    setPhoto(p);
    setPhase("reading");
    setError(null);
    try {
      const out = await mutate<ScanOut>("/api/receipts/scan", { image_base64: p.base64, mime_type: p.mime });
      setScan(out);
      setLines(toLines(out.draft));
      setSupplier(out.draft.supplier_name ?? out.draft.supplier_text ?? "");
      setNotaDate(/^\d{4}-\d{2}-\d{2}$/.test(out.draft.date) && out.draft.date <= todayIso() ? out.draft.date : todayIso());
      setTotal(whole(out.draft.total_amount));
      setPhase("check");
    } catch (e) {
      setError(errText(e, "Foto belum bisa dibaca — coba lagi."));
      setPhase("failed");
    }
  }

  function close() {
    if (busy) return;
    setPhase("idle");
    setScan(null);
    setError(null);
  }

  const itemOptions = useMemo(
    () => [
      { value: SKIP, label: "Tidak masuk stok (biaya saja)" },
      ...(items.data ?? []).map((i) => ({ value: i.id, label: `${i.name} (${i.unit})` })),
    ],
    [items.data]
  );
  const stocked = lines.filter((l) => l.target && l.target !== SKIP);
  const stockedValue = stocked.reduce((s, l) => s + num(l.lineTotal), 0);
  const totalValue = num(total);
  const badLine = lines.find((l) => !l.name.trim() || !(num(l.quantity) > 0));
  const missing = badLine
    ? `Isi nama dan jumlah "${badLine.name.trim() || "baris kosong"}"`
    : stocked.length === 0 && !(totalValue > 0)
      ? "Isi total nota, atau pilih barang yang masuk stok"
      : notaDate > todayIso()
        ? "Tanggal nota tidak boleh setelah hari ini"
        : null;
  const needsCheck = scan && (scan.draft.confidence !== "high" || scan.draft.ambiguities.length > 0);

  async function confirm() {
    if (!scan || missing || busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await mutate<ConfirmOut>("/api/receipts/confirm", {
        image_path: scan.image_path,
        supplier: supplier.trim(),
        nota_date: notaDate || null,
        total_amount: totalValue,
        lines: lines.map((l) => ({
          name: l.name.trim(),
          quantity: num(l.quantity),
          unit: l.unit.trim(),
          unit_price: num(l.unitPrice),
          line_total: num(l.lineTotal),
          ...(l.target && l.target !== SKIP ? { item_id: l.target } : { skip: true }),
        })),
      });
      const parts = [
        out.stocked.length ? `${out.stocked.length} barang masuk stok` : null,
        Number(out.expense_amount) > 0 ? `${formatRupiah(out.expense_amount)} dicatat sebagai pengeluaran` : null,
      ].filter(Boolean);
      onSaved(`Nota tersimpan${parts.length ? ": " + parts.join(", ") : ""}.`);
      setPhase("idle");
      setScan(null);
    } catch (e) {
      setError(errText(e, "Gagal menyimpan — coba lagi."));
    } finally {
      setBusy(false);
    }
  }

  const set = (key: string, patch: Partial<EditLine>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  return (
    <>
      <button onClick={() => input.current?.click()} className="btn-accent px-4 py-2.5 text-sm">
        <IconCamera className="h-[18px] w-[18px]" /> Foto nota
      </button>
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        tabIndex={-1}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          try {
            await read(await readPhoto(file));
          } catch (err) {
            setError(errText(err, "Foto tidak bisa dibuka."));
            setPhase("failed");
          }
        }}
      />

      <Sheet open={phase === "reading" || phase === "failed"} onClose={close} title={phase === "reading" ? "Membaca nota…" : "Nota belum terbaca"}>
        <div className="space-y-4">
          {photo && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={photo.previewUrl} alt="Foto nota" className="mx-auto max-h-56 rounded-2xl object-contain" />
          )}
          {phase === "reading" ? (
            <p className="ink-soft text-center text-sm" role="status">
              Biasanya 5–15 detik. Belum ada yang disimpan — kamu periksa dulu hasilnya.
            </p>
          ) : (
            <>
              <p className="notice notice-bad" role="alert">{error}</p>
              <div className="flex gap-2">
                {photo && (
                  <button onClick={() => void read(photo)} className="btn-quiet flex-1 py-3 text-sm">
                    Coba baca lagi
                  </button>
                )}
                <button
                  onClick={() => {
                    const p = photo;
                    close();
                    onManual(p);
                  }}
                  className="btn-accent flex-1 py-3 text-sm"
                >
                  Catat manual
                </button>
              </div>
            </>
          )}
        </div>
      </Sheet>

      <Sheet open={phase === "check" && scan !== null} onClose={close} title="Periksa nota">
        {scan && (
          <div className="space-y-4">
            <div className="flex items-start gap-3">
              {photo && (
                <a href={photo.previewUrl} target="_blank" rel="noreferrer" className="shrink-0" title="Buka foto">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={photo.previewUrl} alt="Foto nota" className="h-20 w-16 rounded-xl object-cover" />
                </a>
              )}
              <p className="ink-soft text-sm">
                Cocokkan dengan nota. Baris yang dipilih barangnya masuk stok; sisanya dicatat sebagai biaya. Belum ada yang
                disimpan sampai kamu tekan Simpan.
              </p>
            </div>
            {needsCheck && (
              <div className="notice notice-warn text-sm" role="alert">
                <p className="flex items-center gap-1.5 font-semibold">
                  <IconAlert className="h-4 w-4" /> Ada bagian yang kurang jelas — cek lagi
                </p>
                {scan.draft.ambiguities.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 font-normal">
                    {scan.draft.ambiguities.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">Toko / supplier</span>
                <input className="field" maxLength={120} value={supplier} onChange={(e) => setSupplier(e.target.value)} />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">Tanggal nota</span>
                <input type="date" className="field" max={todayIso()} value={notaDate} onChange={(e) => setNotaDate(e.target.value)} />
              </label>
            </div>

            <ul className="space-y-3">
              {lines.map((l) => {
                const toStock = !!l.target && l.target !== SKIP;
                return (
                  <li key={l.key} className="surface-inset rounded-2xl p-3">
                    <div className="grid grid-cols-[minmax(0,1fr)_5rem_4.5rem] gap-2">
                      <input className="field py-2 text-sm font-medium" aria-label="Nama barang" value={l.name} onChange={(e) => set(l.key, { name: e.target.value })} />
                      <input className="field py-2 text-sm tabular-nums" aria-label="Jumlah" inputMode="decimal" value={l.quantity} onChange={(e) => set(l.key, { quantity: e.target.value.replace(/[^0-9.,]/g, "") })} />
                      <input className="field py-2 text-sm" aria-label="Satuan" placeholder="satuan" value={l.unit} onChange={(e) => set(l.key, { unit: e.target.value.slice(0, 20) })} />
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2">
                      <label className="block">
                        <span className="ink-faint text-xs">Harga satuan</span>
                        <input className="field py-2 text-sm tabular-nums" inputMode="numeric" value={l.unitPrice} onChange={(e) => set(l.key, { unitPrice: digits(e.target.value) })} />
                      </label>
                      <label className="block">
                        <span className="ink-faint text-xs">Total baris</span>
                        <input className="field py-2 text-sm tabular-nums" inputMode="numeric" value={l.lineTotal} onChange={(e) => set(l.key, { lineTotal: digits(e.target.value) })} />
                      </label>
                    </div>
                    <div className="mt-2">
                      <span className="ink-faint text-xs">Masuk stok sebagai</span>
                      <Select
                        variant="field"
                        allowEmpty={false}
                        ariaLabel={`Barang untuk ${l.name}`}
                        options={itemOptions}
                        value={l.target || SKIP}
                        onChange={(v) => set(l.key, { target: v, reason: null })}
                      />
                      {l.reason && !toStock && <p className="mt-1 text-xs" style={{ color: "var(--warn)" }}>Tidak cocok otomatis: {l.reason}</p>}
                      {l.reason && toStock && <p className="mt-1 text-xs" style={{ color: "var(--warn)" }}>Perlu dicek: {l.reason}</p>}
                    </div>
                  </li>
                );
              })}
            </ul>

            <label className="block">
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">Total nota (Rp)</span>
              <input className="field py-3 text-xl font-semibold tabular-nums" inputMode="numeric" value={total} onChange={(e) => setTotal(digits(e.target.value))} />
            </label>
            <p className="ink-soft text-sm tabular-nums">
              {stocked.length} baris masuk stok ({formatRupiah(stockedValue)}) ·{" "}
              {formatRupiah(Math.max(0, totalValue - stockedValue))} dicatat sebagai biaya lain
            </p>
            {error && <p className="notice notice-bad">{error}</p>}
            <div>
              <button onClick={confirm} disabled={!!missing || busy} className="btn-accent w-full py-3.5">
                {busy ? "Menyimpan…" : "Simpan nota"}
              </button>
              <FormHint missing={missing} />
            </div>
          </div>
        )}
      </Sheet>
    </>
  );
}
