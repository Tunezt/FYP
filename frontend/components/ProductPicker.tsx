"use client";

import { useEffect, useId, useState } from "react";
import { IconCheck, IconClose } from "@/components/icons";
import { formatRupiah } from "@/lib/format";
import {
  groupRule,
  hasSizeChoice,
  missingChoices,
  missingMessage,
  selectedModifiers,
  selectedVariant,
  toggleModifier,
  unitPrice,
  type Product,
  type Selection,
} from "@/lib/choices";

/** The product sheet shared by the till and the QR menu (svc-1).
 *
 *  Every question is visible at once with its status ("wajib" until answered,
 *  then a tick), and the add button says exactly what is still missing rather
 *  than just greying out. Editing a cart line opens the same sheet with that
 *  line's own answers; adding always starts from `freshSelection`. */
export function ProductPicker({
  product,
  initial,
  editing = false,
  maxQty = 99,
  meta,
  onSubmit,
  onRemove,
  onClose,
}: {
  product: Product;
  initial: Selection;
  editing?: boolean;
  maxQty?: number;
  /** A quiet line under the name — stock at the till, nothing on the menu. */
  meta?: string;
  onSubmit: (sel: Selection) => void;
  onRemove?: () => void;
  onClose: () => void;
}) {
  const [sel, setSel] = useState<Selection>(initial);
  const titleId = useId();
  const noteId = useId();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const variant = selectedVariant(product, sel);
  const mods = selectedModifiers(product, sel);
  const missing = missingChoices(product, sel);
  const explain = missingMessage(missing);
  const unit = unitPrice(product, sel);
  const cappedQty = Math.min(sel.qty, Math.max(1, maxQty));
  const ready = missing.length === 0 && maxQty > 0;
  const base = Number(variant?.sell_price ?? product.sell_price);

  return (
    <div className="sheet-scrim z-[55]" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="sheet-panel sm:max-w-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 px-6 pb-3 pt-5">
          <div className="min-w-0">
            <h2 id={titleId} className="text-[21px] font-semibold leading-tight tracking-[-0.02em]">
              {product.name}
            </h2>
            <p className="ink-soft mt-0.5 text-sm tabular-nums">
              {hasSizeChoice(product) && !variant ? `mulai ${formatRupiah(Math.min(...product.variants.map((v) => Number(v.sell_price))))}` : formatRupiah(base)}
              {meta ? ` · ${meta}` : ""}
            </p>
          </div>
          <button onClick={onClose} aria-label="Tutup" className="icon-btn ink-soft -mr-2 h-10 w-10 shrink-0 rounded-full">
            <IconClose className="h-5 w-5" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-4">
          {hasSizeChoice(product) && (
            <ChoiceGroup label="Ukuran" required answered={!!variant} hint={null}>
              <div role="radiogroup" aria-label="Ukuran" className="grid grid-cols-2 gap-2">
                {product.variants.map((v) => (
                  <OptionButton
                    key={v.id}
                    role="radio"
                    on={sel.variantId === v.id}
                    onPress={() => setSel({ ...sel, variantId: v.id })}
                    label={v.name}
                    price={formatRupiah(v.sell_price)}
                  />
                ))}
              </div>
            </ChoiceGroup>
          )}

          {product.modifier_groups.map((g) => {
            const { required, min, max } = groupRule(g);
            const picked = sel.chosen[g.id] ?? [];
            const full = max !== null && g.selection === "multi" && picked.length >= max;
            const hint =
              g.selection === "multi"
                ? max !== null
                  ? min > 1
                    ? `pilih ${min}–${max}`
                    : `boleh sampai ${max}`
                  : min > 1
                    ? `pilih minimal ${min}`
                    : "boleh lebih dari satu"
                : null;
            return (
              <ChoiceGroup key={g.id} label={g.name} required={required} answered={picked.length >= Math.max(1, min)} hint={hint}>
                <div
                  role={g.selection === "single" ? "radiogroup" : "group"}
                  aria-label={g.name}
                  className="grid grid-cols-2 gap-2"
                >
                  {g.modifiers.map((m) => {
                    const on = picked.includes(m.id);
                    const delta = Number(m.price_delta);
                    return (
                      <OptionButton
                        key={m.id}
                        role={g.selection === "single" ? "radio" : "checkbox"}
                        on={on}
                        disabled={!on && full}
                        onPress={() => setSel(toggleModifier(sel, g, m.id))}
                        label={m.name}
                        price={delta !== 0 ? `${delta > 0 ? "+" : "−"}${formatRupiah(Math.abs(delta))}` : null}
                      />
                    );
                  })}
                </div>
              </ChoiceGroup>
            );
          })}

          <div className="mt-5">
            <label htmlFor={noteId} className="flex items-baseline justify-between">
              <span className="text-[15px] font-semibold">Catatan</span>
              <span className="ink-faint text-[13px]">opsional</span>
            </label>
            <input
              id={noteId}
              value={sel.notes}
              maxLength={200}
              onChange={(e) => setSel({ ...sel, notes: e.target.value.slice(0, 200) })}
              className="field mt-2"
              placeholder="mis. es sedikit, tanpa gula"
            />
          </div>
        </div>

        <div className="hairline-t px-6 pb-6 pt-4">
          <div className="flex items-center justify-between gap-4">
            <div className="surface-inset flex items-center gap-1 rounded-2xl p-1" role="group" aria-label="Jumlah">
              <button
                onClick={() => setSel({ ...sel, qty: Math.max(1, cappedQty - 1) })}
                disabled={cappedQty <= 1}
                aria-label="Kurangi jumlah"
                className="h-11 w-11 rounded-xl text-2xl font-medium transition-colors hover:bg-[color:var(--surface)] disabled:opacity-30"
              >
                −
              </button>
              <span className="w-9 text-center text-lg font-semibold tabular-nums" aria-live="polite">
                {cappedQty}
              </span>
              <button
                onClick={() => setSel({ ...sel, qty: Math.min(maxQty, cappedQty + 1) })}
                disabled={cappedQty >= maxQty}
                aria-label="Tambah jumlah"
                className="h-11 w-11 rounded-xl text-2xl font-medium transition-colors hover:bg-[color:var(--surface)] disabled:opacity-30"
              >
                +
              </button>
            </div>
            <div className="min-w-0 text-right">
              <p className="ink-faint truncate text-xs tabular-nums">
                {variant || !hasSizeChoice(product) ? formatRupiah(base) : "pilih ukuran"}
                {mods.filter((m) => Number(m.price_delta) !== 0).map((m) => ` + ${m.name} ${formatRupiah(m.price_delta)}`).join("")}
                {cappedQty > 1 ? ` × ${cappedQty}` : ""}
              </p>
              <p className="text-2xl font-semibold tabular-nums tracking-[-0.02em]">{formatRupiah(unit * cappedQty)}</p>
            </div>
          </div>
          <p className="mt-3 min-h-[1.25rem] text-center text-sm font-medium" style={{ color: explain ? "var(--warn)" : undefined }} aria-live="polite">
            {maxQty <= 0 ? "Stok habis" : explain ?? ""}
          </p>
          <div className="mt-2 flex gap-2">
            {editing && onRemove && (
              <button onClick={onRemove} className="btn-quiet px-4 py-3.5 text-[15px] text-[color:var(--bad)]">
                Hapus
              </button>
            )}
            <button
              onClick={() => ready && onSubmit({ ...sel, qty: cappedQty, notes: sel.notes.trim() })}
              disabled={!ready}
              className="btn-accent flex-1 py-3.5 text-base"
            >
              {editing ? "Simpan perubahan" : "Tambah ke pesanan"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChoiceGroup({
  label,
  required,
  answered,
  hint,
  children,
}: {
  label: string;
  required: boolean;
  answered: boolean;
  hint: string | null;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-5 first:mt-1">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-[15px] font-semibold">
          {label} <span className="ink-faint font-normal">· {required ? "wajib" : "opsional"}</span>
        </h3>
        <span className="flex items-center gap-1 text-[13px]" style={{ color: required && !answered ? "var(--warn)" : "var(--ink-faint)" }}>
          {required && answered ? (
            <>
              <span style={{ color: "var(--good)" }}><IconCheck className="h-3.5 w-3.5" /></span> <span className="ink-faint">{hint ?? "terpilih"}</span>
            </>
          ) : required ? (
            hint ?? "belum dipilih"
          ) : (
            hint
          )}
        </span>
      </div>
      {children}
    </section>
  );
}

function OptionButton({
  role,
  on,
  disabled = false,
  onPress,
  label,
  price,
}: {
  role: "radio" | "checkbox";
  on: boolean;
  disabled?: boolean;
  onPress: () => void;
  label: string;
  price: string | null;
}) {
  return (
    <button
      type="button"
      role={role}
      aria-checked={on}
      disabled={disabled}
      onClick={onPress}
      className={`flex min-h-[3.25rem] items-center gap-2.5 rounded-2xl px-3.5 py-2.5 text-left text-[15px] font-medium leading-snug transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        on ? "toggle-on" : "toggle-off"
      }`}
    >
      <span
        aria-hidden
        className={`flex h-5 w-5 shrink-0 items-center justify-center ${role === "radio" ? "rounded-full" : "rounded-md"}`}
        style={{
          boxShadow: on ? "none" : "inset 0 0 0 1.5px var(--hairline-strong)",
          background: on ? "var(--accent-fill)" : "transparent",
          color: "var(--on-accent)",
        }}
      >
        {on && <IconCheck className="h-3.5 w-3.5" />}
      </span>
      <span className="flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-x-2">
        <span className="break-words">{label}</span>
        {price && <span className={`text-sm tabular-nums ${on ? "" : "ink-soft"}`}>{price}</span>}
      </span>
    </button>
  );
}
