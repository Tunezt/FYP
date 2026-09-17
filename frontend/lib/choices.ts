// Product choices (svc-1): one set of rules for the till and the QR menu.
//
// Nothing is chosen for the customer. A product with several sizes opens with
// no size; a required group (Suhu) opens unanswered even when the catalogue
// marks a default; optional extras open empty. The server refuses the same
// gaps (`require_explicit_choices`, `_resolve_modifiers`), so this module only
// decides what to ask and how to explain what is still missing.

export type Variant = { id: string; name: string; sell_price: string; is_default: boolean };
export type Modifier = { id: string; name: string; price_delta: string; is_default: boolean };
export type ModifierGroup = {
  id: string;
  name: string;
  selection: "single" | "multi";
  is_required: boolean;
  min_select: number;
  max_select: number | null;
  modifiers: Modifier[];
};
export type Product = {
  id: string;
  name: string;
  sell_price: string;
  variants: Variant[];
  modifier_groups: ModifierGroup[];
};

/** What the customer answered. `chosen` is group id → modifier ids, in tap order. */
export type Selection = {
  variantId: string | null;
  chosen: Record<string, string[]>;
  qty: number;
  notes: string;
};

export const hasSizeChoice = (p: Product) => p.variants.length > 1;

/** Nothing to ask: one genuine variant and no modifier groups at all. Optional
 *  extras are not a question, but they are a choice the cashier may want, so a
 *  product that has them still opens the picker (with "Tambah" ready at once). */
export const isQuickAdd = (p: Product) => !hasSizeChoice(p) && p.modifier_groups.length === 0;

export function groupRule(g: ModifierGroup) {
  const min = g.is_required ? Math.max(1, g.min_select) : Math.max(0, g.min_select);
  const max = g.selection === "single" ? 1 : g.max_select;
  return { required: min > 0, min, max };
}

/** A new line always starts fresh. The only thing filled in is a size that is
 *  not a choice (a single variant). */
export function freshSelection(p: Product): Selection {
  return {
    variantId: p.variants.length === 1 ? p.variants[0].id : null,
    chosen: {},
    qty: 1,
    notes: "",
  };
}

export function selectedVariant(p: Product, sel: Selection): Variant | null {
  if (sel.variantId) return p.variants.find((v) => v.id === sel.variantId) ?? null;
  return p.variants.length === 1 ? p.variants[0] : null;
}

export function selectedModifiers(p: Product, sel: Selection): Modifier[] {
  const out: Modifier[] = [];
  for (const g of p.modifier_groups) {
    for (const id of sel.chosen[g.id] ?? []) {
      const m = g.modifiers.find((x) => x.id === id);
      if (m) out.push(m);
    }
  }
  return out;
}

/** Lower-case names of what still needs an answer, in the order shown. */
export function missingChoices(p: Product, sel: Selection): string[] {
  const missing: string[] = [];
  if (hasSizeChoice(p) && !selectedVariant(p, sel)) missing.push("ukuran");
  for (const g of p.modifier_groups) {
    const { min } = groupRule(g);
    if ((sel.chosen[g.id]?.length ?? 0) < min) missing.push(g.name.toLocaleLowerCase("id-ID"));
  }
  return missing;
}

/** "Pilih ukuran dan suhu" · "Pilih ukuran, suhu, dan gula". */
export function missingMessage(missing: string[]): string | null {
  if (missing.length === 0) return null;
  if (missing.length === 1) return `Pilih ${missing[0]}`;
  return `Pilih ${missing.slice(0, -1).join(", ")}${missing.length > 2 ? "," : ""} dan ${missing[missing.length - 1]}`;
}

/** Tap a modifier. Single-select replaces; tapping the chosen one again clears
 *  it only when the group is optional. Multi-select toggles and stops at max. */
export function toggleModifier(sel: Selection, g: ModifierGroup, modifierId: string): Selection {
  const current = sel.chosen[g.id] ?? [];
  const has = current.includes(modifierId);
  const { required, max } = groupRule(g);
  let next: string[];
  if (g.selection === "single") next = has ? (required ? current : []) : [modifierId];
  else if (has) next = current.filter((x) => x !== modifierId);
  else if (max !== null && current.length >= max) next = current;
  else next = [...current, modifierId];
  return { ...sel, chosen: { ...sel.chosen, [g.id]: next } };
}

export function unitPrice(p: Product, sel: Selection): number {
  const v = selectedVariant(p, sel);
  const base = Number(v?.sell_price ?? p.sell_price);
  return base + selectedModifiers(p, sel).reduce((s, m) => s + Number(m.price_delta), 0);
}

/** Two lines are the same line only when everything the kitchen reads is the
 *  same: product, size, every modifier, and the note. */
export function identityKey(itemId: string, variantId: string | null, modifierIds: string[], notes: string): string {
  return `${itemId}|${variantId ?? "-"}|${[...modifierIds].sort().join(",")}|${notes.trim()}`;
}

/** "Americano · Large" — the size is written whenever the product has sizes,
 *  including the default one, because "Standar" was a choice too. */
export function displayName(p: Product, variant: Variant | null): string {
  return variant && hasSizeChoice(p) ? `${p.name} · ${variant.name}` : p.name;
}
