"use client";

import { useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import type { InventoryItem, PromoCondition, PromoRow } from "@/lib/types";
import { EmptyState, ErrorState, Plate, Sheet, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconPlus } from "@/components/icons";

const DAYS = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"];
const KIND_LABEL: Record<PromoRow["kind"], string> = {
  percent_off: "Diskon persen",
  amount_off: "Potongan rupiah",
  bonus_item: "Bonus barang",
};

type Draft = {
  name: string;
  kind: PromoRow["kind"];
  value: string;            // percent as "10" or rupiah as "5000"
  item_id: string;          // "" = whole bill
  bonus_item_id: string;    // "" = same item
  bonus_quantity: string;
  max_per_order: string;
  buy_quantity: string;     // multiples condition
  days: number[];           // day_of_week condition ([] = every day)
  time_start: string;       // time_window ("" = all day)
  time_end: string;
  starts_at: string;        // date_range ("" = open)
  ends_at: string;
  min_spend: string;
};
const EMPTY: Draft = {
  name: "", kind: "percent_off", value: "", item_id: "", bonus_item_id: "", bonus_quantity: "1", max_per_order: "",
  buy_quantity: "1", days: [], time_start: "", time_end: "", starts_at: "", ends_at: "", min_spend: "",
};

function describeConditions(conds: PromoCondition[]): string[] {
  const out: string[] = [];
  for (const c of conds) {
    if (c.kind === "day_of_week" && c.days_of_week) out.push(c.days_of_week.map((d) => DAYS[d]).join(", "));
    if (c.kind === "time_window") out.push(`${(c.time_start ?? "").slice(0, 5)}–${(c.time_end ?? "").slice(0, 5)}`);
    if (c.kind === "date_range")
      out.push(
        `${c.starts_at ? new Date(c.starts_at).toLocaleDateString("id-ID") : "…"} s/d ${c.ends_at ? new Date(c.ends_at).toLocaleDateString("id-ID") : "…"}`
      );
    if (c.kind === "min_spend" && c.amount) out.push(`min. belanja ${formatRupiah(c.amount)}`);
    if (c.kind === "multiples" && c.quantity) out.push(`tiap ${Number(c.quantity)} pcs`);
  }
  return out.length ? out : ["selalu berlaku"];
}

export default function PromosPage() {
  const promos = useOwnerData<PromoRow[]>("/api/promos?include_inactive=true");
  const items = useOwnerData<InventoryItem[]>("/api/items");
  const mutate = useOwnerMutation();
  const [editing, setEditing] = useState<PromoRow | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const itemName = (id: string | null) => (items.data ?? []).find((i) => i.id === id)?.name ?? "—";

  function open(p: PromoRow | "new") {
    setEditing(p);
    setError(null);
    if (p === "new") {
      setDraft(EMPTY);
      return;
    }
    const cond = (k: PromoCondition["kind"]) => p.conditions.find((c) => c.kind === k);
    setDraft({
      name: p.name,
      kind: p.kind,
      value: p.kind === "percent_off" ? String(Math.round(Number(p.value) * 10000) / 100) : String(Math.round(Number(p.value))),
      item_id: p.item_id ?? "",
      bonus_item_id: p.bonus_item_id ?? "",
      bonus_quantity: String(Number(p.bonus_quantity)),
      max_per_order: p.max_per_order ? String(p.max_per_order) : "",
      buy_quantity: cond("multiples")?.quantity ? String(Number(cond("multiples")!.quantity)) : "1",
      days: cond("day_of_week")?.days_of_week ?? [],
      time_start: (cond("time_window")?.time_start ?? "").slice(0, 5),
      time_end: (cond("time_window")?.time_end ?? "").slice(0, 5),
      starts_at: cond("date_range")?.starts_at ? cond("date_range")!.starts_at!.slice(0, 10) : "",
      ends_at: cond("date_range")?.ends_at ? cond("date_range")!.ends_at!.slice(0, 10) : "",
      min_spend: cond("min_spend")?.amount ? String(Math.round(Number(cond("min_spend")!.amount))) : "",
    });
  }

  function conditionsFromDraft() {
    const conds: Record<string, unknown>[] = [];
    if (draft.days.length > 0 && draft.days.length < 7) conds.push({ kind: "day_of_week", days_of_week: [...draft.days].sort() });
    if (draft.time_start && draft.time_end) conds.push({ kind: "time_window", time_start: draft.time_start, time_end: draft.time_end });
    if (draft.starts_at || draft.ends_at)
      conds.push({
        kind: "date_range",
        starts_at: draft.starts_at ? new Date(draft.starts_at + "T00:00:00").toISOString() : null,
        ends_at: draft.ends_at ? new Date(draft.ends_at + "T00:00:00").toISOString() : null,
      });
    if (Number(draft.min_spend) > 0) conds.push({ kind: "min_spend", amount: Number(draft.min_spend) });
    if (Number(draft.buy_quantity) > 0 && Number(draft.buy_quantity) !== 1) conds.push({ kind: "multiples", quantity: Number(draft.buy_quantity) });
    return conds;
  }

  async function save() {
    if (!draft.name.trim()) {
      setError("Nama promo tidak boleh kosong.");
      return;
    }
    if (draft.kind !== "bonus_item" && !(Number(draft.value) > 0)) {
      setError(draft.kind === "percent_off" ? "Isi persen diskon (1–100)." : "Isi potongan rupiah.");
      return;
    }
    if (draft.kind === "bonus_item" && !draft.item_id) {
      setError("Bonus barang perlu barang yang dibeli.");
      return;
    }
    setBusy(true);
    setError(null);
    const value = draft.kind === "percent_off" ? (Number(draft.value) / 100).toFixed(4) : draft.kind === "amount_off" ? Number(draft.value).toFixed(2) : "0";
    try {
      if (editing === "new") {
        await mutate("/api/promos", {
          name: draft.name.trim(),
          kind: draft.kind,
          value,
          item_id: draft.item_id || null,
          bonus_item_id: draft.kind === "bonus_item" && draft.bonus_item_id ? draft.bonus_item_id : null,
          bonus_quantity: draft.kind === "bonus_item" ? Number(draft.bonus_quantity || 1) : 1,
          max_per_order: draft.max_per_order ? Number(draft.max_per_order) : null,
          conditions: conditionsFromDraft(),
        });
      } else if (editing) {
        await mutate(`/api/promos/${editing.id}`, {
          name: draft.name.trim(),
          value,
          bonus_quantity: draft.kind === "bonus_item" ? Number(draft.bonus_quantity || 1) : undefined,
          max_per_order: draft.max_per_order ? Number(draft.max_per_order) : undefined,
          conditions: conditionsFromDraft(),
        }, "PATCH");
      }
      setEditing(null);
      promos.reload();
    } catch (e: unknown) {
      setError(e instanceof Error && "detail" in e ? String((e as { detail: string }).detail) : "Gagal menyimpan — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(p: PromoRow) {
    await mutate(`/api/promos/${p.id}`, { is_active: !p.is_active }, "PATCH");
    promos.reload();
  }

  return (
    <div className="animate-fade-up space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-[1.65rem] font-bold tracking-tight md:text-3xl">
            Promo
            <HelpTip title="Promo">
              Promo menyala sendiri saat semua syaratnya terpenuhi di kasir — hari, jam, tanggal, minimal
              belanja — dan berhenti sendiri begitu tidak. Biayanya masuk pembukuan sebagai &ldquo;Diskon promo&rdquo;,
              terpisah dari diskon kasir.
            </HelpTip>
          </h1>
        </div>
        <button onClick={() => open("new")} className="btn-accent flex items-center gap-2 px-4 py-2.5 text-sm">
          <IconPlus className="h-4 w-4" /> Promo baru
        </button>
      </header>

      {promos.loading ? (
        <Skeleton className="h-56" />
      ) : promos.error && !promos.data ? (
        <Plate>
          <ErrorState onRetry={promos.reload} />
        </Plate>
      ) : !promos.data || promos.data.length === 0 ? (
        <Plate>
          <EmptyState emoji="🎉" title="Belum ada promo">
            Buat beli 1 gratis 1, diskon jam sepi, atau potongan untuk belanja di atas nominal tertentu.
          </EmptyState>
        </Plate>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {promos.data.map((p) => (
            <li key={p.id}>
              <Plate className={`flex h-full flex-col px-5 py-4 ${p.is_active ? "" : "opacity-60"}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold">{p.name}</p>
                    <p className="ink-soft text-xs">
                      {KIND_LABEL[p.kind]}
                      {p.kind === "percent_off" ? ` · ${Math.round(Number(p.value) * 10000) / 100}%` : ""}
                      {p.kind === "amount_off" ? ` · ${formatRupiah(p.value)}` : ""}
                      {p.item_id ? ` · ${itemName(p.item_id)}` : p.kind !== "bonus_item" ? " · seluruh struk" : ""}
                      {p.kind === "bonus_item" ? ` → gratis ${Number(p.bonus_quantity)} ${p.bonus_item_id ? itemName(p.bonus_item_id) : "barang yang sama"}` : ""}
                      {p.max_per_order ? ` · maks ${p.max_per_order}×/struk` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button onClick={() => open(p)} className="btn-quiet px-2.5 py-1 text-xs">Ubah</button>
                    <button onClick={() => toggle(p)} className="btn-quiet px-2.5 py-1 text-xs">{p.is_active ? "Matikan" : "Nyalakan"}</button>
                  </div>
                </div>
                <p className="ink-faint mt-2 text-xs">{describeConditions(p.conditions).join(" · ")}</p>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <dt className="ink-faint">Dipakai</dt>
                    <dd className="font-semibold tabular-nums">{p.applications}×</dd>
                  </div>
                  <div>
                    <dt className="ink-faint">Biaya promo</dt>
                    <dd className="font-semibold tabular-nums">{formatRupiah(p.given_away)}</dd>
                  </div>
                </dl>
              </Plate>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <Sheet open title={editing === "new" ? "Promo baru" : "Ubah promo"} onClose={() => !busy && setEditing(null)}>
          <div className="space-y-3">
            <label className="block">
              <span className="ink-soft mb-1.5 block text-xs font-medium">Nama</span>
              <input className="field" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} autoFocus />
            </label>
            {editing === "new" && (
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Jenis</span>
                <select className="field" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as Draft["kind"] })}>
                  <option value="percent_off">Diskon persen</option>
                  <option value="amount_off">Potongan rupiah</option>
                  <option value="bonus_item">Bonus barang (beli N gratis M)</option>
                </select>
              </label>
            )}
            {draft.kind !== "bonus_item" && (
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">{draft.kind === "percent_off" ? "Diskon (%)" : "Potongan (Rp)"}</span>
                <input className="field" inputMode="decimal" value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value.replace(/[^0-9.]/g, "") })} />
              </label>
            )}
            {editing === "new" && (
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">{draft.kind === "bonus_item" ? "Barang yang dibeli" : "Berlaku untuk"}</span>
                <select className="field" value={draft.item_id} onChange={(e) => setDraft({ ...draft, item_id: e.target.value })}>
                  {draft.kind !== "bonus_item" && <option value="">Seluruh struk</option>}
                  {draft.kind === "bonus_item" && <option value="">— pilih barang —</option>}
                  {(items.data ?? []).map((i) => (
                    <option key={i.id} value={i.id}>{i.name}</option>
                  ))}
                </select>
              </label>
            )}
            {draft.kind === "bonus_item" && (
              <div className="grid grid-cols-3 gap-2">
                <label className="block">
                  <span className="ink-soft mb-1.5 block text-xs font-medium">Beli (pcs)</span>
                  <input className="field" inputMode="numeric" value={draft.buy_quantity} onChange={(e) => setDraft({ ...draft, buy_quantity: e.target.value.replace(/[^0-9]/g, "") })} />
                </label>
                <label className="block">
                  <span className="ink-soft mb-1.5 block text-xs font-medium">Gratis (pcs)</span>
                  <input className="field" inputMode="numeric" value={draft.bonus_quantity} onChange={(e) => setDraft({ ...draft, bonus_quantity: e.target.value.replace(/[^0-9]/g, "") })} />
                </label>
                {editing === "new" && (
                  <label className="block">
                    <span className="ink-soft mb-1.5 block text-xs font-medium">Barang gratis</span>
                    <select className="field" value={draft.bonus_item_id} onChange={(e) => setDraft({ ...draft, bonus_item_id: e.target.value })}>
                      <option value="">Barang yang sama</option>
                      {(items.data ?? []).map((i) => (
                        <option key={i.id} value={i.id}>{i.name}</option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            )}
            {draft.kind !== "bonus_item" && draft.item_id && (
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Tiap berapa pcs (kelipatan)</span>
                <input className="field" inputMode="numeric" value={draft.buy_quantity} onChange={(e) => setDraft({ ...draft, buy_quantity: e.target.value.replace(/[^0-9]/g, "") })} />
              </label>
            )}
            <label className="block">
              <span className="ink-soft mb-1.5 block text-xs font-medium">Maksimal per struk (kosong = tanpa batas)</span>
              <input className="field" inputMode="numeric" value={draft.max_per_order} onChange={(e) => setDraft({ ...draft, max_per_order: e.target.value.replace(/[^0-9]/g, "") })} />
            </label>

            <p className="ink-soft pt-1 text-xs font-semibold uppercase tracking-wide">Syarat (kosong = selalu)</p>
            <div>
              <span className="ink-soft mb-1.5 block text-xs font-medium">Hari</span>
              <div className="flex flex-wrap gap-1">
                {DAYS.map((d, i) => {
                  const on = draft.days.includes(i);
                  return (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDraft({ ...draft, days: on ? draft.days.filter((x) => x !== i) : [...draft.days, i] })}
                      className={`rounded-xl px-2.5 py-1 text-xs font-medium ${on ? "bg-accent-gradient text-white" : "glass-card"}`}
                    >
                      {d}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Jam mulai</span>
                <input className="field" type="time" value={draft.time_start} onChange={(e) => setDraft({ ...draft, time_start: e.target.value })} />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Jam selesai</span>
                <input className="field" type="time" value={draft.time_end} onChange={(e) => setDraft({ ...draft, time_end: e.target.value })} />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Mulai tanggal</span>
                <input className="field" type="date" value={draft.starts_at} onChange={(e) => setDraft({ ...draft, starts_at: e.target.value })} />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Sampai tanggal (tidak termasuk)</span>
                <input className="field" type="date" value={draft.ends_at} onChange={(e) => setDraft({ ...draft, ends_at: e.target.value })} />
              </label>
            </div>
            <label className="block">
              <span className="ink-soft mb-1.5 block text-xs font-medium">Minimal belanja (Rp)</span>
              <input className="field" inputMode="numeric" value={draft.min_spend} onChange={(e) => setDraft({ ...draft, min_spend: e.target.value.replace(/[^0-9]/g, "") })} />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button onClick={save} disabled={busy} className="btn-accent px-5 py-2.5 text-sm">
              {busy ? "Menyimpan…" : "Simpan"}
            </button>
          </div>
        </Sheet>
      )}
    </div>
  );
}
