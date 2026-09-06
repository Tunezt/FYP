"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { formatQty } from "@/lib/format";

// Kitchen display (M11-T2). Same pairing link as the till, a different page:
// every paid order is a ticket, oldest first; the cook moves it forward —
// Mulai (preparing), Siap (ready), Selesai (done, off the board). Polled every
// few seconds; a kitchen's wifi is not to be trusted with a socket.

const KITCHEN_TOKEN_KEY = "wp_kitchen_token";

type StaffLite = { id: string; name: string; role: string };
type PosBusiness = { business_name: string; staff: StaffLite[] };
type State = "new" | "preparing" | "ready" | "done";
type Ticket = {
  order_id: string;
  code: string;
  source: string;
  order_type: string;
  table_label: string | null;
  guest_name: string | null;
  note: string | null;
  sold_at: string;
  state: State;
  state_since: string | null;
  lines: { name: string; quantity: string; modifiers: string[]; notes: string | null }[];
};

const STATE_LABEL: Record<State, string> = { new: "Baru", preparing: "Disiapkan", ready: "Siap", done: "Selesai" };
const minutesSince = (iso: string) => Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));

export default function KitchenPage() {
  const params = useParams<{ businessToken: string }>();
  const pairingToken = params.businessToken;
  const [business, setBusiness] = useState<PosBusiness | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [staff, setStaff] = useState<StaffLite | null>(null);
  const [pin, setPin] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [staffName, setStaffName] = useState("");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(KITCHEN_TOKEN_KEY);
      if (saved) {
        const [, payload] = saved.split(".");
        const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
        if (claims.exp * 1000 > Date.now()) {
          setToken(saved);
          setStaffName(localStorage.getItem(`${KITCHEN_TOKEN_KEY}:name`) ?? "");
        }
      }
    } catch {
      /* no saved session */
    }
    api<PosBusiness>(`/pos/business/${pairingToken}`)
      .then(setBusiness)
      .catch((e: unknown) => setBootError(e instanceof ApiError ? e.detail : "Tidak bisa terhubung ke server."));
  }, [pairingToken]);

  async function login() {
    if (!staff || pin.length < 4) return;
    setLoginError(null);
    try {
      const res = await api<{ token: string; staff_name: string; business_name: string }>("/pos/login", {
        body: { pairing_token: pairingToken, staff_id: staff.id, pin },
      });
      localStorage.setItem(KITCHEN_TOKEN_KEY, res.token);
      localStorage.setItem(`${KITCHEN_TOKEN_KEY}:name`, res.staff_name);
      setToken(res.token);
      setStaffName(res.staff_name);
      setPin("");
    } catch (e: unknown) {
      setLoginError(e instanceof ApiError ? e.detail : "PIN salah — coba lagi");
      setPin("");
    }
  }

  function lock() {
    localStorage.removeItem(KITCHEN_TOKEN_KEY);
    localStorage.removeItem(`${KITCHEN_TOKEN_KEY}:name`);
    setToken(null);
    setStaff(null);
  }

  if (bootError) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6 text-center">
        <div className="glass-card px-6 py-8">
          <p className="text-lg font-bold">Layar dapur tidak bisa dibuka</p>
          <p className="ink-soft mt-2 text-sm">{bootError}</p>
        </div>
      </main>
    );
  }

  if (token) {
    return <Board token={token} businessName={business?.business_name ?? ""} staffName={staffName} onLock={lock} onExpired={lock} />;
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6">
      <p className="ink-faint text-xs font-medium uppercase tracking-widest">{business?.business_name ?? "…"}</p>
      <h1 className="mt-1 text-2xl font-bold">Layar dapur</h1>
      <p className="ink-soft mt-1 text-sm">Siapa yang jaga dapur? Pilih nama, masukkan PIN kasir.</p>
      <div className="mt-5 grid grid-cols-2 gap-2">
        {(business?.staff ?? []).map((s) => (
          <button
            key={s.id}
            onClick={() => {
              setStaff(s);
              setPin("");
              setLoginError(null);
            }}
            className={`rounded-2xl px-4 py-3 text-left text-sm font-semibold ${staff?.id === s.id ? "bg-accent-gradient text-white shadow-pop" : "glass-card"}`}
          >
            {s.name}
          </button>
        ))}
      </div>
      {staff && (
        <div className="mt-5 flex gap-2">
          <input
            autoFocus
            type="password"
            inputMode="numeric"
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
            onKeyDown={(e) => e.key === "Enter" && login()}
            className="glass-card flex-1 rounded-2xl px-4 py-3 text-center text-2xl font-bold tracking-[0.5em]"
            placeholder="PIN"
          />
          <button onClick={login} disabled={pin.length < 4} className="btn-accent px-6 py-3 text-base disabled:opacity-50">
            Masuk
          </button>
        </div>
      )}
      {loginError && <p className="mt-3 text-sm text-red-600">{loginError}</p>}
    </main>
  );
}

function Board({
  token,
  businessName,
  staffName,
  onLock,
  onExpired,
}: {
  token: string;
  businessName: string;
  staffName: string;
  onLock: () => void;
  onExpired: () => void;
}) {
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const load = useCallback(() => {
    api<Ticket[]>("/pos/kitchen", { token })
      .then(setTickets)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) onExpired();
      });
  }, [token, onExpired]);

  useEffect(() => {
    load();
    const poll = setInterval(load, 5000);
    const clock = setInterval(() => setTick((n) => n + 1), 30000);
    return () => {
      clearInterval(poll);
      clearInterval(clock);
    };
  }, [load]);

  async function move(t: Ticket, state: State) {
    if (busy) return;
    setBusy(t.order_id);
    setError(null);
    try {
      const updated = await api<Ticket>(`/pos/kitchen/${t.order_id}/state`, { token, body: { state } });
      setTickets((prev) => (prev ?? []).flatMap((x) => (x.order_id !== t.order_id ? [x] : state === "done" ? [] : [updated])));
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : "Gagal mengubah status — coba lagi.");
      load();
    } finally {
      setBusy(null);
    }
  }

  const counts = { new: 0, preparing: 0, ready: 0 } as Record<"new" | "preparing" | "ready", number>;
  for (const t of tickets ?? []) if (t.state !== "done") counts[t.state] += 1;

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-4 pb-8">
      <header className="hairline-b sticky top-0 z-10 -mx-4 mb-5 flex items-center justify-between bg-[color:var(--bg-base)]/80 px-4 py-3 backdrop-blur-xl">
        <div>
          <p className="ink-faint text-xs font-medium uppercase tracking-widest">{businessName}</p>
          <p className="text-lg font-bold">Dapur · {staffName}</p>
        </div>
        <div className="flex items-center gap-2 text-sm font-semibold">
          <span className="glass-card px-3 py-1.5">Baru {counts.new}</span>
          <span className="glass-card px-3 py-1.5">Disiapkan {counts.preparing}</span>
          <span className="glass-card px-3 py-1.5">Siap {counts.ready}</span>
          <button onClick={onLock} className="btn-quiet px-4 py-2">
            🔒 Kunci
          </button>
        </div>
      </header>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {tickets === null ? (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="glass-card h-48 animate-pulse" />
          ))}
        </div>
      ) : tickets.length === 0 ? (
        <div className="glass-card px-6 py-16 text-center">
          <p className="text-xl font-bold">Tidak ada pesanan</p>
          <p className="ink-soft mt-1 text-sm">Pesanan yang sudah dibayar muncul di sini otomatis.</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
          {tickets.map((t) => {
            const waited = minutesSince(t.sold_at);
            const late = waited >= 15;
            const busyHere = busy === t.order_id;
            return (
              <article
                key={t.order_id}
                className={`glass-card flex flex-col px-4 py-3 ${t.state === "ready" ? "ring-2 ring-emerald-500/60" : ""}`}
                style={late && t.state !== "ready" ? { boxShadow: "0 0 0 2px var(--warn)" } : undefined}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-2xl font-black tracking-tight">{t.code}</p>
                    <p className="ink-soft text-xs">
                      {t.order_type === "dine_in" ? t.table_label || "Makan di sini" : t.order_type === "takeaway" ? "Bawa pulang" : t.order_type}
                      {t.guest_name ? ` · ${t.guest_name}` : ""}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-bold tabular-nums" style={late ? { color: "var(--warn)" } : undefined}>
                      {waited} mnt
                    </p>
                    <p className="ink-faint text-[11px] font-medium uppercase tracking-wide">{STATE_LABEL[t.state]}</p>
                  </div>
                </div>
                <ul className="mt-3 flex-1 space-y-1.5">
                  {t.lines.map((l, i) => (
                    <li key={i} className="text-base leading-tight">
                      <span className="font-black">{formatQty(l.quantity)}×</span> <span className="font-semibold">{l.name}</span>
                      {l.modifiers.length > 0 && <span className="ink-soft block text-sm">{l.modifiers.join(", ")}</span>}
                      {l.notes && (
                        <span className="block text-sm font-semibold" style={{ color: "var(--warn)" }}>
                          {l.notes}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                {t.note && <p className="mt-2 text-sm font-semibold" style={{ color: "var(--warn)" }}>Catatan: {t.note}</p>}
                <div className="mt-3 flex gap-2">
                  {t.state === "new" && (
                    <button onClick={() => move(t, "preparing")} disabled={busyHere} className="btn-quiet flex-1 py-2.5 text-sm font-bold">
                      Mulai
                    </button>
                  )}
                  {t.state === "preparing" && (
                    <button onClick={() => move(t, "ready")} disabled={busyHere} className="btn-quiet flex-1 py-2.5 text-sm font-bold">
                      Siap
                    </button>
                  )}
                  <button onClick={() => move(t, "done")} disabled={busyHere} className="btn-accent flex-1 py-2.5 text-sm font-bold">
                    Selesai ✓
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </main>
  );
}
