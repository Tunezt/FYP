/** Demo mode — renders the dashboard from local fixtures, no backend needed.
 *
 * Why this exists: the app is useless to look at when the database is
 * unreachable (every page is an error state), which blocks design review and
 * would kill a live demo if Supabase hiccups. This is a FRONTEND-ONLY preview
 * path: it never weakens backend auth (the API still demands a real JWT), it
 * just short-circuits `useOwnerData` before any request is made.
 *
 * Enabled explicitly by the owner (button on /login, or ?demo=1), and only in
 * development or when NEXT_PUBLIC_DEMO_MODE=1. Every screen shows a banner so
 * fixture numbers can never be mistaken for real ones.
 */
import { OWNER_TOKEN_KEY } from "@/lib/api";
import type {
  AlertRow,
  Business,
  CashMovementRow,
  CustomerRow,
  ExpenseRow,
  InventoryItem,
  LoyaltySettings,
  Overview,
  Page,
  PnlMonth,
  PricingSettings,
  PromoRow,
  VoucherRow,
  ReceiptRow,
  SaleRow,
  ShiftRow,
  StaffMember,
  TrendPoint,
} from "@/lib/types";

export const DEMO_KEY = "wp_demo_mode";
const DEMO_TOKEN = "demo-mode-no-backend";

export function demoAllowed(): boolean {
  return process.env.NODE_ENV === "development" || process.env.NEXT_PUBLIC_DEMO_MODE === "1";
}

export function isDemo(): boolean {
  if (typeof window === "undefined" || !demoAllowed()) return false;
  return localStorage.getItem(DEMO_KEY) === "1";
}

export function enableDemo(): void {
  localStorage.setItem(DEMO_KEY, "1");
  // Satisfies the layout's auth guard; no request ever carries it.
  localStorage.setItem(OWNER_TOKEN_KEY, DEMO_TOKEN);
}

export function disableDemo(): void {
  localStorage.removeItem(DEMO_KEY);
  if (localStorage.getItem(OWNER_TOKEN_KEY) === DEMO_TOKEN) {
    localStorage.removeItem(OWNER_TOKEN_KEY);
  }
}

// ── deterministic helpers ───────────────────────────────────────────────────

/** Stable pseudo-random so the fixtures don't reshuffle on every render. */
function rng(seed: number) {
  let s = seed;
  return () => {
    s |= 0;
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Asia/Jakarta is a fixed UTC+7 (no DST), so wall-clock math is exact.
const JKT = 7;

function jktDay(offsetDays: number) {
  const shifted = new Date(Date.now() + JKT * 3600_000);
  shifted.setUTCDate(shifted.getUTCDate() - offsetDays);
  return { y: shifted.getUTCFullYear(), m: shifted.getUTCMonth(), d: shifted.getUTCDate() };
}

/** ISO timestamp for a Jakarta wall-clock time N days ago. */
function jktIso(offsetDays: number, hour: number, minute = 0): string {
  const { y, m, d } = jktDay(offsetDays);
  return new Date(Date.UTC(y, m, d, hour - JKT, minute)).toISOString();
}

function jktDateOnly(offsetDays: number): string {
  const { y, m, d } = jktDay(offsetDays);
  return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

// ── fixture data (mirrors the seeded demo café) ─────────────────────────────

type ItemSpec = {
  name: string;
  unit: string;
  stock: number;
  cost: number;
  sell: number;
  reorder: number;
  usage: number | null;
  weight: number;
};

const ITEM_SPECS: ItemSpec[] = [
  { name: "Es Kopi Susu", unit: "cup", stock: 46, cost: 8000, sell: 22000, reorder: 20, usage: 6.4, weight: 10 },
  { name: "Kopi Arabica (cup)", unit: "cup", stock: 31, cost: 7000, sell: 20000, reorder: 15, usage: 4.1, weight: 7 },
  { name: "Americano", unit: "cup", stock: 38, cost: 6000, sell: 18000, reorder: 15, usage: 3.2, weight: 5 },
  { name: "Matcha Latte", unit: "cup", stock: 22, cost: 11000, sell: 28000, reorder: 10, usage: 2.6, weight: 4 },
  { name: "Teh Tarik", unit: "cup", stock: 27, cost: 4000, sell: 15000, reorder: 10, usage: 2.4, weight: 4 },
  { name: "Roti Bakar Coklat", unit: "pcs", stock: 15, cost: 9000, sell: 24000, reorder: 8, usage: 1.9, weight: 3 },
  { name: "Croissant", unit: "pcs", stock: 4, cost: 12000, sell: 28000, reorder: 6, usage: 4.3, weight: 3 },
  { name: "Nasi Goreng Senja", unit: "porsi", stock: 12, cost: 15000, sell: 35000, reorder: 6, usage: 1.5, weight: 2 },
  { name: "Biji Arabica", unit: "kg", stock: 2.4, cost: 145000, sell: 0, reorder: 3, usage: 0.9, weight: 0 },
  { name: "Gula Aren", unit: "kg", stock: 1.5, cost: 38000, sell: 0, reorder: 2, usage: null, weight: 0 },
  { name: "Susu UHT", unit: "liter", stock: 9, cost: 17000, sell: 0, reorder: 10, usage: null, weight: 0 },
];

const STAFF: StaffMember[] = [
  { id: "demo-staff-1", name: "Ibu Ratna", role: "owner", is_active: true, created_at: jktIso(120, 9) },
  { id: "demo-staff-2", name: "Sari", role: "staff", is_active: true, created_at: jktIso(118, 10) },
  { id: "demo-staff-3", name: "Budi", role: "staff", is_active: true, created_at: jktIso(96, 11) },
];

function buildItems(): InventoryItem[] {
  return ITEM_SPECS.map((spec, i) => ({
    id: `demo-item-${i + 1}`,
    name: spec.name,
    unit: spec.unit,
    current_stock: String(spec.stock),
    cost_price: String(spec.cost),
    sell_price: String(spec.sell),
    reorder_threshold: String(spec.reorder),
    avg_daily_usage: spec.usage,
    days_remaining: spec.usage ? Number((spec.stock / spec.usage).toFixed(1)) : null,
    below_reorder_threshold: spec.stock <= spec.reorder,
  }));
}

const SELLABLE = ITEM_SPECS.filter((s) => s.weight > 0);

/** Sales for one Jakarta day, newest first. */
function salesForDay(dayOffset: number, count: number): SaleRow[] {
  const rand = rng(1000 + dayOffset);
  const staffNames = ["Sari", "Budi", "Ibu Ratna"];
  const rows: SaleRow[] = [];
  const openHour = 7;
  const closeHour = dayOffset === 0 ? Math.max(8, jktNowHour()) : 21;
  const span = Math.max(1, closeHour - openHour);

  for (let i = 0; i < count; i++) {
    const totalWeight = SELLABLE.reduce((s, x) => s + x.weight, 0);
    let pick = rand() * totalWeight;
    let spec = SELLABLE[0];
    for (const candidate of SELLABLE) {
      pick -= candidate.weight;
      if (pick <= 0) {
        spec = candidate;
        break;
      }
    }
    const qty = rand() < 0.2 ? 2 : 1;
    // Spread evenly across opening hours, newest first.
    const minutesFromOpen = Math.round(((count - 1 - i) / count) * span * 60);
    const hour = openHour + Math.floor(minutesFromOpen / 60);
    const minute = minutesFromOpen % 60;
    rows.push({
      id: `demo-sale-${dayOffset}-${i}`,
      item_name: spec.name,
      staff_name: staffNames[Math.floor(rand() * staffNames.length)],
      quantity: String(qty),
      unit_price: String(spec.sell),
      total_price: String(spec.sell * qty),
      sold_at: jktIso(dayOffset, hour, minute),
    });
  }
  return rows;
}

function jktNowHour(): number {
  return new Date(Date.now() + JKT * 3600_000).getUTCHours();
}

const DAY_COUNTS = [15, 24, 21, 19, 26, 28, 17, 20];

function allSales(): SaleRow[] {
  return DAY_COUNTS.flatMap((count, dayOffset) => salesForDay(dayOffset, count));
}

function dayRevenue(dayOffset: number): number {
  return salesForDay(dayOffset, DAY_COUNTS[dayOffset] ?? 18).reduce(
    (sum, r) => sum + Number(r.total_price),
    0
  );
}

function buildTrend(days: number): TrendPoint[] {
  const rand = rng(77);
  const points: TrendPoint[] = [];
  for (let offset = days - 1; offset >= 0; offset--) {
    const date = jktDateOnly(offset);
    if (offset < DAY_COUNTS.length) {
      points.push({
        date,
        revenue: dayRevenue(offset),
        transactions: DAY_COUNTS[offset],
      });
      continue;
    }
    const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
    const weekend = weekday === 0 || weekday === 6;
    const base = weekend ? 520_000 : 390_000;
    const drift = 1 - offset * 0.0025; // gentle growth toward today
    const revenue = Math.round((base * drift + (rand() - 0.5) * 90_000) / 1000) * 1000;
    points.push({
      date,
      revenue: Math.max(120_000, revenue),
      transactions: Math.max(6, Math.round(revenue / 24_000)),
    });
  }
  return points;
}

const EXPENSE_SPECS: [string, string, number, number, ExpenseRow["source"]][] = [
  ["bahan baku", "Belanja biji kopi + gula mingguan", 1_450_000, 1, "receipt"],
  ["operasional", "Gas 3kg x2", 44_000, 1, "manual"],
  ["bahan baku", "Susu UHT 2 dus", 408_000, 3, "receipt"],
  ["operasional", "Listrik & air bulan ini", 850_000, 6, "manual"],
  ["gaji", "Gaji mingguan Sari & Budi", 1_200_000, 7, "manual"],
  ["bahan baku", "Roti & pastry dari Toko Manis", 320_000, 9, "receipt"],
  ["lainnya", "Service mesin espresso", 350_000, 14, "manual"],
  ["operasional", "Kemasan cup + sedotan", 275_000, 18, "receipt"],
  ["bahan baku", "Matcha bubuk 1kg", 385_000, 22, "manual"],
];

function buildExpenses(): ExpenseRow[] {
  return EXPENSE_SPECS.map(([category, description, amount, dayOffset, source], i) => ({
    id: `demo-expense-${i + 1}`,
    amount: String(amount),
    category,
    description,
    source,
    receipt_id: source === "receipt" ? `demo-receipt-${(i % 3) + 1}` : null,
    occurred_at: jktIso(dayOffset, 9 + (i % 6)),
  }));
}

const RECEIPTS: ReceiptRow[] = [
  {
    id: "demo-receipt-1",
    supplier: "Toko Sinar Jaya",
    total_amount: "1450000",
    occurred_at: jktIso(1, 8),
    created_at: jktIso(1, 8),
    image_signed_url: null,
    item_count: 4,
  },
  {
    id: "demo-receipt-2",
    supplier: "CV Susu Segar",
    total_amount: "408000",
    occurred_at: jktIso(3, 10),
    created_at: jktIso(3, 10),
    image_signed_url: null,
    item_count: 2,
  },
  {
    id: "demo-receipt-3",
    supplier: "Toko Manis",
    total_amount: "320000",
    occurred_at: jktIso(9, 7),
    created_at: jktIso(9, 7),
    image_signed_url: null,
    item_count: 3,
  },
];

const ALERTS: AlertRow[] = [
  {
    id: "demo-alert-1",
    type: "low_stock",
    metric: "days_remaining",
    severity: "high",
    message: "Gula Aren: tinggal 1,5 kg — di bawah batas minimum 2 kg",
    related_item_name: "Gula Aren",
    is_acknowledged: false,
    created_at: jktIso(0, 6, 30),
  },
  {
    id: "demo-alert-2",
    type: "low_stock",
    metric: "days_remaining",
    severity: "medium",
    message: "Croissant: ±0,9 hari tersisa (4 pcs @ 4,3/hari)",
    related_item_name: "Croissant",
    is_acknowledged: false,
    created_at: jktIso(1, 23, 30),
  },
  {
    id: "demo-alert-3",
    type: "anomaly",
    metric: "daily_revenue",
    severity: "medium",
    message: "Penjualan kemarin Rp 612.000 — jauh di atas normal (rata-rata 30 hari Rp 421.000, z=3.4)",
    related_item_name: null,
    is_acknowledged: true,
    created_at: jktIso(2, 23, 30),
  },
];

function buildPnl(months: number): PnlMonth[] {
  const rand = rng(2026);
  const out: PnlMonth[] = [];
  const now = new Date(Date.now() + JKT * 3600_000);
  for (let back = months - 1; back >= 0; back--) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
    const month = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
    const revenue = Math.round((9_800_000 + (months - back) * 420_000 + (rand() - 0.5) * 1_400_000) / 1000) * 1000;
    const expenses = Math.round((6_100_000 + (rand() - 0.4) * 900_000) / 1000) * 1000;
    out.push({ month, revenue, expenses, net: revenue - expenses });
  }
  // Current month is partial — scale it down to the elapsed share.
  const last = out[out.length - 1];
  const dayOfMonth = now.getUTCDate();
  const share = Math.min(1, dayOfMonth / 30);
  last.revenue = Math.round((last.revenue * share) / 1000) * 1000;
  last.expenses = Math.round((last.expenses * share) / 1000) * 1000;
  last.net = last.revenue - last.expenses;
  return out;
}

const BUSINESS: Business = {
  id: "demo-business",
  name: "Kopi Kenangan Senja",
  business_type: "cafe",
  owner_phone: "628120001111",
  language_preference: "id",
  timezone: "Asia/Jakarta",
  onboarding_completed_at: jktIso(120, 9),
};

function buildOverview(): Overview {
  const items = buildItems();
  const pnl = buildPnl(6);
  const thisMonth = pnl[pnl.length - 1];
  return {
    business_name: BUSINESS.name,
    today_revenue: dayRevenue(0),
    today_transactions: DAY_COUNTS[0],
    yesterday_revenue: dayRevenue(1),
    month_revenue: thisMonth.revenue,
    month_expenses: thisMonth.expenses,
    month_net: thisMonth.net,
    unacknowledged_alerts: ALERTS.filter((a) => !a.is_acknowledged).length,
    low_stock_items: items.filter((i) => i.below_reorder_threshold).length,
    onboarding_completed: true,
  };
}


// Till sessions and the cash through them (M7-T1 … M7-T3): yesterday Sari
// counted 5.000 short, Budi counted exactly; today's till is still open.
const SHIFTS: ShiftRow[] = [
  {
    id: "demo-shift-1",
    staff_id: "demo-staff-2",
    staff_name: "Sari",
    status: "open",
    opening_float: "200000",
    opened_at: jktIso(0, 7),
    closed_at: null,
    closed_by: null,
    cash_sales: "268000",
    cash_refunds: "0",
    cash_in: "0",
    cash_out: "35000",
    expected_cash: "433000",
    counted_cash: null,
    variance: null,
    notes: null,
  },
  {
    id: "demo-shift-2",
    staff_id: "demo-staff-2",
    staff_name: "Sari",
    status: "closed",
    opening_float: "200000",
    opened_at: jktIso(1, 7),
    closed_at: jktIso(1, 21),
    closed_by: "demo-staff-1",
    cash_sales: "512000",
    cash_refunds: "22000",
    cash_in: "50000",
    cash_out: "90000",
    expected_cash: "650000",
    counted_cash: "645000",
    variance: "-5000",
    notes: "kurang 5rb, mungkin kembalian",
  },
  {
    id: "demo-shift-3",
    staff_id: "demo-staff-3",
    staff_name: "Budi",
    status: "closed",
    opening_float: "150000",
    opened_at: jktIso(2, 13),
    closed_at: jktIso(2, 21),
    closed_by: "demo-staff-3",
    cash_sales: "324000",
    cash_refunds: "0",
    cash_in: "0",
    cash_out: "120000",
    expected_cash: "354000",
    counted_cash: "354000",
    variance: "0",
    notes: null,
  },
];

const CASH_MOVEMENTS: CashMovementRow[] = [
  {
    id: "demo-cash-1",
    shift_id: "demo-shift-1",
    staff_name: "Sari",
    kind: "petty_cash",
    via: "cash",
    direction: "out",
    amount: "35000",
    reason: "es batu 3 balok",
    category: "operasional",
    supplier_name: null,
    occurred_at: jktIso(0, 10),
  },
  {
    id: "demo-cash-2",
    shift_id: "demo-shift-2",
    staff_name: "Sari",
    kind: "cash_in",
    via: "owner",
    direction: "in",
    amount: "50000",
    reason: "tambah modal receh",
    category: null,
    supplier_name: null,
    occurred_at: jktIso(1, 11),
  },
  {
    id: "demo-cash-3",
    shift_id: "demo-shift-2",
    staff_name: "Sari",
    kind: "supplier_payment",
    via: "cash",
    direction: "out",
    amount: "90000",
    reason: "bayar susu",
    category: null,
    supplier_name: "CV Susu Segar",
    occurred_at: jktIso(1, 15),
  },
  {
    id: "demo-cash-4",
    shift_id: "demo-shift-3",
    staff_name: "Budi",
    kind: "bank_drop",
    via: "cash",
    direction: "out",
    amount: "120000",
    reason: "setor ke bank",
    category: null,
    supplier_name: null,
    occurred_at: jktIso(2, 20),
  },
];

const PRICING: PricingSettings = {
  tax_rate: "0.1000",
  tax_inclusive: true,
  service_charge_rate: "0.0000",
  service_before_tax: true,
  rounding_unit: "100.00",
  rounding_mode: "nearest",
  discount_requires_pin: true,
};

const CUSTOMERS: CustomerRow[] = [
  { id: "demo-cust-1", name: "Andi Wijaya", phone: "6281234567890", address: "Jl. Melati 3", birthday: "1990-05-17", notes: "suka kopi susu, gula sedikit", is_active: true, visits: 14, total_spent: "412000", points_balance: 312, last_visit: jktIso(0, 9), created_at: jktIso(80, 10) },
  { id: "demo-cust-2", name: "Rina Kartika", phone: "6281322223333", address: null, birthday: null, notes: null, is_active: true, visits: 6, total_spent: "168000", points_balance: 48, last_visit: jktIso(2, 16), created_at: jktIso(40, 12) },
  { id: "demo-cust-3", name: "Pak Budi (kantor sebelah)", phone: "6281200001111", address: "Ruko Sentra 12", birthday: "1978-11-02", notes: "pesan rame-rame tiap Jumat", is_active: true, visits: 9, total_spent: "945000", points_balance: 905, last_visit: jktIso(4, 12), created_at: jktIso(60, 9) },
  { id: "demo-cust-4", name: "Ibu tanpa nomor", phone: null, address: null, birthday: null, notes: null, is_active: true, visits: 1, total_spent: "22000", points_balance: 0, last_visit: jktIso(9, 8), created_at: jktIso(9, 8) },
];

const LOYALTY: LoyaltySettings = { is_active: true, rupiah_per_point: "1000.00", point_value: "100.00", min_redeem_points: 10 };

const PROMOS: PromoRow[] = [
  {
    id: "demo-promo-1", name: "Beli 1 gratis 1 Kopi Arabica (sore)", kind: "bonus_item", value: "0", item_id: "demo-item-4",
    bonus_item_id: null, bonus_quantity: "1.000", max_per_order: 2, is_active: true, created_at: jktIso(20, 9),
    conditions: [
      { kind: "day_of_week", starts_at: null, ends_at: null, days_of_week: [0, 1, 2, 3, 4], time_start: null, time_end: null, amount: null, quantity: null },
      { kind: "time_window", starts_at: null, ends_at: null, days_of_week: null, time_start: "14:00:00", time_end: "17:00:00", amount: null, quantity: null },
    ],
    applications: 37, given_away: "740000",
  },
  {
    id: "demo-promo-2", name: "Jumat Nasi Goreng 10%", kind: "percent_off", value: "0.1000", item_id: "demo-item-6",
    bonus_item_id: null, bonus_quantity: "1.000", max_per_order: null, is_active: true, created_at: jktIso(12, 9),
    conditions: [{ kind: "day_of_week", starts_at: null, ends_at: null, days_of_week: [4], time_start: null, time_end: null, amount: null, quantity: null }],
    applications: 9, given_away: "31500",
  },
];

const VOUCHERS: VoucherRow[] = [
  { id: "demo-v-1", code: "SELAMAT-DATANG", kind: "amount_off", value: "5000.0000", max_discount: null, min_spend: "25000.00", starts_at: null, expires_at: jktIso(-60, 0), max_uses: 100, uses: 23, batch_id: null, batch_name: null, is_active: true, created_at: jktIso(30, 9) },
  { id: "demo-v-2", code: "SENJA-7KQ2MN4P", kind: "percent_off", value: "0.2000", max_discount: "15000.00", min_spend: "0.00", starts_at: null, expires_at: jktIso(-30, 0), max_uses: 1, uses: 1, batch_id: "demo-batch-1", batch_name: "Flyer September", is_active: true, created_at: jktIso(10, 9) },
  { id: "demo-v-3", code: "SENJA-B3XW9RTD", kind: "percent_off", value: "0.2000", max_discount: "15000.00", min_spend: "0.00", starts_at: null, expires_at: jktIso(-30, 0), max_uses: 1, uses: 0, batch_id: "demo-batch-1", batch_name: "Flyer September", is_active: true, created_at: jktIso(10, 9) },
];

function paginate<T>(rows: T[], page: number, pageSize: number, inflateTotal = 0): Page<T> {
  const start = (page - 1) * pageSize;
  return {
    total: rows.length + inflateTotal,
    page,
    page_size: pageSize,
    rows: rows.slice(start, start + pageSize),
  };
}

/** Fixture for an API path, or null when the path has no demo equivalent. */
export function demoData(path: string): unknown {
  const [pathname, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  const num = (key: string, fallback: number) => Number(params.get(key) ?? fallback) || fallback;

  if (pathname === "/api/overview") return buildOverview();
  if (pathname === "/api/sales-trend") return buildTrend(num("days", 30));
  if (pathname === "/api/items") return buildItems();
  if (pathname === "/api/pnl") return buildPnl(num("months", 6));
  if (pathname === "/api/alerts") return ALERTS.slice(0, num("limit", 50));
  if (pathname === "/api/business") return BUSINESS;
  if (pathname === "/auth/staff") return STAFF;
  if (pathname === "/api/sales") {
    return paginate(allSales(), num("page", 1), num("page_size", 40), 128);
  }
  if (pathname === "/api/expenses") {
    return paginate(buildExpenses(), num("page", 1), num("page_size", 20));
  }
  if (pathname === "/api/receipts") {
    return paginate(RECEIPTS, num("page", 1), num("page_size", 6));
  }
  if (pathname === "/api/pricing-settings") return PRICING;
  if (pathname === "/api/loyalty-settings") return LOYALTY;
  if (pathname === "/api/promos") return PROMOS;
  if (pathname === "/api/vouchers") {
    const q = (params.get("q") ?? "").toUpperCase();
    return VOUCHERS.filter((v) => !q || v.code.includes(q));
  }
  if (/^\/api\/customers\/[^/]+\/points$/.test(pathname)) return [];
  if (pathname === "/api/customers") {
    const q = (params.get("q") ?? "").toLowerCase();
    const rows = CUSTOMERS.filter((c) => !q || c.name.toLowerCase().includes(q) || (c.phone ?? "").includes(q.replace(/\D/g, "")));
    return paginate(rows, num("page", 1), num("page_size", 30));
  }
  if (pathname === "/api/shifts") return SHIFTS.slice(0, num("limit", 30));
  if (pathname === "/api/cash-movements") return CASH_MOVEMENTS.slice(0, num("limit", 50));
  return null;
}
