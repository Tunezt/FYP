"use client";

import { useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import type { Business, StaffMember } from "@/lib/types";
import { CopyField, Glass, Plate, SectionTitle, Sheet, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconPlus } from "@/components/icons";
import { initials } from "@/lib/format";

export default function SettingsPage() {
  const business = useOwnerData<Business>("/api/business");
  const staff = useOwnerData<StaffMember[]>("/auth/staff");
  const mutate = useOwnerMutation();

  const [profileDraft, setProfileDraft] = useState<{ name: string; business_type: string } | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [staffSheet, setStaffSheet] = useState(false);
  const [staffDraft, setStaffDraft] = useState({ name: "", pin: "" });
  const [staffBusy, setStaffBusy] = useState(false);
  const [staffError, setStaffError] = useState<string | null>(null);
  const [pairing, setPairing] = useState<string | null>(null);
  const [pairingBusy, setPairingBusy] = useState(false);

  const b = business.data;
  const draft = profileDraft ?? { name: b?.name ?? "", business_type: b?.business_type ?? "cafe" };

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
      setStaffError(e instanceof Error ? e.message : "Gagal menambah staf");
    } finally {
      setStaffBusy(false);
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
    <div className="animate-fade-up space-y-8">
      <h1 className="text-2xl font-bold tracking-tight md:text-3xl">Pengaturan</h1>

      {/* Business profile */}
      <section>
        <SectionTitle>Profil usaha</SectionTitle>
        {business.loading ? (
          <Skeleton className="h-40" />
        ) : (
          <Glass className="space-y-3 px-6 py-5">
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
          </Glass>
        )}
      </section>

      {/* Staff */}
      <section>
        <SectionTitle
          action={
            <button onClick={() => setStaffSheet(true)} className="btn-quiet px-3 py-1.5 text-sm">
              <IconPlus className="h-4 w-4" /> Tambah staf
            </button>
          }
        >
          Staf kasir
        </SectionTitle>
        {staff.loading ? (
          <Skeleton className="h-24" />
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {(staff.data ?? []).map((member) => (
              <li
                key={member.id}
                className={`flex items-center gap-3 rounded-2xl px-4 py-3 ${member.is_active ? "" : "opacity-45"}`}
                style={{ border: "1px solid var(--hairline)" }}
              >
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-accent-gradient text-sm font-bold text-white">
                  {initials(member.name)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{member.name}</p>
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
                    className="ink-faint shrink-0 text-xs hover:text-red-500"
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
        <SectionTitle
          hint={
            <HelpTip title="Layar kasir">
              Buka tautan ini sekali di browser tablet/HP kasir. Setelah itu perangkat selalu
              langsung masuk ke layar kasir usaha ini — staf tinggal pilih nama dan masukkan PIN.
            </HelpTip>
          }
        >
          Layar kasir (POS)
        </SectionTitle>
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

      {/* Stock template */}
      <section>
        <SectionTitle>Template stok</SectionTitle>
        <p className="ink-soft mb-3 max-w-lg text-sm">
          Isi template Excel ini lalu kirim ke asisten WhatsApp untuk mengisi stok awal sekaligus.
          Bisa juga langsung foto buku stok — nanti dibaca otomatis.
        </p>
        <a
          href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/stock-template`}
          onClick={(e) => {
            // Authenticated download: fetch with token, save as blob.
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
            <p className="rounded-2xl bg-red-500/10 px-4 py-3 text-sm font-medium text-red-600">
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
