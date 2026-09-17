"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { formatRupiah } from "@/lib/format";
import { ProductPicker } from "@/components/ProductPicker";
import { IconCheck, IconClose, IconNote, IconSearch } from "@/components/icons";
import {
  displayName,
  freshSelection,
  identityKey,
  isQuickAdd,
  selectedModifiers,
  selectedVariant,
  type Modifier,
  type ModifierGroup,
  type Selection,
  type Variant,
} from "@/lib/choices";
import { newRef } from "@/lib/pos";

// The QR menu (M11-T1, svc-8): the customer's side of the same system. A guest
// scans the code on the table, chooses, sees the real total, and sends. Nothing
// is charged here — they show the order code at the counter and pay there; the
// kitchen starts after payment, and this page follows the order until it is
// handed over. The order is kept by the server; the phone keeps only its id and
// the private key that lets it see its own details.

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
type CartLine = { uid: string; item: MenuItem; variant: Variant | null; modifiers: Modifier[]; qty: number; notes: string };
type SavedLine = { item_id: string; variant_id: string | null; modifier_ids: string[]; qty: number; notes: string };
type Quote = { subtotal: string; service_charge: string; tax_total: string; tax_inclusive: boolean; rounding: string; total: string };
type Ticket = {
  id: string;
  code: string;
  status: "open" | "completed" | "voided" | "refunded";
  order_type: string;
  table_label: string | null;
  guest_name: string | null;
  note: string | null;
  placed_at: string;
  lines: { name: string; modifiers: string[]; quantity: string; unit_price: string; line_total: string; notes: string | null }[];
  subtotal: string;
  service_charge: string;
  tax_total: string;
  rounding: string;
  total: string;
  is_estimate: boolean;
  kitchen_state: "new" | "preparing" | "ready" | "done" | null;
  access_key: string | null;
  revised: boolean;
};
type OrderType = "dine_in" | "takeaway";

const lineKey = (l: CartLine) => identityKey(l.item.id, l.variant?.id ?? null, l.modifiers.map((m) => m.id), l.notes);
const linePrice = (l: CartLine) => Number(l.variant?.sell_price ?? l.item.sell_price) + l.modifiers.reduce((s, m) => s + Number(m.price_delta), 0);
const lineSelection = (l: CartLine): Selection => {
  const chosen: Record<string, string[]> = {};
  for (const g of l.item.modifier_groups) {
    const ids = l.modifiers.filter((m) => g.modifiers.some((x) => x.id === m.id)).map((m) => m.id);
    if (ids.length) chosen[g.id] = ids;
  }
  return { variantId: l.variant?.id ?? null, chosen, qty: l.qty, notes: l.notes };
};
let lineSeq = 0;

function store<T>(key: string, value: T | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private browsing: the page still works, it just forgets on refresh */
  }
}
function recall<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export default function MenuPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const suffix = token.slice(-16);
  const ORDER_KEY = `wp_menu_order:${suffix}`;
  const CART_KEY = `wp_menu_cart:${suffix}`;

  const [menu, setMenu] = useState<Menu | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [search, setSearch] = useState("");
  const [picker, setPicker] = useState<{ item: MenuItem; editUid: string | null; initial: Selection } | null>(null);
  const [checkout, setCheckout] = useState(false);
  const [orderType, setOrderType] = useState<OrderType>("dine_in");
  const [table, setTable] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [placed, setPlaced] = useState<{ id: string; key: string | null } | null>(null);
  const submitRef = useRef<string | null>(null);

  const loadMenu = useCallback(async () => {
    try {
      const m = await api<Menu>(`/menu/${token}`);
      setMenu(m);
      setLoadError(null);
      return m;
    } catch (e: unknown) {
      setLoadError(e instanceof ApiError ? e.detail : "Menu belum bisa dimuat — periksa sinyal, lalu coba lagi.");
      return null;
    }
  }, [token]);
  useEffect(() => {
    void loadMenu();
    setPlaced(recall<{ id: string; key: string | null }>(ORDER_KEY));
  }, [loadMenu, ORDER_KEY]);

  /** Rebuild a cart from ids against the menu as it is now: anything gone or
   *  sold out is dropped, and the guest is told which. */
  const rebuild = useCallback((saved: SavedLine[], m: Menu) => {
    const byId = new Map(m.items.map((i) => [i.id, i]));
    const kept: CartLine[] = [];
    const dropped: string[] = [];
    for (const s of saved) {
      const item = byId.get(s.item_id);
      const variant = item && s.variant_id ? item.variants.find((v) => v.id === s.variant_id) ?? null : item ? selectedVariant(item, freshSelection(item)) : null;
      const mods = item ? item.modifier_groups.flatMap((g) => g.modifiers).filter((x) => s.modifier_ids.includes(x.id)) : [];
      if (!item || !item.available || (s.variant_id && !variant) || mods.length !== s.modifier_ids.length) {
        dropped.push(item?.name ?? "Satu menu");
        continue;
      }
      kept.push({ uid: `m${++lineSeq}`, item, variant, modifiers: mods, qty: s.qty, notes: s.notes });
    }
    return { kept, dropped };
  }, []);

  // A refresh before sending keeps the guest's choices.
  const restored = useRef(false);
  useEffect(() => {
    if (!menu || restored.current) return;
    restored.current = true;
    const saved = recall<{ lines: SavedLine[]; ref: string | null }>(CART_KEY);
    if (!saved) return;
    const { kept, dropped } = rebuild(saved.lines, menu);
    setCart(kept);
    submitRef.current = saved.ref;
    if (dropped.length) setNotice(`${dropped.join(", ")} sedang tidak tersedia dan dihapus dari pesananmu.`);
  }, [menu, CART_KEY, rebuild]);
  useEffect(() => {
    if (!restored.current) return;
    store(
      CART_KEY,
      cart.length
        ? { lines: cart.map((l) => ({ item_id: l.item.id, variant_id: l.variant?.id ?? null, modifier_ids: l.modifiers.map((m) => m.id), qty: l.qty, notes: l.notes })), ref: submitRef.current }
        : null
    );
  }, [cart, CART_KEY]);

  function putLine(item: MenuItem, sel: Selection, replaceUid: string | null) {
    submitRef.current = null; // a changed cart is a new submission
    setCart((prev) => {
      const base = replaceUid ? prev.filter((l) => l.uid !== replaceUid) : prev;
      const draft: CartLine = {
        uid: replaceUid ?? `m${++lineSeq}`,
        item,
        variant: selectedVariant(item, sel),
        modifiers: selectedModifiers(item, sel),
        qty: sel.qty,
        notes: sel.notes.trim(),
      };
      const found = base.find((l) => lineKey(l) === lineKey(draft));
      if (found) return base.map((l) => (l === found ? { ...l, qty: Math.min(99, l.qty + draft.qty) } : l));
      const at = replaceUid ? prev.findIndex((l) => l.uid === replaceUid) : -1;
      return at >= 0 ? [...base.slice(0, at), draft, ...base.slice(at)] : [...base, draft];
    });
  }

  function changeQty(uid: string, delta: number) {
    submitRef.current = null;
    setCart((prev) => prev.map((l) => (l.uid === uid ? { ...l, qty: Math.min(99, l.qty + delta) } : l)).filter((l) => l.qty > 0));
  }

  const body = useMemo(
    () => cart.map((l) => ({ item_id: l.item.id, variant_id: l.variant?.id ?? null, modifier_ids: l.modifiers.map((m) => m.id), quantity: l.qty, notes: l.notes || null })),
    [cart]
  );

  // The real total, from the server, before sending.
  useEffect(() => {
    if (!checkout || cart.length === 0) {
      setQuote(null);
      return;
    }
    let cancelled = false;
    setQuoteError(null);
    const handle = setTimeout(() => {
      api<Quote>(`/menu/${token}/quote`, { body: { lines: body, order_type: orderType } })
        .then((q) => !cancelled && setQuote(q))
        .catch(async (e: unknown) => {
          if (cancelled) return;
          setQuote(null);
          setQuoteError(e instanceof ApiError ? e.detail : "Total belum bisa dihitung — periksa sinyal.");
          if (e instanceof ApiError && (e.status === 409 || e.status === 404)) {
            const m = await loadMenu();
            if (m) {
              const { kept, dropped } = rebuild(
                cart.map((l) => ({ item_id: l.item.id, variant_id: l.variant?.id ?? null, modifier_ids: l.modifiers.map((x) => x.id), qty: l.qty, notes: l.notes })),
                m
              );
              if (dropped.length) {
                setCart(kept);
                setNotice(`${dropped.join(", ")} baru saja habis dan dihapus dari pesananmu.`);
              }
            }
          }
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [checkout, body, orderType, token, loadMenu, rebuild, cart]);

  async function send() {
    if (cart.length === 0 || busy || !quote) return;
    if (orderType === "dine_in" && !table.trim()) {
      setSubmitError("Isi nomor meja dulu ya.");
      return;
    }
    setBusy(true);
    setSubmitError(null);
    // One reference per submission, kept across retries and refreshes: if the
    // first try reached the café before the signal dropped, the retry returns
    // that same order instead of a second one.
    if (!submitRef.current) submitRef.current = newRef();
    store(CART_KEY, { lines: cart.map((l) => ({ item_id: l.item.id, variant_id: l.variant?.id ?? null, modifier_ids: l.modifiers.map((m) => m.id), qty: l.qty, notes: l.notes })), ref: submitRef.current });
    try {
      const t = await api<Ticket>(`/menu/${token}/orders`, {
        body: {
          lines: body,
          order_type: orderType,
          table_label: orderType === "dine_in" ? table.trim() : null,
          guest_name: name.trim() || null,
          note: note.trim() || null,
          client_ref: submitRef.current,
          expected_total: quote.total,
        },
      });
      const saved = { id: t.id, key: t.access_key };
      store(ORDER_KEY, saved);
      store(CART_KEY, null);
      submitRef.current = null;
      setCart([]);
      setCheckout(false);
      setPlaced(saved);
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        setSubmitError(e.detail);
        if (e.status === 409) {
          // A price or availability moved: re-quote with the cart intact.
          submitRef.current = null;
          setQuote(null);
          const m = await loadMenu();
          if (m) {
            const { kept, dropped } = rebuild(
              cart.map((l) => ({ item_id: l.item.id, variant_id: l.variant?.id ?? null, modifier_ids: l.modifiers.map((x) => x.id), qty: l.qty, notes: l.notes })),
              m
            );
            setCart(kept);
            if (dropped.length) setNotice(`${dropped.join(", ")} sedang tidak tersedia dan dihapus dari pesananmu.`);
          }
        }
      } else {
        setSubmitError("Sinyal terputus. Tekan Kirim lagi — pesananmu tidak akan terkirim dua kali.");
      }
    } finally {
      setBusy(false);
    }
  }

  function orderAgain() {
    store(ORDER_KEY, null);
    setPlaced(null);
  }

  if (placed) {
    return <OrderStatus token={token} placed={placed} businessName={menu?.business_name ?? ""} onAgain={orderAgain} />;
  }

  if (loadError && !menu) {
    return (
      <main className="mx-auto flex min-h-[100dvh] max-w-md items-center justify-center px-6 text-center">
        <div>
          <p className="text-lg font-semibold">Menu belum bisa dibuka</p>
          <p className="ink-soft mt-1 text-sm">{loadError}</p>
          <button onClick={() => void loadMenu()} className="btn-accent mt-5 px-6 py-3">
            Coba lagi
          </button>
        </div>
      </main>
    );
  }

  const count = cart.reduce((n, l) => n + l.qty, 0);
  const estimate = cart.reduce((s, l) => s + linePrice(l) * l.qty, 0);
  const q = search.trim().toLocaleLowerCase("id-ID");
  const items = (menu?.items ?? []).filter((i) => !q || i.name.toLocaleLowerCase("id-ID").includes(q));

  return (
    <main className="mx-auto min-h-[100dvh] max-w-lg px-4 pb-32">
      <header className="pb-3 pt-6">
        <p className="ink-soft text-[13px] font-medium">Pesan dari meja · bayar di kasir</p>
        <h1 className="mt-0.5 text-[26px] font-semibold tracking-[-0.025em]">{menu?.business_name ?? " "}</h1>
      </header>
      <div className="sticky-bar sticky top-0 z-10 -mx-4 px-4 py-2">
        <label className="relative block">
          <span className="sr-only">Cari menu</span>
          <IconSearch className="ink-faint pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2" />
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} className="field py-2.5 pl-10" placeholder="Cari menu" />
        </label>
      </div>

      {notice && (
        <p role="status" className="notice notice-warn mt-2 flex items-start justify-between gap-3">
          {notice}
          <button onClick={() => setNotice(null)} aria-label="Tutup pemberitahuan" className="shrink-0">
            <IconClose className="h-4 w-4" />
          </button>
        </p>
      )}

      {menu === null ? (
        <ul className="mt-3 space-y-2" aria-busy>
          {Array.from({ length: 6 }).map((_, i) => (
            <li key={i} className="glass-card h-[4.5rem] animate-pulse" />
          ))}
        </ul>
      ) : items.length === 0 ? (
        <p className="ink-soft mt-10 text-center text-sm">{q ? `Tidak ada menu "${search}".` : "Menu belum diisi. Tanya kasir ya."}</p>
      ) : (
        <ul className="glass-card mt-3 overflow-hidden p-0">
          {items.map((item, i) => {
            const inCart = cart.filter((l) => l.item.id === item.id).reduce((n, l) => n + l.qty, 0);
            const sized = item.variants.length > 1;
            const from = sized ? Math.min(...item.variants.map((v) => Number(v.sell_price))) : Number(item.sell_price);
            return (
              <li key={item.id} className={i > 0 ? "hairline-t" : ""}>
                <button
                  disabled={!item.available}
                  onClick={() => (isQuickAdd(item) ? putLine(item, freshSelection(item), null) : setPicker({ item, editUid: null, initial: freshSelection(item) }))}
                  className="flex min-h-[4.25rem] w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-[color:var(--row-hover)] active:bg-[color:var(--row-press)] disabled:cursor-not-allowed"
                >
                  <div className={`min-w-0 flex-1 ${item.available ? "" : "opacity-50"}`}>
                    <p className="text-[16px] font-semibold leading-snug">{item.name}</p>
                    <p className="text-sm tabular-nums">
                      {sized && <span className="ink-soft">dari </span>}
                      {formatRupiah(from)}
                      {!isQuickAdd(item) && item.available && <span className="ink-faint"> · pilih {sized ? "ukuran" : "varian"}</span>}
                    </p>
                  </div>
                  {!item.available ? (
                    <span className="pill-quiet">Habis</span>
                  ) : (
                    <span
                      aria-hidden
                      className={`flex h-9 min-w-9 shrink-0 items-center justify-center rounded-full px-2.5 text-sm font-semibold tabular-nums ${inCart > 0 ? "toggle-on" : "toggle-off"}`}
                    >
                      {inCart > 0 ? inCart : "+"}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {cart.length > 0 && !checkout && (
        <div className="fixed inset-x-0 bottom-0 z-20 px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <button
            onClick={() => {
              setSubmitError(null);
              setCheckout(true);
            }}
            className="btn-accent mx-auto flex w-full max-w-lg items-center justify-between px-5 py-4 text-base"
          >
            <span className="tabular-nums">
              {count} item · {formatRupiah(estimate)}
            </span>
            <span>Lihat pesanan</span>
          </button>
        </div>
      )}

      {picker && (
        <ProductPicker
          key={`${picker.item.id}:${picker.editUid ?? "new"}`}
          product={picker.item}
          initial={picker.initial}
          editing={picker.editUid !== null}
          onSubmit={(sel) => {
            putLine(picker.item, sel, picker.editUid);
            setPicker(null);
          }}
          onRemove={() => {
            if (picker.editUid) changeQty(picker.editUid, -999);
            setPicker(null);
          }}
          onClose={() => setPicker(null)}
        />
      )}

      {checkout && (
        <div className="sheet-scrim z-50" onClick={() => !busy && setCheckout(false)}>
          <div role="dialog" aria-modal="true" aria-label="Pesanan kamu" className="sheet-panel sm:max-w-lg" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 pb-2 pt-4">
              <h2 className="text-[21px] font-semibold tracking-[-0.02em]">Pesanan kamu</h2>
              <button onClick={() => setCheckout(false)} aria-label="Kembali ke menu" className="icon-btn ink-soft -mr-2 h-10 w-10 rounded-full">
                <IconClose className="h-5 w-5" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-5">
              {cart.length === 0 ? (
                <p className="ink-soft py-8 text-center text-sm">Pesananmu kosong. Kembali ke menu untuk memilih.</p>
              ) : (
                <ul className="hairline-t">
                  {cart.map((l) => (
                    <li key={l.uid} className="hairline-b flex items-start gap-3 py-3">
                      <button onClick={() => setPicker({ item: l.item, editUid: l.uid, initial: lineSelection(l) })} className="min-w-0 flex-1 text-left">
                        <p className="text-[15px] font-semibold leading-snug">{displayName(l.item, l.variant)}</p>
                        {l.modifiers.length > 0 && <p className="ink-soft text-[13px]">{l.modifiers.map((m) => m.name).join(", ")}</p>}
                        {l.notes && (
                          <p className="flex items-center gap-1 text-[13px]" style={{ color: "var(--warn)" }}>
                            <IconNote className="h-3.5 w-3.5 shrink-0" /> {l.notes}
                          </p>
                        )}
                        <p className="ink-faint text-xs tabular-nums">{formatRupiah(linePrice(l))} · ubah</p>
                      </button>
                      <div className="flex shrink-0 flex-col items-end gap-1.5">
                        <p className="text-[15px] font-semibold tabular-nums">{formatRupiah(linePrice(l) * l.qty)}</p>
                        <div className="surface-inset flex items-center rounded-xl p-0.5">
                          <button onClick={() => changeQty(l.uid, -1)} aria-label={`Kurangi ${l.item.name}`} className="h-9 w-9 rounded-[10px] text-lg">
                            −
                          </button>
                          <span className="w-7 text-center text-sm font-semibold tabular-nums">{l.qty}</span>
                          <button onClick={() => changeQty(l.uid, 1)} aria-label={`Tambah ${l.item.name}`} className="h-9 w-9 rounded-[10px] text-lg">
                            +
                          </button>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}

              <div className="segmented mt-4 grid w-full grid-cols-2 gap-[3px]" role="group" aria-label="Makan di mana">
                {(
                  [
                    ["dine_in", "Makan di sini"],
                    ["takeaway", "Bawa pulang"],
                  ] as [OrderType, string][]
                ).map(([kind, label]) => (
                  <button key={kind} onClick={() => setOrderType(kind)} aria-pressed={orderType === kind} className="segmented-item min-h-[2.75rem]">
                    {label}
                  </button>
                ))}
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {orderType === "dine_in" && (
                  <input value={table} onChange={(e) => setTable(e.target.value.slice(0, 20))} className="field" placeholder="Nomor meja" aria-label="Nomor meja" />
                )}
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value.slice(0, 60))}
                  className={`field ${orderType === "dine_in" ? "" : "col-span-2"}`}
                  placeholder="Nama (opsional)"
                  aria-label="Nama"
                />
              </div>
              <input value={note} onChange={(e) => setNote(e.target.value.slice(0, 200))} className="field mt-2" placeholder="Pesan untuk dapur (opsional)" aria-label="Pesan untuk dapur" />
            </div>

            <div className="hairline-t px-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-3">
              {quote && (
                <dl className="mb-1 space-y-0.5 text-[13px]">
                  {(Number(quote.service_charge) > 0 || Number(quote.tax_total) > 0 || Number(quote.rounding) !== 0) && (
                    <div className="flex justify-between">
                      <dt className="ink-soft">Subtotal</dt>
                      <dd className="tabular-nums">{formatRupiah(quote.subtotal)}</dd>
                    </div>
                  )}
                  {Number(quote.service_charge) > 0 && (
                    <div className="flex justify-between">
                      <dt className="ink-soft">Service</dt>
                      <dd className="tabular-nums">{formatRupiah(quote.service_charge)}</dd>
                    </div>
                  )}
                  {Number(quote.tax_total) > 0 && (
                    <div className="flex justify-between">
                      <dt className="ink-soft">{quote.tax_inclusive ? "Pajak (termasuk)" : "Pajak"}</dt>
                      <dd className="tabular-nums">{formatRupiah(quote.tax_total)}</dd>
                    </div>
                  )}
                  {Number(quote.rounding) !== 0 && (
                    <div className="flex justify-between">
                      <dt className="ink-soft">Pembulatan</dt>
                      <dd className="tabular-nums">{formatRupiah(quote.rounding)}</dd>
                    </div>
                  )}
                </dl>
              )}
              <div className="flex items-baseline justify-between">
                <span className="ink-soft text-sm">Total</span>
                <span className={`text-[26px] font-semibold tabular-nums tracking-[-0.02em] ${quote ? "" : "opacity-50"}`}>{formatRupiah(quote?.total ?? estimate)}</span>
              </div>
              <p className="ink-soft mt-1 text-[13px] leading-snug">Belum dibayar. Setelah kirim, tunjukkan kode pesanan ke kasir dan bayar di sana. Pesanan mulai dibuat setelah dibayar.</p>
              {(quoteError || submitError) && (
                <p role="alert" className="notice notice-bad mt-2">
                  {submitError ?? quoteError}
                </p>
              )}
              <button onClick={send} disabled={busy || cart.length === 0 || !quote} className="btn-accent mt-3 w-full py-3.5 text-base">
                {busy ? "Mengirim…" : !quote && !quoteError ? "Menghitung total…" : "Kirim pesanan"}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

const STEPS = [
  { id: "pay", label: "Bayar di kasir" },
  { id: "new", label: "Masuk dapur" },
  { id: "preparing", label: "Disiapkan" },
  { id: "ready", label: "Siap diambil" },
] as const;

function OrderStatus({
  token,
  placed,
  businessName,
  onAgain,
}: {
  token: string;
  placed: { id: string; key: string | null };
  businessName: string;
  onAgain: () => void;
}) {
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);

  const load = useCallback(async () => {
    try {
      const t = await api<Ticket>(`/menu/${token}/orders/${placed.id}${placed.key ? `?key=${encodeURIComponent(placed.key)}` : ""}`);
      setTicket(t);
      setError(null);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 404) setGone(true);
      else setError("Status belum bisa diperbarui — periksa sinyal. Kode pesananmu tetap berlaku.");
    }
  }, [token, placed]);

  const finished = ticket !== null && (ticket.status === "voided" || ticket.status === "refunded" || ticket.kitchen_state === "done");
  useEffect(() => {
    void load();
    if (finished) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 6000);
    return () => clearInterval(id);
  }, [load, finished]);

  if (gone) {
    return (
      <main className="mx-auto flex min-h-[100dvh] max-w-md flex-col items-center justify-center px-6 text-center">
        <p className="text-lg font-semibold">Pesanan tidak ditemukan</p>
        <p className="ink-soft mt-1 text-sm">Tanyakan ke kasir, atau buat pesanan baru.</p>
        <button onClick={onAgain} className="btn-accent mt-5 px-6 py-3">
          Kembali ke menu
        </button>
      </main>
    );
  }

  const waiting = ticket?.status === "open";
  const cancelled = ticket?.status === "voided" || ticket?.status === "refunded";
  const stepIndex = !ticket ? -1 : waiting ? 0 : ticket.kitchen_state === "new" ? 1 : ticket.kitchen_state === "preparing" ? 2 : 3;
  const handedOver = ticket?.kitchen_state === "done";

  const headline = !ticket
    ? "Memuat pesanan…"
    : cancelled
      ? ticket.status === "refunded"
        ? "Pesanan dikembalikan"
        : "Pesanan dibatalkan"
      : waiting
        ? "Tunjukkan kode ini ke kasir"
        : handedOver
          ? "Pesanan sudah diserahkan"
          : ticket.kitchen_state === "ready"
            ? ticket.order_type === "takeaway"
              ? "Siap diambil di kasir"
              : "Pesananmu siap"
            : ticket.kitchen_state === "preparing"
              ? "Sedang disiapkan"
              : "Sudah dibayar · masuk dapur";

  const sub = !ticket
    ? ""
    : cancelled
      ? "Kasir tidak memproses pesanan ini. Silakan tanya di kasir."
      : waiting
        ? "Bayar di kasir dulu ya. Pesanan mulai dibuat setelah dibayar."
        : handedOver
          ? "Terima kasih, selamat menikmati!"
          : ticket.kitchen_state === "ready"
            ? ticket.order_type === "takeaway"
              ? "Sebutkan kode pesananmu saat mengambil."
              : "Pesananmu segera diantar ke meja, atau ambil di kasir dengan kode ini."
            : "Halaman ini diperbarui otomatis.";

  return (
    <main className="mx-auto min-h-[100dvh] max-w-md px-4 pb-10 pt-6">
      <p className="ink-soft text-[13px] font-medium">{businessName}</p>
      <section className="glass-card mt-3 px-6 pb-6 pt-7 text-center" aria-live="polite">
        <p className="ink-soft text-sm">Kode pesanan</p>
        <p className="mt-1 text-[56px] font-semibold leading-none tabular-nums tracking-[-0.03em]">{ticket?.code ?? "…"}</p>
        <h1 className="mt-4 text-[20px] font-semibold tracking-[-0.015em]">{headline}</h1>
        <p className="ink-soft mx-auto mt-1 max-w-[18rem] text-[15px]">{sub}</p>
        {ticket?.revised && !cancelled && <p className="notice notice-warn mt-3 text-sm">Kasir memperbarui pesananmu. Periksa isi dan totalnya di bawah.</p>}
        {error && <p className="notice notice-warn mt-3 text-sm">{error}</p>}

        {ticket && !cancelled && (
          <ol className="mt-6 grid grid-cols-4 gap-1.5 text-left" aria-label="Langkah pesanan">
            {STEPS.map((s, i) => {
              const done = handedOver || i < stepIndex;
              const current = !handedOver && i === stepIndex;
              return (
                <li key={s.id} aria-current={current ? "step" : undefined}>
                  <span
                    className="block h-1.5 rounded-full"
                    style={{ background: done ? "var(--good)" : current ? "var(--ink)" : "var(--fill)" }}
                  />
                  <span className={`mt-1.5 flex items-center gap-1 text-[12px] leading-tight ${current ? "font-semibold" : done ? "ink-soft" : "ink-faint"}`}>
                    {done && <IconCheck className="h-3 w-3 shrink-0" />}
                    {s.label}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      {ticket && (
        <section className="glass-card mt-3 overflow-hidden p-0" aria-label="Isi pesanan">
          <ul>
            {ticket.lines.map((l, i) => (
              <li key={i} className={`flex items-start justify-between gap-3 px-5 py-3 ${i > 0 ? "hairline-t" : ""}`}>
                <div className="min-w-0">
                  <p className="text-[15px] font-semibold">
                    {Number(l.quantity)}× {l.name}
                  </p>
                  {l.modifiers.length > 0 && <p className="ink-soft text-[13px]">{l.modifiers.join(", ")}</p>}
                  {l.notes && <p className="ink-soft text-[13px]">Catatan: {l.notes}</p>}
                </div>
                <span className="shrink-0 text-sm tabular-nums">{formatRupiah(l.line_total)}</span>
              </li>
            ))}
          </ul>
          <div className="hairline-t flex items-baseline justify-between px-5 py-3">
            <span className="ink-soft text-sm">{ticket.is_estimate ? "Total dibayar di kasir" : "Total dibayar"}</span>
            <span className="text-[21px] font-semibold tabular-nums">{formatRupiah(ticket.total)}</span>
          </div>
          {(ticket.table_label || ticket.guest_name) && (
            <p className="ink-soft hairline-t px-5 py-2.5 text-[13px]">
              {ticket.order_type === "takeaway" ? "Bawa pulang" : ticket.table_label}
              {ticket.guest_name ? ` · ${ticket.guest_name}` : ""}
            </p>
          )}
        </section>
      )}

      {finished && (
        <button onClick={onAgain} className="btn-accent mt-5 w-full py-3.5 text-base">
          Pesan lagi
        </button>
      )}
    </main>
  );
}
