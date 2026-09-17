"use client";

import { useMemo, useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { formatQty, formatRupiah } from "@/lib/format";
import type { InventoryItem } from "@/lib/types";
import { EmptyState, ErrorState, Glass, ItemIcon, RowChevron, Sheet, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconBox, IconPlus } from "@/components/icons";

type Draft = {
  name: string;
  unit: string;
  current_stock: string;
  cost_price: string;
  sell_price: string;
  reorder_threshold: string;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  unit: "pcs",
  current_stock: "0",
  cost_price: "0",
  sell_price: "0",
  reorder_threshold: "0",
};

function isAtRisk(item: InventoryItem): boolean {
  return (
    item.below_reorder_threshold || (item.days_remaining !== null && item.days_remaining <= 3)
  );
}

function riskPill(item: InventoryItem): { text: string; cls: string } {
  const stock = Number(item.current_stock);
  if (stock <= 0) return { text: "habis", cls: "pill-bad" };
  if (item.days_remaining !== null && item.days_remaining <= 1)
    return { text: `±${item.days_remaining} hari`, cls: "pill-bad" };
  if (item.days_remaining !== null && item.days_remaining <= 3)
    return { text: `±${item.days_remaining} hari`, cls: "pill-warn" };
  if (item.below_reorder_threshold) return { text: "di bawah minimum", cls: "pill-warn" };
  return { text: "aman", cls: "pill-good" };
}

export default function InventoryPage() {
  const items = useOwnerData<InventoryItem[]>("/api/items");
  const mutate = useOwnerMutation();
  const [editing, setEditing] = useState<InventoryItem | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { risky, safe } = useMemo(() => {
    const all = items.data ?? [];
    return {
      risky: all
        .filter(isAtRisk)
        .sort((a, b) => (a.days_remaining ?? 99) - (b.days_remaining ?? 99)),
      safe: all.filter((i) => !isAtRisk(i)),
    };
  }, [items.data]);

  function openAdd() {
    setDraft(EMPTY_DRAFT);
    setError(null);
    setAdding(true);
  }

  function openEdit(item: InventoryItem) {
    setDraft({
      name: item.name,
      unit: item.unit,
      current_stock: String(Number(item.current_stock)),
      cost_price: String(Number(item.cost_price)),
      sell_price: String(Number(item.sell_price)),
      reorder_threshold: String(Number(item.reorder_threshold)),
    });
    setError(null);
    setEditing(item);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = {
        name: draft.name.trim(),
        unit: draft.unit.trim() || "pcs",
        current_stock: Number(draft.current_stock) || 0,
        cost_price: Number(draft.cost_price) || 0,
        sell_price: Number(draft.sell_price) || 0,
        reorder_threshold: Number(draft.reorder_threshold) || 0,
      };
      if (editing) {
        await mutate(`/api/items/${editing.id}`, body, "PATCH");
      } else {
        await mutate("/api/items", body);
      }
      setAdding(false);
      setEditing(null);
      items.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal menyimpan — coba lagi ya.");
    } finally {
      setBusy(false);
    }
  }

  const sheetOpen = adding || editing !== null;

  return (
    <div className="animate-fade-up space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Stok</h1>
          <p className="ink-soft mt-1 flex items-center gap-2 text-sm">
            Perkiraan hari tersisa dihitung dari penjualan 14 hari terakhir
            <HelpTip title="Hari tersisa">
              Sistem menghitung rata-rata penjualan harian tiap barang selama 14 hari, lalu
              memperkirakan kapan stok habis. Barang yang jarang terjual tidak diberi angka.
            </HelpTip>
          </p>
        </div>
        <button onClick={openAdd} className="btn-accent px-4 py-2.5 text-sm">
          <IconPlus className="h-4 w-4" /> Tambah barang
        </button>
      </header>

      {items.loading ? (
        <Skeleton className="h-72" />
      ) : items.error && !items.data ? (
        <Glass>
          <ErrorState onRetry={items.reload} />
        </Glass>
      ) : !items.data || items.data.length === 0 ? (
        <Glass>
          <EmptyState icon={<IconBox className="h-5 w-5" />} title="Stok masih kosong">
            Tambah barang di sini, atau kirim foto buku stok / file Excel ke asisten WhatsApp —
            nanti terisi otomatis.
          </EmptyState>
        </Glass>
      ) : (
        <>
          {risky.length > 0 && (
            <section>
              <div className="mb-3 flex items-baseline gap-2">
                <h2 className="section-title">Perlu perhatian</h2>
                <span className="pill-warn tabular-nums">{risky.length}</span>
              </div>
              <ItemRows items={risky} onEdit={openEdit} />
            </section>
          )}
          <section>
            <h2 className="mb-3 section-title">
              {risky.length > 0 ? "Aman" : "Semua barang"}
            </h2>
            {safe.length === 0 ? (
              <p className="ink-faint group-card px-5 py-4 text-sm">Semua barang sedang butuh perhatian.</p>
            ) : (
              <ItemRows items={safe} onEdit={openEdit} />
            )}
          </section>
        </>
      )}

      <Sheet
        open={sheetOpen}
        onClose={() => {
          setAdding(false);
          setEditing(null);
        }}
        title={editing ? `Ubah ${editing.name}` : "Barang baru"}
      >
        <div className="space-y-3">
          <Field label="Nama barang">
            <input
              className="field"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="cth. Biji Arabica"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Jumlah stok">
              <input
                className="field tabular-nums"
                type="number"
                min="0"
                value={draft.current_stock}
                onChange={(e) => setDraft({ ...draft, current_stock: e.target.value })}
              />
            </Field>
            <Field label="Satuan">
              <input
                className="field"
                value={draft.unit}
                onChange={(e) => setDraft({ ...draft, unit: e.target.value })}
                placeholder="kg / pcs / liter"
              />
            </Field>
            <Field label="Harga modal (Rp)">
              <input
                className="field tabular-nums"
                type="number"
                min="0"
                value={draft.cost_price}
                onChange={(e) => setDraft({ ...draft, cost_price: e.target.value })}
              />
            </Field>
            <Field label="Harga jual (Rp)">
              <input
                className="field tabular-nums"
                type="number"
                min="0"
                value={draft.sell_price}
                onChange={(e) => setDraft({ ...draft, sell_price: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Batas minimum (dapat peringatan kalau di bawah ini)">
            <input
              className="field tabular-nums"
              type="number"
              min="0"
              value={draft.reorder_threshold}
              onChange={(e) => setDraft({ ...draft, reorder_threshold: e.target.value })}
            />
          </Field>
          {error && (
            <p className="notice notice-bad">
              {error}
            </p>
          )}
          <button
            onClick={save}
            disabled={busy || !draft.name.trim()}
            className="btn-accent w-full py-3.5"
          >
            {busy ? "Menyimpan…" : "Simpan"}
          </button>
        </div>
      </Sheet>
    </div>
  );
}

function ItemRows({ items, onEdit }: { items: InventoryItem[]; onEdit: (i: InventoryItem) => void }) {
  return (
    <ul className="group-card group-body">
      {items.map((item) => {
        const pill = riskPill(item);
        return (
          <li key={item.id}>
            <button
              onClick={() => onEdit(item)}
              className="list-row list-row-action"
              style={{ ["--row-inset" as string]: "4.25rem" }}
            >
              <ItemIcon name={item.name} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{item.name}</p>
                <p className="ink-faint truncate text-xs">
                  {Number(item.sell_price) > 0
                    ? `jual ${formatRupiah(item.sell_price)}`
                    : "bahan baku"}
                  {item.avg_daily_usage ? ` · ±${item.avg_daily_usage} ${item.unit}/hari` : ""}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p className="mb-0.5 text-sm font-semibold tabular-nums">
                  {formatQty(item.current_stock)}{" "}
                  <span className="ink-faint text-xs font-normal">{item.unit}</span>
                </p>
                <span className={pill.cls}>{pill.text}</span>
              </div>
              <RowChevron />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="ink-soft mb-1.5 block text-[13px] font-medium">{label}</span>
      {children}
    </label>
  );
}
