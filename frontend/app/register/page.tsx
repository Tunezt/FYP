"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError, OWNER_TOKEN_KEY } from "@/lib/api";
import { CopyField } from "@/components/ui";
import { IconChat, IconCheck } from "@/components/icons";
import { initials } from "@/lib/format";

const BUSINESS_TYPES = [
  { value: "cafe", label: "☕ Kafe" },
  { value: "warung", label: "🍜 Warung" },
  { value: "toko", label: "🏪 Toko" },
  { value: "lainnya", label: "✨ Lainnya" },
];

const LANGUAGES = [
  { value: "id", label: "Bahasa Indonesia" },
  { value: "ms", label: "Bahasa Malaysia" },
  { value: "en", label: "English" },
];

type Step = 1 | 2 | 3;

function ErrorNote({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="rounded-2xl px-4 py-3 text-sm font-medium"
      style={{ background: "var(--bad-bg)", color: "var(--bad)" }}
    >
      {children}
    </p>
  );
}

export default function RegisterPage() {
  const router = useRouter();
  const [regToken, setRegToken] = useState<string | null | "missing">(null);
  const [step, setStep] = useState<Step>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 1 — business profile
  const [businessName, setBusinessName] = useState("");
  const [businessType, setBusinessType] = useState("cafe");
  const [ownerName, setOwnerName] = useState("");
  const [ownerPin, setOwnerPin] = useState("");
  const [language, setLanguage] = useState("id");

  // Step 2 — staff + kiosk
  const [staffName, setStaffName] = useState("");
  const [staffPin, setStaffPin] = useState("");
  const [staffList, setStaffList] = useState<string[]>([]);
  const [pairing, setPairing] = useState<string | null>(null);

  useEffect(() => {
    setRegToken(sessionStorage.getItem("wp_registration_token") ?? "missing");
  }, []);

  if (regToken === null) return null;

  if (regToken === "missing" && step === 1) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <div className="glass-card max-w-md animate-scale-in px-8 py-10 text-center">
          <p className="text-3xl">🌱</p>
          <h1 className="mt-4 text-xl font-bold">Daftar Warung Pintar</h1>
          <ol className="ink-soft mx-auto mt-4 max-w-xs space-y-2 text-left text-sm">
            <li>1. Masukkan nomor WhatsApp usahamu</li>
            <li>2. Terima kode masuk di WhatsApp</li>
            <li>3. Isi profil usaha &amp; PIN kasir — selesai, ±2 menit</li>
          </ol>
          <Link href="/login" className="btn-accent mt-6 inline-flex px-6 py-3">
            Mulai dengan nomor WhatsApp
          </Link>
        </div>
      </main>
    );
  }

  async function createBusiness() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ token: string }>("/auth/register", {
        body: {
          registration_token: regToken,
          business_name: businessName.trim(),
          business_type: businessType,
          owner_name: ownerName.trim(),
          owner_pin: ownerPin,
          language_preference: language,
        },
      });
      localStorage.setItem(OWNER_TOKEN_KEY, res.token);
      sessionStorage.removeItem("wp_registration_token");
      setStep(2);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Pendaftaran gagal — coba lagi ya.");
    } finally {
      setBusy(false);
    }
  }

  async function addStaff() {
    if (!staffName.trim() || staffPin.length !== 4) return;
    setBusy(true);
    setError(null);
    try {
      await api("/auth/staff", {
        token: localStorage.getItem(OWNER_TOKEN_KEY),
        body: { name: staffName.trim(), pin: staffPin },
      });
      setStaffList((list) => [...list, staffName.trim()]);
      setStaffName("");
      setStaffPin("");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Gagal menambah staf — coba lagi ya.");
    } finally {
      setBusy(false);
    }
  }

  async function makePairing() {
    setBusy(true);
    try {
      const res = await api<{ pos_path: string }>("/auth/pos-pairing", {
        token: localStorage.getItem(OWNER_TOKEN_KEY),
        body: {},
      });
      setPairing(`${window.location.origin}${res.pos_path}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center px-4 py-10">
      {/* Progress */}
      <div className="mb-8 flex items-center justify-center gap-2">
        {[1, 2, 3].map((s) => (
          <span
            key={s}
            className={`h-2 rounded-full transition-all duration-300 ${
              s === step
                ? "w-8 bg-accent-600"
                : s < step
                  ? "w-2 bg-accent-600"
                  : "w-2 bg-[color:var(--hairline)]"
            }`}
          />
        ))}
      </div>

      {step === 1 && (
        <div className="glass-card animate-fade-up px-6 py-7 sm:px-8">
          <h1 className="text-xl font-bold">Cerita dikit soal usahamu</h1>
          <p className="ink-soft mt-1 text-sm">Nomor WhatsApp sudah terverifikasi ✓</p>

          <div className="mt-5 space-y-4">
            <label className="block">
              <span className="ink-soft mb-1.5 block text-xs font-medium">Nama usaha</span>
              <input
                className="field"
                value={businessName}
                onChange={(e) => setBusinessName(e.target.value)}
                placeholder="cth. Kopi Kenangan Senja"
                autoFocus
              />
            </label>

            <div>
              <span className="ink-soft mb-1.5 block text-xs font-medium">Jenis usaha</span>
              <div className="flex flex-wrap gap-2">
                {BUSINESS_TYPES.map((t) => (
                  <button
                    key={t.value}
                    onClick={() => setBusinessType(t.value)}
                    className={`rounded-2xl px-4 py-2 text-sm font-medium transition-all ${
                      businessType === t.value ? "btn-accent px-4 py-2" : "btn-quiet px-4 py-2"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Nama kamu</span>
                <input
                  className="field"
                  value={ownerName}
                  onChange={(e) => setOwnerName(e.target.value)}
                  placeholder="cth. Ibu Ratna"
                />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">
                  PIN kasir kamu (4 angka)
                </span>
                <input
                  className="field tabular-nums"
                  inputMode="numeric"
                  maxLength={4}
                  value={ownerPin}
                  onChange={(e) => setOwnerPin(e.target.value.replace(/\D/g, ""))}
                  placeholder="••••"
                />
              </label>
            </div>

            <div>
              <span className="ink-soft mb-1.5 block text-xs font-medium">
                Bahasa asisten WhatsApp
              </span>
              <div className="flex flex-wrap gap-2">
                {LANGUAGES.map((l) => (
                  <button
                    key={l.value}
                    onClick={() => setLanguage(l.value)}
                    className={`rounded-2xl px-4 py-2 text-sm font-medium ${
                      language === l.value ? "btn-accent px-4 py-2" : "btn-quiet px-4 py-2"
                    }`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
              <p className="ink-faint mt-1.5 text-xs">Campur-campur juga dimengerti kok.</p>
            </div>

            {error && <ErrorNote>{error}</ErrorNote>}

            <button
              onClick={createBusiness}
              disabled={busy || !businessName.trim() || !ownerName.trim() || ownerPin.length !== 4}
              className="btn-accent w-full py-3.5"
            >
              {busy ? "Menyiapkan…" : "Buat usaha →"}
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="glass-card animate-fade-up px-6 py-7 sm:px-8">
          <h1 className="text-xl font-bold">Siapa yang jaga kasir?</h1>
          <p className="ink-soft mt-1 text-sm">
            Staf masuk ke layar kasir pakai nama + PIN — tanpa akun, tanpa email.
          </p>

          <div className="mt-5 space-y-4">
            {staffList.length > 0 && (
              <ul className="flex flex-wrap gap-2">
                {staffList.map((name) => (
                  <li
                    key={name}
                    className="flex items-center gap-2 rounded-2xl px-3 py-1.5 text-sm font-medium"
                    style={{ border: "1px solid var(--hairline)" }}
                  >
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent-gradient text-[10px] font-bold text-white">
                      {initials(name)}
                    </span>
                    {name} <IconCheck className="h-3.5 w-3.5 text-[color:var(--good)]" />
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2">
              <input
                className="field flex-1"
                placeholder="Nama staf"
                value={staffName}
                onChange={(e) => setStaffName(e.target.value)}
              />
              <input
                className="field w-28 tabular-nums"
                placeholder="PIN"
                inputMode="numeric"
                maxLength={4}
                value={staffPin}
                onChange={(e) => setStaffPin(e.target.value.replace(/\D/g, ""))}
              />
              <button
                onClick={addStaff}
                disabled={busy || !staffName.trim() || staffPin.length !== 4}
                className="btn-quiet px-4"
              >
                +
              </button>
            </div>

            <div className="hairline-b" />

            <div>
              <p className="text-sm font-semibold">Layar kasir</p>
              <p className="ink-soft mb-2 mt-0.5 text-xs">
                Buka tautan ini sekali di tablet/HP kasir — selanjutnya otomatis.
              </p>
              {pairing ? (
                <CopyField value={pairing} />
              ) : (
                <button onClick={makePairing} disabled={busy} className="btn-quiet px-4 py-2.5 text-sm">
                  Buat tautan kasir
                </button>
              )}
            </div>

            {error && <ErrorNote>{error}</ErrorNote>}

            <div className="flex gap-3">
              <button onClick={() => setStep(3)} className="btn-accent flex-1 py-3.5">
                Lanjut →
              </button>
            </div>
            <p className="ink-faint text-center text-xs">Bisa ditambah kapan saja di Pengaturan.</p>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="glass-card animate-fade-up px-6 py-7 text-center sm:px-8">
          <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-accent-gradient text-2xl shadow-pop">
            <IconChat className="h-7 w-7 text-white" />
          </span>
          <h1 className="mt-4 text-xl font-bold">Terakhir: isi stok lewat WhatsApp</h1>
          <p className="ink-soft mx-auto mt-2 max-w-sm text-sm leading-relaxed">
            Mulai sekarang semuanya lewat chat. Kirim ke nomor WhatsApp usaha:
          </p>
          <div className="mx-auto mt-4 max-w-sm space-y-2 text-left">
            <div className="rounded-2xl px-4 py-3 text-sm" style={{ border: "1px solid var(--hairline)" }}>
              📸 <span className="font-medium">Foto buku stok</span>
              <span className="ink-soft"> — dibaca otomatis, kamu tinggal konfirmasi</span>
            </div>
            <div className="rounded-2xl px-4 py-3 text-sm" style={{ border: "1px solid var(--hairline)" }}>
              📄 <span className="font-medium">File Excel</span>
              <span className="ink-soft"> — template bisa diunduh di Pengaturan</span>
            </div>
            <div className="rounded-2xl px-4 py-3 text-sm" style={{ border: "1px solid var(--hairline)" }}>
              💬 <span className="font-medium">Atau tanya apa saja</span>
              <span className="ink-soft"> — &ldquo;stok arabica berapa?&rdquo;</span>
            </div>
          </div>
          <button onClick={() => router.replace("/overview")} className="btn-accent mt-6 w-full py-3.5">
            Buka dashboard 🎉
          </button>
        </div>
      )}
    </main>
  );
}
