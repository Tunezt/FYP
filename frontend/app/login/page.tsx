"use client";

import { BrandMark } from "@/components/ui";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError, OWNER_TOKEN_KEY } from "@/lib/api";
import { demoAllowed, enableDemo } from "@/lib/demo";
import { IconChat } from "@/components/icons";

type Step = "phone" | "code";

export default function LoginPage() {
  const router = useRouter();
  const [showDemo, setShowDemo] = useState(false);
  const [step, setStep] = useState<Step>("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
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

  async function requestOtp() {
    setBusy(true);
    setError(null);
    try {
      await api<{ sent: boolean }>("/auth/request-otp", { body: { phone } });
      setStep("code");
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

  async function verify() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{
        registered: boolean;
        token: string | null;
        registration_token: string | null;
      }>("/auth/verify-otp", { body: { phone, code } });
      if (res.registered && res.token) {
        localStorage.setItem(OWNER_TOKEN_KEY, res.token);
        router.replace("/overview");
      } else if (res.registration_token) {
        sessionStorage.setItem("wp_registration_token", res.registration_token);
        router.replace("/register");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Kode salah atau kedaluwarsa.");
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
          {step === "phone" ? (
            <>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">
                  Nomor WhatsApp usaha
                </span>
                <input
                  className="field tabular-nums"
                  inputMode="tel"
                  placeholder="0812 3456 7890"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && phone && requestOtp()}
                  autoFocus
                />
              </label>
              <p className="ink-faint mt-2 flex items-start gap-1.5 text-xs leading-relaxed">
                <IconChat className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                Kode masuk dikirim ke WhatsApp kamu — tanpa kata sandi.
              </p>
              <button
                onClick={requestOtp}
                disabled={busy || phone.replace(/\D/g, "").length < 8}
                className="btn-accent mt-5 w-full py-3.5"
              >
                {busy ? "Mengirim…" : "Kirim kode"}
              </button>
            </>
          ) : (
            <>
              <p className="ink-soft text-sm">
                Kode 6 angka terkirim ke <span className="font-semibold">{phone}</span>
              </p>
              <input
                className="field mt-3 py-3 text-center text-2xl font-semibold tabular-nums tracking-[0.45em]"
                inputMode="numeric"
                maxLength={6}
                placeholder="······"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                onKeyDown={(e) => e.key === "Enter" && code.length === 6 && verify()}
                autoFocus
              />
              {error === null && (
                <p className="ink-faint mt-2 text-xs">Berlaku 10 menit.</p>
              )}
              <button
                onClick={verify}
                disabled={busy || code.length !== 6}
                className="btn-accent mt-4 w-full py-3.5"
              >
                {busy ? "Memeriksa…" : "Masuk"}
              </button>
              <button
                onClick={() => {
                  setStep("phone");
                  setCode("");
                  setError(null);
                }}
                className="ink-soft mt-3 w-full text-center text-sm"
              >
                Ganti nomor
              </button>
            </>
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
          Belum punya akun? Masukkan nomormu — pendaftaran otomatis dimulai. Atau lihat{" "}
          <Link href="/register" className="font-semibold text-[color:var(--accent)] hover:underline">
            cara kerjanya
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
