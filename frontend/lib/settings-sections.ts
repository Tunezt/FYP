import type { IndexEntry } from "@/components/SectionIndex";

/** The Pengaturan page's contents, in page order, in three groups. The sidebar
 * (components/SettingsSubnav.tsx) lists them nested under "Pengaturan"; the
 * chip row on a narrower screen lists them flat. Section ids live on the
 * sections in app/(dashboard)/settings/page.tsx, so keep the order the same. */
export const SETTINGS_GROUPS: { label: string; entries: IndexEntry[] }[] = [
  {
    label: "Usaha",
    entries: [
      { id: "profil", label: "Profil usaha" },
      { id: "whatsapp", label: "WhatsApp asisten" },
      { id: "pajak", label: "Pajak & service" },
      { id: "poin", label: "Program poin" },
    ],
  },
  {
    label: "Kasir",
    entries: [
      { id: "staf", label: "Staf kasir" },
      { id: "kasir", label: "Layar kasir" },
      { id: "printer", label: "Printer" },
      { id: "struk", label: "Data di struk" },
      { id: "menu-qr", label: "Menu QR" },
    ],
  },
  {
    label: "Data",
    entries: [
      { id: "template", label: "Template stok" },
      { id: "impor", label: "Impor katalog" },
    ],
  },
];

export const SETTINGS_INDEX: IndexEntry[] = SETTINGS_GROUPS.flatMap((g) => g.entries);
