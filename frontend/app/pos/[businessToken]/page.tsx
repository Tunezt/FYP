"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError, POS_PAIRING_KEY, POS_TOKEN_KEY } from "@/lib/api";
import { Select } from "@/components/Select";
import { IconBackspace, IconCheck, IconExternal, IconLock, IconPlugOff } from "@/components/icons";
import { formatQty, formatRupiah, initials } from "@/lib/format";
import { ORDER_TYPE_LABEL, type OrderType } from "@/lib/types";

type StaffLite = { id: string; name: string; role: string };
type PosBusiness = { business_name: string; staff: StaffLite[] };
type Variant = { id: string; name: string; sell_price: string; is_default: boolean };
type Modifier = { id: string; name: string; price_delta: string; is_default: boolean };
type ModifierGroup = {
  id: string;
  name: string;
  selection: "single" | "multi";
  is_required: boolean;
  min_select: number;
  max_select: number | null;
  modifiers: Modifier[];
};
type Item = {
  id: string;
  name: string;
  unit: string;
  current_stock: string;
  sell_price: string;
  reorder_threshold: string;
  variants: Variant[];
  modifier_groups: ModifierGroup[];
  made_to_order: boolean; // has a recipe: components are consumed, its own stock is not the limit
};
type Receipt = {
  order_id: string;
  number: string;
  business_name: string;
  staff_name: string | null;
  customer_name: string | null;
  points_earned: number;
  points_redeemed: number;
  status: string;
  order_type: string;
  table_label: string | null;
  delivery_address: string | null;
  delivery_fee: string;
  sold_at: string;
  lines: {
    name: string;
    variant: string | null;
    quantity: string;
    unit_price: string;
    line_total: string;
    modifiers: { name: string; price_delta: string }[];
    notes: string | null;
  }[];
  subtotal: string;
  discount_total: string;
  promo_total: string;
  promo_names: string[];
  voucher_total: string;
  voucher_code: string | null;
  service_charge: string;
  tax_total: string;
  tax_inclusive: boolean;
  rounding: string;
  total: string;
  payments: { method: string; amount: string }[];
};
type OrderResult = {
  id: string;
  total: string;
  points_earned: number;
  points_redeemed: number;
  lines: { item_name: string; quantity: string; line_total: string; remaining_stock: string }[];
  payments: { method: string; amount: string }[];
};
// A cart line is an item at one size (variant) with a set of chosen modifiers;
// stock is the item's. Same item + size + modifiers merge into one line.
type CartLine = { item: Item; variant: Variant | null; modifiers: Modifier[]; qty: number; discount: number };
// What the server says the cart comes to (M7-T4b) — the kiosk never adds tax
// or rounding itself, so the screen and the ledger cannot disagree.
type Quote = {
  subtotal: string;
  discount_total: string;
  promo_total: string;
  promos: { promo_id: string; name: string; amount: string; bonus_quantity: string }[];
  voucher_total: string;
  voucher_code: string | null;
  voucher_error: string | null;
  lines: { item_id: string; quantity: string; is_bonus: boolean; promo_name: string | null; promo_discount: string }[];
  service_charge: string;
  delivery_fee: string;
  tax_total: string;
  tax_inclusive: boolean;
  rounding: string;
  total: string;
  discount_requires_pin: boolean;
};
type PayMode = "cash" | "qris" | "split";
// Till session (M7-T1). Money as strings straight from the API (numeric(12,2)).
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
// Cash in and out (M7-T2).
type CashKind = "cash_in" | "petty_cash" | "supplier_payment" | "bank_drop";
type SupplierLite = { id: string; name: string };
// Customers at the till (M8-T1).
type CustomerLite = { id: string; name: string; phone: string | null; visits: number; points_balance: number; points_value: string };
type Loyalty = { is_active: boolean; rupiah_per_point: string; point_value: string; min_redeem_points: number };
// A guest's order from the QR e-menu (M11-T1): an open row in this till's own
// order table. Settling it is the ordinary sale, on that row.
type Ticket = {
  id: string;
  code: string;
  status: string;
  order_type: string;
  table_label: string | null;
  guest_name: string | null;
  guest_phone: string | null;
  note: string | null;
  placed_at: string;
  lines: { name: string; modifiers: string[]; quantity: string; unit_price: string; line_total: string; notes: string | null }[];
  total: string;
  is_estimate: boolean;
};

/** One row of the recent-sales list (M15-T11): enough to recognise the sale
 *  across the counter without opening it. */
type OrderRow = {
  id: string;
  number: string;
  sold_at: string;
  status: string;
  order_type: string;
  total: string;
  line_count: number;
  staff_name: string | null;
  customer_name: string | null;
  table_label: string | null;
};

type OrdersPage = { total: number; rows: OrderRow[] };

type ReversalResult = {
  order_id: string;
  status: string;
  reversing_lines: { id: string; item_id: string; quantity: string; line_total: string; stock_after: string | null }[];
  reversing_payments: { id: string; method: string; amount: string; reference: string | null }[];
};

const lineKey = (itemId: string, variant: Variant | null, modifiers: Modifier[]) =>
  `${itemId}:${variant?.id ?? "default"}:${modifiers.map((m) => m.id).sort().join(",")}`;
const linePrice = (l: { item: Item; variant: Variant | null; modifiers: Modifier[] }) =>
  Number(l.variant?.sell_price ?? l.item.sell_price) + l.modifiers.reduce((s, m) => s + Number(m.price_delta), 0);
const lineName = (l: { item: Item; variant: Variant | null; modifiers: Modifier[] }) => {
  const base = l.variant && l.item.variants.length > 1 ? `${l.item.name} · ${l.variant.name}` : l.item.name;
  return l.modifiers.length ? `${base} (${l.modifiers.map((m) => m.name).join(", ")})` : base;
};
const defaultChoices = (item: Item): Record<string, Modifier[]> =>
  Object.fromEntries(item.modifier_groups.map((g) => [g.id, g.modifiers.filter((m) => m.is_default)]));
const missingRequired = (item: Item, chosen: Record<string, Modifier[]>) =>
  item.modifier_groups.filter((g) => g.is_required && (chosen[g.id]?.length ?? 0) < Math.max(1, g.min_select));

type Screen =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "pick-staff"; business: PosBusiness }
  // `notice` (M15-T12) is why the last attempt failed when it was not simply
  // the wrong PIN — a cooldown that says how long to wait. Shaking the pad
  // silently for the fifth time is how a cashier decides the tablet is broken.
  | { kind: "pin"; business: PosBusiness; staff: StaffLite; pin: string; shake: boolean; notice?: string }
  | { kind: "sell"; staffName: string; businessName: string };

export default function PosPage() {
  const params = useParams<{ businessToken: string }>();
  const pairingToken = params.businessToken;
  const [screen, setScreen] = useState<Screen>({ kind: "loading" });
  const [posToken, setPosToken] = useState<string | null>(null);

  useEffect(() => {
    // Persist the pairing so this kiosk always boots into this business.
    localStorage.setItem(POS_PAIRING_KEY, pairingToken);
    api<PosBusiness>(`/pos/business/${pairingToken}`)
      .then((business) => setScreen({ kind: "pick-staff", business }))
      .catch((e: unknown) =>
        setScreen({
          kind: "error",
          message: e instanceof ApiError ? e.detail : "Tidak bisa terhubung ke server.",
        })
      );
  }, [pairingToken]);

  const tryPin = useCallback(
    async (staff: StaffLite, business: PosBusiness, pin: string) => {
      try {
        const res = await api<{ token: string; staff_name: string; business_name: string }>(
          "/pos/login",
          { body: { pairing_token: pairingToken, staff_id: staff.id, pin } }
        );
        localStorage.setItem(POS_TOKEN_KEY, res.token);
        setPosToken(res.token);
        setScreen({ kind: "sell", staffName: res.staff_name, businessName: res.business_name });
      } catch (e: unknown) {
        if (e instanceof ApiError && e.status === 401 && e.detail.startsWith("Perangkat ini")) {
          // Re-paired while this kiosk was open (M15-T8): no PIN will work here
          // again, so stop shaking the pad and say so.
          setScreen({ kind: "error", message: e.detail });
          return;
        }
        // A cooldown (M15-T12) is not a wrong PIN, and the difference matters:
        // the right PIN will not work either until the wait is over, so say so
        // rather than letting the cashier keep trying the one they know.
        const notice = e instanceof ApiError && e.status === 429 ? e.detail : undefined;
        setScreen({ kind: "pin", business, staff, pin: "", shake: true, notice });
        setTimeout(
          () =>
            setScreen((s) => (s.kind === "pin" ? { ...s, shake: false } : s)),
          500
        );
      }
    },
    [pairingToken]
  );

  if (screen.kind === "loading") {
    return (
      <Center>
        <p className="ink-soft animate-pulse text-lg">Menyiapkan kasir…</p>
      </Center>
    );
  }

  if (screen.kind === "error") {
    return (
      <Center>
        <div className="glass-card mx-6 max-w-md px-8 py-10 text-center">
          <span className="surface-inset ink-faint mx-auto flex h-12 w-12 items-center justify-center rounded-2xl" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
            <IconPlugOff className="h-6 w-6" />
          </span>
          <h1 className="mt-4 text-xl font-semibold tracking-[-0.015em]">Kasir belum terhubung</h1>
          <p className="ink-soft mt-2">{screen.message}</p>
        </div>
      </Center>
    );
  }

  if (screen.kind === "pick-staff") {
    return (
      <Center>
        <div className="w-full max-w-2xl animate-fade-up px-6">
          <p className="ink-soft text-center text-[15px] font-medium">
            {screen.business.business_name}
          </p>
          <h1 className="mt-1 text-center text-[2rem] font-semibold tracking-[-0.025em]">Siapa yang jaga?</h1>
          <div className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-3">
            {screen.business.staff.map((s, i) => (
              <button
                key={s.id}
                onClick={() =>
                  setScreen({ kind: "pin", business: screen.business, staff: s, pin: "", shake: false })
                }
                className="glass-card flex flex-col items-center gap-3 px-4 py-8 transition-[transform,box-shadow] duration-150 hover:shadow-key active:scale-[0.98]"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <span className="surface-inset ink-soft flex h-16 w-16 items-center justify-center rounded-full text-xl font-semibold" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
                  {initials(s.name)}
                </span>
                <span className="text-[17px] font-semibold">{s.name}</span>
                {s.role === "owner" && (
                  <span className="pill-good">
                    pemilik
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      </Center>
    );
  }

  if (screen.kind === "pin") {
    return (
      <PinPad
        staff={screen.staff}
        pin={screen.pin}
        shake={screen.shake}
        notice={screen.notice}
        onBack={() => setScreen({ kind: "pick-staff", business: screen.business })}
        onDigit={(d) => {
          const pin = screen.pin + d;
          if (pin.length === 4) {
            void tryPin(screen.staff, screen.business, pin);
            setScreen({ ...screen, pin });
          } else {
            setScreen({ ...screen, pin });
          }
        }}
        onDelete={() => setScreen({ ...screen, pin: screen.pin.slice(0, -1) })}
      />
    );
  }

  return (
    <SellScreen
      posToken={posToken}
      pairingToken={pairingToken}
      staffName={screen.staffName}
      businessName={screen.businessName}
      onLock={() => {
        localStorage.removeItem(POS_TOKEN_KEY);
        setPosToken(null);
        api<PosBusiness>(`/pos/business/${pairingToken}`)
          .then((business) => setScreen({ kind: "pick-staff", business }))
          .catch((e: unknown) =>
            // A device the owner has re-paired (M15-T8) answers with its own
            // message; showing "Koneksi terputus" would send staff to check
            // the WiFi for something the WiFi cannot fix.
            setScreen({
              kind: "error",
              message: e instanceof ApiError ? e.detail : "Koneksi terputus.",
            })
          );
      }}
    />
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center">{children}</main>;
}

function PinPad({
  staff,
  pin,
  shake,
  notice,
  onDigit,
  onDelete,
  onBack,
}: {
  staff: StaffLite;
  pin: string;
  shake: boolean;
  notice?: string;
  onDigit: (d: string) => void;
  onDelete: () => void;
  onBack: () => void;
}) {
  return (
    <Center>
      <div className="w-full max-w-sm animate-scale-in px-6 text-center">
        <span className="surface-inset ink-soft mx-auto flex h-16 w-16 items-center justify-center rounded-full text-xl font-semibold" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
          {initials(staff.name)}
        </span>
        <h1 className="mt-4 text-2xl font-semibold tracking-[-0.02em]">Halo, {staff.name}</h1>
        {notice ? (
          <p
            className="mx-auto mt-3 max-w-xs notice notice-bad"
          >
            {notice}
          </p>
        ) : (
          <p className="ink-soft mt-1">Masukkan PIN 4 angka</p>
        )}

        <div
          className={`mt-6 flex justify-center gap-4 ${shake ? "animate-[shake_0.4s_ease-in-out]" : ""}`}
          style={
            shake
              ? { animation: "shake 0.4s ease-in-out" }
              : undefined
          }
        >
          {[0, 1, 2, 3].map((i) => (
            <span
              key={i}
              className={`h-3.5 w-3.5 rounded-full border-[1.5px] transition-all duration-150 ${
                i < pin.length
                  ? "border-[color:var(--accent)] bg-[color:var(--accent-fill)]"
                  : "border-[color:var(--ink-faint)]"
              }`}
            />
          ))}
        </div>
        <style>{`@keyframes shake { 0%,100%{transform:translateX(0)} 20%{transform:translateX(-10px)} 40%{transform:translateX(10px)} 60%{transform:translateX(-6px)} 80%{transform:translateX(6px)} }`}</style>

        <div className="mx-auto mt-8 grid max-w-[18rem] grid-cols-3 gap-x-5 gap-y-4">
          {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
            <PinKey key={d} label={d} onPress={() => onDigit(d)} />
          ))}
          <button
            onClick={onBack}
            className="rounded-2xl py-5 text-[15px] font-medium text-[color:var(--ink-soft)] transition-colors hover:text-[color:var(--ink)] active:opacity-60"
          >
            batal
          </button>
          <PinKey label="0" onPress={() => onDigit("0")} />
          <button
            onClick={onDelete}
            aria-label="hapus"
            className="ink-soft flex items-center justify-center rounded-2xl py-5 transition-colors hover:text-[color:var(--ink)] active:opacity-60"
          >
            <IconBackspace className="h-7 w-7" />
          </button>
        </div>
      </div>
    </Center>
  );
}

function PinKey({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <button
      onClick={onPress}
      className="rounded-full bg-[color:var(--surface)] py-5 text-[26px] font-normal tabular-nums shadow-key transition-[transform,background-color] duration-100 hover:bg-[color:var(--surface-inset)] active:scale-95 active:bg-[color:var(--row-press)]"
    >
      {label}
    </button>
  );
}

function SellScreen({
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
  const [items, setItems] = useState<Item[] | null>(null);
  const [selected, setSelected] = useState<Item | null>(null);
  const [variant, setVariant] = useState<Variant | null>(null);
  const [chosen, setChosen] = useState<Record<string, Modifier[]>>({});
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [qty, setQty] = useState(1);
  // One order = many lines + one or more payments (M3-T3). The cart is the order
  // being built; nothing is written until "Bayar" succeeds.
  const [cart, setCart] = useState<CartLine[]>([]);
  const [cartOpen, setCartOpen] = useState(false);
  const [paying, setPaying] = useState(false);
  const [payMode, setPayMode] = useState<PayMode>("cash");
  // Order type routing (M11-T3): the type decides the service charge and the
  // delivery fee (the server prices it), and what the till must collect.
  const [orderType, setOrderType] = useState<OrderType>("takeaway");
  const [tableLabel, setTableLabel] = useState("");
  const [deliveryAddress, setDeliveryAddress] = useState("");
  const [deliveryName, setDeliveryName] = useState("");
  const [deliveryPhone, setDeliveryPhone] = useState("");
  // Discounts and the priced bill (M7-T4b).
  const [billDiscount, setBillDiscount] = useState("");
  const [voucherCode, setVoucherCode] = useState("");
  const [managerPin, setManagerPin] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [lineDiscountFor, setLineDiscountFor] = useState<string | null>(null);
  // Customer attached to this order (M8-T1).
  const [customer, setCustomer] = useState<CustomerLite | null>(null);
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerMatches, setCustomerMatches] = useState<CustomerLite[]>([]);
  const [customerNew, setCustomerNew] = useState<{ name: string; phone: string } | null>(null);
  const [customerError, setCustomerError] = useState<string | null>(null);
  // Points (M8-T2): the programme, and whether this order is paid partly with points.
  const [loyalty, setLoyalty] = useState<Loyalty | null>(null);
  const [usePoints, setUsePoints] = useState(false);

  const [lineDiscountDraft, setLineDiscountDraft] = useState("");
  const [cashPart, setCashPart] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<OrderResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const token = posToken ?? (typeof window !== "undefined" ? localStorage.getItem(POS_TOKEN_KEY) : null);

  // Till session (M7-T1): the cashier's open shift with its live expected cash.
  // undefined = not loaded yet, null = no shift open.
  const [shift, setShift] = useState<Shift | null | undefined>(undefined);
  const [shiftSheet, setShiftSheet] = useState<"open" | "close" | null>(null);
  const [shiftAmount, setShiftAmount] = useState("");
  const [shiftNote, setShiftNote] = useState("");
  const [shiftResult, setShiftResult] = useState<Shift | null>(null);
  const [shiftError, setShiftError] = useState<string | null>(null);
  const loadShift = useCallback(() => {
    api<Shift | null>("/pos/shift", { token }).then(setShift).catch(() => setShift(null));
  }, [token]);
  useEffect(() => {
    loadShift();
  }, [loadShift]);

  // Void and refund (M15-T11): find the sale, read it back, reverse it.
  const [reversalSheet, setReversalSheet] = useState(false);

  // Cash in and out (M7-T2): posted to the ledger, stamped with the open shift.
  const [cashSheet, setCashSheet] = useState(false);
  const [cashKind, setCashKind] = useState<CashKind>("petty_cash");
  const [cashInput, setCashInput] = useState("");
  const [cashReason, setCashReason] = useState("");
  const [cashCategory, setCashCategory] = useState("operasional");
  const [cashSupplier, setCashSupplier] = useState("");
  const [suppliers, setSuppliers] = useState<SupplierLite[]>([]);
  const [cashError, setCashError] = useState<string | null>(null);
  const [cashDone, setCashDone] = useState<string | null>(null);
  // The e-menu queue (M11-T1): polled, never pushed — a till on a warung's
  // wifi cannot hold a socket open all day.
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [ticketSheet, setTicketSheet] = useState(false);
  const [ticketBusy, setTicketBusy] = useState<string | null>(null);
  const [ticketError, setTicketError] = useState<string | null>(null);
  const [cancelFor, setCancelFor] = useState<string | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const loadTickets = useCallback(() => {
    api<Ticket[]>("/pos/tickets", { token }).then(setTickets).catch(() => undefined);
  }, [token]);
  useEffect(() => {
    loadTickets();
    const id = setInterval(loadTickets, 15000);
    return () => clearInterval(id);
  }, [loadTickets]);
  async function settleTicket(t: Ticket, method: "cash" | "qris") {
    if (ticketBusy) return;
    setTicketBusy(t.id);
    setTicketError(null);
    try {
      const res = await api<OrderResult>(`/pos/tickets/${t.id}/settle`, {
        token,
        body: { payments: [{ method, amount: Number(t.total) }] },
      });
      setFlash(res);
      setTimeout(() => setFlash(null), 2600);
      loadItems();
      loadShift();
      loadTickets();
      if (tickets.length <= 1) setTicketSheet(false);
    } catch (e: unknown) {
      setTicketError(e instanceof ApiError ? e.detail : "Gagal memproses pesanan — coba lagi.");
      loadTickets();
    } finally {
      setTicketBusy(null);
    }
  }
  async function cancelTicket(t: Ticket) {
    if (ticketBusy) return;
    setTicketBusy(t.id);
    setTicketError(null);
    try {
      await api<Ticket>(`/pos/tickets/${t.id}/cancel`, { token, body: { reason: cancelReason.trim() || null } });
      setCancelFor(null);
      setCancelReason("");
      loadTickets();
    } catch (e: unknown) {
      setTicketError(e instanceof ApiError ? e.detail : "Gagal membatalkan — coba lagi.");
      loadTickets();
    } finally {
      setTicketBusy(null);
    }
  }
  const waitingMinutes = (iso: string) => Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  function openCashSheet() {
    setCashError(null);
    setCashSheet(true);
    if (suppliers.length === 0) {
      api<SupplierLite[]>("/pos/suppliers", { token }).then(setSuppliers).catch(() => setSuppliers([]));
    }
  }
  async function submitCash() {
    const amount = Number(cashInput || 0);
    if (!Number.isFinite(amount) || amount <= 0) {
      setCashError("Masukkan jumlah lebih dari nol.");
      return;
    }
    if (!cashReason.trim()) {
      setCashError("Tulis alasannya.");
      return;
    }
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
      setCashDone(
        `${cashKind === "cash_in" ? "Kas masuk" : cashKind === "bank_drop" ? "Setor bank" : cashKind === "supplier_payment" ? "Bayar supplier" : "Kas keluar"} ${formatRupiah(amount)} dicatat`
      );
      setTimeout(() => setCashDone(null), 2600);
      loadShift();
    } catch (e: unknown) {
      setCashError(e instanceof ApiError ? e.detail : "Gagal menyimpan — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  async function submitShift() {
    const amount = Number(shiftAmount || 0);
    if (!Number.isFinite(amount) || amount < 0) {
      setShiftError("Masukkan angka yang benar.");
      return;
    }
    setBusy(true);
    setShiftError(null);
    try {
      if (shiftSheet === "open") {
        const res = await api<Shift>("/pos/shift/open", { token, body: { opening_float: amount } });
        setShift(res);
      } else {
        const res = await api<Shift>("/pos/shift/close", {
          token,
          body: { counted_cash: amount, notes: shiftNote.trim() || null },
        });
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

  const loadItems = useCallback(() => {
    api<Item[]>("/pos/items", { token }).then(setItems).catch(() => setItems([]));
  }, [token]);

  useEffect(loadItems, [loadItems]);

  const sellable = useMemo(
    () => (items ?? []).filter((i) => Number(i.sell_price) > 0),
    [items]
  );

  // Units of one item across all its sizes — stock is shared by the parent.
  const cartQty = (itemId: string) => cart.filter((l) => l.item.id === itemId).reduce((n, l) => n + l.qty, 0);
  const cartCount = cart.reduce((n, l) => n + l.qty, 0);
  const cartGross = cart.reduce((s, l) => s + linePrice(l) * l.qty, 0);
  const anyDiscount = Number(billDiscount || 0) > 0 || cart.some((l) => l.discount > 0);
  // What the customer pays: the server's priced total when we have it, else the plain sum.
  const cartTotal = quote ? Number(quote.total) : cartGross;
  const needsPin = anyDiscount && (quote?.discount_requires_pin ?? true);

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

  // The points programme (M8-T2), once per kiosk session.
  useEffect(() => {
    api<Loyalty>("/pos/loyalty", { token }).then(setLoyalty).catch(() => setLoyalty(null));
  }, [token]);

  // Re-price whenever the cart or a discount changes. Debounced a touch so a
  // quick run of taps is one request.
  useEffect(() => {
    if (cart.length === 0) {
      setQuote(null);
      return;
    }
    const handle = setTimeout(() => {
      api<Quote>("/pos/quote", {
        token,
        body: {
          lines: cart.map((l) => ({
            item_id: l.item.id,
            variant_id: l.variant?.id ?? null,
            modifier_ids: l.modifiers.map((m) => m.id),
            quantity: l.qty,
            line_discount: l.discount,
          })),
          bill_discount: Number(billDiscount || 0),
          voucher_code: voucherCode.trim() || null,
          order_type: orderType,
        },
      })
        .then((q) => {
          setQuote(q);
          setError(null);
        })
        .catch((e: unknown) => {
          setQuote(null);
          setError(e instanceof ApiError ? e.detail : "Tidak bisa menghitung total.");
        });
    }, 150);
    return () => clearTimeout(handle);
  }, [cart, billDiscount, voucherCode, token, orderType]);

  const stockCap = (item: Item) => (item.made_to_order ? 999 : Number(item.current_stock));

  function addToCart(item: Item, v: Variant | null, mods: Modifier[], n: number) {
    setCart((c) => {
      const key = lineKey(item.id, v, mods);
      const others = c.filter((l) => l.item.id === item.id && lineKey(l.item.id, l.variant, l.modifiers) !== key)
        .reduce((s, l) => s + l.qty, 0);
      const room = Math.max(0, stockCap(item) - others);
      const existing = c.find((l) => lineKey(l.item.id, l.variant, l.modifiers) === key);
      if (existing) {
        return c.map((l) =>
          lineKey(l.item.id, l.variant, l.modifiers) === key ? { ...l, qty: Math.min(room, l.qty + n) } : l
        );
      }
      return [...c, { item, variant: v, modifiers: mods, qty: Math.min(room, n), discount: 0 }];
    });
  }

  function changeLine(key: string, delta: number) {
    setCart((c) =>
      c
        .map((l) => {
          if (lineKey(l.item.id, l.variant, l.modifiers) !== key) return l;
          const others = c.filter((o) => o.item.id === l.item.id && lineKey(o.item.id, o.variant, o.modifiers) !== key)
            .reduce((s, o) => s + o.qty, 0);
          const room = Math.max(0, stockCap(l.item) - others);
          return { ...l, qty: Math.min(room, l.qty + delta) };
        })
        .filter((l) => l.qty > 0)
    );
  }

  function setLineDiscount(key: string, amount: number) {
    setCart((c) =>
      c.map((l) => (lineKey(l.item.id, l.variant, l.modifiers) === key ? { ...l, discount: Math.max(0, amount) } : l))
    );
  }

  function toggleModifier(group: ModifierGroup, m: Modifier) {
    setChosen((prev) => {
      const current = prev[group.id] ?? [];
      const has = current.some((x) => x.id === m.id);
      let next: Modifier[];
      if (group.selection === "single") {
        next = has ? (group.is_required ? current : []) : [m];
      } else if (has) {
        next = current.filter((x) => x.id !== m.id);
      } else if (group.max_select !== null && current.length >= group.max_select) {
        next = current;
      } else {
        next = [...current, m];
      }
      return { ...prev, [group.id]: next };
    });
  }

  async function printReceipt(orderId: string) {
    try {
      const r = await api<Receipt>(`/pos/orders/${orderId}/receipt`, { token });
      setReceipt(r);
      setTimeout(() => window.print(), 300);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Struk tidak bisa dimuat.");
    }
  }

  // Points pay first (whole points only, capped at the bill and at the balance); the rest by the chosen mode.
  const pointValue = Number(loyalty?.point_value ?? 0);
  const pointsAvailable = customer ? Math.max(0, customer.points_balance) : 0;
  const pointsUsable =
    usePoints && loyalty?.is_active && customer && pointValue > 0
      ? Math.min(pointsAvailable, Math.floor(cartTotal / pointValue))
      : 0;
  const pointsAmount = pointsUsable >= (loyalty?.min_redeem_points ?? 0) ? pointsUsable * pointValue : 0;
  const moneyDue = cartTotal - pointsAmount;
  const cashAmount = payMode === "cash" ? moneyDue : payMode === "qris" ? 0 : Number(cashPart || 0);
  const qrisAmount = moneyDue - cashAmount;
  const splitValid = payMode !== "split" || (cashAmount > 0 && cashAmount < moneyDue) || moneyDue === 0;

  async function confirmOrder() {
    if (cart.length === 0 || busy || !splitValid) return;
    if (orderType === "delivery" && !deliveryAddress.trim()) {
      setError("Pesanan antar perlu alamat pengantaran.");
      return;
    }
    if (orderType === "delivery" && !deliveryPhone.trim() && !customer?.phone) {
      setError("Pesanan antar perlu nomor HP penerima.");
      return;
    }
    if (needsPin && managerPin.length < 4) {
      setError("Diskon perlu PIN manajer — minta pemilik atau manajer memasukkan PIN-nya.");
      return;
    }
    setBusy(true);
    setError(null);
    const payments = [
      ...(pointsAmount > 0 ? [{ method: "points", amount: pointsAmount }] : []),
      ...(cashAmount > 0 ? [{ method: "cash", amount: cashAmount }] : []),
      ...(qrisAmount > 0 ? [{ method: "qris", amount: qrisAmount }] : []),
    ];
    try {
      const res = await api<OrderResult>("/pos/orders", {
        token,
        body: {
          lines: cart.map((l) => ({
            item_id: l.item.id,
            variant_id: l.variant?.id ?? null,
            modifier_ids: l.modifiers.map((m) => m.id),
            quantity: l.qty,
            line_discount: l.discount,
          })),
          payments,
          order_type: orderType,
          table_label: orderType === "dine_in" ? tableLabel.trim() || null : null,
          delivery_address: orderType === "delivery" ? deliveryAddress.trim() || null : null,
          guest_name: orderType === "delivery" ? deliveryName.trim() || null : null,
          guest_phone: orderType === "delivery" ? deliveryPhone.trim() || null : null,
          bill_discount: Number(billDiscount || 0),
          manager_pin: needsPin ? managerPin || null : null,
          customer_id: customer?.id ?? null,
          voucher_code: quote?.voucher_code ?? null,
        },
      });
      setFlash(res);
      setCart([]);
      setBillDiscount("");
      setVoucherCode("");
      setManagerPin("");
      setQuote(null);
      setCustomer(null);
      setCustomerQuery("");
      setCustomerNew(null);
      setUsePoints(false);
      setCartOpen(false);
      setPaying(false);
      setPayMode("cash");
      setCashPart("");
      setTableLabel("");
      setDeliveryAddress("");
      setDeliveryName("");
      setDeliveryPhone("");
      loadItems();
      loadShift();
      setTimeout(() => setFlash(null), 2600);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal menyimpan — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-4 pb-8">
      <header className="hairline-b sticky-bar sticky top-0 z-10 -mx-4 mb-6 flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:py-4">
        <div className="min-w-0">
          <p className="ink-faint truncate text-[13px] font-medium">{businessName}</p>
          <p className="truncate text-lg font-semibold tracking-[-0.015em]">Kasir · {staffName}</p>
        </div>
        <div className="-mx-4 flex items-center gap-2 overflow-x-auto px-4 pb-0.5 [scrollbar-width:none] sm:mx-0 sm:justify-end sm:overflow-visible sm:px-0 [&>*]:shrink-0">
          {shift === undefined ? null : shift === null ? (
            <button
              onClick={() => {
                setShiftError(null);
                setShiftSheet("open");
              }}
              className="btn-accent px-4 py-2 text-sm"
            >
              Buka shift
            </button>
          ) : (
            <button
              onClick={() => {
                setShiftError(null);
                setShiftSheet("close");
              }}
              className="btn-quiet flex-col items-start gap-0 px-4 py-1.5 text-left"
              title="Tutup shift"
            >
              <p className="ink-faint text-[11px] font-medium">Shift buka · kas seharusnya</p>
              <p className="text-sm font-semibold tabular-nums">{formatRupiah(shift.expected_cash ?? shift.opening_float)}</p>
            </button>
          )}
          <button
            onClick={() => {
              setTicketError(null);
              setTicketSheet(true);
              loadTickets();
            }}
            className={`relative px-4 py-2 text-sm ${tickets.length > 0 ? "btn-accent" : "btn-quiet"}`}
            title="Pesanan dari menu QR"
          >
            Pesanan
            {tickets.length > 0 && (
              <span className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-[color:var(--surface)] px-1.5 text-[11px] font-bold tabular-nums text-[color:var(--accent)] shadow-key">
                {tickets.length}
              </span>
            )}
          </button>
          <button
            onClick={() => setReversalSheet(true)}
            className="btn-quiet px-4 py-2 text-sm"
            title="Transaksi hari ini — batalkan atau kembalikan"
          >
            Transaksi
          </button>
          <button onClick={openCashSheet} className="btn-quiet px-4 py-2 text-sm" title="Kas masuk / keluar">
            Kas
          </button>
          <a href={`/kitchen/${pairingToken}`} target="_blank" rel="noreferrer" className="btn-quiet px-4 py-2 text-sm" title="Layar dapur (M11-T2)">
            Dapur <IconExternal className="ink-faint h-3.5 w-3.5" />
          </a>
          <button onClick={onLock} className="btn-quiet px-4 py-2 text-sm">
            <IconLock className="h-4 w-4" /> Kunci
          </button>
        </div>
      </header>

      {cashDone && (
        <p className="notice notice-good mb-4">{cashDone}</p>
      )}

      {items === null ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="glass-card h-36 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {sellable.map((item) => {
            const stock = Number(item.current_stock);
            const low = !item.made_to_order && stock <= Number(item.reorder_threshold);
            const out = !item.made_to_order && stock <= 0;
            const inCart = cartQty(item.id);
            return (
              <button
                key={item.id}
                disabled={out}
                onClick={() => {
                  setSelected(item);
                  setVariant(item.variants.find((v) => v.is_default) ?? item.variants[0] ?? null);
                  setChosen(defaultChoices(item));
                  setQty(1);
                  setError(null);
                }}
                className={`glass-card relative flex min-h-[8.5rem] flex-col items-start gap-1 px-5 py-5 text-left transition-[transform,box-shadow] duration-150 ${
                  out ? "opacity-40" : "hover:shadow-key active:scale-[0.98]"
                }`}
              >
                {inCart > 0 && (
                  <span className="absolute right-3 top-3 flex h-6 min-w-6 items-center justify-center rounded-full bg-[color:var(--accent-fill)] px-2 text-xs font-bold tabular-nums text-[color:var(--on-accent)]">
                    {inCart}
                  </span>
                )}
                <span className="pr-6 text-[15px] font-semibold leading-snug">{item.name}</span>
                <span className="font-semibold tabular-nums text-[color:var(--accent)]">
                  {formatRupiah(item.sell_price)}
                </span>
                <span
                  className={`mt-auto pt-2 text-xs font-medium ${low ? "" : "ink-faint"}`}
                  style={low ? { color: "var(--warn)" } : undefined}
                >
                  {item.variants.length > 1 ? `${item.variants.length} ukuran · ` : ""}
                  {item.made_to_order ? "dibuat saat dipesan" : out ? "habis" : `sisa ${formatQty(stock)} ${item.unit}`}
                  {low && !out ? " · hampir habis" : ""}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* Shift sheet (M7-T1): open with a float, close with a count */}
      {shiftSheet && (
        <div
          className="sheet-scrim"
          onClick={() => !busy && setShiftSheet(null)}
        >
          <div
            className="sheet-panel block overflow-y-auto sm:max-w-md px-8 pb-10 pt-6 sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-[19px] font-semibold tracking-[-0.015em]">{shiftSheet === "open" ? "Buka shift" : "Tutup shift"}</p>
            {shiftSheet === "open" ? (
              <p className="ink-soft text-sm">Modal awal di laci kasir.</p>
            ) : shift ? (
              // M7-T3: the count is only trustworthy if the cashier can see the
              // sum it is being checked against, line by line.
              <dl className="mt-3 space-y-1 text-sm">
                <div className="flex justify-between">
                  <dt className="ink-soft">Modal awal</dt>
                  <dd className="tabular-nums">{formatRupiah(shift.opening_float)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="ink-soft">Penjualan tunai</dt>
                  <dd className="tabular-nums">+{formatRupiah(shift.cash_sales)}</dd>
                </div>
                {Number(shift.cash_refunds) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Refund tunai</dt>
                    <dd className="tabular-nums">−{formatRupiah(shift.cash_refunds)}</dd>
                  </div>
                )}
                {Number(shift.cash_in) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Kas masuk</dt>
                    <dd className="tabular-nums">+{formatRupiah(shift.cash_in)}</dd>
                  </div>
                )}
                {Number(shift.cash_out) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Kas keluar</dt>
                    <dd className="tabular-nums">−{formatRupiah(shift.cash_out)}</dd>
                  </div>
                )}
                <div className="hairline-t flex justify-between pt-1">
                  <dt className="font-semibold">Kas seharusnya</dt>
                  <dd className="font-semibold tabular-nums">{formatRupiah(shift.expected_cash ?? 0)}</dd>
                </div>
              </dl>
            ) : null}
            <label className="mt-5 block">
              <span className="ink-soft text-[13px] font-medium">
                {shiftSheet === "open" ? "Modal awal (Rp)" : "Uang dihitung (Rp)"}
              </span>
              <input
                autoFocus
                inputMode="numeric"
                value={shiftAmount}
                onChange={(e) => setShiftAmount(e.target.value.replace(/[^0-9]/g, ""))}
                className="field mt-1 w-full py-3 text-2xl font-bold tabular-nums"
                placeholder="0"
              />
            </label>
            {shiftSheet === "close" && (
              <input
                value={shiftNote}
                onChange={(e) => setShiftNote(e.target.value)}
                className="field mt-3 w-full text-sm"
                placeholder="Catatan (opsional)"
              />
            )}
            {shiftError && <p className="mt-3 text-sm text-[color:var(--bad)]">{shiftError}</p>}
            <div className="mt-5 flex gap-3">
              <button onClick={() => setShiftSheet(null)} className="btn-quiet flex-1 py-3">
                Batal
              </button>
              <button onClick={submitShift} disabled={busy} className="btn-accent flex-1 py-3 text-lg">
                {shiftSheet === "open" ? "Buka" : "Tutup shift"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Cash sheet (M7-T2): cash in, petty cash, supplier paid, bank drop */}
      {cashSheet && (
        <div
          className="sheet-scrim"
          onClick={() => !busy && setCashSheet(false)}
        >
          <div
            className="sheet-panel block overflow-y-auto sm:max-w-md px-8 pb-10 pt-6 sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
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
                <button
                  key={kind}
                  onClick={() => setCashKind(kind)}
                  aria-pressed={cashKind === kind}
                  className={`rounded-2xl px-3 py-2.5 text-sm font-semibold transition-colors ${
                    cashKind === kind ? "toggle-on" : "toggle-off"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <label className="mt-4 block">
              <span className="ink-soft text-[13px] font-medium">Jumlah (Rp)</span>
              <input
                autoFocus
                inputMode="numeric"
                value={cashInput}
                onChange={(e) => setCashInput(e.target.value.replace(/[^0-9]/g, ""))}
                className="field mt-1 w-full py-3 text-2xl font-bold tabular-nums"
                placeholder="0"
              />
            </label>
            <input
              value={cashReason}
              onChange={(e) => setCashReason(e.target.value)}
              className="field mt-3 w-full text-sm"
              placeholder={cashKind === "petty_cash" ? "Beli apa? (mis. es batu)" : "Alasan"}
            />
            {cashKind === "petty_cash" && (
              <div className="mt-3 flex flex-wrap gap-2">
                {["bahan baku", "operasional", "lainnya"].map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setCashCategory(cat)}
                    aria-pressed={cashCategory === cat}
                  className={`rounded-2xl px-3 py-1.5 text-xs font-semibold transition-colors ${
                      cashCategory === cat ? "toggle-on" : "toggle-off"
                    }`}
                  >
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
            {cashError && <p className="mt-3 text-sm text-[color:var(--bad)]">{cashError}</p>}
            <div className="mt-5 flex gap-3">
              <button onClick={() => setCashSheet(false)} className="btn-quiet flex-1 py-3">
                Batal
              </button>
              <button onClick={submitCash} disabled={busy} className="btn-accent flex-1 py-3 text-lg">
                Catat
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Void and refund (M15-T11) */}
      {reversalSheet && (
        <ReversalSheet
          token={token}
          onClose={() => setReversalSheet(false)}
          onReversed={() => {
            loadShift();
            loadItems();
          }}
        />
      )}

      {/* The e-menu queue (M11-T1): pay a guest's ticket here, or cancel it */}
      {ticketSheet && (
        <div
          className="sheet-scrim"
          onClick={() => !ticketBusy && setTicketSheet(false)}
        >
          <div
            className="sheet-panel block overflow-y-auto sm:max-w-lg px-6 pb-10 pt-6 sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[19px] font-semibold tracking-[-0.015em]">Pesanan dari menu QR</p>
                <p className="ink-soft text-sm">Tamu memesan dari meja; bayar di sini, stok dan pembukuan ikut saat dibayar.</p>
              </div>
              <button onClick={() => setTicketSheet(false)} className="ink-soft rounded-full px-3 py-1 text-sm">
                tutup
              </button>
            </div>
            {ticketError && <p className="mt-3 text-sm text-[color:var(--bad)]">{ticketError}</p>}
            {tickets.length === 0 ? (
              <p className="surface-inset rounded-2xl mt-4 px-4 py-6 text-center text-sm">Belum ada pesanan yang menunggu.</p>
            ) : (
              <ul className="mt-4 space-y-3">
                {tickets.map((t) => {
                  const busyHere = ticketBusy === t.id;
                  return (
                    <li key={t.id} className="surface-inset rounded-2xl px-4 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-lg font-bold tracking-[-0.02em]">{t.code}</p>
                          <p className="ink-soft text-xs">
                            {t.order_type === "dine_in" ? t.table_label || "Makan di sini" : "Bawa pulang"}
                            {t.guest_name ? ` · ${t.guest_name}` : ""} · {waitingMinutes(t.placed_at)} mnt lalu
                          </p>
                        </div>
                        <p className="text-lg font-bold tabular-nums">{formatRupiah(t.total)}</p>
                      </div>
                      <ul className="mt-2 space-y-0.5 text-sm">
                        {t.lines.map((l, i) => (
                          <li key={i} className="flex justify-between gap-3">
                            <span className="min-w-0 truncate">
                              {l.quantity}× {l.name}
                              {l.modifiers.length > 0 ? <span className="ink-faint"> ({l.modifiers.join(", ")})</span> : null}
                              {l.notes ? <span className="ink-faint"> — {l.notes}</span> : null}
                            </span>
                            <span className="shrink-0 tabular-nums">{formatRupiah(l.line_total)}</span>
                          </li>
                        ))}
                      </ul>
                      {t.note && <p className="ink-soft mt-1 text-xs">Catatan: {t.note}</p>}
                      {cancelFor === t.id ? (
                        <div className="mt-3 flex gap-2">
                          <input
                            autoFocus
                            value={cancelReason}
                            onChange={(e) => setCancelReason(e.target.value.slice(0, 200))}
                            className="field flex-1 text-sm"
                            placeholder="Alasan (mis. bahan habis)"
                          />
                          <button onClick={() => setCancelFor(null)} className="btn-quiet px-3 py-2 text-sm">
                            Kembali
                          </button>
                          <button onClick={() => cancelTicket(t)} disabled={busyHere} className="rounded-2xl px-3 py-2 text-sm font-semibold text-[color:var(--bad)]">
                            Batalkan
                          </button>
                        </div>
                      ) : (
                        <div className="mt-3 flex gap-2">
                          <button onClick={() => settleTicket(t, "cash")} disabled={busyHere} className="btn-accent flex-1 py-2.5 text-sm">
                            {busyHere ? "…" : "Bayar tunai"}
                          </button>
                          <button onClick={() => settleTicket(t, "qris")} disabled={busyHere} className="btn-accent flex-1 py-2.5 text-sm">
                            QRIS
                          </button>
                          <button
                            onClick={() => {
                              setCancelFor(t.id);
                              setCancelReason("");
                            }}
                            disabled={busyHere}
                            className="btn-quiet px-3 py-2.5 text-sm"
                          >
                            Batal
                          </button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}

      {/* Shift closed: the count against the expectation */}
      {shiftResult && (
        <div
          className="sheet-scrim items-center p-6"
          onClick={() => setShiftResult(null)}
        >
          <div
            className="sheet-panel block w-full max-w-sm overflow-y-auto rounded-4xl px-8 py-8"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="ink-faint text-center text-xs font-medium uppercase tracking-wide">
              Shift ditutup · {shiftResult.staff_name}
            </p>
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="ink-soft">Modal awal + tunai bersih</dt>
                <dd className="tabular-nums">{formatRupiah(shiftResult.opening_float)} + {formatRupiah(
                  Number(shiftResult.cash_sales) - Number(shiftResult.cash_refunds) +
                    Number(shiftResult.cash_in) - Number(shiftResult.cash_out),
                )}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="ink-soft">Kas seharusnya</dt>
                <dd className="font-semibold tabular-nums">{formatRupiah(shiftResult.expected_cash ?? 0)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="ink-soft">Uang dihitung</dt>
                <dd className="font-semibold tabular-nums">{formatRupiah(shiftResult.counted_cash ?? 0)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="ink-soft">Selisih</dt>
                <dd className={`font-bold tabular-nums ${Number(shiftResult.variance) < 0 ? "text-[color:var(--bad)]" : ""}`}>
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

      {/* Quantity sheet */}
      {selected && (
        <div
          className="sheet-scrim"
          onClick={() => !busy && setSelected(null)}
        >
          <div
            className="sheet-panel block overflow-y-auto sm:max-w-md px-8 pb-10 pt-6 sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mx-auto mb-5 h-1.5 w-10 rounded-full bg-[color:var(--ink-faint)] opacity-40 sm:hidden" />
            <p className="text-[19px] font-semibold tracking-[-0.015em]">{selected.name}</p>
            <p className="ink-soft text-sm">
              {formatRupiah(variant?.sell_price ?? selected.sell_price)} / {selected.unit}
              {selected.made_to_order ? " · dibuat saat dipesan" : ` · sisa ${formatQty(selected.current_stock)}`}
            </p>

            {selected.variants.length > 1 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {selected.variants.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => setVariant(v)}
                    aria-pressed={variant?.id === v.id}
                  className={`rounded-2xl px-4 py-2 text-sm font-semibold transition-colors ${
                      variant?.id === v.id ? "toggle-on" : "toggle-off"
                    }`}
                  >
                    {v.name} · {formatRupiah(v.sell_price)}
                  </button>
                ))}
              </div>
            )}

            {selected.modifier_groups.map((g) => (
              <div key={g.id} className="mt-4">
                <p className="ink-soft text-[13px] font-semibold">
                  {g.name}
                  {g.is_required ? " · wajib" : g.selection === "multi" ? " · boleh lebih dari satu" : ""}
                </p>
                <div className="mt-1.5 flex flex-wrap gap-2">
                  {g.modifiers.map((m) => {
                    const on = (chosen[g.id] ?? []).some((x) => x.id === m.id);
                    return (
                      <button
                        key={m.id}
                        onClick={() => toggleModifier(g, m)}
                        aria-pressed={on}
                  className={`rounded-2xl px-3 py-2 text-sm font-medium transition-colors ${
                          on ? "toggle-on" : "toggle-off"
                        }`}
                      >
                        {m.name}
                        {Number(m.price_delta) > 0 ? ` +${formatRupiah(m.price_delta)}` : ""}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}

            <div className="mt-6 flex items-center justify-center gap-6">
              <QtyButton label="−" onPress={() => setQty((q) => Math.max(1, q - 1))} />
              <span className="w-16 text-center text-4xl font-bold tabular-nums">{qty}</span>
              <QtyButton
                label="+"
                onPress={() => setQty((q) => Math.min(stockCap(selected), q + 1))}
              />
            </div>

            {(() => {
              const mods = Object.values(chosen).flat();
              const missing = missingRequired(selected, chosen);
              const unit = linePrice({ item: selected, variant, modifiers: mods });
              return (
                <>
                  {missing.length > 0 && (
                    <p className="ink-faint mt-4 text-center text-sm">
                      Pilih dulu: {missing.map((g) => g.name).join(", ")}
                    </p>
                  )}
                  <button
                    onClick={() => {
                      addToCart(selected, variant, mods, qty);
                      setSelected(null);
                      setQty(1);
                    }}
                    disabled={missing.length > 0}
                    className="btn-accent mt-6 w-full py-4 text-lg disabled:opacity-50"
                  >
                    Tambah {formatRupiah(unit * qty)}
                  </button>
                </>
              );
            })()}
          </div>
        </div>
      )}

      {/* Cart bar */}
      {cart.length > 0 && !paying && (
        <div className="fixed inset-x-0 bottom-0 z-20 flex justify-center px-4 pb-4">
          <div className="dock w-full max-w-2xl rounded-3xl px-5 py-3">
            {cartOpen && (
              <ul className="hairline-b mb-3 max-h-64 overflow-y-auto pb-2">
                {cart.map((l) => {
                  const key = lineKey(l.item.id, l.variant, l.modifiers);
                  return (
                    <li key={key} className="flex items-center gap-3 py-2">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">{lineName(l)}</p>
                        <p className="ink-faint text-xs">
                          {formatRupiah(linePrice(l))} × {l.qty}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => changeLine(key, -1)}
                          aria-label={`kurangi ${lineName(l)}`}
                          className="glass-card h-9 w-9 rounded-full text-lg font-bold active:scale-90"
                        >
                          −
                        </button>
                        <span className="w-6 text-center font-semibold tabular-nums">{l.qty}</span>
                        <button
                          onClick={() => changeLine(key, +1)}
                          aria-label={`tambah ${lineName(l)}`}
                          className="glass-card h-9 w-9 rounded-full text-lg font-bold active:scale-90"
                        >
                          +
                        </button>
                      </div>
                      <button
                        onClick={() => {
                          setLineDiscountFor(key);
                          setLineDiscountDraft(l.discount ? String(l.discount) : "");
                        }}
                        className="w-28 text-right"
                        title="Diskon baris"
                      >
                        <span className="block text-sm font-semibold tabular-nums">
                          {formatRupiah(linePrice(l) * l.qty - l.discount)}
                        </span>
                        <span className="ink-faint block text-[10px]">
                          {l.discount > 0 ? `diskon ${formatRupiah(l.discount)}` : "diskon"}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <button onClick={() => setCartOpen((o) => !o)} className="min-w-0 flex-1 basis-full text-left sm:basis-auto">
                <p className="ink-soft text-[13px] font-medium">
                  {cartCount} item · {cartOpen ? "tutup" : "lihat keranjang"}
                </p>
                <p className="text-2xl font-bold tabular-nums">{formatRupiah(cartTotal)}</p>
              </button>
              <button onClick={() => setCart([])} className="btn-quiet px-3 py-2.5 text-sm max-sm:flex-1">
                Kosongkan
              </button>
              <button
                onClick={() => {
                  setError(null);
                  setPaying(true);
                }}
                className="btn-accent px-7 py-3 text-lg max-sm:flex-[2]"
              >
                Bayar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Line discount (M7-T4b) */}
      {lineDiscountFor && (
        <div
          className="sheet-scrim z-[55]"
          onClick={() => setLineDiscountFor(null)}
        >
          <div
            className="sheet-panel block overflow-y-auto sm:max-w-sm px-8 pb-10 pt-6 sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-[19px] font-semibold tracking-[-0.015em]">Diskon baris</p>
            <p className="ink-soft text-sm">Potongan dalam rupiah untuk baris ini saja.</p>
            <input
              autoFocus
              inputMode="numeric"
              value={lineDiscountDraft}
              onChange={(e) => setLineDiscountDraft(e.target.value.replace(/[^0-9]/g, ""))}
              className="field mt-4 w-full py-3 text-2xl font-bold tabular-nums"
              placeholder="0"
            />
            <div className="mt-5 flex gap-3">
              <button
                onClick={() => {
                  setLineDiscount(lineDiscountFor, 0);
                  setLineDiscountFor(null);
                }}
                className="btn-quiet flex-1 py-3"
              >
                Hapus
              </button>
              <button
                onClick={() => {
                  setLineDiscount(lineDiscountFor, Number(lineDiscountDraft || 0));
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

      {/* Payment sheet */}
      {paying && (
        <div
          className="sheet-scrim"
          onClick={() => !busy && setPaying(false)}
        >
          <div
            className="sheet-panel block overflow-y-auto sm:max-w-md px-8 pb-10 pt-6 sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="ink-soft text-[13px] font-medium">
              {cartCount} item
            </p>
            <p className="text-3xl font-bold tabular-nums">{formatRupiah(cartTotal)}</p>
            {/* Order type (M11-T3): routes service charge / delivery fee and what to collect */}
            <div className="mt-3 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
              {(Object.keys(ORDER_TYPE_LABEL) as OrderType[]).map((kind) => (
                <button
                  key={kind}
                  onClick={() => setOrderType(kind)}
                  aria-pressed={orderType === kind}
                  className={`rounded-2xl px-2 py-2 text-xs font-semibold transition-colors ${
                    orderType === kind ? "toggle-on" : "toggle-off"
                  }`}
                >
                  {ORDER_TYPE_LABEL[kind]}
                </button>
              ))}
            </div>
            {orderType === "dine_in" && (
              <input
                value={tableLabel}
                onChange={(e) => setTableLabel(e.target.value.slice(0, 20))}
                className="field mt-2 w-full text-sm"
                placeholder="Nomor meja (opsional)"
              />
            )}
            {orderType === "delivery" && (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <input
                  value={deliveryName}
                  onChange={(e) => setDeliveryName(e.target.value.slice(0, 60))}
                  className="field text-sm"
                  placeholder="Nama penerima"
                />
                <input
                  inputMode="tel"
                  value={deliveryPhone}
                  onChange={(e) => setDeliveryPhone(e.target.value.slice(0, 32))}
                  className="field text-sm tabular-nums"
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
            {/* The bill as the server priced it (M7-T4b) */}
            {quote && (
              <dl className="mt-3 space-y-0.5 text-sm">
                <div className="flex justify-between">
                  <dt className="ink-soft">Subtotal</dt>
                  <dd className="tabular-nums">{formatRupiah(quote.subtotal)}</dd>
                </div>
                {Number(quote.discount_total) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Diskon</dt>
                    <dd className="tabular-nums">− {formatRupiah(quote.discount_total)}</dd>
                  </div>
                )}
                {Number(quote.voucher_total) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Voucher {quote.voucher_code}</dt>
                    <dd className="tabular-nums">− {formatRupiah(quote.voucher_total)}</dd>
                  </div>
                )}
                {quote.promos.map((p, i) => (
                  <div key={`${p.promo_id}-${i}`} className="flex justify-between">
                    <dt className="ink-soft truncate">
                      🎉 {p.name}
                      {Number(p.bonus_quantity) > 0 ? ` (+${Number(p.bonus_quantity)} gratis)` : ""}
                    </dt>
                    <dd className="shrink-0 tabular-nums">− {formatRupiah(p.amount)}</dd>
                  </div>
                ))}
                {Number(quote.service_charge) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Service charge</dt>
                    <dd className="tabular-nums">+ {formatRupiah(quote.service_charge)}</dd>
                  </div>
                )}
                {Number(quote.delivery_fee) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Ongkos kirim</dt>
                    <dd className="tabular-nums">+ {formatRupiah(quote.delivery_fee)}</dd>
                  </div>
                )}
                {Number(quote.tax_total) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">{quote.tax_inclusive ? "Pajak (sudah termasuk)" : "Pajak"}</dt>
                    <dd className="tabular-nums">
                      {quote.tax_inclusive ? "" : "+ "}
                      {formatRupiah(quote.tax_total)}
                    </dd>
                  </div>
                )}
                {Number(quote.rounding) !== 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Pembulatan</dt>
                    <dd className="tabular-nums">
                      {Number(quote.rounding) > 0 ? "+ " : "− "}
                      {formatRupiah(Math.abs(Number(quote.rounding)))}
                    </dd>
                  </div>
                )}
              </dl>
            )}
            {/* Customer (M8-T1): optional, by name or phone; quick add inline */}
            <div className="mt-4">
              <span className="ink-soft text-[13px] font-medium">Pelanggan (opsional)</span>
              {customer ? (
                <div className="surface-inset rounded-2xl mt-1 flex items-center justify-between rounded-2xl px-3 py-2 text-sm">
                  <span className="truncate">
                    <span className="font-semibold">{customer.name}</span>
                    {customer.phone ? <span className="ink-faint"> · {customer.phone}</span> : null}
                    {customer.visits > 0 ? <span className="ink-faint"> · {customer.visits}× datang</span> : null}
                    {loyalty?.is_active ? (
                      <span className="ink-faint"> · {customer.points_balance} poin</span>
                    ) : null}
                  </span>
                  <button
                    onClick={() => {
                      setCustomer(null);
                      setUsePoints(false);
                    }}
                    className="ink-soft ml-2 shrink-0 text-xs"
                  >
                    ganti
                  </button>
                </div>
              ) : customerNew ? (
                <div className="mt-1 grid grid-cols-2 gap-2">
                  <input
                    autoFocus
                    value={customerNew.name}
                    onChange={(e) => setCustomerNew({ ...customerNew, name: e.target.value })}
                    className="field text-sm"
                    placeholder="Nama"
                  />
                  <input
                    inputMode="tel"
                    value={customerNew.phone}
                    onChange={(e) => setCustomerNew({ ...customerNew, phone: e.target.value })}
                    className="field text-sm tabular-nums"
                    placeholder="Nomor HP"
                  />
                  <button onClick={quickAddCustomer} className="btn-accent col-span-1 py-2 text-sm">
                    Simpan pelanggan
                  </button>
                  <button onClick={() => setCustomerNew(null)} className="btn-quiet py-2 text-sm">
                    Batal
                  </button>
                  {customerError && <p className="col-span-2 text-xs text-[color:var(--bad)]">{customerError}</p>}
                </div>
              ) : (
                <div className="relative mt-1">
                  <input
                    value={customerQuery}
                    onChange={(e) => setCustomerQuery(e.target.value)}
                    className="field w-full text-sm"
                    placeholder="Cari nama atau nomor HP"
                  />
                  {(customerMatches.length > 0 || customerQuery.trim().length >= 2) && (
                    <ul className="absolute bg-[color:var(--surface-float)] shadow-pop left-0 right-0 top-full z-10 mt-1 max-h-44 overflow-auto rounded-2xl py-1 text-sm">
                      {customerMatches.map((m) => (
                        <li key={m.id}>
                          <button
                            onClick={() => {
                              setCustomer(m);
                              setCustomerQuery("");
                            }}
                            className="flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-[color:var(--accent-soft)]/40"
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
                          className="ink-soft w-full px-3 py-1.5 text-left text-xs hover:bg-[color:var(--accent-soft)]/40"
                        >
                          + Pelanggan baru &ldquo;{customerQuery.trim()}&rdquo;
                        </button>
                      </li>
                    </ul>
                  )}
                </div>
              )}
            </div>
            <label className="mt-3 block">
              <span className="ink-soft text-[13px] font-medium">Kode voucher</span>
              <input
                value={voucherCode}
                onChange={(e) => setVoucherCode(e.target.value.toUpperCase())}
                className="field mt-1 w-full font-mono text-sm"
                placeholder="mis. HEMAT5"
              />
              {quote?.voucher_error && voucherCode.trim() && (
                <span className="mt-1 block text-xs text-[color:var(--bad)]">{quote.voucher_error}</span>
              )}
            </label>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <label className="block">
                <span className="ink-soft text-[13px] font-medium">Diskon struk (Rp)</span>
                <input
                  inputMode="numeric"
                  value={billDiscount}
                  onChange={(e) => setBillDiscount(e.target.value.replace(/[^0-9]/g, ""))}
                  className="field mt-1 w-full text-sm tabular-nums"
                  placeholder="0"
                />
              </label>
              {needsPin && (
                <label className="block">
                  <span className="ink-soft text-[13px] font-medium">PIN pemilik / manajer</span>
                  <input
                    type="password"
                    inputMode="numeric"
                    value={managerPin}
                    onChange={(e) => setManagerPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
                    className="field mt-1 w-full text-sm tabular-nums"
                    placeholder="••••"
                  />
                </label>
              )}
            </div>

            {loyalty?.is_active && customer && customer.points_balance >= (loyalty.min_redeem_points || 1) && (
              <label className="surface-inset rounded-2xl mt-3 flex items-center justify-between rounded-2xl px-3 py-2 text-sm">
                <span>
                  <input type="checkbox" className="mr-2" checked={usePoints} onChange={(e) => setUsePoints(e.target.checked)} />
                  Pakai poin
                  <span className="ink-faint"> ({customer.points_balance} poin ≈ {formatRupiah(customer.points_balance * pointValue)})</span>
                </span>
                {pointsAmount > 0 && (
                  <span className="font-semibold tabular-nums">
                    − {formatRupiah(pointsAmount)} <span className="ink-faint font-normal">({pointsUsable} poin)</span>
                  </span>
                )}
              </label>
            )}
            {pointsAmount > 0 && (
              <p className="ink-soft mt-2 text-sm">
                Sisa dibayar <span className="font-semibold tabular-nums">{formatRupiah(moneyDue)}</span>
              </p>
            )}

            <div className="mt-5 grid grid-cols-3 gap-2">
              {(
                [
                  ["cash", "Tunai"],
                  ["qris", "QRIS"],
                  ["split", "Bagi dua"],
                ] as [PayMode, string][]
              ).map(([mode, label]) => (
                <button
                  key={mode}
                  onClick={() => setPayMode(mode)}
                  aria-pressed={payMode === mode}
                  className={`rounded-2xl py-3 text-sm font-semibold transition-colors ${
                    payMode === mode ? "toggle-on" : "toggle-off"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {payMode === "split" && (
              <div className="mt-5">
                <label className="ink-soft text-sm" htmlFor="cash-part">
                  Tunai (sisanya QRIS)
                </label>
                <input
                  id="cash-part"
                  inputMode="numeric"
                  value={cashPart}
                  onChange={(e) => setCashPart(e.target.value.replace(/[^0-9]/g, ""))}
                  placeholder="0"
                  className="field mt-1 w-full py-3 text-xl font-semibold tabular-nums"
                />
                <p className="ink-faint mt-2 text-sm">
                  Tunai {formatRupiah(cashAmount)} · QRIS {formatRupiah(Math.max(0, qrisAmount))}
                  {!splitValid && cashPart !== "" && " — tunai harus di antara 0 dan total"}
                </p>
              </div>
            )}

            {error && (
              <p
                className="mt-4 rounded-2xl px-4 py-3 text-center text-sm font-medium"
                style={{ background: "var(--bad-bg)", color: "var(--bad)" }}
              >
                {error}
              </p>
            )}

            <button
              onClick={confirmOrder}
              disabled={busy || !splitValid}
              className="btn-accent mt-6 w-full py-4 text-lg disabled:opacity-50"
            >
              {busy ? "Menyimpan…" : `Catat ${formatRupiah(cartTotal)}${pointsAmount > 0 ? ` (${formatRupiah(moneyDue)} + poin)` : ""}`}
            </button>
          </div>
        </div>
      )}

      {/* Success flash */}
      {flash && (
        <div className="fixed inset-x-0 bottom-8 z-30 flex justify-center">
          <div className="dock flex animate-scale-in items-center gap-3 rounded-3xl px-6 py-4">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-full text-[color:var(--on-accent)]"
              style={{ background: "var(--good)" }}
            >
              <IconCheck className="h-5 w-5" />
            </span>
            <div>
              <p className="font-semibold">
                {formatRupiah(flash.total)} ·{" "}
                {flash.lines.map((l) => `${formatQty(l.quantity)}× ${l.item_name}`).join(", ")}
              </p>
              <p className="ink-soft text-xs">
                {flash.payments
                  .map((p) => `${p.method === "cash" ? "tunai" : p.method === "points" ? "poin" : p.method.toUpperCase()} ${formatRupiah(p.amount)}`)
                  .join(" + ")}
                {flash.points_earned > 0 ? ` · +${flash.points_earned} poin` : ""}
              </p>
            </div>
            <button onClick={() => printReceipt(flash.id)} className="btn-quiet ml-2 px-3 py-2 text-sm">
              🖨 Cetak struk
            </button>
          </div>
        </div>
      )}

      {receipt && <ReceiptSheet receipt={receipt} onClose={() => setReceipt(null)} />}
    </main>
  );
}

/** Printable receipt (browser print — no drivers, roadmap §4.1). Every line
 * shows its size and modifiers as sold. */
/** M15-T11 — the screen that was missing.
 *
 *  `POST /pos/orders/{id}/void` and `/refund` have existed and been tested since
 *  M3-T4, and M15-T7 gave them a manager role and an audit trail, but nothing in
 *  the app ever called them: a sale rung up wrong needed a developer. Three
 *  steps, in the order a cashier thinks in — find it, look at it, reverse it.
 *
 *  The list is today's business day only (the server decides, M15-T4). A mistake
 *  found after the shift closed is the owner's job on the dashboard. */
function ReversalSheet({
  token,
  onClose,
  onReversed,
}: {
  token: string | null;
  onClose: () => void;
  onReversed: () => void;
}) {
  const [rows, setRows] = useState<OrderRow[] | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Receipt | null>(null);
  const [mode, setMode] = useState<"void" | "refund" | null>(null);
  const [restock, setRestock] = useState(true);
  const [pin, setPin] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ result: ReversalResult; receipt: Receipt } | null>(null);
  const [printing, setPrinting] = useState<Receipt | null>(null);

  const load = useCallback(
    (q: string) => {
      api<OrdersPage>(`/pos/orders?q=${encodeURIComponent(q)}`, { token })
        .then((page) => setRows(page.rows))
        .catch((e: unknown) => {
          setRows([]);
          setError(e instanceof ApiError ? e.detail : "Tidak bisa memuat transaksi.");
        });
    },
    [token]
  );
  useEffect(() => {
    const id = setTimeout(() => load(query), query ? 250 : 0);
    return () => clearTimeout(id);
  }, [load, query]);

  async function openOrder(row: OrderRow) {
    setError(null);
    try {
      setOpen(await api<Receipt>(`/pos/orders/${row.id}/receipt`, { token }));
      setMode(null);
      setPin("");
      setNote("");
      setRestock(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Tidak bisa membuka struk.");
    }
  }

  async function submit() {
    if (!open || !mode) return;
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { manager_pin: pin, note: note.trim() || null };
      if (mode === "refund") body.restock = restock;
      const result = await api<ReversalResult>(`/pos/orders/${open.order_id}/${mode}`, { body, token });
      const receipt = await api<Receipt>(`/pos/orders/${open.order_id}/receipt`, { token });
      setDone({ result, receipt });
      setOpen(null);
      setMode(null);
      setPin("");
      onReversed();
      load(query);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Gagal memproses — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  const time = (iso: string) =>
    new Date(iso).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });

  return (
    <div
      className="sheet-scrim"
      onClick={() => !busy && onClose()}
    >
      <div
        className="sheet-panel block overflow-y-auto sm:max-w-lg px-6 pb-10 pt-6 sm:pb-8"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div>
            <p className="text-[19px] font-semibold tracking-[-0.015em]">Transaksi hari ini</p>
            <p className="ink-soft text-sm">
              Salah pencet? Buka transaksinya, lalu batalkan atau kembalikan. Perlu PIN pemilik atau
              manajer.
            </p>
          </div>
          <button onClick={onClose} className="ink-soft rounded-full px-3 py-1 text-sm">
            tutup
          </button>
        </div>

        {error && <p className="mt-3 text-sm text-[color:var(--bad)]">{error}</p>}

        {/* 3 — what happened */}
        {done ? (
          <div className="mt-4">
            <div
              className="rounded-3xl px-5 py-4"
              style={{ background: "var(--good-bg)", color: "var(--good)" }}
            >
              <p className="text-lg font-bold">
                {done.result.status === "voided" ? "Transaksi dibatalkan" : "Uang dikembalikan"}
              </p>
              <p className="text-sm">
                #{done.receipt.number} · {formatRupiah(done.receipt.total)}
                {done.result.reversing_lines.some((l) => l.stock_after !== null)
                  ? " · stok sudah dikembalikan"
                  : " · stok tidak dikembalikan"}
              </p>
            </div>
            <div className="mt-4 flex gap-3">
              <button onClick={() => setPrinting(done.receipt)} className="btn-quiet flex-1 py-3">
                🖨 Cetak struk
              </button>
              <button
                onClick={() => {
                  setDone(null);
                  load(query);
                }}
                className="btn-accent flex-1 py-3"
              >
                Selesai
              </button>
            </div>
          </div>
        ) : open ? (
          /* 2 — look at it, then choose */
          <div className="mt-4">
            <button onClick={() => setOpen(null)} className="ink-soft text-sm">
              ← kembali ke daftar
            </button>
            <div className="surface-inset rounded-2xl mt-3 px-4 py-3">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-lg font-bold tracking-[-0.02em]">#{open.number}</p>
                <p className="text-lg font-bold tabular-nums">{formatRupiah(open.total)}</p>
              </div>
              <p className="ink-soft text-xs">
                {time(open.sold_at)}
                {open.staff_name ? ` · ${open.staff_name}` : ""}
                {open.customer_name ? ` · utk ${open.customer_name}` : ""}
              </p>
              <ul className="mt-2 space-y-0.5 text-sm">
                {open.lines
                  .filter((l) => Number(l.quantity) > 0)
                  .map((l, i) => (
                    <li key={i} className="flex justify-between gap-3">
                      <span className="min-w-0 truncate">
                        {formatQty(l.quantity)}× {l.name}
                        {l.variant && l.variant !== "Standar" ? ` (${l.variant})` : ""}
                      </span>
                      <span className="tabular-nums">{formatRupiah(l.line_total)}</span>
                    </li>
                  ))}
              </ul>
            </div>

            {open.status !== "completed" ? (
              <p className="surface-inset rounded-2xl mt-4 px-4 py-6 text-center text-sm">
                Transaksi ini {open.status === "voided" ? "sudah dibatalkan" : "sudah dikembalikan"} —
                tidak bisa dibalik lagi.
              </p>
            ) : (
              <>
                <div className="mt-4 grid grid-cols-2 gap-3">
                  <button
                    onClick={() => setMode("void")}
                    className={`rounded-2xl px-4 py-3 text-sm font-semibold ${mode === "void" ? "btn-accent" : "btn-quiet"}`}
                  >
                    Batalkan
                    <span className="ink-faint block text-[11px] font-normal">salah pencet, barang belum diambil</span>
                  </button>
                  <button
                    onClick={() => setMode("refund")}
                    className={`rounded-2xl px-4 py-3 text-sm font-semibold ${mode === "refund" ? "btn-accent" : "btn-quiet"}`}
                  >
                    Kembalikan
                    <span className="ink-faint block text-[11px] font-normal">uang dikembalikan ke pembeli</span>
                  </button>
                </div>

                {mode && (
                  <div className="mt-4 space-y-3">
                    {mode === "refund" && (
                      <label className="surface-inset rounded-2xl flex items-center justify-between gap-3 rounded-2xl px-4 py-3 text-sm">
                        <span>
                          Barang kembali ke stok
                          <span className="ink-faint block text-[11px]">
                            matikan kalau barangnya sudah terpakai, tumpah, atau dibuang
                          </span>
                        </span>
                        <input
                          type="checkbox"
                          checked={restock}
                          onChange={(e) => setRestock(e.target.checked)}
                          className="h-5 w-5"
                        />
                      </label>
                    )}
                    <label className="block">
                      <span className="ink-soft text-[13px] font-medium">Alasan</span>
                      <input
                        value={note}
                        onChange={(e) => setNote(e.target.value.slice(0, 200))}
                        className="field mt-1 w-full text-sm"
                        placeholder="cth. salah pencet menu"
                      />
                    </label>
                    <label className="block">
                      <span className="ink-soft text-[13px] font-medium">PIN pemilik / manajer</span>
                      <input
                        type="password"
                        inputMode="numeric"
                        value={pin}
                        onChange={(e) => setPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
                        className="field mt-1 w-full text-sm tabular-nums"
                        placeholder="••••"
                      />
                    </label>
                    <button
                      onClick={submit}
                      disabled={busy || pin.length < 4}
                      className="btn-accent w-full py-3.5 text-lg"
                    >
                      {busy
                        ? "Memproses…"
                        : mode === "void"
                          ? `Batalkan #${open.number}`
                          : `Kembalikan ${formatRupiah(open.total)}`}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        ) : (
          /* 1 — find it */
          <div className="mt-4">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="field w-full py-3 text-sm"
              placeholder="Cari nomor struk, mis. A1B2C3D4"
            />
            {rows === null ? (
              <p className="surface-inset rounded-2xl mt-4 px-4 py-6 text-center text-sm">Memuat…</p>
            ) : rows.length === 0 ? (
              <p className="surface-inset rounded-2xl mt-4 px-4 py-6 text-center text-sm">
                {query ? "Nomor struk itu tidak ada hari ini." : "Belum ada transaksi hari ini."}
              </p>
            ) : (
              <ul className="mt-4 space-y-2">
                {rows.map((row) => (
                  <li key={row.id}>
                    <button
                      onClick={() => openOrder(row)}
                      className="surface-inset rounded-2xl flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
                    >
                      <div className="min-w-0">
                        <p className="font-bold tracking-tight">
                          #{row.number}
                          {row.status !== "completed" && (
                            <span className="ink-faint font-normal">
                              {" "}
                              · {row.status === "voided" ? "dibatalkan" : "dikembalikan"}
                            </span>
                          )}
                        </p>
                        <p className="ink-soft text-xs">
                          {time(row.sold_at)} · {row.line_count} item
                          {row.staff_name ? ` · ${row.staff_name}` : ""}
                        </p>
                      </div>
                      <p className="shrink-0 font-semibold tabular-nums">{formatRupiah(row.total)}</p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
      {printing && <ReceiptSheet receipt={printing} onClose={() => setPrinting(null)} />}
    </div>
  );
}


function ReceiptSheet({ receipt, onClose }: { receipt: Receipt; onClose: () => void }) {
  const when = new Date(receipt.sold_at).toLocaleString("id-ID", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
  const method = (m: string) => (m === "cash" ? "Tunai" : m.toUpperCase());
  return (
    <div className="sheet-scrim z-[60] items-center p-6 print:bg-transparent print:backdrop-blur-none" onClick={onClose}>
      <style>{`@media print { body * { visibility: hidden; } #receipt, #receipt * { visibility: visible; } #receipt { position: absolute; left: 0; top: 0; width: 80mm; box-shadow: none; border-radius: 0; } }`}</style>
      <div
        id="receipt"
        onClick={(e) => e.stopPropagation()}
        className="w-[320px] rounded-2xl bg-white px-5 py-6 font-mono text-[12px] leading-5 text-black shadow-pop"
      >
        <p className="text-center text-sm font-bold uppercase">{receipt.business_name}</p>
        <p className="text-center">{when}</p>
        <p className="text-center">
          #{receipt.number}
          {receipt.staff_name ? ` · ${receipt.staff_name}` : ""}
          {receipt.customer_name ? ` · utk ${receipt.customer_name}` : ""}
          {receipt.status !== "completed" ? ` · ${receipt.status === "voided" ? "DIBATALKAN" : "DIKEMBALIKAN"}` : ""}
        </p>
        <p className="text-center">
          {ORDER_TYPE_LABEL[receipt.order_type as OrderType] ?? receipt.order_type}
          {receipt.table_label ? ` · ${receipt.table_label}` : ""}
          {receipt.delivery_address ? ` · ${receipt.delivery_address}` : ""}
        </p>
        <hr className="my-3 border-dashed border-black" />
        {receipt.lines.map((l, i) => (
          <div key={i} className="mb-2">
            <div className="flex justify-between gap-2">
              <span>
                {formatQty(l.quantity)}× {l.name}
                {l.variant && l.variant !== "Standar" ? ` (${l.variant})` : ""}
              </span>
              <span>{formatRupiah(l.line_total)}</span>
            </div>
            {l.modifiers.map((m, j) => (
              <div key={j} className="flex justify-between gap-2 pl-4 text-[11px]">
                <span>+ {m.name}</span>
                <span>{Number(m.price_delta) > 0 ? formatRupiah(m.price_delta) : ""}</span>
              </div>
            ))}
            {l.notes && <div className="pl-4 text-[11px] italic">{l.notes}</div>}
          </div>
        ))}
        <hr className="my-3 border-dashed border-black" />
        {(Number(receipt.discount_total) > 0 ||
          Number(receipt.promo_total) > 0 ||
          Number(receipt.voucher_total) > 0 ||
          Number(receipt.service_charge) > 0 ||
          Number(receipt.delivery_fee) > 0 ||
          Number(receipt.tax_total) > 0 ||
          Number(receipt.rounding) !== 0) && (
          <div className="space-y-0.5">
            <div className="flex justify-between">
              <span>Subtotal</span>
              <span>{formatRupiah(receipt.subtotal)}</span>
            </div>
            {Number(receipt.discount_total) > 0 && (
              <div className="flex justify-between">
                <span>Diskon</span>
                <span>-{formatRupiah(receipt.discount_total)}</span>
              </div>
            )}
            {Number(receipt.promo_total) > 0 && (
              <div className="flex justify-between">
                <span>Promo{receipt.promo_names.length ? ` (${receipt.promo_names.join(", ")})` : ""}</span>
                <span>-{formatRupiah(receipt.promo_total)}</span>
              </div>
            )}
            {Number(receipt.voucher_total) > 0 && (
              <div className="flex justify-between">
                <span>Voucher {receipt.voucher_code ?? ""}</span>
                <span>-{formatRupiah(receipt.voucher_total)}</span>
              </div>
            )}
            {Number(receipt.service_charge) > 0 && (
              <div className="flex justify-between">
                <span>Service</span>
                <span>{formatRupiah(receipt.service_charge)}</span>
              </div>
            )}
            {Number(receipt.delivery_fee) > 0 && (
              <div className="flex justify-between">
                <span>Ongkos kirim</span>
                <span>{formatRupiah(receipt.delivery_fee)}</span>
              </div>
            )}
            {Number(receipt.tax_total) > 0 && (
              <div className="flex justify-between">
                <span>{receipt.tax_inclusive ? "Pajak (termasuk)" : "Pajak"}</span>
                <span>{formatRupiah(receipt.tax_total)}</span>
              </div>
            )}
            {Number(receipt.rounding) !== 0 && (
              <div className="flex justify-between">
                <span>Pembulatan</span>
                <span>
                  {Number(receipt.rounding) < 0 ? "-" : ""}
                  {formatRupiah(Math.abs(Number(receipt.rounding)))}
                </span>
              </div>
            )}
          </div>
        )}
        <div className="flex justify-between font-bold">
          <span>TOTAL</span>
          <span>{formatRupiah(receipt.total)}</span>
        </div>
        {receipt.payments.map((p, i) => (
          <div key={i} className="flex justify-between">
            <span>{method(p.method)}</span>
            <span>{formatRupiah(p.amount)}</span>
          </div>
        ))}
        {(receipt.points_earned > 0 || receipt.points_redeemed > 0) && (
          <p className="mt-2 text-center text-[11px]">
            {receipt.points_redeemed > 0 ? `Poin dipakai: ${receipt.points_redeemed}` : ""}
            {receipt.points_redeemed > 0 && receipt.points_earned > 0 ? " · " : ""}
            {receipt.points_earned > 0 ? `Poin didapat: +${receipt.points_earned}` : ""}
          </p>
        )}
        <p className="mt-4 text-center">Terima kasih 🙏</p>
        <div className="mt-4 flex gap-2 print:hidden">
          <button onClick={() => window.print()} className="btn-accent flex-1 py-2 text-sm">
            Cetak
          </button>
          <button onClick={onClose} className="btn-quiet flex-1 py-2 text-sm">
            Tutup
          </button>
        </div>
      </div>
    </div>
  );
}

function QtyButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <button
      onClick={onPress}
      className="glass-card h-14 w-14 rounded-full text-2xl font-bold shadow-key transition-transform active:scale-90"
    >
      {label}
    </button>
  );
}
