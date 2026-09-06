"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError, POS_PAIRING_KEY, POS_TOKEN_KEY } from "@/lib/api";
import { formatQty, formatRupiah, initials } from "@/lib/format";

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
  status: string;
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
  service_charge: string;
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
  | { kind: "pin"; business: PosBusiness; staff: StaffLite; pin: string; shake: boolean }
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
      } catch {
        setScreen({ kind: "pin", business, staff, pin: "", shake: true });
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
        <div className="glass-card max-w-md px-8 py-10 text-center">
          <p className="text-4xl">🔌</p>
          <h1 className="mt-4 text-xl font-bold">Kasir belum terhubung</h1>
          <p className="ink-soft mt-2">{screen.message}</p>
        </div>
      </Center>
    );
  }

  if (screen.kind === "pick-staff") {
    return (
      <Center>
        <div className="w-full max-w-2xl animate-fade-up px-6">
          <p className="ink-soft text-center text-sm font-medium uppercase tracking-widest">
            {screen.business.business_name}
          </p>
          <h1 className="mt-2 text-center text-3xl font-bold tracking-tight">Siapa yang jaga?</h1>
          <div className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-3">
            {screen.business.staff.map((s, i) => (
              <button
                key={s.id}
                onClick={() =>
                  setScreen({ kind: "pin", business: screen.business, staff: s, pin: "", shake: false })
                }
                className="glass-card flex flex-col items-center gap-3 px-4 py-8 transition-transform duration-150 hover:scale-[1.03] active:scale-[0.97]"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <span className="flex h-16 w-16 items-center justify-center rounded-full bg-accent-gradient text-xl font-bold text-white shadow-pop">
                  {initials(s.name)}
                </span>
                <span className="text-lg font-semibold">{s.name}</span>
                {s.role === "owner" && (
                  <span className="rounded-full bg-accent-gradient-soft px-3 py-0.5 text-xs font-medium text-accent-600">
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
      staffName={screen.staffName}
      businessName={screen.businessName}
      onLock={() => {
        localStorage.removeItem(POS_TOKEN_KEY);
        setPosToken(null);
        api<PosBusiness>(`/pos/business/${pairingToken}`)
          .then((business) => setScreen({ kind: "pick-staff", business }))
          .catch(() => setScreen({ kind: "error", message: "Koneksi terputus." }));
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
  onDigit,
  onDelete,
  onBack,
}: {
  staff: StaffLite;
  pin: string;
  shake: boolean;
  onDigit: (d: string) => void;
  onDelete: () => void;
  onBack: () => void;
}) {
  return (
    <Center>
      <div className="w-full max-w-sm animate-scale-in px-6 text-center">
        <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-accent-gradient text-xl font-bold text-white shadow-pop">
          {initials(staff.name)}
        </span>
        <h1 className="mt-4 text-2xl font-bold">Halo, {staff.name}</h1>
        <p className="ink-soft mt-1">Masukkan PIN 4 angka</p>

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
              className={`h-4 w-4 rounded-full border-2 transition-all duration-150 ${
                i < pin.length
                  ? "border-accent-500 bg-accent-500 scale-110"
                  : "border-[color:var(--ink-faint)]"
              }`}
            />
          ))}
        </div>
        <style>{`@keyframes shake { 0%,100%{transform:translateX(0)} 20%{transform:translateX(-10px)} 40%{transform:translateX(10px)} 60%{transform:translateX(-6px)} 80%{transform:translateX(6px)} }`}</style>

        <div className="mx-auto mt-8 grid max-w-xs grid-cols-3 gap-3">
          {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
            <PinKey key={d} label={d} onPress={() => onDigit(d)} />
          ))}
          <button
            onClick={onBack}
            className="rounded-2xl py-5 text-sm font-medium text-[color:var(--ink-soft)] transition-transform active:scale-90"
          >
            batal
          </button>
          <PinKey label="0" onPress={() => onDigit("0")} />
          <button
            onClick={onDelete}
            aria-label="hapus"
            className="rounded-2xl py-5 text-2xl transition-transform active:scale-90"
          >
            ⌫
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
      className="glass-card glass-strong rounded-2xl py-5 text-2xl font-semibold shadow-key transition-transform duration-100 active:scale-90"
    >
      {label}
    </button>
  );
}

function SellScreen({
  posToken,
  staffName,
  businessName,
  onLock,
}: {
  posToken: string | null;
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
  // Discounts and the priced bill (M7-T4b).
  const [billDiscount, setBillDiscount] = useState("");
  const [managerPin, setManagerPin] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [lineDiscountFor, setLineDiscountFor] = useState<string | null>(null);
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
  }, [cart, billDiscount, token]);

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

  const cashAmount = payMode === "cash" ? cartTotal : payMode === "qris" ? 0 : Number(cashPart || 0);
  const qrisAmount = cartTotal - cashAmount;
  const splitValid = payMode !== "split" || (cashAmount > 0 && cashAmount < cartTotal);

  async function confirmOrder() {
    if (cart.length === 0 || busy || !splitValid) return;
    if (needsPin && managerPin.length < 4) {
      setError("Diskon perlu PIN pemilik — minta pemilik memasukkan PIN-nya.");
      return;
    }
    setBusy(true);
    setError(null);
    const payments = [
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
          order_type: "takeaway",
          bill_discount: Number(billDiscount || 0),
          manager_pin: needsPin ? managerPin || null : null,
        },
      });
      setFlash(res);
      setCart([]);
      setBillDiscount("");
      setManagerPin("");
      setQuote(null);
      setCartOpen(false);
      setPaying(false);
      setPayMode("cash");
      setCashPart("");
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
      <header className="hairline-b sticky top-0 z-10 -mx-4 mb-6 flex items-center justify-between bg-[color:var(--bg-base)]/80 px-4 py-4 backdrop-blur-xl">
        <div>
          <p className="ink-faint text-xs font-medium uppercase tracking-widest">{businessName}</p>
          <p className="text-lg font-bold">Kasir · {staffName}</p>
        </div>
        <div className="flex items-center gap-2">
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
              className="glass-card px-4 py-1.5 text-left"
              title="Tutup shift"
            >
              <p className="ink-faint text-[10px] font-medium uppercase tracking-wide">Shift buka · kas seharusnya</p>
              <p className="text-sm font-semibold tabular-nums">{formatRupiah(shift.expected_cash ?? shift.opening_float)}</p>
            </button>
          )}
          <button onClick={openCashSheet} className="btn-quiet px-4 py-2 text-sm" title="Kas masuk / keluar">
            Kas
          </button>
          <button onClick={onLock} className="btn-quiet px-4 py-2 text-sm">
            🔒 Kunci
          </button>
        </div>
      </header>

      {cashDone && (
        <p className="glass-card mb-4 px-4 py-2 text-sm font-medium">{cashDone}</p>
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
                className={`glass-card relative flex flex-col items-start gap-1 px-5 py-6 text-left transition-transform duration-150 ${
                  out ? "opacity-40" : "hover:scale-[1.02] active:scale-[0.97]"
                }`}
              >
                {inCart > 0 && (
                  <span className="absolute right-3 top-3 flex h-7 min-w-7 items-center justify-center rounded-full bg-accent-gradient px-2 text-xs font-bold text-white shadow-pop">
                    {inCart}
                  </span>
                )}
                <span className="text-base font-bold leading-tight">{item.name}</span>
                <span className="font-semibold text-accent-700">
                  {formatRupiah(item.sell_price)}
                  {item.variants.length > 1 && (
                    <span className="ink-faint ml-1 text-xs font-medium">· {item.variants.length} ukuran</span>
                  )}
                </span>
                <span
                  className={`mt-1 text-xs font-medium ${low ? "" : "ink-faint"}`}
                  style={low ? { color: "var(--warn)" } : undefined}
                >
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
          className="fixed inset-0 z-30 flex items-end justify-center bg-black/30 backdrop-blur-sm sm:items-center"
          onClick={() => !busy && setShiftSheet(null)}
        >
          <div
            className="glass-card glass-strong w-full max-w-md animate-fade-up rounded-b-none rounded-t-4xl px-8 pb-10 pt-6 sm:rounded-4xl sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-xl font-bold">{shiftSheet === "open" ? "Buka shift" : "Tutup shift"}</p>
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
              <span className="ink-faint text-xs font-medium uppercase tracking-wide">
                {shiftSheet === "open" ? "Modal awal (Rp)" : "Uang dihitung (Rp)"}
              </span>
              <input
                autoFocus
                inputMode="numeric"
                value={shiftAmount}
                onChange={(e) => setShiftAmount(e.target.value.replace(/[^0-9]/g, ""))}
                className="glass-card mt-1 w-full rounded-2xl px-4 py-3 text-2xl font-bold tabular-nums"
                placeholder="0"
              />
            </label>
            {shiftSheet === "close" && (
              <input
                value={shiftNote}
                onChange={(e) => setShiftNote(e.target.value)}
                className="glass-card mt-3 w-full rounded-2xl px-4 py-2 text-sm"
                placeholder="Catatan (opsional)"
              />
            )}
            {shiftError && <p className="mt-3 text-sm text-red-600">{shiftError}</p>}
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
          className="fixed inset-0 z-30 flex items-end justify-center bg-black/30 backdrop-blur-sm sm:items-center"
          onClick={() => !busy && setCashSheet(false)}
        >
          <div
            className="glass-card glass-strong w-full max-w-md animate-fade-up rounded-b-none rounded-t-4xl px-8 pb-10 pt-6 sm:rounded-4xl sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-xl font-bold">Kas masuk / keluar</p>
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
                  className={`rounded-2xl px-3 py-2.5 text-sm font-semibold transition-colors ${
                    cashKind === kind ? "bg-accent-gradient text-white shadow-pop" : "glass-card"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <label className="mt-4 block">
              <span className="ink-faint text-xs font-medium uppercase tracking-wide">Jumlah (Rp)</span>
              <input
                autoFocus
                inputMode="numeric"
                value={cashInput}
                onChange={(e) => setCashInput(e.target.value.replace(/[^0-9]/g, ""))}
                className="glass-card mt-1 w-full rounded-2xl px-4 py-3 text-2xl font-bold tabular-nums"
                placeholder="0"
              />
            </label>
            <input
              value={cashReason}
              onChange={(e) => setCashReason(e.target.value)}
              className="glass-card mt-3 w-full rounded-2xl px-4 py-2 text-sm"
              placeholder={cashKind === "petty_cash" ? "Beli apa? (mis. es batu)" : "Alasan"}
            />
            {cashKind === "petty_cash" && (
              <div className="mt-3 flex flex-wrap gap-2">
                {["bahan baku", "operasional", "lainnya"].map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setCashCategory(cat)}
                    className={`rounded-2xl px-3 py-1.5 text-xs font-semibold transition-colors ${
                      cashCategory === cat ? "bg-accent-gradient text-white shadow-pop" : "glass-card"
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            )}
            {cashKind === "supplier_payment" && (
              <select
                value={cashSupplier}
                onChange={(e) => setCashSupplier(e.target.value)}
                className="glass-card mt-3 w-full rounded-2xl px-4 py-2 text-sm"
              >
                <option value="">Pilih supplier…</option>
                {suppliers.map((sp) => (
                  <option key={sp.id} value={sp.id}>
                    {sp.name}
                  </option>
                ))}
              </select>
            )}
            {cashError && <p className="mt-3 text-sm text-red-600">{cashError}</p>}
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

      {/* Shift closed: the count against the expectation */}
      {shiftResult && (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/40"
          onClick={() => setShiftResult(null)}
        >
          <div
            className="glass-card glass-strong w-full max-w-sm animate-fade-up rounded-4xl px-8 py-8"
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
                <dd className={`font-bold tabular-nums ${Number(shiftResult.variance) < 0 ? "text-red-600" : ""}`}>
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
          className="fixed inset-0 z-20 flex items-end justify-center bg-black/30 backdrop-blur-sm sm:items-center"
          onClick={() => !busy && setSelected(null)}
        >
          <div
            className="glass-card glass-strong w-full max-w-md animate-fade-up rounded-b-none rounded-t-4xl px-8 pb-10 pt-6 sm:rounded-4xl sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mx-auto mb-5 h-1.5 w-10 rounded-full bg-[color:var(--ink-faint)] opacity-40 sm:hidden" />
            <p className="text-xl font-bold">{selected.name}</p>
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
                    className={`rounded-2xl px-4 py-2 text-sm font-semibold transition-colors ${
                      variant?.id === v.id ? "bg-accent-gradient text-white shadow-pop" : "glass-card"
                    }`}
                  >
                    {v.name} · {formatRupiah(v.sell_price)}
                  </button>
                ))}
              </div>
            )}

            {selected.modifier_groups.map((g) => (
              <div key={g.id} className="mt-4">
                <p className="ink-soft text-xs font-semibold uppercase tracking-wide">
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
                        className={`rounded-2xl px-3 py-2 text-sm font-medium transition-colors ${
                          on ? "bg-accent-gradient text-white shadow-pop" : "glass-card"
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
          <div className="glass-card glass-strong w-full max-w-2xl animate-fade-up px-5 py-3 shadow-pop">
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
            <div className="flex items-center gap-4">
              <button onClick={() => setCartOpen((o) => !o)} className="min-w-0 flex-1 text-left">
                <p className="ink-faint text-xs font-medium uppercase tracking-wide">
                  {cartCount} item · {cartOpen ? "tutup" : "lihat keranjang"}
                </p>
                <p className="text-2xl font-bold tabular-nums">{formatRupiah(cartTotal)}</p>
              </button>
              <button onClick={() => setCart([])} className="btn-quiet px-3 py-2 text-sm">
                Kosongkan
              </button>
              <button
                onClick={() => {
                  setError(null);
                  setPaying(true);
                }}
                className="btn-accent px-6 py-3 text-lg"
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
          className="fixed inset-0 z-30 flex items-end justify-center bg-black/30 backdrop-blur-sm sm:items-center"
          onClick={() => setLineDiscountFor(null)}
        >
          <div
            className="glass-card glass-strong w-full max-w-sm animate-fade-up rounded-b-none rounded-t-4xl px-8 pb-10 pt-6 sm:rounded-4xl sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-xl font-bold">Diskon baris</p>
            <p className="ink-soft text-sm">Potongan dalam rupiah untuk baris ini saja.</p>
            <input
              autoFocus
              inputMode="numeric"
              value={lineDiscountDraft}
              onChange={(e) => setLineDiscountDraft(e.target.value.replace(/[^0-9]/g, ""))}
              className="glass-card mt-4 w-full rounded-2xl px-4 py-3 text-2xl font-bold tabular-nums"
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
          className="fixed inset-0 z-20 flex items-end justify-center bg-black/30 backdrop-blur-sm sm:items-center"
          onClick={() => !busy && setPaying(false)}
        >
          <div
            className="glass-card glass-strong w-full max-w-md animate-fade-up rounded-b-none rounded-t-4xl px-8 pb-10 pt-6 sm:rounded-4xl sm:pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="ink-faint text-xs font-medium uppercase tracking-wide">
              {cartCount} item
            </p>
            <p className="text-3xl font-bold tabular-nums">{formatRupiah(cartTotal)}</p>
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
                {Number(quote.service_charge) > 0 && (
                  <div className="flex justify-between">
                    <dt className="ink-soft">Service charge</dt>
                    <dd className="tabular-nums">+ {formatRupiah(quote.service_charge)}</dd>
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
            <div className="mt-4 grid grid-cols-2 gap-2">
              <label className="block">
                <span className="ink-faint text-[10px] font-medium uppercase tracking-wide">Diskon struk (Rp)</span>
                <input
                  inputMode="numeric"
                  value={billDiscount}
                  onChange={(e) => setBillDiscount(e.target.value.replace(/[^0-9]/g, ""))}
                  className="glass-card mt-1 w-full rounded-2xl px-3 py-2 text-sm tabular-nums"
                  placeholder="0"
                />
              </label>
              {needsPin && (
                <label className="block">
                  <span className="ink-faint text-[10px] font-medium uppercase tracking-wide">PIN pemilik</span>
                  <input
                    type="password"
                    inputMode="numeric"
                    value={managerPin}
                    onChange={(e) => setManagerPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
                    className="glass-card mt-1 w-full rounded-2xl px-3 py-2 text-sm tabular-nums"
                    placeholder="••••"
                  />
                </label>
              )}
            </div>

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
                  className={`rounded-2xl py-3 text-sm font-semibold transition-colors ${
                    payMode === mode ? "bg-accent-gradient text-white shadow-pop" : "glass-card"
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
                  className="glass-card mt-1 w-full rounded-2xl px-4 py-3 text-xl font-semibold tabular-nums outline-none"
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
              {busy ? "Menyimpan…" : `Catat ${formatRupiah(cartTotal)}`}
            </button>
          </div>
        </div>
      )}

      {/* Success flash */}
      {flash && (
        <div className="fixed inset-x-0 bottom-8 z-30 flex justify-center">
          <div className="glass-card glass-strong animate-scale-in flex items-center gap-3 px-6 py-4 shadow-pop">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-full text-white"
              style={{ background: "var(--good)" }}
            >
              ✓
            </span>
            <div>
              <p className="font-semibold">
                {formatRupiah(flash.total)} ·{" "}
                {flash.lines.map((l) => `${formatQty(l.quantity)}× ${l.item_name}`).join(", ")}
              </p>
              <p className="ink-soft text-xs">
                {flash.payments
                  .map((p) => `${p.method === "cash" ? "tunai" : p.method.toUpperCase()} ${formatRupiah(p.amount)}`)
                  .join(" + ")}
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
function ReceiptSheet({ receipt, onClose }: { receipt: Receipt; onClose: () => void }) {
  const when = new Date(receipt.sold_at).toLocaleString("id-ID", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
  const method = (m: string) => (m === "cash" ? "Tunai" : m.toUpperCase());
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 print:bg-transparent" onClick={onClose}>
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
          {receipt.status !== "completed" ? ` · ${receipt.status === "voided" ? "DIBATALKAN" : "DIKEMBALIKAN"}` : ""}
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
          Number(receipt.service_charge) > 0 ||
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
            {Number(receipt.service_charge) > 0 && (
              <div className="flex justify-between">
                <span>Service</span>
                <span>{formatRupiah(receipt.service_charge)}</span>
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
