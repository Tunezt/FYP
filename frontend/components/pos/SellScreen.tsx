"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, POS_TOKEN_KEY } from "@/lib/api";
import { Select } from "@/components/Select";
import { ProductPicker } from "@/components/ProductPicker";
import { IconCheck, IconExternal, IconLock, IconPrinter, IconSearch, IconWallet } from "@/components/icons";
import { formatQty, formatRupiah } from "@/lib/format";
import {
  displayName,
  freshSelection,
  identityKey,
  isQuickAdd,
  selectedModifiers,
  selectedVariant,
  type Modifier,
  type Selection,
  type Variant,
} from "@/lib/choices";
import { newRef, orderLabel, type ActiveOrder, type PosItem } from "@/lib/pos";
import { OrderPanel, type PanelContext } from "@/components/pos/OrderPanel";
import { ActiveOrders, type ActiveActions } from "@/components/pos/ActiveOrders";
import { ReceiptSheet, TransactionsView, type Receipt } from "@/components/pos/Receipts";

type Item = PosItem;

// A cart line is an item at one size with its chosen modifiers and a
// preparation note; lines merge only when all of them match (svc-1).
type CartLine = { uid: string; item: Item; variant: Variant | null; modifiers: Modifier[]; qty: number; discount: number; notes: string };

type OrderResult = {
  id: string;
  total: string;
  points_earned: number;
  lines: { item_name: string; quantity: string }[];
  payments: { method: string; amount: string }[];
  parent_number: string | null;
  order_no: string;
  batch_no: number;
};

type Quote = {
  subtotal: string;
  discount_total: string;
  promo_total: string;
  promos: { promo_id: string; name: string; amount: string; bonus_quantity: string }[];
  voucher_total: string;
  voucher_code: string | null;
  voucher_error: string | null;
  service_charge: string;
  delivery_fee: string;
  tax_total: string;
  tax_inclusive: boolean;
  rounding: string;
  total: string;
  discount_requires_pin: boolean;
};

type PayMode = "cash" | "qris" | "split";
type Shift = {
  id: string;
  staff_name: string;
  status: "open" | "closed";
  opening_float: string;
  opened_at: string;
  cash_sales: string;
  cash_refunds: string;
  cash_in: string;
  cash_out: string;
  expected_cash: string | null;
  counted_cash: string | null;
  variance: string | null;
  notes: string | null;
};
type CashKind = "cash_in" | "petty_cash" | "supplier_payment" | "bank_drop";
type SupplierLite = { id: string; name: string };
type CustomerLite = { id: string; name: string; phone: string | null; visits: number; points_balance: number; points_value: string };
type Loyalty = { is_active: boolean; rupiah_per_point: string; point_value: string; min_redeem_points: number };

/** What the order panel is working on. `open` carries the server revision it
 *  was loaded at and a fingerprint of that version, so "ada perubahan" is true
 *  exactly when saving would change something. */
type Ctx =
  | { kind: "new" }
  | { kind: "open"; id: string; rev: number; code: string; source: "pos" | "menu"; guest: string | null; baseline: string }
  | { kind: "addition"; parentId: string; parentCode: string };

type View = "new" | "active" | "history";

const lineKey = (l: CartLine) => identityKey(l.item.id, l.variant?.id ?? null, l.modifiers.map((m) => m.id), l.notes);
const linePrice = (l: CartLine) =>
  Number(l.variant?.sell_price ?? l.item.sell_price) + l.modifiers.reduce((s, m) => s + Number(m.price_delta), 0);
const lineSelection = (l: CartLine): Selection => {
  const chosen: Record<string, string[]> = {};
  for (const g of l.item.modifier_groups) {
    const ids = l.modifiers.filter((m) => g.modifiers.some((x) => x.id === m.id)).map((m) => m.id);
    if (ids.length) chosen[g.id] = ids;
  }
  return { variantId: l.variant?.id ?? null, chosen, qty: l.qty, notes: l.notes };
};
const cartBody = (cart: CartLine[]) =>
  cart.map((l) => ({
    item_id: l.item.id,
    variant_id: l.variant?.id ?? null,
    modifier_ids: l.modifiers.map((m) => m.id),
    quantity: l.qty,
    notes: l.notes || null,
  }));
const fingerprint = (cart: CartLine[], orderType: string, guest: string, table: string) =>
  JSON.stringify([cart.map(lineKey).map((k, i) => `${k}×${cart[i].qty}`).sort(), orderType, guest.trim(), table.trim()]);

let lineSeq = 0;
const NEW_TYPES = ["takeaway", "dine_in", "pickup", "delivery"];
const HELD_TYPES = ["takeaway", "dine_in", "pickup"];

export function SellScreen({
  posToken,
  pairingToken,
  staffName,
  businessName,
  onLock,
}: {
  posToken: string | null;
  pairingToken: string;
  staffName: string;
  businessName: string;
  onLock: () => void;
}) {
  const token = posToken ?? (typeof window !== "undefined" ? localStorage.getItem(POS_TOKEN_KEY) : null);
  const [view, setView] = useState<View>("new");

  // ── Catalogue ────────────────────────────────────────────────────────────
  const [items, setItems] = useState<Item[] | null>(null);
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const loadItems = useCallback(() => {
    api<Item[]>("/pos/items", { token })
      .then((rows) => {
        setItems(rows);
        setItemsError(null);
      })
      .catch((e: unknown) => setItemsError(e instanceof ApiError ? e.detail : "Menu tidak bisa dimuat — periksa koneksi."));
  }, [token]);
  useEffect(loadItems, [loadItems]);
  const sellable = useMemo(() => (items ?? []).filter((i) => Number(i.sell_price) > 0), [items]);
  const visible = useMemo(() => {
    const q = search.trim().toLocaleLowerCase("id-ID");
    return q ? sellable.filter((i) => i.name.toLocaleLowerCase("id-ID").includes(q)) : sellable;
  }, [sellable, search]);
  const itemById = useMemo(() => new Map((items ?? []).map((i) => [i.id, i])), [items]);

  // ── The order being built ───────────────────────────────────────────────
  const [ctx, setCtx] = useState<Ctx>({ kind: "new" });
  const [cart, setCart] = useState<CartLine[]>([]);
  const [orderType, setOrderType] = useState("takeaway");
  const [guestName, setGuestName] = useState("");
  const [tableLabel, setTableLabel] = useState("");
  const [externalRef, setExternalRef] = useState("");
  const [picker, setPicker] = useState<{ item: Item; editUid: string | null; initial: Selection } | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [panelError, setPanelError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const holdRef = useRef(newRef());
  const payRef = useRef(newRef());

  const stockCap = (item: Item) => (item.made_to_order ? 999 : Number(item.current_stock));
  const dirty = ctx.kind === "open" && fingerprint(cart, orderType, guestName, tableLabel) !== ctx.baseline;
  const hasWork = cart.length > 0 && (ctx.kind !== "open" || dirty);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast((t) => (t === message ? null : t)), 2800);
  }, []);

  function resetOrder() {
    setCart([]);
    setCtx({ kind: "new" });
    setOrderType("takeaway");
    setGuestName("");
    setTableLabel("");
    setExternalRef("");
    setPanelError(null);
    setBillDiscount("");
    setVoucherCode("");
    setManagerPin("");
    setCustomer(null);
    setUsePoints(false);
    setDeliveryAddress("");
    setDeliveryPhone("");
    holdRef.current = newRef();
    payRef.current = newRef();
  }

  function putInCart(item: Item, sel: Selection, replaceUid: string | null) {
    setPanelError(null);
    setCart((c) => {
      const base = replaceUid ? c.filter((l) => l.uid !== replaceUid) : c;
      const draft: CartLine = {
        uid: replaceUid ?? `l${++lineSeq}`,
        item,
        variant: selectedVariant(item, sel),
        modifiers: selectedModifiers(item, sel),
        qty: sel.qty,
        discount: replaceUid ? c.find((l) => l.uid === replaceUid)?.discount ?? 0 : 0,
        notes: sel.notes.trim(),
      };
      const key = lineKey(draft);
      const others = base.filter((l) => l.item.id === item.id && lineKey(l) !== key).reduce((s, l) => s + l.qty, 0);
      const room = Math.max(0, stockCap(item) - others);
      const existing = base.find((l) => lineKey(l) === key);
      if (existing) return base.map((l) => (l === existing ? { ...l, qty: Math.min(room, l.qty + draft.qty) } : l));
      const at = replaceUid ? c.findIndex((l) => l.uid === replaceUid) : -1;
      const next = { ...draft, qty: Math.min(room, draft.qty) };
      if (next.qty <= 0) return base;
      return at >= 0 ? [...base.slice(0, at), next, ...base.slice(at)] : [...base, next];
    });
  }

  function openProduct(item: Item) {
    if (isQuickAdd(item)) {
      putInCart(item, freshSelection(item), null);
      return;
    }
    setPicker({ item, editUid: null, initial: freshSelection(item) });
  }

  function changeQty(uid: string, delta: number) {
    setCart((c) =>
      c
        .map((l) => {
          if (l.uid !== uid) return l;
          const others = c.filter((o) => o.item.id === l.item.id && o.uid !== uid).reduce((s, o) => s + o.qty, 0);
          return { ...l, qty: Math.min(Math.max(0, stockCap(l.item) - others), l.qty + delta) };
        })
        .filter((l) => l.qty > 0)
    );
  }

  // ── Server pricing (M7-T4b): the panel never adds tax itself ──────────────
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [billDiscount, setBillDiscount] = useState("");
  const [voucherCode, setVoucherCode] = useState("");
  const [managerPin, setManagerPin] = useState("");
  useEffect(() => {
    if (cart.length === 0) {
      setQuote(null);
      return;
    }
    setQuoting(true);
    const handle = setTimeout(() => {
      api<Quote>("/pos/quote", {
        token,
        body: {
          lines: cart.map((l) => ({ ...cartBody([l])[0], line_discount: l.discount })),
          bill_discount: Number(billDiscount || 0),
          voucher_code: voucherCode.trim() || null,
          order_type: orderType,
        },
      })
        .then((q) => {
          setQuote(q);
          setPanelError(null);
        })
        .catch((e: unknown) => {
          setQuote(null);
          setPanelError(e instanceof ApiError ? e.detail : "Total belum bisa dihitung — periksa koneksi.");
        })
        .finally(() => setQuoting(false));
    }, 150);
    return () => clearTimeout(handle);
  }, [cart, billDiscount, voucherCode, token, orderType]);
  const cartTotal = quote ? Number(quote.total) : cart.reduce((s, l) => s + linePrice(l) * l.qty - l.discount, 0);

  // ── Active orders: polled, never pushed (café wifi) ──────────────────────
  const [active, setActive] = useState<ActiveOrder[] | null>(null);
  const [activeError, setActiveError] = useState<string | null>(null);
  const [activeLoading, setActiveLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const loadActive = useCallback(async () => {
    setActiveLoading(true);
    try {
      const rows = await api<ActiveOrder[]>("/pos/active-orders", { token });
      setActive(rows);
      setActiveError(null);
      return rows;
    } catch (e: unknown) {
      // Keep the last good list on screen, and say it may be old.
      setActiveError(e instanceof ApiError ? e.detail : "Tidak bisa terhubung ke server");
      return null;
    } finally {
      setActiveLoading(false);
    }
  }, [token]);
  useEffect(() => {
    void loadActive();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void loadActive();
    }, 6000);
    return () => clearInterval(id);
  }, [loadActive]);
  const unpaidQr = (active ?? []).filter((o) => o.payment === "unpaid" && o.source === "menu").length;
  const readyCount = (active ?? []).filter((o) => o.prep === "ready").length;

  // ── Holding, resuming, switching customers ───────────────────────────────
  /** Save what is on the panel to the server. Returns false (and says why)
   *  when it could not, so nobody's order is silently thrown away. */
  async function holdCurrent(quiet = false): Promise<boolean> {
    if (cart.length === 0) return true;
    if (ctx.kind === "open" && !dirty) return true;
    if (orderType === "delivery") {
      setPanelError("Pesanan antar dibayar langsung — tidak bisa disimpan untuk nanti.");
      setView("new");
      return false;
    }
    setBusy(true);
    setPanelError(null);
    try {
      let saved: ActiveOrder;
      if (ctx.kind === "open") {
        saved = await api<ActiveOrder>(`/pos/open-orders/${ctx.id}`, {
          token,
          method: "PUT",
          body: { rev: ctx.rev, lines: cartBody(cart), order_type: orderType, guest_name: guestName, table_label: tableLabel, external_ref: externalRef },
        });
      } else {
        saved = await api<ActiveOrder>("/pos/drafts", {
          token,
          body: {
            lines: cartBody(cart),
            order_type: orderType,
            guest_name: guestName.trim() || null,
            table_label: orderType === "dine_in" ? tableLabel.trim() || null : null,
            client_ref: holdRef.current,
            parent_order_id: ctx.kind === "addition" ? ctx.parentId : null,
            external_ref: orderType === "dine_in" ? null : externalRef.trim() || null,
          },
        });
      }
      showToast(`${orderLabel(saved)} disimpan${saved.guest_name ? ` · ${saved.guest_name}` : ""} — ada di Pesanan aktif`);
      resetOrder();
      void loadActive();
      return true;
    } catch (e: unknown) {
      setPanelError(e instanceof ApiError ? e.detail : "Belum tersimpan — periksa koneksi, lalu coba lagi.");
      if (!quiet) setDrawer(true);
      setView("new");
      void loadActive();
      return false;
    } finally {
      setBusy(false);
    }
  }

  function loadIntoPanel(o: ActiveOrder) {
    const lines: CartLine[] = [];
    const missing: string[] = [];
    for (const l of o.lines) {
      const item = l.item_id ? itemById.get(l.item_id) : undefined;
      if (!item) {
        missing.push(l.name);
        continue;
      }
      const variant = l.variant_id ? item.variants.find((v) => v.id === l.variant_id) ?? null : selectedVariant(item, freshSelection(item));
      const mods = item.modifier_groups.flatMap((g) => g.modifiers).filter((m) => l.modifier_ids.includes(m.id));
      lines.push({ uid: `l${++lineSeq}`, item, variant, modifiers: mods, qty: Number(l.quantity), discount: 0, notes: l.notes ?? "" });
    }
    const type = HELD_TYPES.includes(o.order_type) ? o.order_type : "takeaway";
    setCart(lines);
    setOrderType(type);
    setGuestName(o.guest_name ?? "");
    setTableLabel(o.table_label ?? "");
    setExternalRef(o.external_ref ?? "");
    setCtx({
      kind: "open",
      id: o.id,
      rev: o.rev,
      code: o.order_no,
      source: o.source,
      guest: o.guest_name,
      baseline: fingerprint(lines, type, o.guest_name ?? "", o.table_label ?? ""),
    });
    payRef.current = newRef();
    setPanelError(missing.length ? `${missing.join(", ")} sudah tidak ada di menu — ganti atau hapus sebelum dibayar.` : null);
    setView("new");
  }

  async function switchTo(o: ActiveOrder) {
    if (ctx.kind === "open" && ctx.id === o.id) {
      setView("new");
      return true;
    }
    if (hasWork && !(await holdCurrent(true))) return false;
    loadIntoPanel(o);
    return true;
  }

  async function startAddition(o: ActiveOrder) {
    if (hasWork && !(await holdCurrent(true))) return;
    resetOrder();
    setCtx({ kind: "addition", parentId: o.id, parentCode: o.order_no });
    setGuestName(o.guest_name ?? "");
    setOrderType(HELD_TYPES.includes(o.order_type) ? o.order_type : "takeaway");
    setTableLabel(o.table_label ?? "");
    setView("new");
  }

  function closePanelWork() {
    if (ctx.kind === "new") {
      setCart([]);
      setPanelError(null);
      return;
    }
    if (ctx.kind === "open" && dirty && !window.confirm("Perubahan belum disimpan. Tutup tanpa menyimpan?")) return;
    resetOrder();
  }

  // ── Customers, points, payment (M7-T4b, M8) ─────────────────────────────
  const [paying, setPaying] = useState(false);
  const [payMode, setPayMode] = useState<PayMode>("cash");
  const [cashPart, setCashPart] = useState("");
  const [deliveryAddress, setDeliveryAddress] = useState("");
  const [deliveryPhone, setDeliveryPhone] = useState("");
  const [customer, setCustomer] = useState<CustomerLite | null>(null);
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerMatches, setCustomerMatches] = useState<CustomerLite[]>([]);
  const [customerNew, setCustomerNew] = useState<{ name: string; phone: string } | null>(null);
  const [customerError, setCustomerError] = useState<string | null>(null);
  const [loyalty, setLoyalty] = useState<Loyalty | null>(null);
  const [usePoints, setUsePoints] = useState(false);
  const [payError, setPayError] = useState<string | null>(null);
  const [flash, setFlash] = useState<OrderResult | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [lineDiscountFor, setLineDiscountFor] = useState<string | null>(null);
  const [lineDiscountDraft, setLineDiscountDraft] = useState("");

  useEffect(() => {
    api<Loyalty>("/pos/loyalty", { token }).then(setLoyalty).catch(() => setLoyalty(null));
  }, [token]);
  useEffect(() => {
    if (!paying || customer || customerQuery.trim().length < 2) {
      setCustomerMatches([]);
      return;
    }
    const handle = setTimeout(() => {
      api<CustomerLite[]>(`/pos/customers?q=${encodeURIComponent(customerQuery.trim())}`, { token })
        .then(setCustomerMatches)
        .catch(() => setCustomerMatches([]));
    }, 200);
    return () => clearTimeout(handle);
  }, [customerQuery, paying, customer, token]);

  async function quickAddCustomer() {
    if (!customerNew || !customerNew.name.trim()) return;
    setCustomerError(null);
    try {
      const row = await api<CustomerLite>("/pos/customers", {
        token,
        body: { name: customerNew.name.trim(), phone: customerNew.phone.trim() || null },
      });
      setCustomer(row);
      setCustomerNew(null);
      setCustomerQuery("");
    } catch (e: unknown) {
      setCustomerError(e instanceof ApiError ? e.detail : "Gagal menyimpan pelanggan.");
    }
  }

  const anyDiscount = Number(billDiscount || 0) > 0 || cart.some((l) => l.discount > 0);
  const needsPin = anyDiscount && (quote?.discount_requires_pin ?? true);
  const pointValue = Number(loyalty?.point_value ?? 0);
  const pointsUsable =
    usePoints && loyalty?.is_active && customer && pointValue > 0
      ? Math.min(Math.max(0, customer.points_balance), Math.floor(cartTotal / pointValue))
      : 0;
  const pointsAmount = pointsUsable >= (loyalty?.min_redeem_points ?? 0) ? pointsUsable * pointValue : 0;
  const moneyDue = cartTotal - pointsAmount;
  const cashAmount = payMode === "cash" ? moneyDue : payMode === "qris" ? 0 : Number(cashPart || 0);
  const qrisAmount = moneyDue - cashAmount;
  const splitValid = payMode !== "split" || (cashAmount > 0 && cashAmount < moneyDue) || moneyDue === 0;

  function openPay() {
    if (cart.length === 0) return;
    const missing = cart.filter((l) => !itemById.has(l.item.id));
    if (missing.length) {
      setPanelError("Ada item yang sudah tidak ada di menu — hapus dulu.");
      return;
    }
    setPayError(null);
    setDrawer(false);
    setPaying(true);
  }

  async function confirmPay() {
    if (cart.length === 0 || busy || !splitValid) return;
    if (orderType === "delivery" && !deliveryAddress.trim()) return setPayError("Pesanan antar perlu alamat pengantaran.");
    if (orderType === "delivery" && !deliveryPhone.trim() && !customer?.phone) return setPayError("Pesanan antar perlu nomor HP penerima.");
    if (needsPin && managerPin.length < 4) return setPayError("Diskon perlu PIN manajer — minta pemilik atau manajer memasukkan PIN-nya.");
    setBusy(true);
    setPayError(null);
    const payments = [
      ...(pointsAmount > 0 ? [{ method: "points", amount: pointsAmount }] : []),
      ...(cashAmount > 0 ? [{ method: "cash", amount: cashAmount }] : []),
      ...(qrisAmount > 0 ? [{ method: "qris", amount: qrisAmount }] : []),
    ];
    try {
      let res: OrderResult;
      if (ctx.kind === "open") {
        let rev = ctx.rev;
        if (dirty) {
          const saved = await api<ActiveOrder>(`/pos/open-orders/${ctx.id}`, {
            token,
            method: "PUT",
            body: { rev, lines: cartBody(cart), order_type: orderType, guest_name: guestName, table_label: tableLabel, external_ref: externalRef },
          });
          rev = saved.rev;
          setCtx({ ...ctx, rev, baseline: fingerprint(cart, orderType, guestName, tableLabel) });
        }
        res = await api<OrderResult>(`/pos/tickets/${ctx.id}/settle`, {
          token,
          body: {
            payments,
            rev,
            client_ref: payRef.current,
            bill_discount: Number(billDiscount || 0),
            manager_pin: needsPin ? managerPin || null : null,
            customer_id: customer?.id ?? null,
            voucher_code: quote?.voucher_code ?? null,
          },
        });
      } else {
        res = await api<OrderResult>("/pos/orders", {
          token,
          body: {
            lines: cart.map((l) => ({ ...cartBody([l])[0], line_discount: l.discount })),
            payments,
            order_type: orderType,
            table_label: orderType === "dine_in" ? tableLabel.trim() || null : null,
            delivery_address: orderType === "delivery" ? deliveryAddress.trim() || null : null,
            guest_name: guestName.trim() || null,
            guest_phone: orderType === "delivery" ? deliveryPhone.trim() || null : null,
            bill_discount: Number(billDiscount || 0),
            manager_pin: needsPin ? managerPin || null : null,
            customer_id: customer?.id ?? null,
            voucher_code: quote?.voucher_code ?? null,
            client_ref: payRef.current,
            parent_order_id: ctx.kind === "addition" ? ctx.parentId : null,
            external_ref: orderType === "dine_in" ? null : externalRef.trim() || null,
          },
        });
      }
      setFlash(res);
      window.setTimeout(() => setFlash((f) => (f?.id === res.id ? null : f)), 6000);
      setPaying(false);
      setPayMode("cash");
      setCashPart("");
      setCustomerQuery("");
      setCustomerNew(null);
      resetOrder();
      loadItems();
      loadShift();
      void loadActive();
    } catch (e: unknown) {
      // The payment reference is kept: tapping "Bayar" again after a dropped
      // connection replays this same payment instead of charging twice.
      setPayError(e instanceof ApiError ? e.detail : "Koneksi terputus — tekan Bayar lagi. Pembayaran tidak akan tercatat dua kali.");
      if (e instanceof ApiError && e.status === 409) void loadActive();
    } finally {
      setBusy(false);
    }
  }

  async function printReceipt(orderId: string) {
    try {
      setReceipt(await api<Receipt>(`/pos/orders/${orderId}/receipt`, { token }));
    } catch (e: unknown) {
      showToast(e instanceof ApiError ? e.detail : "Struk tidak bisa dimuat.");
    }
  }

  // ── Active-order actions ─────────────────────────────────────────────────
  const actions: ActiveActions = {
    pay: async (o) => {
      if (await switchTo(o)) openPay();
    },
    resume: (o) => void switchTo(o),
    cancel: async (o, reason) => {
      setBusyId(o.id);
      try {
        await api(`/pos/tickets/${o.id}/cancel`, { token, body: { reason: reason || null } });
        if (ctx.kind === "open" && ctx.id === o.id) resetOrder();
        showToast(`${orderLabel(o)} dibatalkan`);
        await loadActive();
        return true;
      } catch (e: unknown) {
        showToast(e instanceof ApiError ? e.detail : "Gagal membatalkan — coba lagi.");
        await loadActive();
        return false;
      } finally {
        setBusyId(null);
      }
    },
    reprice: async (o) => {
      setBusyId(o.id);
      try {
        const updated = await api<ActiveOrder>(`/pos/open-orders/${o.id}/reprice`, { token, body: { rev: o.rev } });
        showToast(`${orderLabel(o)} diperbarui ke harga sekarang · ${formatRupiah(updated.total)}`);
        if (ctx.kind === "open" && ctx.id === o.id) loadIntoPanel(updated);
        await loadActive();
      } catch (e: unknown) {
        showToast(e instanceof ApiError ? e.detail : "Gagal memperbarui harga — coba lagi.");
        await loadActive();
      } finally {
        setBusyId(null);
      }
    },
    addTo: (o) => void startAddition(o),
    handover: async (o) => {
      setBusyId(o.id);
      try {
        await api(`/pos/kitchen/${o.id}/state`, { token, body: { state: "done", expected: "ready" } });
        showToast(`${orderLabel(o)} sudah diserahkan`);
      } catch (e: unknown) {
        showToast(e instanceof ApiError ? e.detail : "Gagal menyimpan — coba lagi.");
      } finally {
        setBusyId(null);
        await loadActive();
      }
    },
    print: (o) => void printReceipt(o.id),
  };

  // ── Shift and cash (M7) ──────────────────────────────────────────────────
  const [shift, setShift] = useState<Shift | null | undefined>(undefined);
  const [shiftSheet, setShiftSheet] = useState<"open" | "close" | null>(null);
  const [shiftAmount, setShiftAmount] = useState("");
  const [shiftNote, setShiftNote] = useState("");
  const [shiftResult, setShiftResult] = useState<Shift | null>(null);
  const [shiftError, setShiftError] = useState<string | null>(null);
  const loadShift = useCallback(() => {
    api<Shift | null>("/pos/shift", { token }).then(setShift).catch(() => setShift(null));
  }, [token]);
  useEffect(loadShift, [loadShift]);

  async function submitShift() {
    const amount = Number(shiftAmount || 0);
    if (!Number.isFinite(amount) || amount < 0) return setShiftError("Masukkan angka yang benar.");
    setBusy(true);
    setShiftError(null);
    try {
      if (shiftSheet === "open") {
        setShift(await api<Shift>("/pos/shift/open", { token, body: { opening_float: amount } }));
      } else {
        const res = await api<Shift>("/pos/shift/close", { token, body: { counted_cash: amount, notes: shiftNote.trim() || null } });
        setShift(null);
        setShiftResult(res);
      }
      setShiftSheet(null);
      setShiftAmount("");
      setShiftNote("");
    } catch (e: unknown) {
      setShiftError(e instanceof ApiError ? e.detail : "Gagal menyimpan — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  const [cashSheet, setCashSheet] = useState(false);
  const [cashKind, setCashKind] = useState<CashKind>("petty_cash");
  const [cashInput, setCashInput] = useState("");
  const [cashReason, setCashReason] = useState("");
  const [cashCategory, setCashCategory] = useState("operasional");
  const [cashSupplier, setCashSupplier] = useState("");
  const [suppliers, setSuppliers] = useState<SupplierLite[]>([]);
  const [cashError, setCashError] = useState<string | null>(null);
  function openCashSheet() {
    setCashError(null);
    setCashSheet(true);
    if (suppliers.length === 0) api<SupplierLite[]>("/pos/suppliers", { token }).then(setSuppliers).catch(() => setSuppliers([]));
  }
  async function submitCash() {
    const amount = Number(cashInput || 0);
    if (!Number.isFinite(amount) || amount <= 0) return setCashError("Masukkan jumlah lebih dari nol.");
    if (!cashReason.trim()) return setCashError("Tulis alasannya.");
    setBusy(true);
    setCashError(null);
    try {
      await api("/pos/cash", {
        token,
        body: {
          kind: cashKind,
          amount,
          reason: cashReason.trim(),
          category: cashKind === "petty_cash" ? cashCategory : null,
          supplier_id: cashKind === "supplier_payment" ? cashSupplier || null : null,
        },
      });
      setCashSheet(false);
      setCashInput("");
      setCashReason("");
      showToast(
        `${cashKind === "cash_in" ? "Kas masuk" : cashKind === "bank_drop" ? "Setor bank" : cashKind === "supplier_payment" ? "Bayar supplier" : "Kas keluar"} ${formatRupiah(amount)} dicatat`
      );
      loadShift();
    } catch (e: unknown) {
      setCashError(e instanceof ApiError ? e.detail : "Gagal menyimpan — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  // ── Panel props, shared by the side panel and the drawer ────────────────
  const panelCtx: PanelContext =
    ctx.kind === "new"
      ? { kind: "new" }
      : ctx.kind === "open"
        ? { kind: "open", code: ctx.code, source: ctx.source, guest: ctx.guest, dirty }
        : { kind: "addition", parentCode: ctx.parentCode };
  const panelLines = cart.map((l) => ({
    uid: l.uid,
    title: displayName(l.item, l.variant),
    modifiers: l.modifiers.map((m) => m.name),
    notes: l.notes,
    qty: l.qty,
    unitPrice: linePrice(l),
    discount: l.discount,
    maxQty: Math.max(0, stockCap(l.item) - cart.filter((o) => o.item.id === l.item.id && o.uid !== l.uid).reduce((n, o) => n + o.qty, 0)),
  }));
  const panel = (onClose?: () => void) => (
    <OrderPanel
      context={panelCtx}
      lines={panelLines}
      quote={quote}
      quoting={quoting}
      orderType={orderType}
      orderTypes={ctx.kind === "new" ? NEW_TYPES : HELD_TYPES}
      onOrderType={setOrderType}
      guestName={guestName}
      onGuestName={setGuestName}
      tableLabel={tableLabel}
      onTableLabel={setTableLabel}
      externalRef={externalRef}
      onExternalRef={setExternalRef}
      onQty={changeQty}
      onEdit={(uid) => {
        const l = cart.find((x) => x.uid === uid);
        if (l) setPicker({ item: l.item, editUid: uid, initial: lineSelection(l) });
      }}
      onDiscount={
        ctx.kind === "open"
          ? null
          : (uid) => {
              const l = cart.find((x) => x.uid === uid);
              setLineDiscountFor(uid);
              setLineDiscountDraft(l?.discount ? String(l.discount) : "");
            }
      }
      onClear={closePanelWork}
      onHold={() => void holdCurrent()}
      onPay={openPay}
      onClose={onClose}
      error={panelError}
      busy={busy}
    />
  );
  const count = cart.reduce((n, l) => n + l.qty, 0);

  return (
    <div className="min-h-[100dvh]">
      <header className="hairline-b sticky-bar sticky top-0 z-30">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 md:flex-nowrap">
          <div className="min-w-0 md:w-52">
            <p className="ink-faint truncate text-[12px] font-medium">{businessName}</p>
            <p className="truncate text-[15px] font-semibold tracking-[-0.01em]">Kasir · {staffName}</p>
          </div>
          <nav aria-label="Ruang kerja kasir" className="order-last w-full md:order-none md:w-auto md:flex-1 md:text-center">
            <div className="segmented flex w-full md:inline-flex md:w-auto" role="tablist">
              {(
                [
                  ["new", ctx.kind === "open" ? `Pesanan ${ctx.code}` : ctx.kind === "addition" ? "Tambah pesanan" : "Pesanan baru", count],
                  ["active", "Pesanan aktif", (active ?? []).length],
                  ["history", "Riwayat transaksi", null],
                ] as [View, string, number | null][]
              ).map(([id, label, n]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={view === id}
                  onClick={() => setView(id)}
                  className="segmented-item relative flex min-h-[2.5rem] flex-1 items-center justify-center gap-1.5 whitespace-nowrap px-3 md:flex-none md:px-4"
                >
                  {label}
                  {n !== null && n > 0 && <span className={`tabular-nums ${view === id ? "font-semibold" : "ink-faint"}`}>{n}</span>}
                  {id === "active" && (unpaidQr > 0 || readyCount > 0) && (
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ background: readyCount > 0 ? "var(--good)" : "var(--warn)" }}
                      aria-label={`${unpaidQr} pesanan QR menunggu bayar, ${readyCount} siap diambil`}
                    />
                  )}
                </button>
              ))}
            </div>
          </nav>
          <div className="ml-auto flex items-center gap-1.5 md:ml-0">
            {shift === undefined ? null : shift === null ? (
              <button
                onClick={() => {
                  setShiftError(null);
                  setShiftSheet("open");
                }}
                className="btn-quiet whitespace-nowrap px-3.5 py-2 text-sm"
                style={{ color: "var(--warn)" }}
              >
                Buka shift
              </button>
            ) : (
              <button
                onClick={() => {
                  setShiftError(null);
                  setShiftSheet("close");
                }}
                className="rounded-xl px-3 py-1.5 text-left transition-colors hover:bg-[color:var(--row-hover)]"
                title="Tutup shift"
              >
                <span className="ink-faint block text-[11px] font-medium leading-tight">Kas seharusnya</span>
                <span className="block text-sm font-semibold tabular-nums leading-tight">{formatRupiah(shift.expected_cash ?? shift.opening_float)}</span>
              </button>
            )}
            <button onClick={openCashSheet} className="icon-btn ink-soft h-10 w-auto gap-1.5 rounded-xl px-2.5 text-sm" title="Kas masuk / keluar">
              <IconWallet className="h-[18px] w-[18px]" /> <span className="hidden xl:inline">Kas</span>
            </button>
            <a href={`/kitchen/${pairingToken}`} target="_blank" rel="noreferrer" className="icon-btn ink-soft h-10 w-auto gap-1.5 rounded-xl px-2.5 text-sm" title="Buka layar dapur">
              <IconExternal className="h-[18px] w-[18px]" /> <span className="hidden xl:inline">Dapur</span>
            </a>
            <button onClick={onLock} className="icon-btn ink-soft h-10 w-auto gap-1.5 rounded-xl px-2.5 text-sm" title="Kunci kasir">
              <IconLock className="h-[18px] w-[18px]" /> <span className="hidden xl:inline">Kunci</span>
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] px-4">
        {view === "new" && (
          <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_390px] lg:gap-5">
            <div className="min-w-0 pb-32 pt-4 lg:pb-8">
              <div className="flex items-center gap-3">
                <label className="relative block min-w-0 flex-1">
                  <span className="sr-only">Cari menu</span>
                  <IconSearch className="ink-faint pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2" />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="field py-2.5 pl-10"
                    placeholder="Cari menu"
                    type="search"
                  />
                </label>
                {ctx.kind === "addition" && <span className="pill-quiet shrink-0 py-1 text-[13px]">Tambahan · Pesanan {ctx.parentCode}</span>}
              </div>
              {itemsError && (
                <p className="notice notice-bad mt-3 flex items-center justify-between gap-3">
                  {itemsError}
                  <button onClick={loadItems} className="underline underline-offset-2">
                    Coba lagi
                  </button>
                </p>
              )}
              {items === null ? (
                <div className="mt-4 grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-2.5">
                  {Array.from({ length: 12 }).map((_, i) => (
                    <div key={i} className="glass-card h-[5.75rem] animate-pulse" />
                  ))}
                </div>
              ) : visible.length === 0 ? (
                <p className="ink-soft mt-10 text-center text-sm">{search ? `Tidak ada menu "${search}".` : "Menu belum diisi — tambahkan barang di dashboard."}</p>
              ) : (
                <div className="mt-4 grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-2.5">
                  {visible.map((item) => {
                    const stock = Number(item.current_stock);
                    const out = !item.made_to_order && stock <= 0;
                    const low = !item.made_to_order && !out && stock <= Number(item.reorder_threshold);
                    const inCart = cart.filter((l) => l.item.id === item.id).reduce((n, l) => n + l.qty, 0);
                    const asks = !isQuickAdd(item);
                    const minPrice = item.variants.length > 1 ? Math.min(...item.variants.map((v) => Number(v.sell_price))) : Number(item.sell_price);
                    return (
                      <button
                        key={item.id}
                        disabled={out}
                        onClick={() => openProduct(item)}
                        className="glass-card group relative flex min-h-[5.75rem] flex-col items-start px-3.5 py-3 text-left transition-[transform,box-shadow] duration-150 hover:shadow-key active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45 disabled:active:scale-100"
                      >
                        {inCart > 0 && (
                          <span className="absolute right-2 top-2 flex h-6 min-w-6 items-center justify-center rounded-full bg-[color:var(--accent-fill)] px-1.5 text-xs font-semibold tabular-nums text-[color:var(--on-accent)]">
                            {inCart}
                          </span>
                        )}
                        <span className="line-clamp-2 pr-6 text-[15px] font-semibold leading-snug">{item.name}</span>
                        <span className="mt-auto flex w-full flex-wrap items-baseline justify-between gap-x-2 pt-1.5">
                          <span className="whitespace-nowrap text-sm font-medium tabular-nums">
                            {item.variants.length > 1 ? <span className="ink-faint font-normal">dari </span> : null}
                            {formatRupiah(item.variants.length > 1 ? minPrice : item.sell_price)}
                          </span>
                          <span className={`whitespace-nowrap text-[11.5px] font-medium ${low || out ? "" : "ink-faint"}`} style={low || out ? { color: "var(--warn)" } : undefined}>
                            {out ? "habis" : low ? `sisa ${formatQty(stock)}` : asks ? "ada pilihan" : ""}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Wide till: the order is always in view */}
            <aside className="hidden lg:block">
              <div className="glass-card sticky top-[4.5rem] my-4 h-[calc(100dvh-5.5rem)] overflow-hidden p-0">{panel()}</div>
            </aside>

            {/* Tablet: a persistent summary that opens the drawer */}
            <div className="fixed inset-x-0 bottom-0 z-20 px-4 pb-4 lg:hidden">
              <div className="dock mx-auto flex max-w-3xl items-center gap-3 rounded-3xl px-4 py-3">
                <button onClick={() => setDrawer(true)} className="min-w-0 flex-1 text-left" aria-label="Lihat pesanan">
                  <p className="ink-soft truncate text-[13px] font-medium">
                    {ctx.kind === "open" ? `Pesanan ${ctx.code}` : ctx.kind === "addition" ? `Tambahan · Pesanan ${ctx.parentCode}` : "Pesanan baru"} ·{" "}
                    {count > 0 ? `${count} item · lihat` : "kosong"}
                  </p>
                  <p className="text-[22px] font-semibold tabular-nums tracking-[-0.02em]">{formatRupiah(cartTotal)}</p>
                </button>
                <button onClick={() => setDrawer(true)} className="btn-quiet px-4 py-3 text-sm">
                  Pesanan
                </button>
                <button onClick={openPay} disabled={cart.length === 0 || busy} className="btn-accent px-6 py-3">
                  Bayar
                </button>
              </div>
            </div>
            {drawer && (
              <div className="sheet-scrim lg:hidden" onClick={() => setDrawer(false)}>
                <div className="sheet-panel h-[88dvh] sm:max-w-lg" onClick={(e) => e.stopPropagation()}>
                  {panel(() => setDrawer(false))}
                </div>
              </div>
            )}
          </div>
        )}

        {view === "active" && (
          <div className="pb-10 pt-4">
            <ActiveOrders
              orders={active}
              stale={activeLoading && active === null}
              error={activeError}
              busyId={busyId}
              actions={actions}
              onRetry={() => void loadActive()}
            />
          </div>
        )}

        {view === "history" && (
          <div className="mx-auto max-w-2xl pb-10 pt-4">
            <TransactionsView
              token={token}
              onReversed={() => {
                loadShift();
                loadItems();
                void loadActive();
              }}
            />
          </div>
        )}
      </main>

      {picker && (
        <ProductPicker
          key={`${picker.item.id}:${picker.editUid ?? "new"}`}
          product={picker.item}
          initial={picker.initial}
          editing={picker.editUid !== null}
          maxQty={Math.max(
            0,
            stockCap(picker.item) - cart.filter((l) => l.item.id === picker.item.id && l.uid !== picker.editUid).reduce((n, l) => n + l.qty, 0)
          )}
          meta={picker.item.made_to_order ? "dibuat saat dipesan" : `sisa ${formatQty(picker.item.current_stock)} ${picker.item.unit}`}
          onSubmit={(sel) => {
            putInCart(picker.item, sel, picker.editUid);
            setPicker(null);
          }}
          onRemove={() => {
            if (picker.editUid) setCart((c) => c.filter((l) => l.uid !== picker.editUid));
            setPicker(null);
          }}
          onClose={() => setPicker(null)}
        />
      )}

      {lineDiscountFor && (
        <div className="sheet-scrim z-[60]" onClick={() => setLineDiscountFor(null)}>
          <div className="sheet-panel block px-6 pb-8 pt-5 sm:max-w-sm" onClick={(e) => e.stopPropagation()}>
            <p className="text-[19px] font-semibold tracking-[-0.015em]">Diskon baris</p>
            <p className="ink-soft text-sm">Potongan rupiah untuk baris ini saja.</p>
            <input
              autoFocus
              inputMode="numeric"
              value={lineDiscountDraft}
              onChange={(e) => setLineDiscountDraft(e.target.value.replace(/[^0-9]/g, ""))}
              className="field mt-4 py-3 text-2xl font-semibold tabular-nums"
              placeholder="0"
              aria-label="Diskon dalam rupiah"
            />
            <div className="mt-5 flex gap-3">
              <button
                onClick={() => {
                  setCart((c) => c.map((l) => (l.uid === lineDiscountFor ? { ...l, discount: 0 } : l)));
                  setLineDiscountFor(null);
                }}
                className="btn-quiet flex-1 py-3"
              >
                Hapus diskon
              </button>
              <button
                onClick={() => {
                  const amount = Math.max(0, Number(lineDiscountDraft || 0));
                  setCart((c) => c.map((l) => (l.uid === lineDiscountFor ? { ...l, discount: amount } : l)));
                  setLineDiscountFor(null);
                }}
                className="btn-accent flex-1 py-3"
              >
                Simpan
              </button>
            </div>
          </div>
        </div>
      )}

      {paying && (
        <div className="sheet-scrim z-[55]" onClick={() => !busy && setPaying(false)}>
          <div role="dialog" aria-modal="true" aria-label="Pembayaran" className="sheet-panel sm:max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="overflow-y-auto px-6 pb-6 pt-5">
              <p className="ink-soft text-[13px] font-medium">
                {ctx.kind === "open" ? `Pesanan ${ctx.code}` : ctx.kind === "addition" ? `Tambahan · Pesanan ${ctx.parentCode}` : "Pesanan baru"} · {count} item
              </p>
              <p className="text-[32px] font-semibold tabular-nums tracking-[-0.025em]">{formatRupiah(cartTotal)}</p>
              {quote && Number(quote.promo_total) > 0 && (
                <p className="ink-soft text-sm">
                  Promo: {quote.promos.map((p) => p.name).join(", ")} (− {formatRupiah(quote.promo_total)})
                </p>
              )}

              {orderType === "delivery" && (
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <input
                    inputMode="tel"
                    value={deliveryPhone}
                    onChange={(e) => setDeliveryPhone(e.target.value.slice(0, 32))}
                    className="field col-span-2 text-sm tabular-nums"
                    placeholder={customer?.phone ? `HP: ${customer.phone}` : "Nomor HP penerima"}
                  />
                  <input
                    value={deliveryAddress}
                    onChange={(e) => setDeliveryAddress(e.target.value.slice(0, 300))}
                    className="field col-span-2 text-sm"
                    placeholder="Alamat pengantaran"
                  />
                </div>
              )}

              <div className="mt-4">
                <span className="text-[13px] font-medium">Pelanggan terdaftar (opsional)</span>
                {customer ? (
                  <div className="surface-inset mt-1 flex items-center justify-between rounded-2xl px-3 py-2 text-sm">
                    <span className="truncate">
                      <span className="font-semibold">{customer.name}</span>
                      {customer.phone ? <span className="ink-faint"> · {customer.phone}</span> : null}
                      {loyalty?.is_active ? <span className="ink-faint"> · {customer.points_balance} poin</span> : null}
                    </span>
                    <button
                      onClick={() => {
                        setCustomer(null);
                        setUsePoints(false);
                      }}
                      className="ink-soft ml-2 shrink-0 rounded-lg px-2 py-1 text-xs"
                    >
                      ganti
                    </button>
                  </div>
                ) : customerNew ? (
                  <div className="mt-1 grid grid-cols-2 gap-2">
                    <input autoFocus value={customerNew.name} onChange={(e) => setCustomerNew({ ...customerNew, name: e.target.value })} className="field text-sm" placeholder="Nama" />
                    <input inputMode="tel" value={customerNew.phone} onChange={(e) => setCustomerNew({ ...customerNew, phone: e.target.value })} className="field text-sm tabular-nums" placeholder="Nomor HP" />
                    <button onClick={quickAddCustomer} className="btn-accent py-2 text-sm">
                      Simpan pelanggan
                    </button>
                    <button onClick={() => setCustomerNew(null)} className="btn-quiet py-2 text-sm">
                      Batal
                    </button>
                    {customerError && <p className="col-span-2 text-xs text-[color:var(--bad)]">{customerError}</p>}
                  </div>
                ) : (
                  <div className="relative mt-1">
                    <input value={customerQuery} onChange={(e) => setCustomerQuery(e.target.value)} className="field text-sm" placeholder="Cari nama atau nomor HP" />
                    {(customerMatches.length > 0 || customerQuery.trim().length >= 2) && (
                      <ul className="popover-panel absolute left-0 right-0 top-full mt-1 text-sm" style={{ position: "absolute" }}>
                        {customerMatches.map((m) => (
                          <li key={m.id}>
                            <button
                              onClick={() => {
                                setCustomer(m);
                                setCustomerQuery("");
                              }}
                              className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left hover:bg-[color:var(--row-hover)]"
                            >
                              <span className="truncate">{m.name}</span>
                              <span className="ink-faint ml-2 shrink-0 text-xs tabular-nums">{m.phone ?? ""}</span>
                            </button>
                          </li>
                        ))}
                        <li>
                          <button
                            onClick={() => {
                              const q = customerQuery.trim();
                              setCustomerNew(/^[\d+\s-]+$/.test(q) ? { name: "", phone: q } : { name: q, phone: "" });
                            }}
                            className="ink-soft w-full rounded-xl px-3 py-2 text-left text-xs hover:bg-[color:var(--row-hover)]"
                          >
                            + Pelanggan baru &ldquo;{customerQuery.trim()}&rdquo;
                          </button>
                        </li>
                      </ul>
                    )}
                  </div>
                )}
              </div>

              <div className="mt-3 grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="text-[13px] font-medium">Kode voucher</span>
                  <input value={voucherCode} onChange={(e) => setVoucherCode(e.target.value.toUpperCase())} className="field mt-1 font-mono text-sm" placeholder="HEMAT5" />
                </label>
                <label className="block">
                  <span className="text-[13px] font-medium">Diskon struk (Rp)</span>
                  <input inputMode="numeric" value={billDiscount} onChange={(e) => setBillDiscount(e.target.value.replace(/[^0-9]/g, ""))} className="field mt-1 text-sm tabular-nums" placeholder="0" />
                </label>
                {quote?.voucher_error && voucherCode.trim() && <p className="col-span-2 text-xs text-[color:var(--bad)]">{quote.voucher_error}</p>}
                {needsPin && (
                  <label className="col-span-2 block">
                    <span className="text-[13px] font-medium">PIN pemilik / manajer untuk diskon</span>
                    <input
                      type="password"
                      inputMode="numeric"
                      value={managerPin}
                      onChange={(e) => setManagerPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
                      className="field mt-1 text-sm tabular-nums"
                      placeholder="••••"
                    />
                  </label>
                )}
              </div>

              {loyalty?.is_active && customer && customer.points_balance >= (loyalty.min_redeem_points || 1) && (
                <label className="surface-inset mt-3 flex items-center justify-between rounded-2xl px-3 py-2.5 text-sm">
                  <span className="flex items-center gap-2">
                    <input type="checkbox" checked={usePoints} onChange={(e) => setUsePoints(e.target.checked)} />
                    Pakai poin <span className="ink-faint">({customer.points_balance} poin)</span>
                  </span>
                  {pointsAmount > 0 && <span className="font-semibold tabular-nums">− {formatRupiah(pointsAmount)}</span>}
                </label>
              )}

              <div className="segmented mt-4 flex w-full" role="group" aria-label="Cara bayar">
                {(
                  [
                    ["cash", "Tunai"],
                    ["qris", "QRIS"],
                    ["split", "Tunai + QRIS"],
                  ] as [PayMode, string][]
                ).map(([mode, label]) => (
                  <button key={mode} onClick={() => setPayMode(mode)} aria-pressed={payMode === mode} className="segmented-item min-h-[2.75rem] flex-1">
                    {label}
                  </button>
                ))}
              </div>
              {payMode === "split" && (
                <div className="mt-3">
                  <label className="text-[13px] font-medium" htmlFor="cash-part">
                    Bagian tunai (sisanya QRIS)
                  </label>
                  <input id="cash-part" inputMode="numeric" value={cashPart} onChange={(e) => setCashPart(e.target.value.replace(/[^0-9]/g, ""))} placeholder="0" className="field mt-1 py-3 text-xl font-semibold tabular-nums" />
                  <p className="ink-soft mt-1 text-sm tabular-nums">
                    Tunai {formatRupiah(cashAmount)} · QRIS {formatRupiah(Math.max(0, qrisAmount))}
                    {!splitValid && cashPart !== "" && " — tunai harus di antara 0 dan total"}
                  </p>
                </div>
              )}
              {pointsAmount > 0 && <p className="ink-soft mt-2 text-sm">Sisa dibayar {formatRupiah(moneyDue)}</p>}

              {payError && (
                <p role="alert" className="notice notice-bad mt-4">
                  {payError}
                </p>
              )}
              <div className="mt-5 flex gap-2">
                <button onClick={() => setPaying(false)} disabled={busy} className="btn-quiet px-5 py-3.5">
                  Kembali
                </button>
                <button onClick={confirmPay} disabled={busy || !splitValid} className="btn-accent flex-1 py-3.5 text-base">
                  {busy ? "Memproses…" : `Terima ${formatRupiah(moneyDue)}`}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {shiftSheet && (
        <div className="sheet-scrim" onClick={() => !busy && setShiftSheet(null)}>
          <div className="sheet-panel block overflow-y-auto px-6 pb-8 pt-5 sm:max-w-md" onClick={(e) => e.stopPropagation()}>
            <p className="text-[19px] font-semibold tracking-[-0.015em]">{shiftSheet === "open" ? "Buka shift" : "Tutup shift"}</p>
            {shiftSheet === "open" ? (
              <p className="ink-soft text-sm">Modal awal di laci kasir.</p>
            ) : shift ? (
              <dl className="mt-3 space-y-1 text-sm">
                <Row label="Modal awal" value={formatRupiah(shift.opening_float)} />
                <Row label="Penjualan tunai" value={`+${formatRupiah(shift.cash_sales)}`} />
                {Number(shift.cash_refunds) > 0 && <Row label="Refund tunai" value={`−${formatRupiah(shift.cash_refunds)}`} />}
                {Number(shift.cash_in) > 0 && <Row label="Kas masuk" value={`+${formatRupiah(shift.cash_in)}`} />}
                {Number(shift.cash_out) > 0 && <Row label="Kas keluar" value={`−${formatRupiah(shift.cash_out)}`} />}
                <div className="hairline-t flex justify-between pt-1 font-semibold">
                  <dt>Kas seharusnya</dt>
                  <dd className="tabular-nums">{formatRupiah(shift.expected_cash ?? 0)}</dd>
                </div>
              </dl>
            ) : null}
            <label className="mt-5 block">
              <span className="text-[13px] font-medium">{shiftSheet === "open" ? "Modal awal (Rp)" : "Uang dihitung (Rp)"}</span>
              <input autoFocus inputMode="numeric" value={shiftAmount} onChange={(e) => setShiftAmount(e.target.value.replace(/[^0-9]/g, ""))} className="field mt-1 py-3 text-2xl font-semibold tabular-nums" placeholder="0" />
            </label>
            {shiftSheet === "close" && <input value={shiftNote} onChange={(e) => setShiftNote(e.target.value)} className="field mt-3 text-sm" placeholder="Catatan (opsional)" />}
            {shiftError && <p className="notice notice-bad mt-3">{shiftError}</p>}
            <div className="mt-5 flex gap-3">
              <button onClick={() => setShiftSheet(null)} className="btn-quiet flex-1 py-3">
                Batal
              </button>
              <button onClick={submitShift} disabled={busy} className="btn-accent flex-1 py-3">
                {shiftSheet === "open" ? "Buka shift" : "Tutup shift"}
              </button>
            </div>
          </div>
        </div>
      )}

      {shiftResult && (
        <div className="sheet-scrim" onClick={() => setShiftResult(null)}>
          <div className="sheet-panel block px-6 pb-8 pt-5 sm:max-w-sm" onClick={(e) => e.stopPropagation()}>
            <p className="text-[19px] font-semibold tracking-[-0.015em]">Shift ditutup · {shiftResult.staff_name}</p>
            <dl className="mt-4 space-y-2 text-sm">
              <Row label="Kas seharusnya" value={formatRupiah(shiftResult.expected_cash ?? 0)} />
              <Row label="Uang dihitung" value={formatRupiah(shiftResult.counted_cash ?? 0)} />
              <div className="flex justify-between">
                <dt className="ink-soft">Selisih</dt>
                <dd className={`font-semibold tabular-nums ${Number(shiftResult.variance) < 0 ? "text-[color:var(--bad)]" : ""}`}>
                  {Number(shiftResult.variance) > 0 ? "+" : ""}
                  {formatRupiah(shiftResult.variance ?? 0)}
                </dd>
              </div>
            </dl>
            <button onClick={() => setShiftResult(null)} className="btn-accent mt-6 w-full py-3">
              Oke
            </button>
          </div>
        </div>
      )}

      {cashSheet && (
        <div className="sheet-scrim" onClick={() => !busy && setCashSheet(false)}>
          <div className="sheet-panel block overflow-y-auto px-6 pb-8 pt-5 sm:max-w-md" onClick={(e) => e.stopPropagation()}>
            <p className="text-[19px] font-semibold tracking-[-0.015em]">Kas masuk / keluar</p>
            <p className="ink-soft text-sm">Uang laci di luar penjualan. Semua masuk pembukuan.</p>
            <div className="mt-4 grid grid-cols-2 gap-2">
              {(
                [
                  ["petty_cash", "Kas keluar (beli kecil)"],
                  ["cash_in", "Kas masuk"],
                  ["supplier_payment", "Bayar supplier"],
                  ["bank_drop", "Setor ke bank"],
                ] as [CashKind, string][]
              ).map(([kind, label]) => (
                <button key={kind} onClick={() => setCashKind(kind)} aria-pressed={cashKind === kind} className="choice-card min-h-[2.75rem] px-3 py-2.5 text-sm">
                  {label}
                </button>
              ))}
            </div>
            <label className="mt-4 block">
              <span className="text-[13px] font-medium">Jumlah (Rp)</span>
              <input autoFocus inputMode="numeric" value={cashInput} onChange={(e) => setCashInput(e.target.value.replace(/[^0-9]/g, ""))} className="field mt-1 py-3 text-2xl font-semibold tabular-nums" placeholder="0" />
            </label>
            <input value={cashReason} onChange={(e) => setCashReason(e.target.value)} className="field mt-3 text-sm" placeholder={cashKind === "petty_cash" ? "Beli apa? (mis. es batu)" : "Alasan"} />
            {cashKind === "petty_cash" && (
              <div className="mt-3 flex flex-wrap gap-2">
                {["bahan baku", "operasional", "lainnya"].map((cat) => (
                  <button key={cat} onClick={() => setCashCategory(cat)} aria-pressed={cashCategory === cat} className="choice-card px-3 py-1.5 text-xs">
                    {cat}
                  </button>
                ))}
              </div>
            )}
            {cashKind === "supplier_payment" && (
              <Select
                variant="field"
                className="mt-3"
                placeholder="Pilih supplier…"
                ariaLabel="Supplier"
                options={suppliers.map((sp) => ({ value: sp.id, label: sp.name }))}
                value={cashSupplier}
                onChange={setCashSupplier}
              />
            )}
            {cashError && <p className="notice notice-bad mt-3">{cashError}</p>}
            <div className="mt-5 flex gap-3">
              <button onClick={() => setCashSheet(false)} className="btn-quiet flex-1 py-3">
                Batal
              </button>
              <button onClick={submitCash} disabled={busy} className="btn-accent flex-1 py-3">
                Catat
              </button>
            </div>
          </div>
        </div>
      )}

      {flash && (
        <div className="fixed inset-x-0 bottom-24 z-40 flex justify-center px-4 lg:bottom-8" role="status">
          <div className="dock flex max-w-xl animate-scale-in items-center gap-3 rounded-3xl px-5 py-3.5">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white" style={{ background: "var(--good)" }}>
              <IconCheck className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="truncate font-semibold tabular-nums">
                Pesanan {flash.order_no}
                {flash.batch_no ? ` · Tambahan ${flash.batch_no}` : ""} · lunas {formatRupiah(flash.total)}
              </p>
              <p className="ink-soft truncate text-xs">
                {flash.payments.map((p) => `${p.method === "cash" ? "tunai" : p.method === "points" ? "poin" : p.method.toUpperCase()} ${formatRupiah(p.amount)}`).join(" + ")}
                {" · "}masuk dapur
              </p>
            </div>
            <button onClick={() => void printReceipt(flash.id)} className="btn-quiet ml-1 shrink-0 px-3 py-2 text-sm">
              <IconPrinter className="h-4 w-4" /> Struk
            </button>
            <button onClick={() => setFlash(null)} className="ink-soft shrink-0 rounded-lg px-2 py-2 text-sm" aria-label="Tutup">
              ✕
            </button>
          </div>
        </div>
      )}

      {toast && (
        <div className="pointer-events-none fixed inset-x-0 top-20 z-[70] flex justify-center px-4" role="status" aria-live="polite">
          <p className="dock animate-scale-in rounded-2xl px-4 py-2.5 text-sm font-medium">{toast}</p>
        </div>
      )}

      {receipt && <ReceiptSheet receipt={receipt} onClose={() => setReceipt(null)} />}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="ink-soft">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}
