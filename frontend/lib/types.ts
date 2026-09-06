export type Overview = {
  business_name: string;
  today_revenue: number;
  today_transactions: number;
  yesterday_revenue: number;
  month_revenue: number;
  month_expenses: number;
  month_net: number;
  unacknowledged_alerts: number;
  low_stock_items: number;
  onboarding_completed: boolean;
};

export type TrendPoint = { date: string; revenue: number; transactions: number };

export type SaleRow = {
  id: string;
  item_name: string;
  staff_name: string;
  quantity: string;
  unit_price: string;
  total_price: string;
  sold_at: string;
};

export type Page<T> = { total: number; page: number; page_size: number; rows: T[] };

export type InventoryItem = {
  id: string;
  name: string;
  unit: string;
  current_stock: string;
  cost_price: string;
  sell_price: string;
  reorder_threshold: string;
  avg_daily_usage: number | null;
  days_remaining: number | null;
  below_reorder_threshold: boolean;
};

export type ExpenseRow = {
  id: string;
  amount: string;
  category: string | null;
  description: string | null;
  source: string;
  receipt_id: string | null;
  occurred_at: string;
};

export type PnlMonth = { month: string; revenue: number; expenses: number; net: number };

export type AlertRow = {
  id: string;
  type: "anomaly" | "low_stock";
  metric: string | null;
  severity: "low" | "medium" | "high";
  message: string;
  related_item_name: string | null;
  is_acknowledged: boolean;
  created_at: string;
};

export type ReceiptRow = {
  id: string;
  supplier: string | null;
  total_amount: string | null;
  occurred_at: string | null;
  created_at: string;
  image_signed_url: string | null;
  item_count: number;
};

export type Business = {
  id: string;
  name: string;
  business_type: string;
  owner_phone: string;
  language_preference: string;
  timezone: string;
  onboarding_completed_at: string | null;
};

export type StaffMember = {
  id: string;
  name: string;
  role: "owner" | "staff";
  is_active: boolean;
  created_at: string;
};

// Till sessions and the cash that moved through them (M7-T1 … M7-T3).
export type ShiftRow = {
  id: string;
  staff_id: string;
  staff_name: string;
  status: "open" | "closed";
  opening_float: string;
  opened_at: string;
  closed_at: string | null;
  closed_by: string | null;
  cash_sales: string;
  cash_refunds: string;
  cash_in: string;
  cash_out: string;
  expected_cash: string | null;
  counted_cash: string | null;
  variance: string | null;
  notes: string | null;
};

export type CashMovementRow = {
  id: string;
  shift_id: string | null;
  staff_name: string | null;
  kind: "cash_in" | "petty_cash" | "supplier_payment" | "bank_drop";
  via: string;
  direction: "in" | "out";
  amount: string;
  reason: string;
  category: string | null;
  supplier_name: string | null;
  occurred_at: string;
};

// How a bill is built (M7-T4). Rates are fractions (0.11 = 11%).
export type PricingSettings = {
  tax_rate: string;
  tax_inclusive: boolean;
  service_charge_rate: string;
  service_before_tax: boolean;
  rounding_unit: string;
  rounding_mode: "nearest" | "up" | "down";
  discount_requires_pin: boolean;
};

// Customers (M8-T1). History is derived from orders by the API.
export type CustomerRow = {
  id: string;
  name: string;
  phone: string | null;
  address: string | null;
  birthday: string | null;
  notes: string | null;
  is_active: boolean;
  visits: number;
  total_spent: string;
  last_visit: string | null;
  points_balance: number;
  created_at: string;
};

// Points programme (M8-T2).
export type LoyaltySettings = {
  is_active: boolean;
  rupiah_per_point: string;
  point_value: string;
  min_redeem_points: number;
};

export type PointsMovementRow = {
  id: string;
  points_delta: number;
  reason: "earn" | "redeem" | "adjust" | "reversal" | "expire";
  source_type: string | null;
  source_id: string | null;
  amount: string;
  notes: string | null;
  created_at: string;
};

// Promos (M8-T3).
export type PromoCondition = {
  kind: "date_range" | "day_of_week" | "time_window" | "min_spend" | "multiples";
  starts_at: string | null;
  ends_at: string | null;
  days_of_week: number[] | null;
  time_start: string | null;
  time_end: string | null;
  amount: string | null;
  quantity: string | null;
};

export type PromoRow = {
  id: string;
  name: string;
  kind: "percent_off" | "amount_off" | "bonus_item";
  value: string;
  item_id: string | null;
  bonus_item_id: string | null;
  bonus_quantity: string;
  max_per_order: number | null;
  is_active: boolean;
  created_at: string;
  conditions: PromoCondition[];
  applications: number;
  given_away: string;
};

// Vouchers (M8-T4).
export type VoucherRow = {
  id: string;
  code: string;
  kind: "percent_off" | "amount_off";
  value: string;
  max_discount: string | null;
  min_spend: string;
  starts_at: string | null;
  expires_at: string | null;
  max_uses: number;
  uses: number;
  batch_id: string | null;
  batch_name: string | null;
  is_active: boolean;
  created_at: string;
};
