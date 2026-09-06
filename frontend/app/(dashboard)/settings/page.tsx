"use client";

import { useState } from "react";
import QRCode from "qrcode";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import type { Business, LoyaltySettings, PricingSettings, StaffMember } from "@/lib/types";
import { CopyField, Plate, Sheet, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconPlus } from "@/components/icons";
import { initials } from "@/lib/format";

type PricingForm = {
  tax_percent: string;
  tax_inclusive: boolean;
  service_percent: string;
  service_before_tax: boolean;
  rounding_unit: string;
  rounding_mode: "nearest" | "up" | "down";
  discount_requires_pin: boolean;
};

const pct = (fraction: string | undefined) => {
  const n = Number(fraction ?? 0) * 100;
  return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : "0";
};

function toForm(p: PricingSettings | null | undefined): PricingForm {
  return {
    tax_percent: pct(p?.tax_rate),
    tax_inclusive: p?.tax_inclusive ?? true,
    service_percent: pct(p?.service_charge_rate),
    service_before_tax: p?.service_before_tax ?? true,
    rounding_unit: String(Math.round(Number(p?.rounding_unit ?? 0))),
    rounding_mode: p?.rounding_mode ?? "nearest",
    discount_requires_pin: p?.discount_requires_pin ?? true,
  };
}

type LoyaltyForm = { is_active: boolean; rupiah_per_point: string; point_value: string; min_redeem_points: string };

function toLoyaltyForm(l: LoyaltySettings | null | undefined): LoyaltyForm {
  return {
    is_active: l?.is_active ?? false,
    rupiah_per_point: String(Math.round(Number(l?.rupiah_per_point ?? 1000))),
    point_value: String(Math.round(Number(l?.point_value ?? 100))),
    min_redeem_points: String(l?.min_redeem_points ?? 0),
  };
}

export default function SettingsPage() {
  const business = useOwnerData<Business>("/api/business");
  const staff = useOwnerData<StaffMember[]>("/auth/staff");
  const pricing = useOwnerData<PricingSettings>("/api/pricing-settings");
  const loyalty = useOwnerData<LoyaltySettings>("/api/loyalty-settings");
  const mutate = useOwnerMutation();

  const [profileDraft, setProfileDraft] = useState<{ name: string; business_type: string } | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [staffSheet, setStaffSheet] = useState(false);
  const [staffDraft, setStaffDraft] = useState({ name: "", pin: "" });
  const [staffBusy, setStaffBusy] = useState(false);
  const [staffError, setStaffError] = useState<string | null>(null);
  const [pairing, setPairing] = useState<string | null>(null);
  const [pairingBusy, setPairingBusy] = useState(false);
  // The QR e-menu (M11-T1): one link for every table, rendered as a QR to print.
  const [menuLink, setMenuLink] = useState<string | null>(null);
  const [menuQr, setMenuQr] = useState<string | null>(null);
  const [menuBusy, setMenuBusy] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importResult, setImportResult] = useState<{ ok: boolean; message: string } | null>(null);
  // Pricing (M7-T4): the form edits percentages and whole rupiah; the API speaks fractions.
  const [pricingDraft, setPricingDraft] = useState<PricingForm | null>(null);
  const [pricingBusy, setPricingBusy] = useState(false);
  const [pricingError, setPricingError] = useState<string | null>(null);
  // Points programme (M8-T2).
  const [loyaltyDraft, setLoyaltyDraft] = useState<LoyaltyForm | null>(null);
  const [loyaltyBusy, setLoyaltyBusy] = useState(false);
  const [loyaltyError, setLoyaltyError] = useState<string | null>(null);

  async function importCatalog(file: File) {
    setImportBusy(true);
    setImportResult(null);
    try {
      const token = localStorage.getItem("wp_owner_token");
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/catalog-import`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/octet-stream" },
        body: file,
      });
      const data = await res.json();
      if (!res.ok) {
        setImportResult({ ok: false, message: typeof data.detail === "string" ? data.detail : "Impor gagal." });
        return;
      }
      setImportResult({
        ok: true,
        message:
          `Tersimpan: ${data.items_created} barang baru, ${data.items_updated} diperbarui, ` +
          `${data.variants} varian, ${data.modifier_groups} kelompok pilihan (${data.modifiers} pilihan), ` +
          `${data.uoms} satuan, ${data.conversions} konversi, ${data.recipe_lines} baris resep.`,
      });
    } catch {
      setImportResult({ ok: false, message: "Tidak bisa terhubung ke server." });
    } finally {
      setImportBusy(false);
    }
  }

  const b = business.data;
  const draft = profileDraft ?? { name: b?.name ?? "", business_type: b?.business_type ?? "cafe" };
  const pricingForm: PricingForm = pricingDraft ?? toForm(pricing.data);
  const loyaltyForm: LoyaltyForm = loyaltyDraft ?? toLoyaltyForm(loyalty.data);

  async function saveLoyalty() {
    setLoyaltyBusy(true);
    setLoyaltyError(null);
    try {
      const per = Number(loyaltyForm.rupiah_per_point);
      const value = Number(loyaltyForm.point_value);
      const min = Number(loyaltyForm.min_redeem_points);
      if (!(per > 0) || !(value >= 0) || !(min >= 0)) {
        setLoyaltyError("Rupiah per poin harus lebih dari nol; nilai poin dan minimal tukar tidak boleh negatif.");
        return;
      }
      await mutate(
        "/api/loyalty-settings",
        { is_active: loyaltyForm.is_active, rupiah_per_point: per.toFixed(2), point_value: value.toFixed(2), min_redeem_points: Math.round(min) },
        "PATCH",
      );
      setLoyaltyDraft(null);
      loyalty.reload();
    } catch {
      setLoyaltyError("Gagal menyimpan — coba lagi.");
    } finally {
      setLoyaltyBusy(false);
    }
  }

  async function savePricing() {
    setPricingBusy(true);
    setPricingError(null);
    try {
      const tax = Number(pricingForm.tax_percent);
      const service = Number(pricingForm.service_percent);
      const unit = Number(pricingForm.rounding_unit);
      if (!(tax >= 0 && tax < 100) || !(service >= 0 && service < 100) || !(unit >= 0)) {
        setPricingError("Persentase harus 0–99 dan pembulatan tidak boleh negatif.");
        return;
      }
      await mutate(
        "/api/pricing-settings",
        {
          tax_rate: (tax / 100).toFixed(4),
          tax_inclusive: pricingForm.tax_inclusive,
          service_charge_rate: (service / 100).toFixed(4),
          service_before_tax: pricingForm.service_before_tax,
          rounding_unit: unit.toFixed(2),
          rounding_mode: pricingForm.rounding_mode,
          discount_requires_pin: pricingForm.discount_requires_pin,
        },
        "PATCH",
      );
      setPricingDraft(null);
      pricing.reload();
    } catch {
      setPricingError("Gagal menyimpan — coba lagi.");
    } finally {
      setPricingBusy(false);
    }
  }

  async function saveProfile() {
    setSavingProfile(true);
    try {
      await mutate("/api/business", draft, "PATCH");
      setProfileDraft(null);
      business.reload();
    } finally {
      setSavingProfile(false);
    }
  }

  async function createStaff() {
    setStaffBusy(true);
    setStaffError(null);
    try {
      await mutate("/auth/staff", { name: staffDraft.name.trim(), pin: staffDraft.pin });
      setStaffSheet(false);
      setStaffDraft({ name: "", pin: "" });
      staff.reload();
    } catch (e) {
      setStaffError(e instanceof Error ? e.message : "Gagal menambah staf — coba lagi ya.");
    } finally {
      setStaffBusy(false);
    }
  }

  async function generateMenuLink() {
    setMenuBusy(true);
    try {
      const res = await mutate<{ menu_path: string }>("/auth/menu-link");
      const url = `${window.location.origin}${res.menu_path}`;
      setMenuLink(url);
      setMenuQr(await QRCode.toDataURL(url, { width: 640, margin: 1, errorCorrectionLevel: "M" }));
    } finally {
      setMenuBusy(false);
    }
  }

  async function generatePairing() {
    setPairingBusy(true);
    try {
      const res = await mutate<{ pos_path: string }>("/auth/pos-pairing");
      setPairing(`${window.location.origin}${res.pos_path}`);
    } finally {
      setPairingBusy(false);
    }
  }

  return (
    <div className="animate-fade-up max-w-3xl space-y-8">
      <h1 className="text-[1.65rem] font-bold tracking-tight md:text-3xl">Pengaturan</h1>

      {/* Business profile */}
      <section>
        <h2 className="mb-2 text-base font-bold">Profil usaha</h2>
        {business.loading ? (
          <Skeleton className="h-40" />
        ) : (
          <Plate className="space-y-3 px-6 py-5">
            <label className="block">
              <span className="ink-soft mb-1.5 block text-xs font-medium">Nama usaha</span>
              <input
                className="field"
                value={draft.name}
                onChange={(e) => setProfileDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="ink-soft mb-1.5 block text-xs font-medium">Jenis usaha</span>
              <input
                className="field"
                value={draft.business_type}
                onChange={(e) => setProfileDraft({ ...draft, business_type: e.target.value })}
                placeholder="cafe / warung / toko"
              />
            </label>
            <div className="ink-soft flex flex-wrap gap-x-6 gap-y-1 pt-1 text-xs">
              <span>WhatsApp: +{b?.owner_phone}</span>
              <span>Zona waktu: {b?.timezone}</span>
            </div>
            {profileDraft && (
              <button onClick={saveProfile} disabled={savingProfile} className="btn-accent px-5 py-2.5 text-sm">
                {savingProfile ? "Menyimpan…" : "Simpan perubahan"}
              </button>
            )}
          </Plate>
        )}
      </section>


      {/* Pricing (M7-T4): tax, service charge, rounding, discount gate */}
      <section>
        <h2 className="mb-2 flex items-center gap-2 text-base font-bold">
          Pajak, service &amp; pembulatan
          <HelpTip title="Cara struk dihitung">
            Pajak bisa sudah termasuk di harga menu (harga yang tertulis = yang dibayar) atau
            ditambahkan di struk. Service charge bisa ikut kena pajak atau tidak. Pembulatan
            berlaku pada total akhir. Semua berlaku untuk transaksi berikutnya — yang sudah
            tercatat tidak berubah.
          </HelpTip>
        </h2>
        {pricing.loading ? (
          <Skeleton className="h-56" />
        ) : (
          <Plate className="space-y-4 px-6 py-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Pajak (%)</span>
                <input
                  className="field"
                  inputMode="decimal"
                  value={pricingForm.tax_percent}
                  onChange={(e) => setPricingDraft({ ...pricingForm, tax_percent: e.target.value })}
                  placeholder="0"
                />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Service charge (%)</span>
                <input
                  className="field"
                  inputMode="decimal"
                  value={pricingForm.service_percent}
                  onChange={(e) => setPricingDraft({ ...pricingForm, service_percent: e.target.value })}
                  placeholder="0"
                />
              </label>
            </div>
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={pricingForm.tax_inclusive}
                onChange={(e) => setPricingDraft({ ...pricingForm, tax_inclusive: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-medium">Harga menu sudah termasuk pajak</span>
                <span className="ink-faint block text-xs">
                  Dicentang: pelanggan bayar sesuai harga menu, pajak dipisahkan di pembukuan. Tidak
                  dicentang: pajak ditambahkan di struk.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={pricingForm.service_before_tax}
                onChange={(e) => setPricingDraft({ ...pricingForm, service_before_tax: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-medium">Service charge ikut kena pajak</span>
                <span className="ink-faint block text-xs">
                  Dicentang: pajak dihitung dari harga + service. Tidak dicentang: service dihitung
                  setelah pajak dan tidak dipajaki.
                </span>
              </span>
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Pembulatan total (Rp)</span>
                <select
                  className="field"
                  value={pricingForm.rounding_unit}
                  onChange={(e) => setPricingDraft({ ...pricingForm, rounding_unit: e.target.value })}
                >
                  <option value="0">Tidak dibulatkan</option>
                  <option value="50">Ke Rp 50</option>
                  <option value="100">Ke Rp 100</option>
                  <option value="500">Ke Rp 500</option>
                  <option value="1000">Ke Rp 1.000</option>
                </select>
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Arah pembulatan</span>
                <select
                  className="field"
                  value={pricingForm.rounding_mode}
                  onChange={(e) =>
                    setPricingDraft({ ...pricingForm, rounding_mode: e.target.value as PricingForm["rounding_mode"] })
                  }
                >
                  <option value="nearest">Terdekat</option>
                  <option value="up">Selalu ke atas</option>
                  <option value="down">Selalu ke bawah</option>
                </select>
              </label>
            </div>
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={pricingForm.discount_requires_pin}
                onChange={(e) => setPricingDraft({ ...pricingForm, discount_requires_pin: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-medium">Diskon perlu PIN pemilik</span>
                <span className="ink-faint block text-xs">
                  Kasir harus minta PIN kamu sebelum memberi diskon per barang atau per struk.
                </span>
              </span>
            </label>
            {pricingError && <p className="text-sm text-red-600">{pricingError}</p>}
            {pricingDraft && (
              <button onClick={savePricing} disabled={pricingBusy} className="btn-accent px-5 py-2.5 text-sm">
                {pricingBusy ? "Menyimpan…" : "Simpan perubahan"}
              </button>
            )}
          </Plate>
        )}
      </section>

      {/* Points programme (M8-T2) */}
      <section>
        <h2 className="mb-2 flex items-center gap-2 text-base font-bold">
          Program poin
          <HelpTip title="Cara poin bekerja">
            Pelanggan yang dikaitkan di kasir dapat poin dari bagian yang dibayar uang (bukan dari poin).
            Poin bisa dipakai bayar di kasir senilai &ldquo;nilai satu poin&rdquo;. Biaya poin masuk
            pembukuan sebagai beban pemasaran dan liabilitas poin, jadi laporan tetap jujur.
          </HelpTip>
        </h2>
        {loyalty.loading ? (
          <Skeleton className="h-40" />
        ) : (
          <Plate className="space-y-4 px-6 py-5">
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={loyaltyForm.is_active}
                onChange={(e) => setLoyaltyDraft({ ...loyaltyForm, is_active: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-medium">Aktifkan program poin</span>
                <span className="ink-faint block text-xs">Mati: tidak ada poin baru dan poin tidak bisa dipakai bayar.</span>
              </span>
            </label>
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Belanja per 1 poin (Rp)</span>
                <input
                  className="field"
                  inputMode="numeric"
                  value={loyaltyForm.rupiah_per_point}
                  onChange={(e) => setLoyaltyDraft({ ...loyaltyForm, rupiah_per_point: e.target.value.replace(/[^0-9]/g, "") })}
                />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Nilai 1 poin saat dipakai (Rp)</span>
                <input
                  className="field"
                  inputMode="numeric"
                  value={loyaltyForm.point_value}
                  onChange={(e) => setLoyaltyDraft({ ...loyaltyForm, point_value: e.target.value.replace(/[^0-9]/g, "") })}
                />
              </label>
              <label className="block">
                <span className="ink-soft mb-1.5 block text-xs font-medium">Minimal tukar (poin)</span>
                <input
                  className="field"
                  inputMode="numeric"
                  value={loyaltyForm.min_redeem_points}
                  onChange={(e) => setLoyaltyDraft({ ...loyaltyForm, min_redeem_points: e.target.value.replace(/[^0-9]/g, "") })}
                />
              </label>
            </div>
            <p className="ink-faint text-xs">
              Contoh: belanja Rp {Number(loyaltyForm.rupiah_per_point || 0).toLocaleString("id-ID")} = 1 poin; 100 poin = Rp{" "}
              {(100 * Number(loyaltyForm.point_value || 0)).toLocaleString("id-ID")} potongan.
            </p>
            {loyaltyError && <p className="text-sm text-red-600">{loyaltyError}</p>}
            {loyaltyDraft && (
              <button onClick={saveLoyalty} disabled={loyaltyBusy} className="btn-accent px-5 py-2.5 text-sm">
                {loyaltyBusy ? "Menyimpan…" : "Simpan perubahan"}
              </button>
            )}
          </Plate>
        )}
      </section>

      {/* Staff */}
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-base font-bold">Staf kasir</h2>
          <button onClick={() => setStaffSheet(true)} className="btn-quiet px-3 py-1.5 text-sm">
            <IconPlus className="h-4 w-4" /> Tambah staf
          </button>
        </div>
        {staff.loading ? (
          <Skeleton className="h-24" />
        ) : (
          <ul>
            {(staff.data ?? []).map((member) => (
              <li key={member.id} className={`list-row ${member.is_active ? "" : "opacity-45"}`}>
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent-gradient text-sm font-bold text-white">
                  {initials(member.name)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">{member.name}</p>
                  <p className="ink-faint text-xs">
                    {member.role === "owner" ? "pemilik" : "staf"}
                    {!member.is_active && " · nonaktif"}
                  </p>
                </div>
                {member.role !== "owner" && member.is_active && (
                  <button
                    onClick={async () => {
                      await mutate(`/auth/staff/${member.id}/deactivate`);
                      staff.reload();
                    }}
                    className="ink-faint shrink-0 text-xs hover:text-[color:var(--bad)]"
                  >
                    nonaktifkan
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* POS pairing */}
      <section>
        <h2 className="mb-2 flex items-center gap-2 text-base font-bold">
          Layar kasir (POS)
          <HelpTip title="Layar kasir">
            Buka tautan ini sekali di browser tablet/HP kasir. Setelah itu perangkat selalu
            langsung masuk ke layar kasir usaha ini — staf tinggal pilih nama dan masukkan PIN.
          </HelpTip>
        </h2>
        <Plate className="space-y-3 px-6 py-5">
          <p className="ink-soft text-sm">
            Hubungkan perangkat kasir dengan tautan berpasangan. Tautan berlaku setahun dan hanya
            membuka layar kasir — bukan dashboard ini.
          </p>
          {pairing ? (
            <CopyField value={pairing} />
          ) : (
            <button onClick={generatePairing} disabled={pairingBusy} className="btn-accent px-5 py-2.5 text-sm">
              {pairingBusy ? "Membuat…" : "Buat tautan kasir"}
            </button>
          )}
        </Plate>
      </section>

      {/* QR e-menu (M11-T1) */}
      <section>
        <h2 className="mb-2 flex items-center gap-2 text-base font-bold">
          Menu QR untuk meja
          <HelpTip title="Menu QR">
            Cetak kode QR ini dan tempel di tiap meja. Tamu memindai, memilih pesanan, lalu mendapat
            kode singkat untuk dibayar di kasir. Pesanan langsung muncul di layar kasir sebagai
            antrean — stok dan pembukuan baru bergerak saat kasir menerima pembayaran.
          </HelpTip>
        </h2>
        <Plate className="space-y-3 px-6 py-5">
          <p className="ink-soft text-sm">
            Satu tautan untuk semua meja, berlaku setahun. Tautan ini hanya membuka menu dan
            mengirim pesanan — tidak bisa melihat stok, harga modal, atau dashboard.
          </p>
          {menuLink ? (
            <div className="space-y-3">
              <CopyField value={menuLink} />
              {menuQr && (
                <div className="flex flex-wrap items-center gap-4">
                  <img src={menuQr} alt="Kode QR menu" className="h-40 w-40 rounded-2xl bg-white p-2" />
                  <a href={menuQr} download="menu-qr.png" className="btn-quiet px-4 py-2 text-sm">
                    Unduh QR (PNG)
                  </a>
                </div>
              )}
            </div>
          ) : (
            <button onClick={generateMenuLink} disabled={menuBusy} className="btn-accent px-5 py-2.5 text-sm">
              {menuBusy ? "Membuat…" : "Buat tautan menu QR"}
            </button>
          )}
        </Plate>
      </section>

      {/* Stock template */}
      <section>
        <h2 className="mb-2 text-base font-bold">Template stok</h2>
        <p className="ink-soft mb-3 max-w-lg text-sm">
          Isi template Excel ini lalu kirim ke asisten WhatsApp untuk mengisi stok awal sekaligus.
          Bisa juga langsung foto buku stok — nanti dibaca otomatis.
        </p>
        <a
          href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/stock-template`}
          onClick={(e) => {
            e.preventDefault();
            const token = localStorage.getItem("wp_owner_token");
            void fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/stock-template`, {
              headers: { Authorization: `Bearer ${token}` },
            })
              .then((r) => r.blob())
              .then((blob) => {
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = url;
                link.download = "template-stok.xlsx";
                link.click();
                URL.revokeObjectURL(url);
              });
          }}
          className="btn-quiet inline-flex px-5 py-2.5 text-sm"
        >
          ⬇︎ Unduh template-stok.xlsx
        </a>
      </section>

      {/* Catalogue import (M4-T6) */}
      <section>
        <h2 className="mb-2 text-base font-bold">Impor katalog</h2>
        <p className="ink-soft mb-3 max-w-lg text-sm">
          Satu berkas Excel untuk barang, ukuran (varian), pilihan tambahan, satuan, konversi, dan resep.
          Semua baris diperiksa dulu — kalau ada yang salah, tidak ada yang disimpan dan setiap baris bermasalah disebutkan.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={() => {
              const token = localStorage.getItem("wp_owner_token");
              void fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/catalog-template`, {
                headers: { Authorization: `Bearer ${token}` },
              })
                .then((r) => r.blob())
                .then((blob) => {
                  const url = URL.createObjectURL(blob);
                  const link = document.createElement("a");
                  link.href = url;
                  link.download = "template-katalog.xlsx";
                  link.click();
                  URL.revokeObjectURL(url);
                });
            }}
            className="btn-quiet px-5 py-2.5 text-sm"
          >
            ⬇︎ Unduh template-katalog.xlsx
          </button>
          <label className="btn-accent cursor-pointer px-5 py-2.5 text-sm">
            {importBusy ? "Memeriksa…" : "⬆︎ Impor berkas Excel"}
            <input
              type="file"
              accept=".xlsx"
              className="hidden"
              disabled={importBusy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) void importCatalog(file);
              }}
            />
          </label>
        </div>
        {importResult && (
          <p
            className="mt-3 max-w-lg rounded-2xl px-4 py-3 text-sm"
            style={
              importResult.ok
                ? { background: "var(--good-bg, var(--accent-soft))", color: "var(--good)" }
                : { background: "var(--bad-bg)", color: "var(--bad)" }
            }
          >
            {importResult.message}
          </p>
        )}
      </section>

      <Sheet open={staffSheet} onClose={() => setStaffSheet(false)} title="Staf baru">
        <div className="space-y-3">
          <label className="block">
            <span className="ink-soft mb-1.5 block text-xs font-medium">Nama</span>
            <input
              className="field"
              value={staffDraft.name}
              onChange={(e) => setStaffDraft({ ...staffDraft, name: e.target.value })}
              placeholder="cth. Sari"
            />
          </label>
          <label className="block">
            <span className="ink-soft mb-1.5 block text-xs font-medium">PIN kasir (4 angka)</span>
            <input
              className="field tabular-nums"
              inputMode="numeric"
              maxLength={4}
              value={staffDraft.pin}
              onChange={(e) => setStaffDraft({ ...staffDraft, pin: e.target.value.replace(/\D/g, "") })}
              placeholder="••••"
            />
          </label>
          {staffError && (
            <p
              className="rounded-2xl px-4 py-3 text-sm font-medium"
              style={{ background: "var(--bad-bg)", color: "var(--bad)" }}
            >
              {staffError}
            </p>
          )}
          <button
            onClick={createStaff}
            disabled={staffBusy || !staffDraft.name.trim() || staffDraft.pin.length !== 4}
            className="btn-accent w-full py-3.5"
          >
            {staffBusy ? "Menyimpan…" : "Tambah staf"}
          </button>
        </div>
      </Sheet>
    </div>
  );
}
