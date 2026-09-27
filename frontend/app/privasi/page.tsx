import type { Metadata } from "next";
import Link from "next/link";
import { ContactLine, LegalPage, Section } from "@/components/LegalPage";

export const metadata: Metadata = {
  title: "Kebijakan privasi · Poernama",
  description: "Data apa yang disimpan sistem Poernama, untuk apa, dan siapa yang memprosesnya.",
};

export default function PrivacyPage() {
  return (
    <LegalPage title="Kebijakan privasi">
      <p>
        Sistem Poernama adalah perangkat lunak kasir, dapur, menu QR, dashboard, dan asisten WhatsApp
        untuk kafe Poernama. Halaman ini menjelaskan data apa yang disimpan, untuk apa, dan siapa yang
        ikut memprosesnya.
      </p>

      <Section heading="Data yang disimpan">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>
            <strong>Pemilik dan staf:</strong> nama, nomor HP, peran, dan PIN. PIN disimpan dalam bentuk
            teracak (hash), tidak pernah sebagai angka aslinya.
          </li>
          <li>
            <strong>Transaksi dan stok:</strong> pesanan, pembayaran, pembatalan, stok, pemasok, dan
            pembukuan usaha.
          </li>
          <li>
            <strong>Pelanggan:</strong> nama dan nomor HP bila pelanggan ikut program poin, beserta
            poinnya.
          </li>
          <li>
            <strong>Tamu yang memesan lewat QR:</strong> nama dan nomor meja yang diisi saat memesan,
            serta nomor HP atau alamat bila diisi untuk pesanan antar.
          </li>
          <li>
            <strong>Pesan WhatsApp pemilik:</strong> pertanyaan yang dikirim ke asisten dan foto nota atau
            faktur yang dikirim untuk dicatat. Foto disimpan agar bisa dicek ulang.
          </li>
          <li>
            <strong>Nomor yang belum terdaftar:</strong> bila nomor lain mengirim pesan, isinya tidak
            disimpan. Nomornya hanya muncul di catatan server sementara, dan dibalas bahwa nomor itu belum
            terdaftar.
          </li>
        </ul>
      </Section>

      <Section heading="Untuk apa">
        <p>
          Hanya untuk menjalankan usaha: mencatat penjualan, menyiapkan pesanan, menghitung stok dan
          laporan, dan menjawab pertanyaan pemilik. Data tidak dijual, tidak dipakai untuk iklan, dan
          tidak dibagikan ke pihak lain selain penyedia layanan di bawah.
        </p>
      </Section>

      <Section heading="Penyedia layanan yang ikut memproses">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>
            <strong>Meta (WhatsApp Business Platform):</strong> mengantar pesan antara pemilik dan asisten.
          </li>
          <li>
            <strong>Google (Gemini):</strong> membaca pertanyaan dan foto nota yang dikirim pemilik lewat
            WhatsApp, untuk menyusun jawaban atau draf catatan.
          </li>
          <li>
            <strong>Supabase:</strong> basis data dan penyimpanan foto.
          </li>
          <li>
            <strong>Railway</strong> dan <strong>Vercel:</strong> server dan halaman web sistem ini.
          </li>
        </ul>
      </Section>

      <Section heading="Berapa lama disimpan">
        <p>
          Catatan usaha disimpan selama kafe memakai sistem ini. Kesalahan diperbaiki dengan catatan
          koreksi, bukan dengan menghapus, supaya pembukuan tetap bisa ditelusuri.
        </p>
      </Section>

      <Section heading="Hak kamu">
        <p>
          Kamu bisa meminta salinan, perbaikan, atau penghapusan data tentang dirimu. Caranya ada di
          halaman{" "}
          <Link href="/hapus-data" className="font-semibold text-[color:var(--accent)] hover:underline">
            penghapusan data
          </Link>
          .
        </p>
      </Section>

      <Section heading="Kontak">
        <p>
          Pertanyaan tentang privasi: <ContactLine />.
        </p>
      </Section>

      <p className="ink-faint hairline-t pt-5 text-[13px]" lang="en">
        In English: Poernama is the point-of-sale, kitchen, QR menu, dashboard and WhatsApp assistant
        system for the Poernama café. It stores staff, sales, customer loyalty and guest order details,
        and the owner&apos;s WhatsApp messages and receipt photos, only to run the business. Messages
        pass through Meta; the owner&apos;s questions and photos are read by Google Gemini; data is
        hosted on Supabase, Railway and Vercel. Nothing is sold or used for advertising. Deletion
        requests: see /hapus-data.
      </p>
    </LegalPage>
  );
}
