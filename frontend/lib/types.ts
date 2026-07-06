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
