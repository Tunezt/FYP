"use client";

import { BrandMark } from "@/components/ui";
import { FormHint, missingText } from "@/components/FormHint";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, OWNER_TOKEN_KEY } from "@/lib/api";
import { demoAllowed, disableDemo, enableDemo } from "@/lib/demo";

// Owner login is phone + owner PIN only. The WhatsApp code needs a Meta
// Authentication template, which Meta gates behind business verification
// (docs/whatsapp-templates.md); `/auth/request-otp` still exists for when it does.
export default function LoginPage() {
  const router = useRouter();
  const [showDemo, setShowDemo] = useState(false);
  const [phone, setPhone] = useState("");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // ?demo=1 jumps straight in; otherwise just reveal the button.
    setShowDemo(demoAllowed());
    if (demoAllowed() && new URLSearchParams(window.location.search).get("demo") === "1") {
      enableDemo();
      router.replace("/overview");
    }
  }, [router]);

  function startDemo() {
    enableDemo();
    router.replace("/overview");
  }

  const ready = phone.replace(/\D/g, "").length >= 8 && pin.length >= 4;

  async function loginWithPin() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ registered: boolean; token: string | null }>("/auth/login-pin", {
        body: { phone, pin },
      });
      if (res.token) {
        disableDemo();   // till-17: a real login ends the demo, or every save is silently faked
        localStorage.setItem(OWNER_TOKEN_KEY, res.token);
        router.replace("/overview");
      }
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.detail);
      } else {
        setError(
          "Tidak bisa terhubung ke server. Pastikan backend berjalan di http://localhost:8000."
        );
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm animate-scale-in">
        <div className="mb-8 text-center">
          <h1 className="flex justify-center">
            <BrandMark size="lg" />
          </h1>
        </div>

        <div className="glass-card px-6 pb-6 pt-5">
          <label className="block">
            <span className="ink-soft mb-1.5 block text-[13px] font-medium">Nomor HP pemilik</span>
            <input
              className="field tabular-nums"
              inputMode="tel"
              autoComplete="username"
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
              autoComplete="current-password"
              maxLength={12}
              placeholder="••••"
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
              onKeyDown={(e) => e.key === "Enter" && ready && !busy && loginWithPin()}
            />
          </label>
          {error === null && (
            <p className="ink-faint mt-2 text-xs">
              PIN yang sama seperti di layar kasir. Salah beberapa kali, harus tunggu sebentar.
            </p>
          )}
          <button
            onClick={loginWithPin}
            disabled={busy || !ready}
            className="btn-accent mt-5 w-full py-3.5"
          >
            {busy ? "Memeriksa…" : "Masuk"}
          </button>
          {!busy && (
            <FormHint
              missing={missingText("Isi", [phone.replace(/\D/g, "").length < 8 && "nomor HP", pin.length < 4 && "PIN (4 angka)"])}
            />
          )}

          {error && <p className="notice notice-bad mt-4">{error}</p>}

          {showDemo && (
            <div className="hairline-t mt-5 pt-5">
              <button onClick={startDemo} className="btn-quiet w-full py-3 text-sm">
                Lihat mode demo (tanpa database)
              </button>
              <p className="ink-faint mt-2 text-center text-xs leading-relaxed">
                Menampilkan dashboard dengan data contoh — untuk melihat tampilan saat database
                belum terhubung.
              </p>
            </div>
          )}
        </div>

        <p className="ink-faint mx-auto mt-6 max-w-xs text-center text-[13px] leading-relaxed">
          Belum punya akun? Hubungi Poernama untuk didaftarkan.
        </p>
      </div>
    </main>
  );
}
