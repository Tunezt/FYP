"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError, POS_PAIRING_KEY, POS_TOKEN_KEY } from "@/lib/api";
import { formatQty, formatRupiah, initials } from "@/lib/format";

type StaffLite = { id: string; name: string; role: string };
type PosBusiness = { business_name: string; staff: StaffLite[] };
type Item = {
  id: string;
  name: string;
  unit: string;
  current_stock: string;
  sell_price: string;
  reorder_threshold: string;
};
type SaleResult = {
  item_name: string;
  quantity: string;
  total_price: string;
  remaining_stock: string;
};

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
  const [qty, setQty] = useState(1);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<SaleResult | null>(null);
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

  async function confirmSale() {
    if (!selected || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api<SaleResult>("/pos/sales", {
        token,
        body: { item_id: selected.id, quantity: qty },
      });
      setFlash(res);
      setSelected(null);
      setQty(1);
      loadItems();
      setTimeout(() => setFlash(null), 2200);
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
            return (
              <button
                key={item.id}
                disabled={out}
                onClick={() => {
                  setSelected(item);
                  setQty(1);
                  setError(null);
                }}
                className={`glass-card relative flex flex-col items-start gap-1 px-5 py-6 text-left transition-transform duration-150 ${
                  out ? "opacity-40" : "hover:scale-[1.02] active:scale-[0.97]"
                }`}
              >
                <span className="text-base font-bold leading-tight">{item.name}</span>
                <span className="text-accent-600 font-semibold">
                  {formatRupiah(item.sell_price)}
                </span>
                <span className={`mt-1 text-xs font-medium ${low ? "text-amber-600" : "ink-faint"}`}>
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
              {formatRupiah(selected.sell_price)} / {selected.unit} · sisa{" "}
              {formatQty(selected.current_stock)}
            </p>

            <div className="mt-6 flex items-center justify-center gap-6">
              <QtyButton label="−" onPress={() => setQty((q) => Math.max(1, q - 1))} />
              <span className="w-16 text-center text-4xl font-bold tabular-nums">{qty}</span>
              <QtyButton
                label="+"
                onPress={() => setQty((q) => Math.min(Number(selected.current_stock), q + 1))}
              />
            </div>

            {error && (
              <p className="mt-4 rounded-2xl bg-red-500/10 px-4 py-3 text-center text-sm font-medium text-red-600">
                {error}
              </p>
            )}

            <button onClick={confirmSale} disabled={busy} className="btn-accent mt-6 w-full py-4 text-lg">
              {busy ? "Menyimpan…" : `Catat ${formatRupiah(Number(selected.sell_price) * qty)}`}
            </button>
          </div>
        </div>
      )}

      {/* Success flash */}
      {flash && (
        <div className="pointer-events-none fixed inset-x-0 bottom-8 z-30 flex justify-center">
          <div className="glass-card glass-strong animate-scale-in flex items-center gap-3 px-6 py-4 shadow-pop">
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-emerald-500 text-white">
              ✓
            </span>
            <div>
              <p className="font-semibold">
                {formatQty(flash.quantity)}× {flash.item_name} · {formatRupiah(flash.total_price)}
              </p>
              <p className="ink-soft text-xs">sisa stok {formatQty(flash.remaining_stock)}</p>
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
