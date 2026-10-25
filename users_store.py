"""Lưu người dùng. SqliteUsers (chạy máy cá nhân) hoặc PgUsers (Supabase, dùng cho Vercel)."""
import os, sqlite3
from werkzeug.security import generate_password_hash

SEED = (("admin", "Quản trị viên", "admin", "ADMIN_PASSWORD"),
        ("analyst", "Nhà phân tích", "analyst", "ANALYST_PASSWORD"),
        ("viewer", "Người xem", "viewer", "VIEWER_PASSWORD"))


def _seed(store):
    if store.count() == 0:
        for u, name, role, env in SEED:
            try:
                store.create(u, generate_password_hash(os.getenv(env, u + "123")), name, role)
            except ValueError:
                pass  # instance khác vừa tạo xong


class SqliteUsers:
    def __init__(self, path):
        self.path = path

    def _x(self, sql, args=(), fetch=None):
        c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row
        try:
            cur = c.execute(sql, args); out = None
            if fetch == "one":
                r = cur.fetchone(); out = dict(r) if r else None
            elif fetch == "all":
                out = [dict(r) for r in cur.fetchall()]
            c.commit(); return out
        except sqlite3.IntegrityError as e:
            raise ValueError("duplicate") from e
        finally:
            c.close()

    def init(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._x("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, full_name TEXT,
            role TEXT NOT NULL CHECK(role IN ('admin','analyst','viewer')))""")
        _seed(self)

    def count(self): return self._x("SELECT COUNT(*) AS n FROM users", fetch="one")["n"]
    def get(self, uid): return self._x("SELECT * FROM users WHERE id=?", (uid,), "one")
    def by_username(self, u): return self._x("SELECT * FROM users WHERE username=?", (u,), "one")
    def all(self): return self._x("SELECT * FROM users ORDER BY id", fetch="all")
    def create(self, u, h, name, role):
        self._x("INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)", (u, h, name, role))
    def update(self, uid, u, name, role, h=None):
        self._x("UPDATE users SET username=?,full_name=?,role=? WHERE id=?", (u, name, role, uid))
        if h: self._x("UPDATE users SET password_hash=? WHERE id=?", (h, uid))
    def delete(self, uid): self._x("DELETE FROM users WHERE id=?", (uid,))


class PgUsers:
    def __init__(self, dsn):
        self.dsn = dsn

    def _x(self, sql, args=(), fetch=None):
        import psycopg2, psycopg2.extras
        kw = {} if "sslmode" in self.dsn else {"sslmode": "require"}
        conn = psycopg2.connect(self.dsn, connect_timeout=10, **kw)
        try:
            with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, args)
                if fetch == "one":
                    r = cur.fetchone(); return dict(r) if r else None
                if fetch == "all":
                    return [dict(r) for r in cur.fetchall()]
        except psycopg2.errors.UniqueViolation as e:
            raise ValueError("duplicate") from e
        finally:
            conn.close()

    def init(self):
        self._x("""CREATE TABLE IF NOT EXISTS app_users(id SERIAL PRIMARY KEY,
            username VARCHAR(60) UNIQUE NOT NULL, password_hash TEXT NOT NULL, full_name VARCHAR(100),
            role VARCHAR(10) NOT NULL CHECK (role IN ('admin','analyst','viewer')))""")
        self._x("ALTER TABLE app_users ENABLE ROW LEVEL SECURITY")
        _seed(self)

    def count(self): return self._x("SELECT COUNT(*) AS n FROM app_users", fetch="one")["n"]
    def get(self, uid): return self._x("SELECT * FROM app_users WHERE id=%s", (uid,), "one")
    def by_username(self, u): return self._x("SELECT * FROM app_users WHERE username=%s", (u,), "one")
    def all(self): return self._x("SELECT * FROM app_users ORDER BY id", fetch="all")
    def create(self, u, h, name, role):
        self._x("INSERT INTO app_users(username,password_hash,full_name,role) VALUES(%s,%s,%s,%s)", (u, h, name, role))
    def update(self, uid, u, name, role, h=None):
        self._x("UPDATE app_users SET username=%s,full_name=%s,role=%s WHERE id=%s", (u, name, role, uid))
        if h: self._x("UPDATE app_users SET password_hash=%s WHERE id=%s", (h, uid))
    def delete(self, uid): self._x("DELETE FROM app_users WHERE id=%s", (uid,))


def get_user_store(source, sqlite_path):
    if source == "postgres":
        return PgUsers(os.environ["DATABASE_URL"])
    return SqliteUsers(sqlite_path)
