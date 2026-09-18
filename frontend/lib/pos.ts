// Shared shapes and small helpers for the till, the kitchen and the QR menu
// (svc-6..8). Money and quantities stay strings straight from the API
// (numeric(12,2) / numeric(12,3)); they become numbers only to be drawn.

import type { Modifier, ModifierGroup, Variant } from "@/lib/choices";

export type PosItem = {
  id: string;
  name: string;
  unit: string;
  current_stock: string;
  sell_price: string;
  reorder_threshold: string;
  variants: Variant[];
  modifier_groups: ModifierGroup[];
  made_to_order: boolean;
};

export type PrepState = "new" | "preparing" | "ready" | "done";

export type ActiveLine = {
  name: string;
  size: string | null;
  quantity: string;
  modifiers: string[];
  notes: string | null;
  line_total: string | null;
  item_id: string | null;
  variant_id: string | null;
  modifier_ids: string[];
  done: boolean;
};

export type PriceChange = { name: string; was: string | null; now: string | null };

/** One order the counter still owes somebody (GET /pos/active-orders). */
export type ActiveOrder = {
  id: string;
  code: string;
  number: string;
  source: "pos" | "menu";
  status: string;
  payment: "unpaid" | "paid" | "cancelled" | "reversed";
  prep: PrepState | null;
  order_type: string;
  table_label: string | null;
  guest_name: string | null;
  note: string | null;
  placed_at: string;
  paid_at: string | null;
  prep_since: string | null;
  total: string;
  is_estimate: boolean;
  rev: number;
  staff_name: string | null;
  lines: ActiveLine[];
  parent_id: string | null;
  parent_code: string | null;
  price_changes: PriceChange[];
  order_no: string;
  service_date: string | null;
  batch_no: number;
  external_ref: string | null;
  previous_day: boolean;
};

export type KitchenLine = {
  name: string;
  quantity: string;
  modifiers: string[];
  notes: string | null;
  line_id: string | null;
  item_name: string;
  size: string | null;
  done: boolean;
};

export type KitchenTicket = {
  order_id: string;
  code: string;
  source: string;
  order_type: string;
  table_label: string | null;
  guest_name: string | null;
  note: string | null;
  delivery_address: string | null;
  sold_at: string;
  state: PrepState;
  state_since: string | null;
  lines: KitchenLine[];
  parent_code: string | null;
  order_no: string;
  batch_no: number;
  status: string;
  reversal_reason: string | null;
  reversed_by: string | null;
  reversed_at: string | null;
  state_by: string | null;
};

export type KitchenBoard = {
  server_time: string;
  tickets: KitchenTicket[];
  cancellations: KitchenTicket[];
  history: KitchenTicket[];
};

export const PREP_LABEL: Record<PrepState, string> = {
  new: "Baru",
  preparing: "Disiapkan",
  ready: "Siap diambil",
  done: "Diserahkan",
};

export const SOURCE_LABEL: Record<string, string> = { pos: "Kasir", menu: "QR" };

export const SERVICE_LABEL: Record<string, string> = {
  dine_in: "Makan di sini",
  takeaway: "Bawa pulang",
  pickup: "Ambil sendiri",
  delivery: "Antar",
};

/** A reference a device invents once per submission, so a retry is the same
 *  submission (svc-2). 20 url-safe characters. */
/** How an order is named out loud and on paper (prt-1). Dine-in with a table:
 *  "MEJA 7" first, "Pesanan 042" under it. Anything else: "PESANAN 042".
 *  A paid addition keeps its original's number and says which batch it is. A
 *  driver's reference is shown beside the local number, never instead of it. */
export type Identity = {
  order_type: string;
  table_label: string | null;
  order_no: string;
  batch_no?: number | null;
  external_ref?: string | null;
};

export function orderHeading(o: Identity): { main: string; sub: string | null } {
  const batch = o.batch_no ? `Tambahan ${o.batch_no}` : null;
  const table = (o.table_label ?? "").trim().replace(/^meja\s*/i, "");
  const driver = o.external_ref ? `Driver ${o.external_ref}` : null;
  if (o.order_type === "dine_in" && table) {
    return { main: `MEJA ${table.toUpperCase()}`, sub: [`Pesanan ${o.order_no}`, batch, driver].filter(Boolean).join(" · ") };
  }
  return { main: `PESANAN ${o.order_no}`, sub: [batch, driver].filter(Boolean).join(" · ") || null };
}

/** "Pesanan 042" / "Pesanan 042 · Tambahan 1", for sentences and toasts. */
export function orderLabel(o: Identity): string {
  return `Pesanan ${o.order_no}${o.batch_no ? ` · Tambahan ${o.batch_no}` : ""}`;
}

export function serviceDateLabel(isoDate: string | null): string {
  if (!isoDate) return "";
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("id-ID", { day: "numeric", month: "short" });
}

export function newRef(): string {
  const bytes = new Uint8Array(15);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"[b % 64]).join("").slice(0, 20);
}

/** Whole minutes since `iso`, measured against a clock that may be the server's. */
export function minutesSince(iso: string, now: number = Date.now()): number {
  return Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60000));
}

export function waitLabel(minutes: number): string {
  if (minutes < 1) return "baru saja";
  if (minutes < 60) return `${minutes} mnt`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} j ${m} mnt` : `${h} jam`;
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
}

export function lineSummary(lines: { name: string; size: string | null; quantity: string }[], max = 2): string {
  const parts = lines.slice(0, max).map((l) => `${Number(l.quantity)}× ${l.size ? `${l.name} ${l.size}` : l.name}`);
  const rest = lines.length - max;
  return rest > 0 ? `${parts.join(", ")} +${rest} lagi` : parts.join(", ");
}

export type { Modifier, ModifierGroup, Variant };
