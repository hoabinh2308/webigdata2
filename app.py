import io, os, sqlite3
from functools import wraps
from urllib.parse import urlencode, urlparse
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from repository import get_repository

BASE = os.path.dirname(os.path.abspath(__file__))
def _p(x): return x if os.path.isabs(x) else os.path.join(BASE, x)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=bool(os.getenv("VERCEL")))
CFG = {"DATA_SOURCE": os.getenv("DATA_SOURCE", "csv"),
       "CSV_SOURCE": _p(os.getenv("CSV_SOURCE", "data/products_clean.csv")),
       "CSV_WORKING": _p(os.getenv("CSV_WORKING", "data/products_working.csv"))}
USERS_DB = _p(os.getenv("USERS_DB", "data/users.db"))
PER_PAGE = int(os.getenv("PER_PAGE", 20))
repo = get_repository(CFG)

ROLES = {"viewer": 1, "analyst": 2, "admin": 3}
ROLE_LABEL = {"viewer": "Người xem", "analyst": "Nhà phân tích", "admin": "Quản trị viên"}

# ---------------- người dùng ----------------
from users_store import get_user_store
store = get_user_store(CFG["DATA_SOURCE"], USERS_DB)
store.init()

@app.before_request
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        g.user = store.get(uid)
        if g.user is None: session.clear()

def role_required(min_role):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **k):
            if g.user is None:
                return redirect(url_for("login", next=request.full_path))
            if ROLES[g.user["role"]] < ROLES[min_role]:
                abort(403)
            return fn(*a, **k)
        return wrap
    return deco

def can(min_role):
    return g.user is not None and ROLES[g.user["role"]] >= ROLES[min_role]

# ---------------- CSRF ----------------
@app.before_request
def csrf_protect():
    if request.method == "POST":
        if request.form.get("csrf_token") != session.get("csrf") or not session.get("csrf"):
            abort(400, "CSRF token không hợp lệ")

@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = os.urandom(16).hex()
    def page_url(p):
        a = request.args.to_dict(); a["page"] = p
        return url_for(request.endpoint, **a) 
    return dict(csrf_token=session["csrf"], can=can, ROLE_LABEL=ROLE_LABEL, page_url=page_url)

@app.errorhandler(403)
def e403(_): return render_template("error.html", code=403, msg="Bạn không có quyền truy cập chức năng này."), 403
@app.errorhandler(404)
def e404(_): return render_template("error.html", code=404, msg="Không tìm thấy trang."), 404
@app.errorhandler(400)
def e400(e): return render_template("error.html", code=400, msg=getattr(e, "description", "Yêu cầu không hợp lệ.")), 400

# ---------------- đăng nhập ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = store.by_username(request.form.get("username", "").strip())
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear(); session["uid"] = u["id"]
            nxt = request.args.get("next", "")
            if nxt and not urlparse(nxt).netloc and nxt.startswith("/"):
                return redirect(nxt)
            return redirect(url_for("home"))
        flash("Sai tên đăng nhập hoặc mật khẩu.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
@role_required("viewer")
def home():
    return render_template("home.html", kpi=repo.summary()["kpi"])

# ---------------- dashboard ----------------
@app.route("/dashboard")
@role_required("viewer")
def dashboard(): return render_template("dashboard.html")

@app.route("/api/dashboard")
@role_required("viewer")
def api_dashboard(): return jsonify(repo.dashboard_data())

# ---------------- sản phẩm ----------------
FILTERS = ("q", "category", "brand", "province", "region", "price_min", "price_max")

@app.route("/products")
@role_required("viewer")
def products():
    f = {k: request.args.get(k, "").strip() for k in FILTERS}
    page = max(request.args.get("page", 1, type=int), 1)
    try:
        rows, total = repo.search(f, page, PER_PAGE)
    except ValueError:
        flash("Khoảng giá không hợp lệ.", "error"); rows, total = [], 0
    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    return render_template("products.html", rows=rows, total=total, page=page,
                           pages=pages, f=f, opt=repo.options())

def parse_form(form):
    opt = repo.options(); err = []
    d = {"name": form.get("name", "").strip(), "seller_id": form.get("seller_id", ""),
         "brand": form.get("brand", "").strip(), "category": form.get("category", "")}
    if not d["name"]: err.append("Tên sản phẩm không được để trống.")
    if d["seller_id"] not in {s["seller_id"] for s in opt["sellers"]}: err.append("Gian hàng không hợp lệ.")
    if d["category"] not in opt["categories"]: err.append("Danh mục không hợp lệ.")
    if not d["brand"]: err.append("Thương hiệu không được để trống.")
    try:
        d["price"] = float(form["price"]); d["original_price"] = float(form["original_price"])
        d["review_count"] = int(form.get("review_count") or 0); d["sold"] = int(form.get("sold") or 0)
        r = form.get("rating", "").strip(); d["rating"] = float(r) if r else None
        if d["price"] <= 0 or d["original_price"] < d["price"]: err.append("Giá phải > 0 và giá gốc ≥ giá bán.")
        if d["review_count"] < 0 or d["sold"] < 0: err.append("Số lượng không được âm.")
        if d["rating"] is not None and not 1 <= d["rating"] <= 5: err.append("Đánh giá phải từ 1 đến 5.")
    except (ValueError, KeyError):
        err.append("Giá và số lượng phải là số.")
    return d, err

@app.route("/products/new", methods=["GET", "POST"])
@role_required("analyst")
def product_new():
    p = {}
    if request.method == "POST":
        p, err = parse_form(request.form)
        if not err:
            repo.create(p); flash("Đã thêm sản phẩm.", "ok"); return redirect(url_for("products"))
        for e in err: flash(e, "error")
    return render_template("product_form.html", p=p, opt=repo.options(), new=True)

@app.route("/products/<int:pid>/edit", methods=["GET", "POST"])
@role_required("analyst")
def product_edit(pid):
    cur = repo.get(pid) or abort(404)
    if request.method == "POST":
        p, err = parse_form(request.form)
        if not err:
            repo.update(pid, p); flash("Đã cập nhật sản phẩm.", "ok"); return redirect(url_for("products"))
        for e in err: flash(e, "error")
        cur = {**cur, **p}
    return render_template("product_form.html", p=cur, opt=repo.options(), new=False)

@app.route("/products/<int:pid>/delete", methods=["POST"])
@role_required("analyst")
def product_delete(pid):
    flash("Đã xóa sản phẩm." if repo.delete(pid) else "Không tìm thấy sản phẩm.", "ok")
    return redirect(request.referrer or url_for("products"))

# ---------------- người dùng (admin) ----------------
@app.route("/users")
@role_required("admin")
def users():
    return render_template("users.html", rows=store.all())

@app.route("/users/new", methods=["GET", "POST"])
@app.route("/users/<int:uid>/edit", methods=["GET", "POST"])
@role_required("admin")
def user_form(uid=None):
    u = store.get(uid) if uid else None
    if uid and not u: abort(404)
    if request.method == "POST":
        un = request.form.get("username", "").strip(); fn = request.form.get("full_name", "").strip()
        role = request.form.get("role"); pw = request.form.get("password", "")
        err = []
        if not un: err.append("Tên đăng nhập không được trống.")
        if role not in ROLES: err.append("Vai trò không hợp lệ.")
        if (not u or pw) and len(pw) < 6: err.append("Mật khẩu tối thiểu 6 ký tự.")
        if u and u["id"] == g.user["id"] and role != "admin": err.append("Không thể tự hạ quyền của chính mình.")
        if not err:
            try:
                if u:
                    store.update(uid, un, fn, role, generate_password_hash(pw) if pw else None)
                else:
                    store.create(un, generate_password_hash(pw), fn, role)
                flash("Đã lưu người dùng.", "ok"); return redirect(url_for("users"))
            except ValueError:
                err.append("Tên đăng nhập đã tồn tại.")
        for e in err: flash(e, "error")
        u = {"id": uid, "username": un, "full_name": fn, "role": role}
    return render_template("user_form.html", u=u)

@app.route("/users/<int:uid>/delete", methods=["POST"])
@role_required("admin")
def user_delete(uid):
    if uid == g.user["id"]: flash("Không thể tự xóa tài khoản của mình.", "error")
    else:
        store.delete(uid); flash("Đã xóa người dùng.", "ok")
    return redirect(url_for("users"))

# ---------------- báo cáo ----------------
@app.route("/reports")
@role_required("viewer")
def reports(): return render_template("reports.html", s=repo.summary())

@app.route("/reports/export/xlsx")
@role_required("analyst")
def export_xlsx():
    from openpyxl import Workbook
    from openpyxl.styles import Font
    s = repo.summary(); k = s["kpi"]; wb = Workbook(); ws = wb.active; ws.title = "Tổng quan"
    ws.append(["Chỉ số", "Giá trị"])
    for a, b in (("Số sản phẩm", k["products"]), ("Giá trung bình (VND)", round(k["avg_price"])),
                 ("Doanh thu tạm tính = price×sold (VND)", k["revenue"]), ("Tổng đã bán", k["sold"])):
        ws.append([a, b])
    def sheet(name, df, cols):
        w = wb.create_sheet(name); w.append([c[1] for c in cols])
        for r in df.to_dict("records"): w.append([r[c[0]] for c in cols])
    sheet("Top thương hiệu", s["top_brands"], [("brand", "Thương hiệu"), ("products", "Số SP"), ("sold", "Đã bán"), ("revenue", "Doanh thu")])
    sheet("Doanh thu theo tỉnh", s["by_province"], [("seller_province", "Tỉnh"), ("products", "Số SP"), ("revenue", "Doanh thu"), ("share", "Tỷ trọng %")])
    sheet("Theo danh mục", s["by_category"], [("category", "Danh mục"), ("products", "Số SP"), ("sold", "Đã bán"), ("revenue", "Doanh thu")])
    sheet("Theo gian hàng", s["by_seller"], [("seller_name", "Gian hàng"), ("products", "Số SP"), ("sold", "Đã bán"), ("revenue", "Doanh thu")])
    for w in wb.worksheets:
        for c in w[1]: c.font = Font(bold=True)
        for col in w.columns: w.column_dimensions[col[0].column_letter].width = 28
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="bao_cao_thong_ke.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/reports/export/pdf")
@role_required("analyst")
def export_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    pdfmetrics.registerFont(TTFont("VN", os.path.join(BASE, "static/fonts/DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("VN-B", os.path.join(BASE, "static/fonts/DejaVuSans-Bold.ttf")))
    h = ParagraphStyle("h", fontName="VN-B", fontSize=14, spaceAfter=6, spaceBefore=12)
    n = ParagraphStyle("n", fontName="VN", fontSize=9)
    s = repo.summary(); k = s["kpi"]
    def tbl(rows, widths):
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "VN", 9), ("FONT", (0, 0), (-1, 0), "VN-B", 9),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E3E6FA")),
                               ("GRID", (0, 0), (-1, -1), .4, colors.grey), ("ALIGN", (1, 1), (-1, -1), "RIGHT")]))
        return t
    f = lambda v: f"{v:,.0f}"
    el = [Paragraph("BÁO CÁO THỐNG KÊ THỊ TRƯỜNG BÁN LẺ TRỰC TUYẾN", ParagraphStyle("t", fontName="VN-B", fontSize=16)),
          Paragraph("Doanh thu tạm tính = price × sold", n), Paragraph("Tổng quan", h),
          tbl([["Chỉ số", "Giá trị"], ["Số sản phẩm", f(k["products"])], ["Giá trung bình (VND)", f(k["avg_price"])],
               ["Doanh thu (VND)", f(k["revenue"])], ["Tổng đã bán", f(k["sold"])]], [250, 200]),
          Paragraph("Top 10 thương hiệu có nhiều sản phẩm nhất", h),
          tbl([["Thương hiệu", "Số SP", "Đã bán"]] + [[r.brand, f(r.products), f(r.sold)] for r in s["top_brands"].itertuples()], [250, 80, 100]),
          Paragraph("Doanh thu theo tỉnh", h),
          tbl([["Tỉnh", "Doanh thu (VND)", "Tỷ trọng"]] + [[r.seller_province, f(r.revenue), f"{r.share:.2f}%"] for r in s["by_province"].itertuples()], [200, 150, 80]),
          Paragraph("Theo danh mục", h),
          tbl([["Danh mục", "Số SP", "Đã bán"]] + [[r.category, f(r.products), f(r.sold)] for r in s["by_category"].itertuples()], [250, 80, 100])]
    buf = io.BytesIO(); SimpleDocTemplate(buf, pagesize=A4).build(el); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="bao_cao_thong_ke.pdf", mimetype="application/pdf")

# ---------------- các trang chờ ML ----------------
PLACEHOLDERS = {
    "predict": ("Dự đoán", "Mô hình học có giám sát (dự đoán giá trị mục tiêu).", "analyst"),
    "clustering": ("Phân cụm", "Mô hình học không giám sát (phân cụm / hồi quy).", "analyst"),
    "anomaly": ("Bất thường", "Mô hình khảo sát và phát hiện điểm bất thường.", "analyst"),
}
def _ph(key):
    title, desc, role = PLACEHOLDERS[key]
    @role_required(role)
    def view(): return render_template("placeholder.html", title=title, desc=desc)
    view.__name__ = key
    return view
for _k in PLACEHOLDERS: app.add_url_rule(f"/{_k}", _k, _ph(_k))

if __name__ == "__main__":
    app.run(debug=True)
