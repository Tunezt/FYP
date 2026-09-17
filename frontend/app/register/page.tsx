"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError, OWNER_TOKEN_KEY } from "@/lib/api";
import { BrandMark, CopyField } from "@/components/ui";
import { IconCamera, IconChat, IconCheck, IconNote, IconPlus } from "@/components/icons";
import { initials } from "@/lib/format";

const BUSINESS_TYPES = [
  { value: "cafe", label: "Kafe" },
  { value: "warung", label: "Warung" },
  { value: "toko", label: "Toko" },
  { value: "lainnya", label: "Lainnya" },
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
      className="notice notice-bad"
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
          <span className="flex justify-center">
            <BrandMark size="md" />
          </span>
          <h1 className="mt-5 text-xl font-semibold tracking-[-0.015em]">Daftar ke Poernama</h1>
          <ol className="mx-auto mt-5 max-w-xs space-y-3 text-left text-sm">
            {["Masukkan nomor WhatsApp usahamu", "Terima kode masuk di WhatsApp", "Isi profil usaha & PIN kasir — selesai, ±2 menit"].map((text, i) => (
              <li key={text} className="flex items-start gap-3">
                <span className="surface-inset ink-soft flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
                  {i + 1}
                </span>
                <span className="ink-soft pt-0.5">{text}</span>
              </li>
            ))}
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
                ? "w-8 bg-[color:var(--accent-fill)]"
                : s < step
                  ? "w-2 bg-[color:var(--accent-fill)]"
                  : "w-2 bg-[color:var(--hairline)]"
            }`}
          />
        ))}
      </div>

      {step === 1 && (
        <div className="glass-card animate-fade-up px-6 py-7 sm:px-8">
          <h1 className="text-xl font-semibold tracking-[-0.015em]">Cerita dikit soal usahamu</h1>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-[color:var(--good)]">
            <IconCheck className="h-4 w-4" /> Nomor WhatsApp sudah terverifikasi
          </p>

          <div className="mt-5 space-y-4">
            <label className="block">
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">Nama usaha</span>
              <input
                className="field"
                value={businessName}
                onChange={(e) => setBusinessName(e.target.value)}
                placeholder="cth. Poernama"
                autoFocus
              />
            </label>

            <div>
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">Jenis usaha</span>
              <div className="flex flex-wrap gap-2">
                {BUSINESS_TYPES.map((t) => (
                  <button
                    key={t.value}
                    type="button"
                    onClick={() => setBusinessType(t.value)}
                    aria-pressed={businessType === t.value}
                    className="choice-chip px-4 py-2 text-sm"
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">Nama kamu</span>
                <input
                  className="field"
                  value={ownerName}
                  onChange={(e) => setOwnerName(e.target.value)}
                  placeholder="cth. Ibu Ratna"
                />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-[13px] font-medium">
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
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">
                Bahasa asisten WhatsApp
              </span>
              <div className="flex flex-wrap gap-2">
                {LANGUAGES.map((l) => (
                  <button
                    key={l.value}
                    type="button"
                    onClick={() => setLanguage(l.value)}
                    aria-pressed={language === l.value}
                    className="choice-chip px-4 py-2 text-sm"
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
              {busy ? "Menyiapkan…" : "Buat usaha"}
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="glass-card animate-fade-up px-6 py-7 sm:px-8">
          <h1 className="text-xl font-semibold tracking-[-0.015em]">Siapa yang jaga kasir?</h1>
          <p className="ink-soft mt-1 text-sm">
            Staf masuk ke layar kasir pakai nama + PIN — tanpa akun, tanpa email.
          </p>

          <div className="mt-5 space-y-4">
            {staffList.length > 0 && (
              <ul className="flex flex-wrap gap-2">
                {staffList.map((name) => (
                  <li
                    key={name}
                    className="flex items-center gap-2 rounded-full py-1 pl-1 pr-3 text-sm font-medium"
                    style={{ boxShadow: "0 0 0 1px var(--hairline-strong)" }}
                  >
                    <span className="surface-inset ink-soft flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-semibold">
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
                className="field w-24 text-center tabular-nums"
                placeholder="PIN"
                aria-label="PIN staf (4 angka)"
                inputMode="numeric"
                maxLength={4}
                value={staffPin}
                onChange={(e) => setStaffPin(e.target.value.replace(/\D/g, ""))}
              />
              <button
                onClick={addStaff}
                disabled={busy || !staffName.trim() || staffPin.length !== 4}
                className="btn-quiet px-3.5"
                aria-label="Tambah staf"
              >
                <IconPlus className="h-5 w-5" />
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
                Lanjut
              </button>
            </div>
            <p className="ink-faint text-center text-xs">Bisa ditambah kapan saja di Pengaturan.</p>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="glass-card animate-fade-up px-6 py-7 text-center sm:px-8">
          <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl text-[color:var(--accent)]" style={{ background: "var(--accent-soft)" }}>
            <IconChat className="h-7 w-7" />
          </span>
          <h1 className="mt-4 text-xl font-semibold tracking-[-0.015em]">Terakhir: isi stok lewat WhatsApp</h1>
          <p className="ink-soft mx-auto mt-2 max-w-sm text-sm leading-relaxed">
            Mulai sekarang semuanya lewat chat. Kirim ke nomor WhatsApp usaha:
          </p>
          <ul className="surface-inset mx-auto mt-5 max-w-sm divide-y divide-[color:var(--hairline)] rounded-2xl text-left text-sm">
            <li className="flex items-start gap-3 px-4 py-3">
              <IconCamera className="ink-soft mt-0.5 h-[18px] w-[18px] shrink-0" />
              <span><span className="font-medium">Foto buku stok</span><span className="ink-soft"> — dibaca otomatis, kamu tinggal konfirmasi</span></span>
            </li>
            <li className="flex items-start gap-3 px-4 py-3">
              <IconNote className="ink-soft mt-0.5 h-[18px] w-[18px] shrink-0" />
              <span><span className="font-medium">File Excel</span><span className="ink-soft"> — template bisa diunduh di Pengaturan</span></span>
            </li>
            <li className="flex items-start gap-3 px-4 py-3">
              <IconChat className="ink-soft mt-0.5 h-[18px] w-[18px] shrink-0" />
              <span><span className="font-medium">Atau tanya apa saja</span><span className="ink-soft"> — &ldquo;stok arabica berapa?&rdquo;</span></span>
            </li>
          </ul>
          <button onClick={() => router.replace("/overview")} className="btn-accent mt-6 w-full py-3.5">
            Buka dashboard
          </button>
        </div>
      )}
    </main>
  );
}
