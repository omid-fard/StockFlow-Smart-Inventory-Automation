PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS organizations (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, sector TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS warehouses (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
  code TEXT NOT NULL, name TEXT NOT NULL, UNIQUE(org_id,code)
);
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
  sku TEXT NOT NULL, name TEXT NOT NULL,
  unit TEXT NOT NULL CHECK(unit IN ('pcs','kg','L','m')),
  precision_digits INTEGER NOT NULL CHECK(precision_digits BETWEEN 0 AND 3),
  safety_milli INTEGER NOT NULL DEFAULT 0 CHECK(safety_milli>=0),
  minimum_milli INTEGER NOT NULL DEFAULT 0 CHECK(minimum_milli>=0),
  lead_days INTEGER NOT NULL DEFAULT 7 CHECK(lead_days>0),
  review_days INTEGER NOT NULL DEFAULT 7 CHECK(review_days>0),
  UNIQUE(org_id,sku)
);
CREATE TABLE IF NOT EXISTS stock_events (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
  warehouse_id TEXT NOT NULL REFERENCES warehouses(id),
  product_id TEXT NOT NULL REFERENCES products(id),
  event_key TEXT NOT NULL, event_type TEXT NOT NULL,
  delta_milli INTEGER NOT NULL CHECK(delta_milli<>0),
  event_date TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(org_id,event_key)
);
CREATE INDEX IF NOT EXISTS idx_stock_lookup ON stock_events(org_id,warehouse_id,product_id);
CREATE INDEX IF NOT EXISTS idx_stock_date ON stock_events(org_id,event_date);
CREATE TABLE IF NOT EXISTS reservations (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
  reservation_ref TEXT NOT NULL, warehouse_id TEXT NOT NULL REFERENCES warehouses(id),
  product_id TEXT NOT NULL REFERENCES products(id),
  quantity_milli INTEGER NOT NULL CHECK(quantity_milli>0),
  status TEXT NOT NULL CHECK(status IN ('ACTIVE','RELEASED','CONSUMED')),
  created_at TEXT NOT NULL, UNIQUE(org_id,reservation_ref)
);
CREATE INDEX IF NOT EXISTS idx_reservation_lookup ON reservations(org_id,warehouse_id,product_id,status);
CREATE TABLE IF NOT EXISTS sales (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
  sale_ref TEXT NOT NULL, warehouse_id TEXT NOT NULL REFERENCES warehouses(id),
  sold_date TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(org_id,sale_ref)
);
CREATE TABLE IF NOT EXISTS sale_lines (
  id TEXT PRIMARY KEY, sale_id TEXT NOT NULL REFERENCES sales(id),
  product_id TEXT NOT NULL REFERENCES products(id),
  quantity_milli INTEGER NOT NULL CHECK(quantity_milli>0),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents>=0)
);
CREATE TABLE IF NOT EXISTS purchase_orders (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
  po_ref TEXT NOT NULL, warehouse_id TEXT NOT NULL REFERENCES warehouses(id),
  supplier TEXT NOT NULL, expected_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ORDERED','PARTIAL','RECEIVED')),
  fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(org_id,po_ref)
);
CREATE TABLE IF NOT EXISTS po_lines (
  id TEXT PRIMARY KEY, po_id TEXT NOT NULL REFERENCES purchase_orders(id),
  product_id TEXT NOT NULL REFERENCES products(id),
  ordered_milli INTEGER NOT NULL CHECK(ordered_milli>0),
  received_milli INTEGER NOT NULL DEFAULT 0 CHECK(received_milli>=0 AND received_milli<=ordered_milli),
  UNIQUE(po_id,product_id)
);
CREATE TABLE IF NOT EXISTS purchase_receipts (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
  po_id TEXT NOT NULL REFERENCES purchase_orders(id), delivery_ref TEXT NOT NULL,
  fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(org_id,delivery_ref)
);
