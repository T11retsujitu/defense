"""防衛調達データベース PoC Web (Flask + SQLite)。

全ページDBから動的生成(ハードコードなし)。PoCのため認証・API・書き込みなし。
起動: python -m web.app  (http://127.0.0.1:5000)

集計の意味論:
- デフォルト年度は「全12か月収録済みの最新年度」。進行中年度は月数を明示する。
- 進行中年度の前年比較は前年同期(同じ暦月の集合)で行う。
- 「企業ランキング」は entity_type が company / foreign_company の民間企業のみ。
  外国政府(FMS)・公的機関を含む集計は「契約相手方ランキング」として分離。
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
COMPANY_TYPES = ("company", "foreign_company")


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


ENTITY_LABELS = {
    "company": "民間企業",
    "foreign_company": "外国企業",
    "foreign_government": "外国政府等(FMS)",
    "gov_agency": "公的機関",
    "other": "区分不明",
}

app.jinja_env.filters["oku"] = fmt_oku
app.jinja_env.filters["yen"] = fmt_yen
app.jinja_env.filters["fy"] = fy_label
app.jinja_env.filters["wareki"] = wareki_fy
app.jinja_env.globals["entity_label"] = lambda t: ENTITY_LABELS.get(t, t)


# ---- 年度・収録期間ヘルパ ----

def fy_months_map() -> dict[int, list[int]]:
    """年度 -> 収録済みの暦月リスト(sourcesから)。"""
    out: dict[int, set] = {}
    for r in db().execute("SELECT fiscal_year, month FROM sources"):
        out.setdefault(r["fiscal_year"], set()).add(r["month"])
    return {fy: sorted(m) for fy, m in out.items()}


def fy_is_complete(fy: int, mmap=None) -> bool:
    mmap = mmap or fy_months_map()
    return len(mmap.get(fy, [])) >= 12


def default_fy() -> int:
    """全月収録済みの最新年度(なければ収録最新年度)。"""
    mmap = fy_months_map()
    complete = [fy for fy, m in mmap.items() if len(m) >= 12]
    if complete:
        return max(complete)
    return max(mmap) if mmap else 2025


def fy_list() -> list[int]:
    return sorted(fy_months_map().keys(), reverse=True)


FY_ORDER = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


def period_label(fy: int, mmap=None) -> str | None:
    """進行中年度の収録期間ラベル(例: 4〜6月)。完了年度はNone。"""
    mmap = mmap or fy_months_map()
    months = mmap.get(fy, [])
    if len(months) >= 12 or not months:
        return None
    ordered = [m for m in FY_ORDER if m in months]
    return f"{ordered[0]}〜{ordered[-1]}月" if len(ordered) > 1 else f"{ordered[0]}月"


def month_filter_sql(months: list[int], alias: str = "c") -> str:
    """contract_dateを暦月集合で絞るSQL断片(同期比較用)。"""
    ms = ",".join(f"'{m:02d}'" for m in months)
    return f"strftime('%m', {alias}.contract_date) IN ({ms})"


def yoy_same_period(conn, fy: int, where_sql: str, params: list) -> tuple:
    """(当年度合計, 前年(同期)合計, 増減率%, 同期ラベル) を返す。

    fyが完了年度なら前年度通年、進行中なら前年同期(同じ暦月)と比較する。
    """
    mmap = fy_months_map()
    cur = conn.execute(
        f"SELECT SUM(amount) t, COUNT(*) n FROM contracts c WHERE {where_sql} AND c.fiscal_year=? AND c.amount IS NOT NULL",
        params + [fy]).fetchone()
    label = period_label(fy, mmap)
    if label:
        months = mmap.get(fy, [])
        prev = conn.execute(
            f"""SELECT SUM(amount) t FROM contracts c WHERE {where_sql} AND c.fiscal_year=?
                AND c.amount IS NOT NULL AND {month_filter_sql(months)}""",
            params + [fy - 1]).fetchone()
    else:
        prev = conn.execute(
            f"SELECT SUM(amount) t FROM contracts c WHERE {where_sql} AND c.fiscal_year=? AND c.amount IS NOT NULL",
            params + [fy - 1]).fetchone()
    yoy = None
    if cur["t"] and prev["t"]:
        yoy = (cur["t"] - prev["t"]) / prev["t"] * 100
    return cur, prev["t"], yoy, label


# ---- ページ ----

@app.route("/")
def index():
    conn = db()
    stats = conn.execute(
        """SELECT COUNT(*) n_contracts, COUNT(DISTINCT company_id) n_companies,
                  MAX(fiscal_year) max_fy
           FROM contracts"""
    ).fetchone()
    last_import = conn.execute("SELECT MAX(retrieved_at) t FROM sources").fetchone()["t"]
    fy = default_fy()
    mmap = fy_months_map()
    ranking = conn.execute(
        f"""SELECT co.name, co.slug, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE c.fiscal_year = ? AND c.amount IS NOT NULL
             AND co.entity_type IN ('company','foreign_company')
           GROUP BY co.id ORDER BY total DESC LIMIT 10""", (fy,)).fetchall()
    recent_large = conn.execute(
        """SELECT c.id, c.contract_date, c.title, c.amount, co.name company, co.slug
           FROM contracts c LEFT JOIN companies co ON co.id = c.company_id
           WHERE c.amount IS NOT NULL AND c.contract_date >=
                 date((SELECT MAX(contract_date) FROM contracts), '-90 days')
           ORDER BY c.amount DESC LIMIT 10""").fetchall()
    domains = conn.execute(
        """SELECT cat.name, cat.slug, COUNT(*) n, SUM(c.amount) total
           FROM contracts c JOIN categories cat ON cat.id = c.domain_category_id
           GROUP BY cat.id ORDER BY cat.sort_order""").fetchall()
    natures = conn.execute(
        """SELECT cat.name, cat.slug, COUNT(*) n, SUM(c.amount) total
           FROM contracts c JOIN categories cat ON cat.id = c.nature_category_id
           GROUP BY cat.id ORDER BY cat.sort_order""").fetchall()
    trend = conn.execute(
        """SELECT fiscal_year, SUM(amount) total, COUNT(*) n
           FROM contracts WHERE amount IS NOT NULL
           GROUP BY fiscal_year ORDER BY fiscal_year""").fetchall()
    max_total = max((r["total"] or 0 for r in trend), default=1)
    trend_labels = {r["fiscal_year"]: period_label(r["fiscal_year"], mmap) for r in trend}
    return render_template(
        "index.html", stats=stats, last_import=last_import, fy=fy,
        ranking=ranking, recent_large=recent_large, domains=domains,
        natures=natures, trend=trend, max_total=max_total,
        trend_labels=trend_labels)


@app.route("/contracts/")
def contracts():
    conn = db()
    q = request.args.get("q", "").strip()
    year = request.args.get("year", type=int)
    company = request.args.get("company", "").strip()
    domain = request.args.get("domain", "").strip()
    nature = request.args.get("nature", "").strip()
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
    if domain:
        where.append("dcat.slug = ?")
        params.append(domain)
    if nature:
        where.append("ncat.slug = ?")
        params.append(nature)
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
        LEFT JOIN categories dcat ON dcat.id = c.domain_category_id
        LEFT JOIN categories ncat ON ncat.id = c.nature_category_id
        WHERE {' AND '.join(where)}"""
    total = conn.execute(f"SELECT COUNT(*) n {base_sql}", params).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT c.id, c.contract_date, c.fiscal_year, c.title, c.amount,
                   c.procurement_method, co.name company, co.slug company_slug,
                   dcat.name domain_name, dcat.slug domain_slug,
                   ncat.name nature_name, ncat.slug nature_slug
            {base_sql}
            ORDER BY c.contract_date DESC, c.amount DESC
            LIMIT ? OFFSET ?""",
        params + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    methods = [r["procurement_method"] for r in conn.execute(
        "SELECT DISTINCT procurement_method FROM contracts ORDER BY 1")]
    domains = conn.execute(
        "SELECT name, slug FROM categories WHERE axis='domain' ORDER BY sort_order").fetchall()
    natures = conn.execute(
        "SELECT name, slug FROM categories WHERE axis='nature' ORDER BY sort_order").fetchall()
    pages = max(math.ceil(total / PER_PAGE), 1)
    return render_template(
        "contracts.html", rows=rows, total=total, page=page, pages=pages,
        years=fy_list(), methods=methods, domains=domains, natures=natures,
        f={"q": q, "year": year, "company": company, "domain": domain,
           "nature": nature, "method": method,
           "amount_min": amount_min, "amount_max": amount_max})


@app.route("/companies/")
def companies():
    fy = request.args.get("year", type=int) or default_fy()
    label = period_label(fy)
    rows = db().execute(
        """SELECT co.name, co.slug, co.entity_type, co.corporate_number,
                  SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE c.fiscal_year = ? AND c.amount IS NOT NULL
           GROUP BY co.id ORDER BY total DESC LIMIT 200""", (fy,)).fetchall()
    return render_template("companies.html", rows=rows, fy=fy, years=fy_list(),
                           plabel=label)


@app.route("/companies/<slug>/")
def company(slug):
    conn = db()
    co = conn.execute("SELECT * FROM companies WHERE slug=?", (slug,)).fetchone()
    if not co:
        abort(404)
    fy = default_fy()
    mmap = fy_months_map()
    yearly = conn.execute(
        """SELECT fiscal_year, SUM(amount) total, COUNT(*) n
           FROM contracts WHERE company_id=? AND amount IS NOT NULL
           GROUP BY fiscal_year ORDER BY fiscal_year""", (co["id"],)).fetchall()
    cur, prev_total, yoy, plabel = yoy_same_period(
        conn, fy, "c.company_id=?", [co["id"]])
    rank = None
    if cur and cur["t"]:
        rank = conn.execute(
            """SELECT COUNT(*) + 1 r FROM (
                 SELECT c.company_id, SUM(c.amount) t FROM contracts c
                 JOIN companies x ON x.id = c.company_id
                 WHERE c.fiscal_year=? AND c.amount IS NOT NULL
                   AND x.entity_type IN ('company','foreign_company')
                 GROUP BY c.company_id
               ) WHERE t > ?""", (fy, cur["t"])).fetchone()["r"]
        if co["entity_type"] not in COMPANY_TYPES:
            rank = None  # 政府機関等は企業ランキング対象外
    domains = conn.execute(
        """SELECT cat.name, cat.slug, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN categories cat ON cat.id = c.domain_category_id
           WHERE c.company_id=? AND c.amount IS NOT NULL
           GROUP BY cat.id ORDER BY total DESC""", (co["id"],)).fetchall()
    natures = conn.execute(
        """SELECT cat.name, cat.slug, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN categories cat ON cat.id = c.nature_category_id
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
    yearly_labels = {r["fiscal_year"]: period_label(r["fiscal_year"], mmap) for r in yearly}
    return render_template(
        "company.html", co=co, fy=fy, yearly=yearly, cur=cur, yoy=yoy,
        plabel=plabel, rank=rank, domains=domains, natures=natures,
        recent=recent, largest=largest, max_total=max_total, aliases=aliases,
        yearly_labels=yearly_labels)


@app.route("/categories/")
def categories():
    conn = db()
    domains = conn.execute(
        """SELECT cat.name, cat.slug, COUNT(*) n, SUM(c.amount) total
           FROM contracts c JOIN categories cat ON cat.id = c.domain_category_id
           GROUP BY cat.id ORDER BY cat.sort_order""").fetchall()
    natures = conn.execute(
        """SELECT cat.name, cat.slug, COUNT(*) n, SUM(c.amount) total
           FROM contracts c JOIN categories cat ON cat.id = c.nature_category_id
           GROUP BY cat.id ORDER BY cat.sort_order""").fetchall()
    return render_template("categories.html", domains=domains, natures=natures)


@app.route("/categories/<slug>/")
def category(slug):
    conn = db()
    cat = conn.execute("SELECT * FROM categories WHERE slug=?", (slug,)).fetchone()
    if not cat:
        abort(404)
    col = "domain_category_id" if cat["axis"] == "domain" else "nature_category_id"
    fy = default_fy()
    mmap = fy_months_map()
    yearly = conn.execute(
        f"""SELECT fiscal_year, SUM(amount) total, COUNT(*) n
           FROM contracts WHERE {col}=? AND amount IS NOT NULL
           GROUP BY fiscal_year ORDER BY fiscal_year""", (cat["id"],)).fetchall()
    cur, prev_total, yoy, plabel = yoy_same_period(conn, fy, f"c.{col}=?", [cat["id"]])
    top_companies = conn.execute(
        f"""SELECT co.name, co.slug, co.entity_type, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE c.{col}=? AND c.amount IS NOT NULL
           GROUP BY co.id ORDER BY total DESC LIMIT 15""", (cat["id"],)).fetchall()
    recent = conn.execute(
        f"""SELECT c.id, c.contract_date, c.title, c.amount, co.name company, co.slug company_slug
           FROM contracts c LEFT JOIN companies co ON co.id=c.company_id
           WHERE c.{col}=? ORDER BY c.contract_date DESC LIMIT 15""",
        (cat["id"],)).fetchall()
    largest = conn.execute(
        f"""SELECT c.id, c.contract_date, c.title, c.amount, co.name company, co.slug company_slug
           FROM contracts c LEFT JOIN companies co ON co.id=c.company_id
           WHERE c.{col}=? AND c.amount IS NOT NULL
           ORDER BY c.amount DESC LIMIT 10""", (cat["id"],)).fetchall()
    max_total = max((r["total"] or 0 for r in yearly), default=1)
    yearly_labels = {r["fiscal_year"]: period_label(r["fiscal_year"], mmap) for r in yearly}
    filter_param = "domain" if cat["axis"] == "domain" else "nature"
    return render_template(
        "category.html", cat=cat, fy=fy, yearly=yearly, cur=cur, yoy=yoy,
        plabel=plabel, top_companies=top_companies, recent=recent,
        largest=largest, max_total=max_total, yearly_labels=yearly_labels,
        filter_param=filter_param)


def _ranking_rows(conn, fy: int, entity_types: tuple | None):
    where = "c.fiscal_year=? AND c.amount IS NOT NULL"
    params: list = [fy]
    if entity_types:
        where += f" AND co.entity_type IN ({','.join('?' * len(entity_types))})"
        params += list(entity_types)
    rows = conn.execute(
        f"""SELECT co.name, co.slug, co.entity_type, SUM(c.amount) total, COUNT(*) n
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE {where} GROUP BY co.id ORDER BY total DESC LIMIT 100""",
        params).fetchall()
    # 前年比較(進行中年度は前年同期)
    mmap = fy_months_map()
    label = period_label(fy, mmap)
    prev_where = where.replace("c.fiscal_year=?", "c.fiscal_year=?")
    prev_params = [fy - 1] + params[1:]
    period_sql = ""
    if label:
        period_sql = " AND " + month_filter_sql(mmap.get(fy, []))
    prev_totals = {r["slug"]: r["total"] for r in conn.execute(
        f"""SELECT co.slug, SUM(c.amount) total
           FROM contracts c JOIN companies co ON co.id = c.company_id
           WHERE {prev_where}{period_sql} GROUP BY co.id""", prev_params)}
    return rows, prev_totals, label


@app.route("/rankings/companies/")
def rankings():
    fy = request.args.get("year", type=int) or default_fy()
    rows, prev_totals, plabel = _ranking_rows(db(), fy, COMPANY_TYPES)
    return render_template("rankings.html", rows=rows, fy=fy, years=fy_list(),
                           prev_totals=prev_totals, plabel=plabel,
                           scope="companies")


@app.route("/rankings/counterparties/")
def rankings_all():
    fy = request.args.get("year", type=int) or default_fy()
    rows, prev_totals, plabel = _ranking_rows(db(), fy, None)
    return render_template("rankings.html", rows=rows, fy=fy, years=fy_list(),
                           prev_totals=prev_totals, plabel=plabel,
                           scope="counterparties")


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
        """SELECT fiscal_year, COUNT(*) files, COUNT(DISTINCT month) months,
                  SUM(row_count) rows_
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
             SUM(CASE WHEN normalization_flags LIKE '%company_ambiguous%' THEN 1 ELSE 0 END) company_ambiguous,
             SUM(CASE WHEN domain_rule_matched=0 THEN 1 ELSE 0 END) domain_unmatched,
             SUM(CASE WHEN nature_rule_matched=0 THEN 1 ELSE 0 END) nature_defaulted,
             SUM(suspected_duplicate) suspected_duplicates,
             COUNT(*) total
           FROM contracts""").fetchone()
    dup_amount = conn.execute(
        "SELECT COALESCE(SUM(amount),0) a FROM contracts WHERE suspected_duplicate=1").fetchone()["a"]
    entity_split = conn.execute(
        """SELECT co.entity_type, COUNT(*) n, SUM(c.amount) total
           FROM contracts c JOIN companies co ON co.id=c.company_id
           WHERE c.amount IS NOT NULL GROUP BY co.entity_type ORDER BY total DESC""").fetchall()
    return render_template("methodology.html", quality=quality,
                           dup_amount=dup_amount, entity_split=entity_split)


@app.route("/contracts/<int:cid>/")
def contract_detail(cid):
    conn = db()
    c = conn.execute(
        """SELECT c.*, co.name company, co.slug company_slug, co.corporate_number,
                  co.entity_type,
                  dcat.name domain_name, dcat.slug domain_slug,
                  ncat.name nature_name, ncat.slug nature_slug,
                  s.title source_title, s.url source_url, s.landing_page, s.license,
                  r.raw_row_json, r.raw_company, r.raw_amount, r.raw_contract_date,
                  r.raw_agency
           FROM contracts c
           LEFT JOIN companies co ON co.id=c.company_id
           LEFT JOIN categories dcat ON dcat.id=c.domain_category_id
           LEFT JOIN categories ncat ON ncat.id=c.nature_category_id
           JOIN sources s ON s.id=c.source_id
           JOIN raw_contracts r ON r.id=c.raw_contract_id
           WHERE c.id=?""", (cid,)).fetchone()
    if not c:
        abort(404)
    return render_template("contract.html", c=c)


if __name__ == "__main__":
    app.run(debug=False, port=5000)
