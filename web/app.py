"""防衛調達データベース PoC Web (Flask + SQLite)。

全ページDBから動的生成(ハードコードなし)。PoCのため認証・API・書き込みなし。
起動: python -m web.app  (http://127.0.0.1:5000)
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

from flask import Flask, abort, g, render_template, request

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "db" / "procurement.db"

app = Flask(__name__)

PER_PAGE = 100


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ---- 表示ヘルパ ----

def fmt_oku(yen) -> str:
    """円 → 億円表示(カンマ付き、小数1桁)。"""
    if yen is None:
        return "—"
    oku = yen / 1e8
    if oku >= 10:
        return f"{oku:,.0f}億円"
    if oku >= 0.1:
        return f"{oku:,.1f}億円"
    return f"{yen:,}円"


def fmt_yen(yen) -> str:
    return "—" if yen is None else f"{yen:,}円"


def wareki_fy(fy: int) -> str:
    return f"令和{fy - 2018}年度"


def fy_label(fy: int) -> str:
    return f"{wareki_fy(fy)} (FY{fy})"


app.jinja_env.filters["oku"] = fmt_oku
app.jinja_env.filters["yen"] = fmt_yen
app.jinja_env.filters["fy"] = fy_label
app.jinja_env.filters["wareki"] = wareki_fy


def latest_fy() -> int:
    row = db().execute("SELECT MAX(fiscal_year) m FROM contracts").fetchone()
    return row["m"] or 2025


def fy_list() -> list[int]:
    return [r["fiscal_year"] for r in db().execute(
        "SELECT DISTINCT fiscal_year FROM contracts ORDER BY fiscal_year DESC")]


# ---- ページ ----

@app.route("/")
def index():
    conn = db()
    stats = conn.execute(
        """SELECT COUNT(*) n_contracts, COUNT(DISTINCT company_id) n_companies,
                  MAX(fiscal_year) max_fy
           FROM contracts"""
    ).fetchone()
    last_import = conn.execute(
        "SELECT MAX(retrieved_at) t FROM sources").fetchone()["t"]
    fy = latest_fy()
    ranking = conn.execute(
        """SELECT co.name, co.slug, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE c.fiscal_year = ? AND c.amount IS NOT NULL
           GROUP BY co.id ORDER BY total DESC LIMIT 10""", (fy,)).fetchall()
    # 収録データ中の最新契約日から90日以内で金額上位
    recent_large = conn.execute(
        """SELECT c.id, c.contract_date, c.title, c.amount, co.name company, co.slug
           FROM contracts c LEFT JOIN companies co ON co.id = c.company_id
           WHERE c.amount IS NOT NULL AND c.contract_date >=
                 date((SELECT MAX(contract_date) FROM contracts), '-90 days')
           ORDER BY c.amount DESC LIMIT 10""").fetchall()
    cats = conn.execute(
        """SELECT cat.name, cat.slug, COUNT(*) n, SUM(c.amount) total
           FROM contracts c JOIN categories cat ON cat.id = c.category_id
           GROUP BY cat.id ORDER BY cat.sort_order""").fetchall()
    trend = conn.execute(
        """SELECT fiscal_year, SUM(amount) total, COUNT(*) n
           FROM contracts WHERE amount IS NOT NULL
           GROUP BY fiscal_year ORDER BY fiscal_year""").fetchall()
    max_total = max((r["total"] or 0 for r in trend), default=1)
    return render_template(
        "index.html", stats=stats, last_import=last_import, fy=fy,
        ranking=ranking, recent_large=recent_large, cats=cats,
        trend=trend, max_total=max_total)


@app.route("/contracts/")
def contracts():
    conn = db()
    q = request.args.get("q", "").strip()
    year = request.args.get("year", type=int)
    company = request.args.get("company", "").strip()
    category = request.args.get("category", "").strip()
    method = request.args.get("method", "").strip()
    amount_min = request.args.get("amount_min", type=float)  # 億円
    amount_max = request.args.get("amount_max", type=float)
    page = max(request.args.get("page", 1, type=int), 1)

    where, params = ["1=1"], []
    if q:
        where.append("c.title LIKE ?")
        params.append(f"%{q}%")
    if year:
        where.append("c.fiscal_year = ?")
        params.append(year)
    if company:
        where.append("co.slug = ?")
        params.append(company)
    if category:
        where.append("cat.slug = ?")
        params.append(category)
    if method:
        where.append("c.procurement_method = ?")
        params.append(method)
    if amount_min is not None:
        where.append("c.amount >= ?")
        params.append(int(amount_min * 1e8))
    if amount_max is not None:
        where.append("c.amount <= ?")
        params.append(int(amount_max * 1e8))

    base_sql = f"""FROM contracts c
        LEFT JOIN companies co ON co.id = c.company_id
        LEFT JOIN categories cat ON cat.id = c.category_id
        WHERE {' AND '.join(where)}"""
    total = conn.execute(f"SELECT COUNT(*) n {base_sql}", params).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT c.id, c.contract_date, c.fiscal_year, c.title, c.amount,
                   c.procurement_method, co.name company, co.slug company_slug,
                   cat.name category, cat.slug category_slug
            {base_sql}
            ORDER BY c.contract_date DESC, c.amount DESC
            LIMIT ? OFFSET ?""",
        params + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    methods = [r["procurement_method"] for r in conn.execute(
        "SELECT DISTINCT procurement_method FROM contracts ORDER BY 1")]
    cats = conn.execute("SELECT name, slug FROM categories ORDER BY sort_order").fetchall()
    pages = max(math.ceil(total / PER_PAGE), 1)
    return render_template(
        "contracts.html", rows=rows, total=total, page=page, pages=pages,
        years=fy_list(), methods=methods, cats=cats,
        f={"q": q, "year": year, "company": company, "category": category,
           "method": method, "amount_min": amount_min, "amount_max": amount_max})


@app.route("/companies/")
def companies():
    fy = request.args.get("year", type=int) or latest_fy()
    rows = db().execute(
        """SELECT co.name, co.slug, co.entity_type, co.corporate_number,
                  SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE c.fiscal_year = ? AND c.amount IS NOT NULL
           GROUP BY co.id ORDER BY total DESC LIMIT 200""", (fy,)).fetchall()
    return render_template("companies.html", rows=rows, fy=fy, years=fy_list())


@app.route("/companies/<slug>/")
def company(slug):
    conn = db()
    co = conn.execute("SELECT * FROM companies WHERE slug=?", (slug,)).fetchone()
    if not co:
        abort(404)
    fy = latest_fy()
    yearly = conn.execute(
        """SELECT fiscal_year, SUM(amount) total, COUNT(*) n
           FROM contracts WHERE company_id=? AND amount IS NOT NULL
           GROUP BY fiscal_year ORDER BY fiscal_year""", (co["id"],)).fetchall()
    ymap = {r["fiscal_year"]: r for r in yearly}
    cur = ymap.get(fy)
    prev = ymap.get(fy - 1)
    yoy = None
    if cur and prev and prev["total"]:
        yoy = (cur["total"] - prev["total"]) / prev["total"] * 100
    rank = None
    if cur:
        rank = conn.execute(
            """SELECT COUNT(*) + 1 r FROM (
                 SELECT company_id, SUM(amount) t FROM contracts
                 WHERE fiscal_year=? AND amount IS NOT NULL GROUP BY company_id
               ) WHERE t > ?""", (fy, cur["total"])).fetchone()["r"]
    cats = conn.execute(
        """SELECT cat.name, cat.slug, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN categories cat ON cat.id = c.category_id
           WHERE c.company_id=? AND c.amount IS NOT NULL
           GROUP BY cat.id ORDER BY total DESC""", (co["id"],)).fetchall()
    recent = conn.execute(
        """SELECT id, contract_date, title, amount, procurement_method, fiscal_year
           FROM contracts WHERE company_id=?
           ORDER BY contract_date DESC LIMIT 15""", (co["id"],)).fetchall()
    largest = conn.execute(
        """SELECT id, contract_date, title, amount, procurement_method, fiscal_year
           FROM contracts WHERE company_id=? AND amount IS NOT NULL
           ORDER BY amount DESC LIMIT 10""", (co["id"],)).fetchall()
    max_total = max((r["total"] or 0 for r in yearly), default=1)
    aliases = conn.execute(
        "SELECT alias, source, confidence FROM company_aliases WHERE company_id=?",
        (co["id"],)).fetchall()
    return render_template(
        "company.html", co=co, fy=fy, yearly=yearly, cur=cur, yoy=yoy,
        rank=rank, cats=cats, recent=recent, largest=largest,
        max_total=max_total, aliases=aliases)


@app.route("/categories/")
def categories():
    rows = db().execute(
        """SELECT cat.name, cat.slug, COUNT(*) n, SUM(c.amount) total
           FROM contracts c JOIN categories cat ON cat.id = c.category_id
           GROUP BY cat.id ORDER BY cat.sort_order""").fetchall()
    return render_template("categories.html", rows=rows)


@app.route("/categories/<slug>/")
def category(slug):
    conn = db()
    cat = conn.execute("SELECT * FROM categories WHERE slug=?", (slug,)).fetchone()
    if not cat:
        abort(404)
    fy = latest_fy()
    yearly = conn.execute(
        """SELECT fiscal_year, SUM(amount) total, COUNT(*) n
           FROM contracts WHERE category_id=? AND amount IS NOT NULL
           GROUP BY fiscal_year ORDER BY fiscal_year""", (cat["id"],)).fetchall()
    ymap = {r["fiscal_year"]: r for r in yearly}
    cur, prev = ymap.get(fy), ymap.get(fy - 1)
    yoy = None
    if cur and prev and prev["total"]:
        yoy = (cur["total"] - prev["total"]) / prev["total"] * 100
    top_companies = conn.execute(
        """SELECT co.name, co.slug, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE c.category_id=? AND c.amount IS NOT NULL
           GROUP BY co.id ORDER BY total DESC LIMIT 15""", (cat["id"],)).fetchall()
    recent = conn.execute(
        """SELECT c.id, c.contract_date, c.title, c.amount, co.name company, co.slug company_slug
           FROM contracts c LEFT JOIN companies co ON co.id=c.company_id
           WHERE c.category_id=? ORDER BY c.contract_date DESC LIMIT 15""",
        (cat["id"],)).fetchall()
    largest = conn.execute(
        """SELECT c.id, c.contract_date, c.title, c.amount, co.name company, co.slug company_slug
           FROM contracts c LEFT JOIN companies co ON co.id=c.company_id
           WHERE c.category_id=? AND c.amount IS NOT NULL
           ORDER BY c.amount DESC LIMIT 10""", (cat["id"],)).fetchall()
    max_total = max((r["total"] or 0 for r in yearly), default=1)
    return render_template(
        "category.html", cat=cat, fy=fy, yearly=yearly, cur=cur, yoy=yoy,
        top_companies=top_companies, recent=recent, largest=largest,
        max_total=max_total)


@app.route("/rankings/companies/")
def rankings():
    fy = request.args.get("year", type=int) or latest_fy()
    conn = db()
    rows = conn.execute(
        """SELECT co.name, co.slug, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE c.fiscal_year=? AND c.amount IS NOT NULL
           GROUP BY co.id ORDER BY total DESC LIMIT 100""", (fy,)).fetchall()
    prev_totals = {r["slug"]: r["total"] for r in conn.execute(
        """SELECT co.slug, SUM(c.amount) total
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE c.fiscal_year=? AND c.amount IS NOT NULL GROUP BY co.id""",
        (fy - 1,))}
    return render_template("rankings.html", rows=rows, fy=fy, years=fy_list(),
                           prev_totals=prev_totals)


@app.route("/sources/")
def sources():
    conn = db()
    rows = conn.execute(
        """SELECT s.*, COUNT(r.id) n_rows FROM sources s
           LEFT JOIN raw_contracts r ON r.source_id = s.id
           GROUP BY s.id ORDER BY s.fiscal_year DESC, s.month DESC""").fetchall()
    jobs = conn.execute(
        "SELECT * FROM import_jobs ORDER BY started_at DESC LIMIT 10").fetchall()
    agg = conn.execute(
        """SELECT fiscal_year, COUNT(*) files, SUM(row_count) rows_
           FROM sources GROUP BY fiscal_year ORDER BY fiscal_year DESC""").fetchall()
    return render_template("sources.html", rows=rows, jobs=jobs, agg=agg)


@app.route("/methodology/")
def methodology():
    conn = db()
    quality = conn.execute(
        """SELECT
             SUM(CASE WHEN normalization_status='ok' THEN 1 ELSE 0 END) ok,
             SUM(CASE WHEN normalization_flags LIKE '%amount_failed%' THEN 1 ELSE 0 END) amount_failed,
             SUM(CASE WHEN normalization_flags LIKE '%date_failed%' THEN 1 ELSE 0 END) date_failed,
             SUM(CASE WHEN normalization_flags LIKE '%company_unresolved%' THEN 1 ELSE 0 END) company_unresolved,
             SUM(CASE WHEN classification_confidence = 0 THEN 1 ELSE 0 END) uncategorized,
             COUNT(*) total
           FROM contracts""").fetchone()
    return render_template("methodology.html", quality=quality)


@app.route("/contracts/<int:cid>/")
def contract_detail(cid):
    conn = db()
    c = conn.execute(
        """SELECT c.*, co.name company, co.slug company_slug, co.corporate_number,
                  cat.name category, cat.slug category_slug,
                  s.title source_title, s.url source_url, s.landing_page, s.license,
                  r.raw_row_json, r.raw_company, r.raw_amount, r.raw_contract_date
           FROM contracts c
           LEFT JOIN companies co ON co.id=c.company_id
           LEFT JOIN categories cat ON cat.id=c.category_id
           JOIN sources s ON s.id=c.source_id
           JOIN raw_contracts r ON r.id=c.raw_contract_id
           WHERE c.id=?""", (cid,)).fetchone()
    if not c:
        abort(404)
    return render_template("contract.html", c=c)


if __name__ == "__main__":
    app.run(debug=False, port=5000)
