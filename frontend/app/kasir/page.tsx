"use client";

import { useEffect, useState } from "react";
import { api, ApiError, POS_PAIRING_KEY } from "@/lib/api";
import { FormHint, missingText } from "@/components/FormHint";
import { Till } from "@/components/pos/Till";
import { Wordmark } from "@/components/Wordmark";

/** The till's own address (kasir-1). A tablet that has been set up goes
 *  straight to "Siapa yang jaga?". One that has not asks, once, for the
 *  owner's phone and PIN: that is what tells this browser which café it is the
 *  till for. No link to make, copy or send (kasir-2). */
export default function KasirPage() {
  // undefined = not read yet (nothing is drawn, so no flash of the wrong screen)
  const [pairing, setPairing] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    try {
      setPairing(localStorage.getItem(POS_PAIRING_KEY));
    } catch {
      setPairing(null);
    }
  }, []);

  if (pairing === undefined) return null;
  if (pairing) return <Till pairingToken={pairing} />;
  return <SetUpTill onPaired={setPairing} />;
}

function SetUpTill({ onPaired }: { onPaired: (token: string) => void }) {
  const [phone, setPhone] = useState("");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = phone.replace(/\D/g, "").length >= 8 && pin.length >= 4;

  async function pair() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      // The owner proves who they are, and the café's till link is fetched
      // with that proof. The owner's session is used for this one request and
      // never stored here: a till is not a way into the dashboard.
      const login = await api<{ registered: boolean; token: string | null }>("/auth/login-pin", { body: { phone, pin } });
      if (!login.token) {
        setError("Nomor ini belum terdaftar sebagai pemilik usaha.");
        return;
      }
      const res = await api<{ pairing_token: string }>("/auth/pos-pairing", { method: "POST", token: login.token });
      try {
        localStorage.setItem(POS_PAIRING_KEY, res.pairing_token);
      } catch {}
      onPaired(res.pairing_token);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Tidak bisa terhubung ke server. Periksa internet lalu coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm animate-scale-in">
        <Wordmark className="mx-auto w-[168px]" />
        <h1 className="mt-9 text-center text-[1.625rem] font-semibold tracking-[-0.022em]">Siapkan kasir</h1>
        <p className="ink-soft mx-auto mt-1.5 max-w-[19rem] text-center text-[15px] leading-relaxed">
          Sekali saja di perangkat ini. Setelah itu kasir langsung terbuka di alamat ini.
        </p>

        <div className="glass-card mt-6 select-text px-6 pb-6 pt-5">
          <label className="block">
            <span className="ink-soft mb-1.5 block text-[13px] font-medium">Nomor HP pemilik</span>
            <input
              className="field tabular-nums"
              inputMode="tel"
              autoComplete="off"
              placeholder="0812 3456 7890"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              autoFocus
            />
          </label>
          <label className="mt-4 block">
            <span className="ink-soft mb-1.5 block text-[13px] font-medium">PIN pemilik</span>
            <input
              className="field py-3 text-center text-2xl font-semibold tabular-nums tracking-[0.45em]"
              inputMode="numeric"
              type="password"
              autoComplete="off"
              maxLength={12}
              placeholder="••••"
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
              onKeyDown={(e) => e.key === "Enter" && void pair()}
            />
          </label>
          <button onClick={() => void pair()} disabled={busy || !ready} className="btn-accent mt-5 w-full py-3.5">
            {busy ? "Memeriksa…" : "Jadikan perangkat ini kasir"}
          </button>
          {!busy && (
            <FormHint missing={missingText("Isi", [phone.replace(/\D/g, "").length < 8 && "nomor HP", pin.length < 4 && "PIN (4 angka)"])} />
          )}
          {error && (
            <p role="alert" className="notice notice-bad mt-4">
              {error}
            </p>
          )}
        </div>

        <p className="ink-faint mx-auto mt-5 max-w-[19rem] text-center text-[13px] leading-relaxed">
          Staf tidak perlu tahu PIN ini. Setelah disiapkan, mereka masuk dengan nama dan PIN masing-masing.
        </p>
      </div>
    </main>
  );
}
