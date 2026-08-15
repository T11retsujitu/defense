"""SQLiteへの投入: sources / raw_contracts / companies / contracts。

raw層は原文のまま、normalized層(contracts)は normalize.py の結果を格納する。
正規化に失敗した値はNULL + normalization_flags に記録し、行自体は破棄しない。
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from . import normalize as N
from .categories import CATEGORY_RULES, CATEGORY_SLUGS, UNCLASSIFIED, classify
from .companies import SEED_COMPANIES, lookup_foreign_entity, seed_entity_type
from .parse import RawRow
from .sources import LICENSE, LANDING_PAGE, ORGANIZATION, SourceFile


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: Path):
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    _seed_categories(conn)
    _seed_companies(conn)
    conn.commit()


def _seed_categories(conn):
    for i, (name, slug, _kw) in enumerate(CATEGORY_RULES):
        conn.execute(
            "INSERT OR IGNORE INTO categories(name, slug, sort_order) VALUES (?,?,?)",
            (name, slug, i),
        )
    conn.execute(
        "INSERT OR IGNORE INTO categories(name, slug, sort_order) VALUES (?,?,?)",
        (UNCLASSIFIED, CATEGORY_SLUGS[UNCLASSIFIED], 998),
    )


def _seed_companies(conn):
    for cn, (name, slug) in SEED_COMPANIES.items():
        conn.execute(
            """INSERT OR IGNORE INTO companies(corporate_number, name, normalized_name, slug, entity_type)
               VALUES (?,?,?,?,?)""",
            (cn, name, N.normalize_company_name(name), slug, seed_entity_type(name)),
        )
        row = conn.execute("SELECT id FROM companies WHERE corporate_number=?", (cn,)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO company_aliases(alias, company_id, source, confidence) VALUES (?,?,?,?)",
            (N.normalize_company_name(name), row["id"], "seed", 1.0),
        )


def upsert_source(conn, sf: SourceFile, sha256: str, row_count: int) -> int:
    conn.execute(
        """INSERT INTO sources(organization, title, url, landing_page, fiscal_year,
                               method_group, month, file_name, sha256, retrieved_at, license, row_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET sha256=excluded.sha256,
               retrieved_at=excluded.retrieved_at, row_count=excluded.row_count""",
        (
            ORGANIZATION, sf.title, sf.url, LANDING_PAGE, sf.fiscal_year,
            sf.method_group, sf.month, sf.file_name, sha256,
            datetime.datetime.now(datetime.timezone.utc).isoformat(), LICENSE, row_count,
        ),
    )
    return conn.execute("SELECT id FROM sources WHERE url=?", (sf.url,)).fetchone()["id"]


def insert_raw_rows(conn, source_id: int, fiscal_year: int, rows: list[RawRow]) -> tuple[int, int]:
    """raw_contracts へUPSERT。返り値: (inserted, updated)"""
    ins = upd = 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for r in rows:
        v = r.values

        def s(field):
            val = v.get(field)
            if val is None:
                return None
            if isinstance(val, (datetime.datetime, datetime.date)):
                return val.isoformat()
            return str(val)

        existing = conn.execute(
            "SELECT id, raw_row_json FROM raw_contracts WHERE source_id=? AND row_index=?",
            (source_id, r.row_index),
        ).fetchone()
        params = (
            fiscal_year, s("title"), s("quantity"), s("unit"), s("agency"),
            s("contract_date"), s("company"), s("corporate_number"),
            s("planned_price"), s("amount"), s("award_rate"),
            s("method_detail"), s("remarks"), r.row_json, now,
        )
        if existing:
            if existing["raw_row_json"] != r.row_json:
                conn.execute(
                    """UPDATE raw_contracts SET fiscal_year=?, raw_title=?, raw_quantity=?, raw_unit=?,
                       raw_agency=?, raw_contract_date=?, raw_company=?, raw_corporate_number=?,
                       raw_planned_price=?, raw_amount=?, raw_award_rate=?, raw_method_detail=?,
                       raw_remarks=?, raw_row_json=?, imported_at=? WHERE id=?""",
                    params + (existing["id"],),
                )
                upd += 1
        else:
            conn.execute(
                """INSERT INTO raw_contracts(source_id, row_index, fiscal_year, raw_title, raw_quantity,
                       raw_unit, raw_agency, raw_contract_date, raw_company, raw_corporate_number,
                       raw_planned_price, raw_amount, raw_award_rate, raw_method_detail, raw_remarks,
                       raw_row_json, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, r.row_index) + params,
            )
            ins += 1
    return ins, upd


def _resolve_company(conn, raw_company: str | None, raw_cn) -> tuple[int | None, bool]:
    """企業解決。返り値: (company_id, resolved_high_confidence)"""
    name, _addr = N.split_company_cell(raw_company)
    if not name:
        return None, False
    cn = N.clean_corporate_number(raw_cn)
    norm = N.normalize_company_name(name)

    if cn:
        row = conn.execute("SELECT id FROM companies WHERE corporate_number=?", (cn,)).fetchone()
        if row:
            conn.execute(
                "INSERT OR IGNORE INTO company_aliases(alias, company_id, source, confidence) VALUES (?,?,?,?)",
                (norm, row["id"], "corporate_number", 1.0),
            )
            return row["id"], True
        # 法人番号つき新規企業: 公表名で登録(slugは法人番号ベース)
        conn.execute(
            """INSERT INTO companies(corporate_number, name, normalized_name, slug, entity_type)
               VALUES (?,?,?,?,?)""",
            (cn, name, norm, f"c{cn}", seed_entity_type(name)),
        )
        cid = conn.execute("SELECT id FROM companies WHERE corporate_number=?", (cn,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_aliases(alias, company_id, source, confidence) VALUES (?,?,?,?)",
            (norm, cid, "corporate_number", 1.0),
        )
        return cid, True

    # 法人番号なし: 外国政府等シード → alias照合
    fe = lookup_foreign_entity(name)
    if fe:
        canon, slug, etype = fe
        row = conn.execute("SELECT id FROM companies WHERE slug=?", (slug,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO companies(corporate_number, name, normalized_name, slug, entity_type) VALUES (NULL,?,?,?,?)",
                (canon, N.normalize_company_name(canon), slug, etype),
            )
            row = conn.execute("SELECT id FROM companies WHERE slug=?", (slug,)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO company_aliases(alias, company_id, source, confidence) VALUES (?,?,?,?)",
            (norm, row["id"], "seed", 1.0),
        )
        return row["id"], True

    row = conn.execute(
        """SELECT company_id AS id, confidence FROM company_aliases WHERE alias=?
           ORDER BY confidence DESC LIMIT 1""",
        (norm,),
    ).fetchone()
    if row:
        return row["id"], row["confidence"] >= 0.9

    # 未知・法人番号なし: 表記正規化名単位で自動登録(低確信度)。
    # 文字列類似での他社への統合はしない。
    slug = "u" + format(abs(hash(norm)) % 10**10, "010d")
    conn.execute(
        "INSERT INTO companies(corporate_number, name, normalized_name, slug, entity_type) VALUES (NULL,?,?,?,?)",
        (name, norm, slug, "other"),
    )
    cid = conn.execute("SELECT id FROM companies WHERE slug=?", (slug,)).fetchone()["id"]
    conn.execute(
        "INSERT OR IGNORE INTO company_aliases(alias, company_id, source, confidence) VALUES (?,?,?,?)",
        (norm, cid, "auto", 0.5),
    )
    return cid, False


def normalize_contracts(conn, source_id: int, method_group: str) -> dict:
    """指定sourceのraw_contractsをcontractsへ正規化投入(全再生成)。統計を返す。"""
    stats = {"rows": 0, "parse_failures": 0, "company_unresolved": 0, "uncategorized": 0}
    cat_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    raws = conn.execute("SELECT * FROM raw_contracts WHERE source_id=?", (source_id,)).fetchall()
    for raw in raws:
        stats["rows"] += 1
        flags = []
        d = N.parse_date(raw["raw_contract_date"])
        if d is None:
            flags.append("date_failed")
        amount = N.parse_amount(raw["raw_amount"])
        if amount is None:
            flags.append("amount_failed")
        planned = N.parse_amount(raw["raw_planned_price"])
        rate = N.parse_rate(raw["raw_award_rate"])

        company_id, resolved = _resolve_company(conn, raw["raw_company"], raw["raw_corporate_number"])
        if company_id is None:
            flags.append("company_missing")
        elif not resolved:
            flags.append("company_unresolved")
            stats["company_unresolved"] += 1

        title = (raw["raw_title"] or "").strip()
        cat_name, conf = classify(title)
        if cat_name == UNCLASSIFIED:
            stats["uncategorized"] += 1

        if method_group == "zuikei":
            method = "随意契約"
        else:
            detail = N.nfkc(raw["raw_method_detail"] or "")
            method = "指名競争入札" if "指名" in detail else "一般競争入札"

        fy = N.fiscal_year_of(d) if d else raw["fiscal_year"]
        if d and fy != raw["fiscal_year"]:
            flags.append("fy_mismatch")

        status = "ok" if not flags else "partial"
        if "date_failed" in flags or "amount_failed" in flags:
            stats["parse_failures"] += 1

        conn.execute(
            """INSERT INTO contracts(raw_contract_id, source_id, fiscal_year, contract_date, company_id,
                   title, amount, planned_price, award_rate, procurement_method, agency, agency_detail,
                   category_id, classification_confidence, normalization_status, normalization_flags,
                   normalization_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(raw_contract_id) DO UPDATE SET
                   fiscal_year=excluded.fiscal_year, contract_date=excluded.contract_date,
                   company_id=excluded.company_id, title=excluded.title, amount=excluded.amount,
                   planned_price=excluded.planned_price, award_rate=excluded.award_rate,
                   procurement_method=excluded.procurement_method, agency=excluded.agency,
                   agency_detail=excluded.agency_detail, category_id=excluded.category_id,
                   classification_confidence=excluded.classification_confidence,
                   normalization_status=excluded.normalization_status,
                   normalization_flags=excluded.normalization_flags,
                   normalization_version=excluded.normalization_version""",
            (
                raw["id"], source_id, raw["fiscal_year"],
                d.isoformat() if d else None, company_id, title, amount, planned,
                rate, method, ORGANIZATION, N.shorten_agency_detail(raw["raw_agency"]),
                cat_ids[cat_name], conf, status,
                ";".join(flags) if flags else None, N.NORMALIZATION_VERSION,
            ),
        )
    return stats
