"use client";

import { useState } from "react";
import { DateRangePicker } from "@/components/DateRangePicker";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { formatRupiah } from "@/lib/format";
import { dayLabel } from "@/lib/dates";
import type { Business, CustomerRow, Page, PointsMovementRow } from "@/lib/types";
import { EmptyState, ErrorState, Glass, Sheet, Skeleton } from "@/components/ui";
import { HelpTip } from "@/components/HelpTip";
import { IconChevronLeft, IconChevronRight, IconPlus, IconSearch, IconUsers } from "@/components/icons";
import { initials } from "@/lib/format";

type Draft = { name: string; phone: string; address: string; birthday: string; notes: string };
const EMPTY: Draft = { name: "", phone: "", address: "", birthday: "", notes: "" };

function prettyPhone(p: string | null): string {
  if (!p) return "—";
  // 6281234567890 → 0812-3456-7890 for reading; the API keeps the international form.
  const local = p.startsWith("62") ? "0" + p.slice(2) : p;
  return local.replace(/(\d{4})(\d{4})(\d+)/, "$1-$2-$3");
}

export default function CustomersPage() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [showInactive, setShowInactive] = useState(false);
  const customers = useOwnerData<Page<CustomerRow>>(
    `/api/customers?q=${encodeURIComponent(q)}&page=${page}&page_size=30&include_inactive=${showInactive}`
  );
  const business = useOwnerData<Business>("/api/business");
  const mutate = useOwnerMutation();
  const tz = business.data?.timezone;
  const dayStart = business.data?.day_start_hour ?? 0;

  const [editing, setEditing] = useState<CustomerRow | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Points (M8-T2): history of the customer being edited, and a manual adjustment.
  const editingId = editing && editing !== "new" ? editing.id : null;
  const history = useOwnerData<PointsMovementRow[]>(editingId ? `/api/customers/${editingId}/points?limit=20` : null);
  const [adjust, setAdjust] = useState({ delta: "", notes: "" });
  const [adjustBusy, setAdjustBusy] = useState(false);

  async function saveAdjust() {
    if (!editingId || !Number(adjust.delta)) return;
    setAdjustBusy(true);
    setError(null);
    try {
      await mutate(`/api/customers/${editingId}/points/adjust`, { points_delta: Number(adjust.delta), notes: adjust.notes.trim() || null });
      setAdjust({ delta: "", notes: "" });
      history.reload();
      customers.reload();
    } catch (e: unknown) {
      setError(e instanceof Error && "detail" in e ? String((e as { detail: string }).detail) : "Gagal menyimpan poin.");
    } finally {
      setAdjustBusy(false);
    }
  }

  function open(c: CustomerRow | "new") {
    setEditing(c);
    setError(null);
    setDraft(
      c === "new"
        ? EMPTY
        : { name: c.name, phone: c.phone ?? "", address: c.address ?? "", birthday: c.birthday ?? "", notes: c.notes ?? "" }
    );
  }

  async function save() {
    if (!draft.name.trim()) {
      setError("Nama tidak boleh kosong.");
      return;
    }
    setBusy(true);
    setError(null);
    const body = {
      name: draft.name.trim(),
      phone: draft.phone.trim(),
      address: draft.address.trim() || null,
      birthday: draft.birthday || null,
      notes: draft.notes.trim() || null,
    };
    try {
      if (editing === "new") await mutate("/api/customers", body);
      else if (editing) await mutate(`/api/customers/${editing.id}`, body, "PATCH");
      setEditing(null);
      customers.reload();
    } catch (e: unknown) {
      setError(e instanceof Error && "detail" in e ? String((e as { detail: string }).detail) : "Gagal menyimpan — coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(c: CustomerRow) {
    await mutate(`/api/customers/${c.id}`, { is_active: !c.is_active }, "PATCH");
    customers.reload();
  }

  const totalPages = customers.data ? Math.max(1, Math.ceil(customers.data.total / customers.data.page_size)) : 1;

  return (
    <div className="animate-fade-up space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 page-title">
            Pelanggan
            <HelpTip title="Pelanggan">
              Nomor HP adalah kuncinya — satu nomor satu pelanggan, dan nanti jadi tujuan poin dan promo
              lewat WhatsApp. Kunjungan dan belanja dihitung dari transaksi yang dikaitkan di kasir.
            </HelpTip>
          </h1>
          {customers.data && <p className="ink-soft mt-1 text-sm">{customers.data.total} pelanggan</p>}
        </div>
        <button onClick={() => open("new")} className="btn-accent px-4 py-2.5 text-sm">
          <IconPlus className="h-4 w-4" /> Tambah pelanggan
        </button>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:max-w-xs">
          <IconSearch className="ink-faint pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" />
          <input
          className="control-field cursor-text pl-9"
          type="search"
          aria-label="Cari pelanggan"
          placeholder="Cari nama atau nomor HP"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        </div>
        <label className="ink-soft flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
          Tampilkan yang nonaktif
        </label>
      </div>

      {customers.loading ? (
        <Skeleton className="h-64" />
      ) : customers.error && !customers.data ? (
        <Glass>
          <ErrorState onRetry={customers.reload} />
        </Glass>
      ) : !customers.data || customers.data.rows.length === 0 ? (
        <Glass>
          <EmptyState icon={<IconUsers className="h-5 w-5" />} title={q ? "Tidak ketemu" : "Belum ada pelanggan"}>
            {q
              ? "Coba nama lain atau sebagian nomor HP."
              : "Kasir bisa mengaitkan pelanggan saat bayar, atau tambahkan di sini."}
          </EmptyState>
        </Glass>
      ) : (
        <>
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {customers.data.rows.map((c) => (
              <li key={c.id}>
                <Glass className="flex h-full flex-col px-5 py-4">
                  <div className="flex items-start gap-3">
                    <span
                      className="surface-inset ink-soft flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[13px] font-semibold"
                      style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}
                    >
                      {initials(c.name)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className={`flex items-center gap-2 truncate text-[15px] font-semibold ${c.is_active ? "" : "ink-soft"}`}>
                        {c.name}
                        {!c.is_active && <span className="pill-quiet">nonaktif</span>}
                      </p>
                      <p className="ink-soft text-xs tabular-nums">{prettyPhone(c.phone)}</p>
                    </div>
                    <button onClick={() => open(c)} className="chip-btn shrink-0">
                      Ubah
                    </button>
                  </div>
                  <dl className="hairline-t mt-4 grid grid-cols-2 gap-x-4 gap-y-2.5 pt-3 text-xs">
                    <div>
                      <dt className="ink-faint">Poin</dt>
                      <dd className="mt-0.5 text-sm font-semibold tabular-nums" style={c.points_balance < 0 ? { color: "var(--bad)" } : undefined}>
                        {c.points_balance}
                      </dd>
                    </div>
                    <div>
                      <dt className="ink-faint">Kunjungan</dt>
                      <dd className="mt-0.5 text-sm font-semibold tabular-nums">{c.visits}×</dd>
                    </div>
                    <div>
                      <dt className="ink-faint">Belanja</dt>
                      <dd className="mt-0.5 text-sm font-semibold tabular-nums">{formatRupiah(c.total_spent)}</dd>
                    </div>
                    <div>
                      <dt className="ink-faint">Terakhir</dt>
                      <dd className="mt-0.5 text-sm font-semibold">{c.last_visit ? dayLabel(new Date(c.last_visit), tz, dayStart) : "—"}</dd>
                    </div>
                  </dl>
                  {(c.address || c.birthday || c.notes) && (
                    <p className="ink-faint mt-2 line-clamp-2 text-xs">
                      {[c.address, c.birthday ? `ulang tahun ${c.birthday.slice(5).split("-").reverse().join("/")}` : null, c.notes]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  )}
                </Glass>
              </li>
            ))}
          </ul>
          {totalPages > 1 && (
            <div className="flex items-center justify-between">
              <button className="btn-quiet gap-1 px-3.5 py-2 text-sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                <IconChevronLeft className="h-4 w-4" /> Sebelumnya
              </button>
              <span className="ink-soft text-sm tabular-nums">
                {page} / {totalPages}
              </span>
              <button className="btn-quiet gap-1 px-3.5 py-2 text-sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                Berikutnya <IconChevronRight className="h-4 w-4" />
              </button>
            </div>
          )}
        </>
      )}

      {editing && (
        <Sheet open title={editing === "new" ? "Pelanggan baru" : "Ubah pelanggan"} onClose={() => !busy && setEditing(null)}>
          <div className="space-y-3">
            <label className="block">
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">Nama</span>
              <input className="field" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} autoFocus />
            </label>
            <label className="block">
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">Nomor HP (WhatsApp)</span>
              <input
                className="field"
                inputMode="tel"
                placeholder="0812 3456 7890"
                value={draft.phone}
                onChange={(e) => setDraft({ ...draft, phone: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">Alamat</span>
              <input className="field" value={draft.address} onChange={(e) => setDraft({ ...draft, address: e.target.value })} />
            </label>
            <label className="block">
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">Tanggal lahir</span>
              <DateRangePicker
                single
                variant="field"
                placeholder="Belum diisi"
                since={draft.birthday}
                until={draft.birthday}
                onChange={(day) => setDraft({ ...draft, birthday: day })}
              />
            </label>
            <label className="block">
              <span className="ink-soft mb-1.5 block text-[13px] font-medium">Catatan</span>
              <input className="field" value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
            </label>
            {editingId && (
              <div className="hairline-t pt-3">
                <p className="text-sm font-semibold">
                  Poin: <span className="tabular-nums">{editing !== "new" ? editing.points_balance : 0}</span>
                </p>
                <div className="mt-2 grid grid-cols-[6rem_1fr_auto] gap-2">
                  <input
                    className="field"
                    inputMode="numeric"
                    placeholder="+10 / -5"
                    value={adjust.delta}
                    onChange={(e) => setAdjust({ ...adjust, delta: e.target.value.replace(/[^0-9-]/g, "") })}
                  />
                  <input
                    className="field"
                    placeholder="Alasan (mis. kompensasi)"
                    value={adjust.notes}
                    onChange={(e) => setAdjust({ ...adjust, notes: e.target.value })}
                  />
                  <button onClick={saveAdjust} disabled={adjustBusy || !Number(adjust.delta)} className="btn-quiet px-3 text-sm">
                    Sesuaikan
                  </button>
                </div>
                {history.data && history.data.length > 0 && (
                  <ul className="surface-inset mt-3 max-h-40 space-y-1 overflow-auto rounded-xl px-3 py-2 text-xs">
                    {history.data.map((m) => (
                      <li key={m.id} className="flex justify-between gap-2">
                        <span className="ink-soft truncate">
                          {{ earn: "dapat", redeem: "dipakai", adjust: "penyesuaian", reversal: "dibatalkan", expire: "kedaluwarsa" }[m.reason]}
                          {m.notes ? ` — ${m.notes}` : ""} · {dayLabel(new Date(m.created_at), tz, dayStart)}
                        </span>
                        <span className={`shrink-0 tabular-nums ${m.points_delta < 0 ? "" : "font-semibold"}`}>
                          {m.points_delta > 0 ? "+" : ""}
                          {m.points_delta}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {error && <p className="notice notice-bad">{error}</p>}
            <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row">
              <button onClick={save} disabled={busy} className="btn-accent flex-1 py-3">
                {busy ? "Menyimpan…" : "Simpan"}
              </button>
              {editing !== "new" && (
                <button onClick={() => toggleActive(editing)} className="btn-quiet px-4 py-3">
                  {editing.is_active ? "Nonaktifkan" : "Aktifkan lagi"}
                </button>
              )}
            </div>
          </div>
        </Sheet>
      )}
    </div>
  );
}
