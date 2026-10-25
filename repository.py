"""Lớp lấy dữ liệu. Phần còn lại của web chỉ gọi các method của lớp này.
Hiện tại: CsvRepository. Sau này: viết SqlServerRepository với CÙNG các method
(search, get, create, update, delete, options, dashboard_data, summary) rồi
đổi DATA_SOURCE trong .env, không phải sửa chỗ khác."""
import os, shutil, threading
from contextlib import contextmanager
import pandas as pd

COLS = ["pid", "seller_id", "seller_name", "seller_province", "seller_region",
        "source_product_id", "name", "brand", "category", "source_category",
        "search_keyword", "price", "original_price", "discount_rate", "rating",
        "review_count", "sold"]
EDITABLE = ["name", "seller_id", "brand", "category", "price", "original_price",
            "rating", "review_count", "sold"]
BINS = [0, 1e5, 5e5, 1e6, 5e6, 2e7, 5e7, float("inf")]
BIN_LABELS = ["Dưới 100 nghìn", "100 - 500 nghìn", "500 nghìn - 1 triệu",
              "1 - 5 triệu", "5 - 20 triệu", "20 - 50 triệu", "Trên 50 triệu"]


def _clean(v):
    if pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v


class CsvRepository:
    def __init__(self, source, working):
        self.working = working
        self.lock = threading.RLock()
        if not os.path.exists(working):
            raw = pd.read_csv(source, encoding="utf-8-sig", dtype={"seller_id": str})
            raw = raw.rename(columns={"id": "source_product_id"})
            raw.insert(0, "pid", range(1, len(raw) + 1))
            raw[COLS].to_csv(working, index=False, encoding="utf-8-sig")
        self.df = pd.read_csv(working, encoding="utf-8-sig", dtype={"seller_id": str})

    def _save(self):
        self.df[COLS].to_csv(self.working, index=False, encoding="utf-8-sig")

    # ---------- đọc ----------
    def search(self, f, page=1, per_page=20):
        d = self.df
        if f.get("q"):
            d = d[d["name"].str.contains(f["q"], case=False, na=False, regex=False)]
        for key, col in (("category", "category"), ("brand", "brand"),
                         ("province", "seller_province"), ("region", "seller_region")):
            if f.get(key):
                d = d[d[col] == f[key]]
        if f.get("price_min") not in (None, ""):
            d = d[d["price"] >= float(f["price_min"])]
        if f.get("price_max") not in (None, ""):
            d = d[d["price"] <= float(f["price_max"])]
        total = len(d)
        part = d.iloc[(page - 1) * per_page: page * per_page]
        rows = [{k: _clean(v) for k, v in r.items()} for r in part.to_dict("records")]
        return rows, total

    def get(self, pid):
        m = self.df[self.df["pid"] == pid]
        if m.empty:
            return None
        return {k: _clean(v) for k, v in m.iloc[0].to_dict().items()}

    def options(self):
        d = self.df
        sellers = (d[["seller_id", "seller_name"]].drop_duplicates()
                   .sort_values("seller_name").to_dict("records"))
        return {
            "categories": sorted(d["category"].dropna().unique()),
            "brands": sorted(d["brand"].dropna().unique()),
            "provinces": sorted(d["seller_province"].dropna().unique()),
            "regions": sorted(d["seller_region"].dropna().unique()),
            "sellers": sellers,
        }

    # ---------- ghi ----------
    def _fill(self, row, data):
        for k in EDITABLE:
            row[k] = data[k]
        s = self.df[self.df["seller_id"] == data["seller_id"]].iloc[0]
        row["seller_name"], row["seller_province"], row["seller_region"] = (
            s["seller_name"], s["seller_province"], s["seller_region"])
        row["discount_rate"] = round(
            (1 - row["price"] / row["original_price"]) * 100, 2)
        return row

    def create(self, data):
        with self.lock:
            pid = int(self.df["pid"].max()) + 1
            row = {c: None for c in COLS}
            row.update(pid=pid, source_product_id=0, category=data["category"],
                       source_category=None, search_keyword=None)
            row = self._fill(row, data)
            self.df = pd.concat([self.df, pd.DataFrame([row])], ignore_index=True)
            self._save()
            return pid

    def update(self, pid, data):
        with self.lock:
            idx = self.df.index[self.df["pid"] == pid]
            if len(idx) == 0:
                return False
            row = self._fill(self.df.loc[idx[0]].to_dict(), data)
            for k, v in row.items():
                self.df.at[idx[0], k] = v
            self._save()
            return True

    def delete(self, pid):
        with self.lock:
            n = len(self.df)
            self.df = self.df[self.df["pid"] != pid]
            self._save()
            return len(self.df) < n

    # ---------- thống kê ----------
    def summary(self):
        """Doanh thu tạm tính = price × sold. Sau này đối chiếu với M_Revenue của Power BI."""
        d = self.df.assign(revenue=self.df["price"] * self.df["sold"])
        top_brands = (d.groupby("brand").agg(products=("pid", "count"), sold=("sold", "sum"),
                      revenue=("revenue", "sum")).sort_values("products", ascending=False)
                      .head(10).reset_index())
        by_prov = (d.groupby("seller_province").agg(products=("pid", "count"),
                   revenue=("revenue", "sum")).sort_values("revenue", ascending=False).reset_index())
        by_prov["share"] = by_prov["revenue"] / by_prov["revenue"].sum() * 100
        by_cat = (d.groupby("category").agg(products=("pid", "count"), sold=("sold", "sum"),
                  revenue=("revenue", "sum")).sort_values("products", ascending=False).reset_index())
        by_seller = (d.groupby("seller_name").agg(products=("pid", "count"), sold=("sold", "sum"),
                     revenue=("revenue", "sum")).sort_values("revenue", ascending=False).reset_index())
        return {
            "kpi": {"products": int(len(d)), "avg_price": float(d["price"].mean()),
                    "revenue": float(d["revenue"].sum()), "sold": int(d["sold"].sum())},
            "top_brands": top_brands, "by_province": by_prov,
            "by_category": by_cat, "by_seller": by_seller,
        }

    def dashboard_data(self):
        s = self.summary()
        d = self.df
        bucket = pd.cut(d["price"], BINS, labels=BIN_LABELS, right=False).value_counts()
        return {
            "kpi": s["kpi"],
            "category": {"labels": s["by_category"]["category"].tolist(),
                         "values": s["by_category"]["products"].tolist()},
            "price": {"labels": BIN_LABELS, "values": [int(bucket.get(l, 0)) for l in BIN_LABELS]},
            "province": {"labels": s["by_province"]["seller_province"].tolist(),
                         "values": [round(v / 1e9, 1) for v in s["by_province"]["revenue"]]},
            "brands": {"labels": s["top_brands"]["brand"].tolist(),
                       "values": s["top_brands"]["products"].tolist()},
            "sellers": {"labels": s["by_seller"]["seller_name"].head(8).tolist(),
                        "revenue": [round(v / 1e9, 1) for v in s["by_seller"]["revenue"].head(8)],
                        "sold": s["by_seller"]["sold"].head(8).astype(int).tolist()},
        }


class SqlServerRepository(CsvRepository):
    """Đọc/ghi SQL Server (CSDL ProductAnalytics). Đọc qua view dbo.vw_ProductFull,
    ghi vào bảng Product. Kế thừa phần tìm kiếm và thống kê từ CsvRepository."""
    NUM = ["price", "original_price", "discount_rate", "rating"]

    def __init__(self, conn_str):
        self.conn_str = conn_str
        self.lock = threading.RLock()
        self._reload()

    def _conn(self):
        import pyodbc
        return pyodbc.connect(self.conn_str)

    def _reload(self):
        with self._conn() as c:
            cur = c.cursor()
            cur.execute("SELECT * FROM dbo.vw_ProductFull ORDER BY pid")
            cols = [x[0] for x in cur.description]
            df = pd.DataFrame.from_records(cur.fetchall(), columns=cols)
        df["seller_id"] = df["seller_id"].astype(str)
        for k in self.NUM:
            df[k] = pd.to_numeric(df[k], errors="coerce")
        for k in ("pid", "source_product_id", "review_count", "sold"):
            df[k] = pd.to_numeric(df[k]).astype("int64")
        self.df = df[COLS]

    @staticmethod
    def _ensure_brand(cur, brand):
        cur.execute("IF NOT EXISTS (SELECT 1 FROM dbo.Brand WHERE brand_name=?) "
                    "INSERT INTO dbo.Brand(brand_name) VALUES(?)", brand, brand)

    @staticmethod
    def _disc(d):
        return round((1 - d["price"] / d["original_price"]) * 100, 2)

    def create(self, d):
        with self.lock, self._conn() as c:
            cur = c.cursor()
            self._ensure_brand(cur, d["brand"])
            cur.execute("""INSERT INTO dbo.Product(source_product_id,name,seller_id,brand_id,category_id,
                              price,original_price,discount_rate,rating,review_count,sold)
                           OUTPUT INSERTED.product_id
                           SELECT 0,?,?,b.brand_id,c.category_id,?,?,?,?,?,?
                           FROM dbo.Brand b, dbo.Category c
                           WHERE b.brand_name=? AND c.category_name=?""",
                        d["name"], d["seller_id"], d["price"], d["original_price"], self._disc(d),
                        d["rating"], d["review_count"], d["sold"], d["brand"], d["category"])
            pid = cur.fetchone()[0]
            c.commit()
        self._reload()
        return int(pid)

    def update(self, pid, d):
        with self.lock, self._conn() as c:
            cur = c.cursor()
            self._ensure_brand(cur, d["brand"])
            cur.execute("""UPDATE p SET name=?, seller_id=?, brand_id=b.brand_id, category_id=c.category_id,
                              price=?, original_price=?, discount_rate=?, rating=?, review_count=?, sold=?
                           FROM dbo.Product p, dbo.Brand b, dbo.Category c
                           WHERE p.product_id=? AND b.brand_name=? AND c.category_name=?""",
                        d["name"], d["seller_id"], d["price"], d["original_price"], self._disc(d),
                        d["rating"], d["review_count"], d["sold"], pid, d["brand"], d["category"])
            ok = cur.rowcount > 0
            c.commit()
        self._reload()
        return ok

    def delete(self, pid):
        with self.lock, self._conn() as c:
            cur = c.cursor()
            cur.execute("DELETE FROM dbo.Product WHERE product_id=?", pid)
            ok = cur.rowcount > 0
            c.commit()
        self._reload()
        return ok


class PostgresRepository:
    """Supabase (Postgres). Không cache dữ liệu, mỗi request truy vấn trực tiếp
    để chạy đúng trên serverless (Vercel có nhiều instance chạy song song)."""
    REV = "SUM(price*sold)::float8"

    def __init__(self, dsn):
        self.dsn = dsn

    @contextmanager
    def _cur(self):
        import psycopg2, psycopg2.extras
        kw = {} if "sslmode" in self.dsn else {"sslmode": "require"}
        conn = psycopg2.connect(self.dsn, connect_timeout=10, **kw)
        try:
            with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                yield cur
        finally:
            conn.close()

    @staticmethod
    def _where(f):
        cl, a = [], []
        if f.get("q"):
            q = f["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            cl.append("name ILIKE %s"); a.append(f"%{q}%")
        for key, col in (("category", "category"), ("brand", "brand"),
                         ("province", "seller_province"), ("region", "seller_region")):
            if f.get(key):
                cl.append(f"{col} = %s"); a.append(f[key])
        if f.get("price_min") not in (None, ""):
            cl.append("price >= %s"); a.append(float(f["price_min"]))
        if f.get("price_max") not in (None, ""):
            cl.append("price <= %s"); a.append(float(f["price_max"]))
        return ((" WHERE " + " AND ".join(cl)) if cl else ""), a

    def search(self, f, page=1, per_page=20):
        w, a = self._where(f)
        with self._cur() as c:
            c.execute("SELECT COUNT(*) AS n FROM vw_product_full" + w, a)
            total = c.fetchone()["n"]
            c.execute("SELECT * FROM vw_product_full" + w + " ORDER BY pid LIMIT %s OFFSET %s",
                      a + [per_page, (page - 1) * per_page])
            rows = [dict(r) for r in c.fetchall()]
        return rows, total

    def get(self, pid):
        with self._cur() as c:
            c.execute("SELECT * FROM vw_product_full WHERE pid=%s", (pid,))
            r = c.fetchone()
        return dict(r) if r else None

    def options(self):
        with self._cur() as c:
            def col(sql, k):
                c.execute(sql); return sorted(r[k] for r in c.fetchall() if r[k] is not None)
            cats = col("SELECT category_name FROM category", "category_name")
            brands = col("SELECT brand_name FROM brand", "brand_name")
            provs = col("SELECT DISTINCT seller_province FROM seller", "seller_province")
            regs = col("SELECT DISTINCT seller_region FROM seller", "seller_region")
            c.execute("SELECT seller_id, seller_name FROM seller ORDER BY seller_name")
            sellers = [dict(r) for r in c.fetchall()]
        return {"categories": cats, "brands": brands, "provinces": provs,
                "regions": regs, "sellers": sellers}

    @staticmethod
    def _disc(d):
        return round((1 - d["price"] / d["original_price"]) * 100, 2)

    def create(self, d):
        with self._cur() as c:
            c.execute("INSERT INTO brand(brand_name) VALUES(%s) ON CONFLICT (brand_name) DO NOTHING", (d["brand"],))
            c.execute("""INSERT INTO product(source_product_id,name,seller_id,brand_id,category_id,
                            price,original_price,discount_rate,rating,review_count,sold)
                         SELECT 0,%s,%s,b.brand_id,c.category_id,%s,%s,%s,%s,%s,%s
                         FROM brand b, category c WHERE b.brand_name=%s AND c.category_name=%s
                         RETURNING product_id""",
                      (d["name"], d["seller_id"], d["price"], d["original_price"], self._disc(d),
                       d["rating"], d["review_count"], d["sold"], d["brand"], d["category"]))
            return c.fetchone()["product_id"]

    def update(self, pid, d):
        with self._cur() as c:
            c.execute("INSERT INTO brand(brand_name) VALUES(%s) ON CONFLICT (brand_name) DO NOTHING", (d["brand"],))
            c.execute("""UPDATE product p SET name=%s, seller_id=%s, brand_id=b.brand_id,
                            category_id=c.category_id, price=%s, original_price=%s, discount_rate=%s,
                            rating=%s, review_count=%s, sold=%s
                         FROM brand b, category c
                         WHERE p.product_id=%s AND b.brand_name=%s AND c.category_name=%s""",
                      (d["name"], d["seller_id"], d["price"], d["original_price"], self._disc(d),
                       d["rating"], d["review_count"], d["sold"], pid, d["brand"], d["category"]))
            return c.rowcount > 0

    def delete(self, pid):
        with self._cur() as c:
            c.execute("DELETE FROM product WHERE product_id=%s", (pid,))
            return c.rowcount > 0

    @staticmethod
    def _df(c, sql):
        c.execute(sql)
        cols = [d[0] for d in c.description]
        return pd.DataFrame([list(r.values()) for r in c.fetchall()], columns=cols)

    def summary(self):
        R = self.REV
        with self._cur() as c:
            c.execute(f"""SELECT COUNT(*) AS n, COALESCE(AVG(price),0)::float8 AS avg_price,
                          COALESCE({R},0) AS revenue, COALESCE(SUM(sold),0)::bigint AS sold FROM vw_product_full""")
            k = c.fetchone()
            g = lambda key, extra="": self._df(c, f"""SELECT {key}, COUNT(*) AS products, SUM(sold)::bigint AS sold,
                {R} AS revenue FROM vw_product_full GROUP BY {key} {extra}""")
            top_brands = g("brand", "ORDER BY products DESC, brand LIMIT 10")
            by_prov = g("seller_province", "ORDER BY revenue DESC")
            by_cat = g("category", "ORDER BY products DESC, category")
            by_seller = g("seller_name", "ORDER BY revenue DESC")
        by_prov["share"] = by_prov["revenue"] / by_prov["revenue"].sum() * 100
        return {"kpi": {"products": int(k["n"]), "avg_price": float(k["avg_price"]),
                        "revenue": float(k["revenue"]), "sold": int(k["sold"])},
                "top_brands": top_brands, "by_province": by_prov,
                "by_category": by_cat, "by_seller": by_seller}

    def dashboard_data(self):
        s = self.summary()
        with self._cur() as c:
            c.execute("""SELECT CASE WHEN price<100000 THEN 0 WHEN price<500000 THEN 1 WHEN price<1000000 THEN 2
                         WHEN price<5000000 THEN 3 WHEN price<20000000 THEN 4 WHEN price<50000000 THEN 5 ELSE 6 END AS b,
                         COUNT(*) AS n FROM product GROUP BY 1""")
            cnt = {r["b"]: r["n"] for r in c.fetchall()}
        return {
            "kpi": s["kpi"],
            "category": {"labels": s["by_category"]["category"].tolist(), "values": s["by_category"]["products"].tolist()},
            "price": {"labels": BIN_LABELS, "values": [int(cnt.get(i, 0)) for i in range(len(BIN_LABELS))]},
            "province": {"labels": s["by_province"]["seller_province"].tolist(),
                         "values": [round(v / 1e9, 1) for v in s["by_province"]["revenue"]]},
            "brands": {"labels": s["top_brands"]["brand"].tolist(), "values": s["top_brands"]["products"].tolist()},
            "sellers": {"labels": s["by_seller"]["seller_name"].head(8).tolist(),
                        "revenue": [round(v / 1e9, 1) for v in s["by_seller"]["revenue"].head(8)],
                        "sold": s["by_seller"]["sold"].head(8).astype(int).tolist()},
        }


def get_repository(cfg):
    if cfg.get("DATA_SOURCE") == "postgres":
        return PostgresRepository(os.environ["DATABASE_URL"])
    if cfg.get("DATA_SOURCE") == "sqlserver":
        return SqlServerRepository(os.environ["SQL_CONN"])
    return CsvRepository(cfg["CSV_SOURCE"], cfg["CSV_WORKING"])
