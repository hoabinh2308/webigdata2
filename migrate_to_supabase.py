"""Tạo bảng trên Supabase và nạp dữ liệu từ data/products_clean.csv (chạy 1 lần).
    python tools/migrate_to_supabase.py
Cần biến DATABASE_URL trong .env. Chạy lại an toàn: nếu đã có dữ liệu thì dừng."""
import os, sys
import pandas as pd, psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
DSN = os.environ["DATABASE_URL"]
CSV = os.path.join(ROOT, "data", "products_clean.csv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seller(
  seller_id VARCHAR(30) PRIMARY KEY, seller_name VARCHAR(100) NOT NULL,
  seller_province VARCHAR(50) NOT NULL, seller_region VARCHAR(20) NOT NULL);
CREATE TABLE IF NOT EXISTS brand(brand_id SERIAL PRIMARY KEY, brand_name VARCHAR(150) NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS category(category_id SERIAL PRIMARY KEY, category_name VARCHAR(100) NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS product(
  product_id SERIAL PRIMARY KEY, source_product_id BIGINT NOT NULL, name TEXT NOT NULL,
  seller_id VARCHAR(30) NOT NULL REFERENCES seller(seller_id),
  brand_id INT NOT NULL REFERENCES brand(brand_id),
  category_id INT NOT NULL REFERENCES category(category_id),
  source_category TEXT, search_keyword TEXT,
  price NUMERIC(15,0) NOT NULL CHECK (price > 0),
  original_price NUMERIC(15,0) NOT NULL,
  discount_rate NUMERIC(5,2) NOT NULL CHECK (discount_rate BETWEEN 0 AND 100),
  rating NUMERIC(2,1) CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
  review_count INT NOT NULL CHECK (review_count >= 0), sold INT NOT NULL CHECK (sold >= 0),
  loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(), CHECK (original_price >= price));
CREATE INDEX IF NOT EXISTS ix_product_seller ON product(seller_id);
CREATE INDEX IF NOT EXISTS ix_product_brand ON product(brand_id);
CREATE INDEX IF NOT EXISTS ix_product_category ON product(category_id);
CREATE TABLE IF NOT EXISTS app_users(id SERIAL PRIMARY KEY, username VARCHAR(60) UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, full_name VARCHAR(100),
  role VARCHAR(10) NOT NULL CHECK (role IN ('admin','analyst','viewer')));
CREATE OR REPLACE VIEW vw_product_full WITH (security_invoker = true) AS
SELECT p.product_id AS pid, s.seller_id, s.seller_name, s.seller_province, s.seller_region,
       p.source_product_id, p.name, b.brand_name AS brand, c.category_name AS category,
       p.source_category, p.search_keyword, p.price::float8 AS price,
       p.original_price::float8 AS original_price, p.discount_rate::float8 AS discount_rate,
       p.rating::float8 AS rating, p.review_count, p.sold
FROM product p JOIN seller s ON s.seller_id = p.seller_id
JOIN brand b ON b.brand_id = p.brand_id JOIN category c ON c.category_id = p.category_id;
-- Bảo mật: chặn API công khai (anon key) của Supabase đọc/ghi các bảng này.
ALTER TABLE seller ENABLE ROW LEVEL SECURITY; ALTER TABLE brand ENABLE ROW LEVEL SECURITY;
ALTER TABLE category ENABLE ROW LEVEL SECURITY; ALTER TABLE product ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON seller, brand, category, product, app_users, vw_product_full FROM anon, authenticated;
  END IF;
END $$;
"""

def py(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    return v.item() if hasattr(v, "item") else v

def main():
    df = pd.read_csv(CSV, encoding="utf-8-sig", dtype={"seller_id": str}).rename(columns={"id": "source_product_id"})
    kw = {} if "sslmode" in DSN else {"sslmode": "require"}
    conn = psycopg2.connect(DSN, connect_timeout=15, **kw)
    with conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute("SELECT COUNT(*) FROM product")
        if cur.fetchone()[0] > 0:
            print("Bảng product đã có dữ liệu, bỏ qua nạp. (Muốn nạp lại: TRUNCATE product, brand, category, seller RESTART IDENTITY CASCADE)")
            return
        sellers = df[["seller_id", "seller_name", "seller_province", "seller_region"]].drop_duplicates("seller_id")
        execute_values(cur, "INSERT INTO seller VALUES %s", [tuple(map(py, r)) for r in sellers.itertuples(index=False)])
        execute_values(cur, "INSERT INTO brand(brand_name) VALUES %s", [(b,) for b in sorted(df["brand"].dropna().unique())])
        execute_values(cur, "INSERT INTO category(category_name) VALUES %s", [(c,) for c in sorted(df["category"].dropna().unique())])
        cur.execute("SELECT brand_name, brand_id FROM brand"); bmap = dict(cur.fetchall())
        cur.execute("SELECT category_name, category_id FROM category"); cmap = dict(cur.fetchall())
        rows = [(py(r.source_product_id), py(r.name), py(r.seller_id), bmap[r.brand], cmap[r.category],
                 py(r.source_category), py(r.search_keyword), py(r.price), py(r.original_price),
                 py(r.discount_rate), py(r.rating), py(r.review_count), py(r.sold))
                for r in df.itertuples(index=False)]
        execute_values(cur, """INSERT INTO product(source_product_id,name,seller_id,brand_id,category_id,source_category,
            search_keyword,price,original_price,discount_rate,rating,review_count,sold) VALUES %s""", rows, page_size=1000)
        print(f"Đã nạp: {len(sellers)} gian hàng, {len(bmap)} thương hiệu, {len(cmap)} danh mục, {len(rows)} sản phẩm.")

if __name__ == "__main__":
    main()
