"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { formatRupiah } from "@/lib/format";

// The QR e-menu (M11-T1). A guest scans the code on the table, orders, and
// gets a short ticket code to quote at the counter. The order lands in the
// till's own queue as an open ticket; nothing is charged here — the cashier
// takes payment, and this page watches the ticket until it is paid.

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
type MenuItem = {
  id: string;
  name: string;
  unit: string;
  sell_price: string;
  available: boolean;
  made_to_order: boolean;
  variants: Variant[];
  modifier_groups: ModifierGroup[];
};
type Menu = { business_name: string; items: MenuItem[] };
type CartLine = { item: MenuItem; variant: Variant | null; modifiers: Modifier[]; qty: number; notes: string };
type Ticket = {
  id: string;
  code: string;
  status: "open" | "completed" | "voided" | "refunded";
  order_type: string;
  table_label: string | null;
  guest_name: string | null;
  placed_at: string;
  lines: { name: string; modifiers: string[]; quantity: string; unit_price: string; line_total: string; notes: string | null }[];
  subtotal: string;
  service_charge: string;
  tax_total: string;
  rounding: string;
  total: string;
  is_estimate: boolean;
};
type OrderType = "dine_in" | "takeaway";

const lineKey = (l: { item: MenuItem; variant: Variant | null; modifiers: Modifier[]; notes: string }) =>
  `${l.item.id}:${l.variant?.id ?? "-"}:${l.modifiers.map((m) => m.id).sort().join(",")}:${l.notes}`;
const linePrice = (l: { item: MenuItem; variant: Variant | null; modifiers: Modifier[] }) =>
  Number(l.variant?.sell_price ?? l.item.sell_price) + l.modifiers.reduce((s, m) => s + Number(m.price_delta), 0);
const lineName = (l: { item: MenuItem; variant: Variant | null; modifiers: Modifier[] }) => {
  const base = l.variant && l.item.variants.length > 1 ? `${l.item.name} · ${l.variant.name}` : l.item.name;
  return l.modifiers.length ? `${base} (${l.modifiers.map((m) => m.name).join(", ")})` : base;
};
const defaultChoices = (item: MenuItem): Record<string, Modifier[]> =>
  Object.fromEntries(item.modifier_groups.map((g) => [g.id, g.modifiers.filter((m) => m.is_default)]));
const missingRequired = (item: MenuItem, chosen: Record<string, Modifier[]>) =>
  item.modifier_groups.filter((g) => g.is_required && (chosen[g.id]?.length ?? 0) < Math.max(1, g.min_select));
const needsPicker = (item: MenuItem) => item.variants.length > 1 || item.modifier_groups.length > 0;

export default function MenuPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const storageKey = `wp_menu_ticket:${token.slice(-16)}`;

  const [menu, setMenu] = useState<Menu | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [picker, setPicker] = useState<MenuItem | null>(null);
  const [variant, setVariant] = useState<Variant | null>(null);
  const [chosen, setChosen] = useState<Record<string, Modifier[]>>({});
  const [qty, setQty] = useState(1);
  const [notes, setNotes] = useState("");
  const [checkout, setCheckout] = useState(false);
  const [orderType, setOrderType] = useState<OrderType>("dine_in");
  const [table, setTable] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [ticket, setTicket] = useState<Ticket | null>(null);

  useEffect(() => {
    api<Menu>(`/menu/${token}`)
      .then(setMenu)
      .catch((e: unknown) => setLoadError(e instanceof ApiError ? e.detail : "Menu tidak bisa dimuat — coba pindai ulang kode QR."));
  }, [token]);

  // A refresh must not lose the guest's ticket: remember it per table code.
  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(storageKey);
    } catch {
      /* private mode */
    }
    if (!saved) return;
    api<Ticket>(`/menu/${token}/orders/${saved}`)
      .then((t) => {
        if (t.status === "open") setTicket(t);
        else localStorage.removeItem(storageKey);
      })
      .catch(() => {
        try {
          localStorage.removeItem(storageKey);
        } catch {
          /* ignore */
        }
      });
  }, [token, storageKey]);

  // Watch the ticket until the cashier has dealt with it.
  useEffect(() => {
    if (!ticket || ticket.status !== "open") return;
    const id = setInterval(() => {
      api<Ticket>(`/menu/${token}/orders/${ticket.id}`).then(setTicket).catch(() => undefined);
    }, 8000);
    return () => clearInterval(id);
  }, [ticket, token]);

  const cartTotal = useMemo(() => cart.reduce((s, l) => s + linePrice(l) * l.qty, 0), [cart]);
  const cartCount = cart.reduce((s, l) => s + l.qty, 0);

  const addLine = useCallback((item: MenuItem, v: Variant | null, mods: Modifier[], n: number, lineNotes: string) => {
    setCart((prev) => {
      const key = lineKey({ item, variant: v, modifiers: mods, notes: lineNotes });
      const found = prev.find((l) => lineKey(l) === key);
      if (found) return prev.map((l) => (l === found ? { ...l, qty: l.qty + n } : l));
      return [...prev, { item, variant: v, modifiers: mods, qty: n, notes: lineNotes }];
    });
  }, []);

  function openPicker(item: MenuItem) {
    if (!item.available) return;
    if (!needsPicker(item)) {
      addLine(item, item.variants[0] ?? null, [], 1, "");
      return;
    }
    setPicker(item);
    setVariant(item.variants.find((v) => v.is_default) ?? item.variants[0] ?? null);
    setChosen(defaultChoices(item));
    setQty(1);
    setNotes("");
  }

  function toggleModifier(group: ModifierGroup, m: Modifier) {
    setChosen((prev) => {
      const current = prev[group.id] ?? [];
      const has = current.some((x) => x.id === m.id);
      if (group.selection === "single") return { ...prev, [group.id]: has && !group.is_required ? [] : [m] };
      if (has) return { ...prev, [group.id]: current.filter((x) => x.id !== m.id) };
      if (group.max_select !== null && current.length >= group.max_select) return prev;
      return { ...prev, [group.id]: [...current, m] };
    });
  }

  function changeLine(key: string, delta: number) {
    setCart((prev) => prev.map((l) => (lineKey(l) === key ? { ...l, qty: l.qty + delta } : l)).filter((l) => l.qty > 0));
  }

  async function placeOrder() {
    if (cart.length === 0 || busy) return;
    if (orderType === "dine_in" && !table.trim()) {
      setSubmitError("Isi nomor meja dulu ya, supaya pesanan bisa diantar.");
      return;
    }
    setBusy(true);
    setSubmitError(null);
    try {
      const t = await api<Ticket>(`/menu/${token}/orders`, {
        body: {
          lines: cart.map((l) => ({
            item_id: l.item.id,
            variant_id: l.variant?.id ?? null,
            modifier_ids: l.modifiers.map((m) => m.id),
            quantity: l.qty,
            notes: l.notes || null,
          })),
          order_type: orderType,
          table_label: orderType === "dine_in" ? table.trim() : null,
          guest_name: name.trim() || null,
          note: note.trim() || null,
        },
      });
      setTicket(t);
      setCart([]);
      setCheckout(false);
      try {
        localStorage.setItem(storageKey, t.id);
      } catch {
        /* ignore */
      }
    } catch (e: unknown) {
      setSubmitError(e instanceof ApiError ? e.detail : "Pesanan belum terkirim — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  function orderAgain() {
    try {
      localStorage.removeItem(storageKey);
    } catch {
      /* ignore */
    }
    setTicket(null);
  }

  if (loadError) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md items-center justify-center px-6 text-center">
        <div className="glass-card px-6 py-8">
          <p className="text-lg font-bold">Menu tidak bisa dibuka</p>
          <p className="ink-soft mt-2 text-sm">{loadError}</p>
        </div>
      </main>
    );
  }

  if (ticket) {
    return <TicketView ticket={ticket} businessName={menu?.business_name ?? ""} onAgain={orderAgain} />;
  }

  const pickerMissing = picker ? missingRequired(picker, chosen) : [];
  const pickerPrice = picker ? linePrice({ item: picker, variant, modifiers: Object.values(chosen).flat() }) : 0;

  return (
    <main className="mx-auto min-h-screen max-w-md px-4 pb-32">
      <header className="hairline-b sticky top-0 z-10 -mx-4 mb-4 bg-[color:var(--bg-base)]/85 px-4 py-4 backdrop-blur-xl">
        <p className="ink-faint text-[11px] font-medium uppercase tracking-widest">Menu</p>
        <p className="text-xl font-bold leading-tight">{menu?.business_name ?? "…"}</p>
        <p className="ink-soft mt-1 text-xs">Pilih pesanan, lalu bayar di kasir. Tidak perlu daftar.</p>
      </header>

      {menu === null ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="glass-card h-20 animate-pulse" />
          ))}
        </div>
      ) : menu.items.length === 0 ? (
        <p className="glass-card px-4 py-6 text-center text-sm">Menu belum diisi. Tanya kasir ya.</p>
      ) : (
        <ul className="space-y-3">
          {menu.items.map((item) => {
            const inCart = cart.filter((l) => l.item.id === item.id).reduce((s, l) => s + l.qty, 0);
            return (
              <li key={item.id}>
                <button
                  disabled={!item.available}
                  onClick={() => openPicker(item)}
                  className={`glass-card flex w-full items-center justify-between gap-3 px-4 py-4 text-left transition-transform ${
                    item.available ? "active:scale-[0.98]" : "opacity-45"
                  }`}
                >
                  <div className="min-w-0">
                    <p className="truncate text-base font-bold">{item.name}</p>
                    <p className="text-sm font-semibold text-accent-700">
                      {formatRupiah(item.sell_price)}
                      {item.variants.length > 1 && <span className="ink-faint ml-1 text-xs font-medium">· {item.variants.length} ukuran</span>}
                    </p>
                    {!item.available && <p className="ink-faint text-xs">habis</p>}
                  </div>
                  <span
                    className={`flex h-9 min-w-9 shrink-0 items-center justify-center rounded-full px-3 text-sm font-bold ${
                      inCart > 0 ? "bg-accent-gradient text-white shadow-pop" : "glass-card"
                    }`}
                  >
                    {inCart > 0 ? inCart : "+"}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {/* Cart bar */}
      {cart.length > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-20 px-4 pb-5">
          <button
            onClick={() => {
              setSubmitError(null);
              setCheckout(true);
            }}
            className="btn-accent mx-auto flex w-full max-w-md items-center justify-between px-5 py-4 text-base shadow-pop"
          >
            <span>
              {cartCount} item · {formatRupiah(cartTotal)}
            </span>
            <span>Pesan →</span>
          </button>
        </div>
      )}

      {/* Size & extras picker */}
      {picker && (
        <div className="fixed inset-0 z-30 flex items-end justify-center bg-black/30 backdrop-blur-sm" onClick={() => setPicker(null)}>
          <div
            className="glass-card glass-strong max-h-[88vh] w-full max-w-md animate-fade-up overflow-y-auto rounded-b-none rounded-t-4xl px-6 pb-8 pt-5"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-xl font-bold">{picker.name}</p>
            {picker.variants.length > 1 && (
              <div className="mt-3">
                <p className="ink-faint text-xs font-medium uppercase tracking-wide">Ukuran</p>
                <div className="mt-1 flex flex-wrap gap-2">
                  {picker.variants.map((v) => (
                    <button
                      key={v.id}
                      onClick={() => setVariant(v)}
                      className={`rounded-2xl px-3 py-2 text-sm font-semibold ${variant?.id === v.id ? "bg-accent-gradient text-white shadow-pop" : "glass-card"}`}
                    >
                      {v.name} · {formatRupiah(v.sell_price)}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {picker.modifier_groups.map((g) => (
              <div key={g.id} className="mt-3">
                <p className="ink-faint text-xs font-medium uppercase tracking-wide">
                  {g.name}
                  {g.is_required ? " · wajib" : ""}
                  {g.selection === "multi" && g.max_select !== null ? ` · maks ${g.max_select}` : ""}
                </p>
                <div className="mt-1 flex flex-wrap gap-2">
                  {g.modifiers.map((m) => {
                    const on = (chosen[g.id] ?? []).some((x) => x.id === m.id);
                    const delta = Number(m.price_delta);
                    return (
                      <button
                        key={m.id}
                        onClick={() => toggleModifier(g, m)}
                        className={`rounded-2xl px-3 py-2 text-sm font-semibold ${on ? "bg-accent-gradient text-white shadow-pop" : "glass-card"}`}
                      >
                        {m.name}
                        {delta !== 0 ? ` ${delta > 0 ? "+" : "−"}${formatRupiah(Math.abs(delta))}` : ""}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value.slice(0, 200))}
              className="glass-card mt-4 w-full rounded-2xl px-4 py-2 text-sm"
              placeholder="Catatan (mis. tanpa gula)"
            />
            <div className="mt-4 flex items-center justify-between">
              <div className="glass-card flex items-center gap-1 rounded-2xl p-1">
                <button onClick={() => setQty((q) => Math.max(1, q - 1))} className="h-10 w-10 rounded-xl text-xl font-bold">
                  −
                </button>
                <span className="w-8 text-center text-lg font-bold tabular-nums">{qty}</span>
                <button onClick={() => setQty((q) => Math.min(99, q + 1))} className="h-10 w-10 rounded-xl text-xl font-bold">
                  +
                </button>
              </div>
              <p className="text-lg font-bold tabular-nums">{formatRupiah(pickerPrice * qty)}</p>
            </div>
            {pickerMissing.length > 0 && <p className="mt-2 text-xs" style={{ color: "var(--warn)" }}>Pilih dulu: {pickerMissing.map((g) => g.name).join(", ")}</p>}
            <button
              disabled={pickerMissing.length > 0}
              onClick={() => {
                addLine(picker, variant, Object.values(chosen).flat(), qty, notes.trim());
                setPicker(null);
              }}
              className="btn-accent mt-4 w-full py-3 text-base disabled:opacity-50"
            >
              Tambah ke pesanan
            </button>
          </div>
        </div>
      )}

      {/* Checkout */}
      {checkout && (
        <div className="fixed inset-0 z-30 flex items-end justify-center bg-black/30 backdrop-blur-sm" onClick={() => !busy && setCheckout(false)}>
          <div
            className="glass-card glass-strong max-h-[92vh] w-full max-w-md animate-fade-up overflow-y-auto rounded-b-none rounded-t-4xl px-6 pb-8 pt-5"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-xl font-bold">Pesanan kamu</p>
            <ul className="mt-3 space-y-2">
              {cart.map((l) => {
                const key = lineKey(l);
                return (
                  <li key={key} className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{lineName(l)}</p>
                      {l.notes && <p className="ink-faint truncate text-xs">{l.notes}</p>}
                      <p className="ink-soft text-xs">{formatRupiah(linePrice(l))}</p>
                    </div>
                    <div className="glass-card flex shrink-0 items-center gap-1 rounded-2xl p-0.5">
                      <button onClick={() => changeLine(key, -1)} className="h-8 w-8 rounded-xl font-bold">
                        −
                      </button>
                      <span className="w-6 text-center text-sm font-bold tabular-nums">{l.qty}</span>
                      <button onClick={() => changeLine(key, 1)} className="h-8 w-8 rounded-xl font-bold">
                        +
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
            <div className="mt-4 grid grid-cols-2 gap-2">
              {(
                [
                  ["dine_in", "Makan di sini"],
                  ["takeaway", "Bawa pulang"],
                ] as [OrderType, string][]
              ).map(([kind, label]) => (
                <button
                  key={kind}
                  onClick={() => setOrderType(kind)}
                  className={`rounded-2xl px-3 py-2.5 text-sm font-semibold ${orderType === kind ? "bg-accent-gradient text-white shadow-pop" : "glass-card"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {orderType === "dine_in" && (
                <input
                  value={table}
                  onChange={(e) => setTable(e.target.value.slice(0, 20))}
                  className="glass-card w-full rounded-2xl px-4 py-2.5 text-sm"
                  placeholder="Nomor meja"
                  autoFocus
                />
              )}
              <input
                value={name}
                onChange={(e) => setName(e.target.value.slice(0, 60))}
                className={`glass-card w-full rounded-2xl px-4 py-2.5 text-sm ${orderType === "dine_in" ? "" : "col-span-2"}`}
                placeholder="Nama (opsional)"
              />
            </div>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value.slice(0, 200))}
              className="glass-card mt-2 w-full rounded-2xl px-4 py-2.5 text-sm"
              placeholder="Pesan untuk dapur (opsional)"
            />
            <div className="mt-4 flex items-center justify-between">
              <span className="ink-soft text-sm">Perkiraan total</span>
              <span className="text-2xl font-bold tabular-nums">{formatRupiah(cartTotal)}</span>
            </div>
            <p className="ink-faint mt-1 text-xs">Pajak/servis (jika ada) dihitung di kasir. Bayar setelah pesanan diterima.</p>
            {submitError && <p className="mt-3 text-sm text-red-600">{submitError}</p>}
            <button onClick={placeOrder} disabled={busy || cart.length === 0} className="btn-accent mt-4 w-full py-3.5 text-lg disabled:opacity-50">
              {busy ? "Mengirim…" : "Kirim pesanan"}
            </button>
          </div>
        </div>
      )}
    </main>
  );
}

function TicketView({ ticket, businessName, onAgain }: { ticket: Ticket; businessName: string; onAgain: () => void }) {
  const waiting = ticket.status === "open";
  const paid = ticket.status === "completed" || ticket.status === "refunded";
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col px-4 py-6">
      <p className="ink-faint text-[11px] font-medium uppercase tracking-widest">{businessName}</p>
      <div className="glass-card glass-strong mt-3 px-6 py-8 text-center">
        <p className="ink-soft text-sm">{waiting ? "Kode pesanan kamu" : paid ? "Sudah dibayar" : "Pesanan dibatalkan"}</p>
        <p className="mt-1 text-5xl font-black tracking-tight">{ticket.code}</p>
        <p className="mt-3 text-sm font-medium">
          {waiting
            ? "Tunjukkan kode ini ke kasir untuk membayar. Pesanan mulai disiapkan setelah dibayar."
            : paid
              ? "Terima kasih! Pesanan sedang disiapkan."
              : "Kasir tidak bisa memproses pesanan ini — silakan tanya di kasir."}
        </p>
        {ticket.table_label && <p className="ink-faint mt-2 text-xs">{ticket.table_label}{ticket.guest_name ? ` · ${ticket.guest_name}` : ""}</p>}
        {waiting && (
          <p className="ink-faint mt-3 flex items-center justify-center gap-2 text-xs">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent-gradient" /> menunggu kasir…
          </p>
        )}
      </div>
      <ul className="glass-card mt-4 divide-y divide-black/5 px-5">
        {ticket.lines.map((l, i) => (
          <li key={i} className="flex items-start justify-between gap-3 py-3 text-sm">
            <div className="min-w-0">
              <p className="font-semibold">
                {l.quantity}× {l.name}
              </p>
              {l.modifiers.length > 0 && <p className="ink-faint text-xs">{l.modifiers.join(", ")}</p>}
              {l.notes && <p className="ink-faint text-xs">{l.notes}</p>}
            </div>
            <span className="shrink-0 tabular-nums">{formatRupiah(l.line_total)}</span>
          </li>
        ))}
        <li className="flex items-center justify-between py-3">
          <span className="ink-soft text-sm">{ticket.is_estimate ? "Perkiraan total" : "Total dibayar"}</span>
          <span className="text-xl font-bold tabular-nums">{formatRupiah(ticket.total)}</span>
        </li>
      </ul>
      {!waiting && (
        <button onClick={onAgain} className="btn-accent mt-6 w-full py-3.5 text-base">
          Pesan lagi
        </button>
      )}
    </main>
  );
}
