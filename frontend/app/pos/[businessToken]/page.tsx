"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError, POS_PAIRING_KEY, POS_TOKEN_KEY } from "@/lib/api";
import { formatQty, formatRupiah, initials } from "@/lib/format";

type StaffLite = { id: string; name: string; role: string };
type PosBusiness = { business_name: string; staff: StaffLite[] };
type Variant = { id: string; name: string; sell_price: string; is_default: boolean };
type Item = {
  id: string;
  name: string;
  unit: string;
  current_stock: string;
  sell_price: string;
  reorder_threshold: string;
  variants: Variant[];
};
type OrderResult = {
  id: string;
  total: string;
  lines: { item_name: string; quantity: string; line_total: string; remaining_stock: string }[];
  payments: { method: string; amount: string }[];
};
// A cart line is an item at one of its sizes (variant); stock is the item's.
type CartLine = { item: Item; variant: Variant | null; qty: number };
type PayMode = "cash" | "qris" | "split";

const lineKey = (itemId: string, variant: Variant | null) => `${itemId}:${variant?.id ?? "default"}`;
const linePrice = (l: { item: Item; variant: Variant | null }) => Number(l.variant?.sell_price ?? l.item.sell_price);
const lineName = (l: { item: Item; variant: Variant | null }) =>
  l.variant && l.item.variants.length > 1 ? `${l.item.name} · ${l.variant.name}` : l.item.name;

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
  const [qty, setQty] = useState(1);
  // One order = many lines + one or more payments (M3-T3). The cart is the order
  // being built; nothing is written until "Bayar" succeeds.
  const [cart, setCart] = useState<CartLine[]>([]);
  const [cartOpen, setCartOpen] = useState(false);
  const [paying, setPaying] = useState(false);
  const [payMode, setPayMode] = useState<PayMode>("cash");
  const [cashPart, setCashPart] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<OrderResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const token = posToken ?? (typeof window !== "undefined" ? localStorage.getItem(POS_TOKEN_KEY) : null);

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
  const cartTotal = cart.reduce((s, l) => s + linePrice(l) * l.qty, 0);

  function addToCart(item: Item, v: Variant | null, n: number) {
    setCart((c) => {
      const key = lineKey(item.id, v);
      const others = c.filter((l) => l.item.id === item.id && lineKey(l.item.id, l.variant) !== key)
        .reduce((s, l) => s + l.qty, 0);
      const room = Math.max(0, Number(item.current_stock) - others);
      const existing = c.find((l) => lineKey(l.item.id, l.variant) === key);
      if (existing) {
        return c.map((l) =>
          lineKey(l.item.id, l.variant) === key ? { ...l, qty: Math.min(room, l.qty + n) } : l
        );
      }
      return [...c, { item, variant: v, qty: Math.min(room, n) }];
    });
  }

  function changeLine(key: string, delta: number) {
    setCart((c) =>
      c
        .map((l) => {
          if (lineKey(l.item.id, l.variant) !== key) return l;
          const others = c.filter((o) => o.item.id === l.item.id && lineKey(o.item.id, o.variant) !== key)
            .reduce((s, o) => s + o.qty, 0);
          const room = Math.max(0, Number(l.item.current_stock) - others);
          return { ...l, qty: Math.min(room, l.qty + delta) };
        })
        .filter((l) => l.qty > 0)
    );
  }

  const cashAmount = payMode === "cash" ? cartTotal : payMode === "qris" ? 0 : Number(cashPart || 0);
  const qrisAmount = cartTotal - cashAmount;
  const splitValid = payMode !== "split" || (cashAmount > 0 && cashAmount < cartTotal);

  async function confirmOrder() {
    if (cart.length === 0 || busy || !splitValid) return;
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
          lines: cart.map((l) => ({ item_id: l.item.id, variant_id: l.variant?.id ?? null, quantity: l.qty })),
          payments,
          order_type: "takeaway",
        },
      });
      setFlash(res);
      setCart([]);
      setCartOpen(false);
      setPaying(false);
      setPayMode("cash");
      setCashPart("");
      loadItems();
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
        <button onClick={onLock} className="btn-quiet px-4 py-2 text-sm">
          🔒 Kunci
        </button>
      </header>

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
            const low = stock <= Number(item.reorder_threshold);
            const out = stock <= 0;
            const inCart = cartQty(item.id);
            return (
              <button
                key={item.id}
                disabled={out}
                onClick={() => {
                  setSelected(item);
                  setVariant(item.variants.find((v) => v.is_default) ?? item.variants[0] ?? null);
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
                  {out ? "habis" : `sisa ${formatQty(stock)} ${item.unit}`}
                  {low && !out ? " · hampir habis" : ""}
                </span>
              </button>
            );
          })}
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
              {formatRupiah(variant?.sell_price ?? selected.sell_price)} / {selected.unit} · sisa{" "}
              {formatQty(selected.current_stock)}
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

            <div className="mt-6 flex items-center justify-center gap-6">
              <QtyButton label="−" onPress={() => setQty((q) => Math.max(1, q - 1))} />
              <span className="w-16 text-center text-4xl font-bold tabular-nums">{qty}</span>
              <QtyButton
                label="+"
                onPress={() => setQty((q) => Math.min(Number(selected.current_stock), q + 1))}
              />
            </div>

            <button
              onClick={() => {
                addToCart(selected, variant, qty);
                setSelected(null);
                setQty(1);
              }}
              className="btn-accent mt-6 w-full py-4 text-lg"
            >
              Tambah {formatRupiah(Number(variant?.sell_price ?? selected.sell_price) * qty)}
            </button>
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
                  const key = lineKey(l.item.id, l.variant);
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
                      <span className="w-24 text-right text-sm font-semibold tabular-nums">
                        {formatRupiah(linePrice(l) * l.qty)}
                      </span>
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
        <div className="pointer-events-none fixed inset-x-0 bottom-8 z-30 flex justify-center">
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
          </div>
        </div>
      )}
    </main>
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
