"use client";

import { useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import type { Business, StaffMember } from "@/lib/types";
import { CopyField, Plate, Sheet, Skeleton } from "@/components/ui";
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
      setStaffError(e instanceof Error ? e.message : "Gagal menambah staf — coba lagi ya.");
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
