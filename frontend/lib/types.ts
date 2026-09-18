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
  prep_station?: PrepStation | null; // prt-2: null = not decided yet
};

export type PrepStation = "bar" | "kitchen" | "none";
export const PREP_STATION_LABEL: Record<PrepStation, string> = {
  bar: "Bar (depan)",
  kitchen: "Dapur",
  none: "Tanpa persiapan",
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
  type: "anomaly" | "low_stock" | "margin_drop" | "stockout_risk" | "void_rate" | "supplier_price";
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
  /** M15-T4: the hour the business day starts (0..23). 0 = calendar day. */
  day_start_hour: number;
  onboarding_completed_at: string | null;
};

export type StaffMember = {
  id: string;
  name: string;
  /** M15-T7: `manager` approves voids, refunds and discounts at the till
   *  and nothing else — the dashboard needs owner scope. */
  role: "owner" | "staff" | "manager";
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

/** One manager authorisation (M15-T7): a void, a refund, or a discount that
 *  needed a PIN. `approver_role` is the role held when it was approved. */
export type ApprovalRow = {
  id: string;
  order_id: string | null;
  action: "discount" | "void" | "refund";
  approved_by: string;
  approver_name: string;
  approver_role: string;
  requested_by: string | null;
  requested_by_name: string | null;
  amount: string | null;
  note: string | null;
  created_at: string;
};

/** Orders as the owner searches them (M15-T11). `number` is the receipt's short
 *  reference — the last eight characters of the id — which is what a customer
 *  can actually read off a printed slip. */
export type OrderRow = {
  id: string;
  number: string;
  sold_at: string;
  status: "completed" | "voided" | "refunded";
  order_type: string;
  total: string;
  line_count: number;
  staff_name: string | null;
  customer_name: string | null;
  table_label: string | null;
  /** M15-T10: "manual_backdated" was typed in afterwards from a paper slip. */
  entry_source: "live" | "manual_backdated";
  order_no?: string; // prt-1: daily service number, "" for orders before it existed
  service_date?: string | null;
};

export type OrdersPage = { total: number; rows: OrderRow[] };

export type ReceiptLine = {
  name: string;
  variant: string | null;
  quantity: string;
  unit_price: string;
  line_total: string;
  modifiers: { name: string; price_delta: string }[];
  notes: string | null;
};

/** The same shape the till prints, from the same endpoint shaping function. */
export type Receipt = {
  order_id: string;
  number: string;
  business_name: string;
  staff_name: string | null;
  customer_name: string | null;
  status: string;
  order_type: string;
  sold_at: string;
  lines: ReceiptLine[];
  subtotal: string;
  total: string;
};

export type ReversalResult = {
  order_id: string;
  status: "voided" | "refunded";
  reversing_lines: { id: string; item_id: string; quantity: string; line_total: string; stock_after: string | null }[];
  reversing_payments: { id: string; method: string; amount: string; reference: string | null }[];
};

/** M15-T12: one subject currently being counted for wrong PINs. `who` is
 *  already resolved to a name by the server — a uuid in a list the owner is
 *  meant to act on is not information. */
export type PinLockoutRow = {
  id: string;
  scope: "pos_login" | "pos_device" | "manager_pin";
  who: string;
  failures: number;
  first_failed_at: string;
  last_failed_at: string;
  locked_until: string | null;
  locked_now: boolean;
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
  service_applies_to: OrderType[]; // M11-T3: where the service charge applies
  delivery_fee: string;                    // flat fee on delivery orders
};
export type OrderType = "dine_in" | "takeaway" | "delivery" | "pickup";
export const ORDER_TYPE_LABEL: Record<OrderType, string> = {
  dine_in: "Makan di tempat",
  takeaway: "Bawa pulang",
  delivery: "Antar",
  pickup: "Ambil sendiri",
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
