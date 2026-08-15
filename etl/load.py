"""SQLiteへの投入: sources / raw_contracts / companies / contracts。

raw層は原文のまま、normalized層(contracts)は normalize.py の結果を格納する。
正規化に失敗した値はNULL + normalization_flags に記録し、行自体は破棄しない。

差し替え対応: 取込はソース単位で raw を UPSERT した後、最新ファイルに
存在しない row_index の raw行とそのcontractsを削除する(ゴースト行を残さない)。
"""
from __future__ import annotations

import datetime
import hashlib
import sqlite3
from pathlib import Path

from . import normalize as N
from .categories import (
    DOMAIN_RULES, DOMAIN_SLUGS, DOMAIN_UNCLASSIFIED,
    NATURE_RULES, NATURE_SLUGS, NATURE_DEFAULT, classify,
)
from .companies import (
    SEED_COMPANIES, US_GOV_RE, guess_entity_type_without_cn,
    lookup_foreign_entity, seed_entity_type,
)
from .parse import RawRow
from .sources import LICENSE, LANDING_PAGE, ORGANIZATION, SourceFile


def _stable_hash(text: str, n: int = 12) -> str:
    """再構築しても変わらない決定的ハッシュ(slug用)。Pythonのhash()は使わない。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


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
    for i, (name, slug, _kw) in enumerate(DOMAIN_RULES):
        conn.execute(
            "INSERT OR IGNORE INTO categories(axis, name, slug, sort_order) VALUES ('domain',?,?,?)",
            (name, slug, i),
        )
    conn.execute(
        "INSERT OR IGNORE INTO categories(axis, name, slug, sort_order) VALUES ('domain',?,?,998)",
        (DOMAIN_UNCLASSIFIED, DOMAIN_SLUGS[DOMAIN_UNCLASSIFIED]),
    )
    for i, (name, slug, _kw) in enumerate(NATURE_RULES):
        conn.execute(
            "INSERT OR IGNORE INTO categories(axis, name, slug, sort_order) VALUES ('nature',?,?,?)",
            (name, slug, i),
        )
    conn.execute(
        "INSERT OR IGNORE INTO categories(axis, name, slug, sort_order) VALUES ('nature',?,?,997)",
        (NATURE_DEFAULT, NATURE_SLUGS[NATURE_DEFAULT]),
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


def insert_raw_rows(conn, source_id: int, fiscal_year: int, rows: list[RawRow]) -> tuple[int, int, int]:
    """raw_contracts をソース単位で同期。返り値: (inserted, updated, deleted)

    - 同一ファイル内で行内容(raw_row_json)が完全一致する行には dup_group を付け、
      2行目以降を suspected_duplicate=1 とする(公表元が同一契約を複数行掲載した
      疑い。真正な同条件別契約の可能性もあるため削除はしない)。
    - 最新ファイルに存在しない row_index の既存行(とそのcontracts)は削除する。
    """
    ins = upd = 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 完全一致重複の検出
    seen: dict[str, int] = {}
    dup_flags: dict[int, tuple[str | None, int]] = {}
    for r in rows:
        h = _stable_hash(r.row_json, 16)
        if r.row_json.strip("[]null, ") == "":
            dup_flags[r.row_index] = (None, 0)
            continue
        if h in seen:
            dup_flags[r.row_index] = (h, 1)
            # 初出行にもグループを付ける
            first_idx = seen[h]
            g, _f = dup_flags[first_idx]
            dup_flags[first_idx] = (h, _f)
        else:
            seen[h] = r.row_index
            dup_flags[r.row_index] = (None, 0)
    # 初出行のdup_group: グループに2行以上ある場合のみ付与
    group_counts: dict[str, int] = {}
    for g, _ in dup_flags.values():
        if g:
            group_counts[g] = group_counts.get(g, 0) + 1
    for r in rows:
        h = _stable_hash(r.row_json, 16)
        if h in group_counts:  # 2行目以降が存在するグループ
            g, f = dup_flags[r.row_index]
            dup_flags[r.row_index] = (h, f)

    for r in rows:
        v = r.values

        def s(field):
            val = v.get(field)
            if val is None:
                return None
            if isinstance(val, (datetime.datetime, datetime.date)):
                return val.isoformat()
            return str(val)

        dup_group, suspected = dup_flags[r.row_index]
        existing = conn.execute(
            "SELECT id, raw_row_json, dup_group, suspected_duplicate FROM raw_contracts WHERE source_id=? AND row_index=?",
            (source_id, r.row_index),
        ).fetchone()
        params = (
            fiscal_year, s("title"), s("quantity"), s("unit"), s("agency"),
            s("contract_date"), s("company"), s("corporate_number"),
            s("planned_price"), s("amount"), s("award_rate"),
            s("method_detail"), s("remarks"), r.row_json, dup_group, suspected, now,
        )
        if existing:
            if (existing["raw_row_json"] != r.row_json
                    or existing["dup_group"] != dup_group
                    or existing["suspected_duplicate"] != suspected):
                conn.execute(
                    """UPDATE raw_contracts SET fiscal_year=?, raw_title=?, raw_quantity=?, raw_unit=?,
                       raw_agency=?, raw_contract_date=?, raw_company=?, raw_corporate_number=?,
                       raw_planned_price=?, raw_amount=?, raw_award_rate=?, raw_method_detail=?,
                       raw_remarks=?, raw_row_json=?, dup_group=?, suspected_duplicate=?, imported_at=?
                       WHERE id=?""",
                    params + (existing["id"],),
                )
                upd += 1
        else:
            conn.execute(
                """INSERT INTO raw_contracts(source_id, row_index, fiscal_year, raw_title, raw_quantity,
                       raw_unit, raw_agency, raw_contract_date, raw_company, raw_corporate_number,
                       raw_planned_price, raw_amount, raw_award_rate, raw_method_detail, raw_remarks,
                       raw_row_json, dup_group, suspected_duplicate, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, r.row_index) + params,
            )
            ins += 1

    # 差し替えで消えた行を削除(contracts側も)
    current_indexes = [r.row_index for r in rows]
    placeholders = ",".join("?" * len(current_indexes)) or "NULL"
    gone = conn.execute(
        f"SELECT id FROM raw_contracts WHERE source_id=? AND row_index NOT IN ({placeholders})",
        [source_id] + current_indexes,
    ).fetchall()
    deleted = 0
    for g in gone:
        conn.execute("DELETE FROM contracts WHERE raw_contract_id=?", (g["id"],))
        conn.execute("DELETE FROM raw_contracts WHERE id=?", (g["id"],))
        deleted += 1
    return ins, upd, deleted


def _resolve_company(conn, raw_company: str | None, raw_cn) -> tuple[int | None, bool, str | None]:
    """企業解決。返り値: (company_id, resolved_high_confidence, flag)

    優先順位: 法人番号 → 外国政府シード → 米政府機関ルール → alias(単一対応時のみ) → 自動登録。
    aliasが複数法人に対応する場合(同名別法人)は自動解決せず未解決とする。
    """
    name, addr = N.split_company_cell(raw_company)
    if not name:
        return None, False, "company_missing"
    cn = N.clean_corporate_number(raw_cn)
    norm = N.normalize_company_name(name)

    if cn:
        row = conn.execute("SELECT id FROM companies WHERE corporate_number=?", (cn,)).fetchone()
        if row:
            conn.execute(
                "INSERT OR IGNORE INTO company_aliases(alias, company_id, source, confidence) VALUES (?,?,?,?)",
                (norm, row["id"], "corporate_number", 1.0),
            )
            return row["id"], True, None
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
        return cid, True, None

    # 法人番号なし: 外国政府等シード
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
        return row["id"], True, None

    # 「米〜省/庁/局/隊」= 米国政府機関(FMS相手方)
    if US_GOV_RE.match(norm):
        row = conn.execute(
            "SELECT id FROM companies WHERE normalized_name=? AND entity_type='foreign_government'",
            (norm,),
        ).fetchone()
        if not row:
            slug = "usgov-" + _stable_hash(norm, 8)
            conn.execute(
                "INSERT INTO companies(corporate_number, name, normalized_name, slug, entity_type) VALUES (NULL,?,?,?,?)",
                (norm + "(FMS)", norm, slug, "foreign_government"),
            )
            row = conn.execute(
                "SELECT id FROM companies WHERE normalized_name=? AND entity_type='foreign_government'",
                (norm,),
            ).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO company_aliases(alias, company_id, source, confidence) VALUES (?,?,?,?)",
            (norm, row["id"], "rule_us_gov", 0.9),
        )
        return row["id"], True, None

    # alias照合: 同一aliasが複数法人に対応する場合は自動解決しない(誤統合防止)
    matches = conn.execute(
        "SELECT DISTINCT company_id, confidence FROM company_aliases WHERE alias=?",
        (norm,),
    ).fetchall()
    ids = {m["company_id"] for m in matches}
    if len(ids) > 1:
        return None, False, "company_ambiguous"
    if len(ids) == 1:
        m = matches[0]
        return m["company_id"], m["confidence"] >= 0.9, None

    # 未知・法人番号なし: 表記正規化名単位で自動登録(決定的slug、低確信度)
    etype = guess_entity_type_without_cn(norm, addr)
    slug = "u" + _stable_hash(norm, 12)
    existing = conn.execute("SELECT id FROM companies WHERE slug=?", (slug,)).fetchone()
    if existing:
        cid = existing["id"]
    else:
        conn.execute(
            "INSERT INTO companies(corporate_number, name, normalized_name, slug, entity_type) VALUES (NULL,?,?,?,?)",
            (name, norm, slug, etype),
        )
        cid = conn.execute("SELECT id FROM companies WHERE slug=?", (slug,)).fetchone()["id"]
    conn.execute(
        "INSERT OR IGNORE INTO company_aliases(alias, company_id, source, confidence) VALUES (?,?,?,?)",
        (norm, cid, "auto", 0.5),
    )
    return cid, False, "company_unresolved"


def normalize_contracts(conn, source_id: int, method_group: str) -> dict:
    """指定sourceのraw_contractsをcontractsへ正規化投入(全再生成)。統計を返す。"""
    stats = {"rows": 0, "parse_failures": 0, "company_unresolved": 0,
             "uncategorized": 0, "annotation_rows": 0, "suspected_duplicates": 0}
    dom_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories WHERE axis='domain'")}
    nat_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories WHERE axis='nature'")}
    raws = conn.execute("SELECT * FROM raw_contracts WHERE source_id=?", (source_id,)).fetchall()
    for raw in raws:
        # 注記行(「※誤記修正」等): 契約実体がない行はcontractsに投入しない(rawには残る)
        if (raw["raw_title"] or "").startswith("※") and not raw["raw_company"] \
                and not raw["raw_amount"] and not raw["raw_contract_date"]:
            stats["annotation_rows"] += 1
            conn.execute("DELETE FROM contracts WHERE raw_contract_id=?", (raw["id"],))
            continue
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

        company_id, resolved, cflag = _resolve_company(conn, raw["raw_company"], raw["raw_corporate_number"])
        if cflag:
            flags.append(cflag)
            if cflag in ("company_unresolved", "company_ambiguous"):
                stats["company_unresolved"] += 1

        title = (raw["raw_title"] or "").strip()
        dom_name, dom_matched, nat_name, nat_matched = classify(title)
        if not dom_matched:
            stats["uncategorized"] += 1

        if method_group == "zuikei":
            method = "随意契約"
        else:
            detail = N.nfkc(raw["raw_method_detail"] or "")
            method = "指名競争入札" if "指名" in detail else "一般競争入札"

        # 年度は契約締結日から導出した値を保存する(日付不明時のみファイル年度)
        fy = N.fiscal_year_of(d) if d else raw["fiscal_year"]
        if d and fy != raw["fiscal_year"]:
            flags.append("fy_mismatch")

        agency = N.parse_agency(raw["raw_agency"])
        if raw["suspected_duplicate"]:
            stats["suspected_duplicates"] += 1

        status = "ok" if not flags else "partial"
        if "date_failed" in flags or "amount_failed" in flags:
            stats["parse_failures"] += 1

        conn.execute(
            """INSERT INTO contracts(raw_contract_id, source_id, fiscal_year, contract_date, company_id,
                   title, amount, planned_price, award_rate, procurement_method, agency,
                   agency_department, agency_location, domain_category_id, nature_category_id,
                   domain_rule_matched, nature_rule_matched, suspected_duplicate,
                   normalization_status, normalization_flags, normalization_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(raw_contract_id) DO UPDATE SET
                   fiscal_year=excluded.fiscal_year, contract_date=excluded.contract_date,
                   company_id=excluded.company_id, title=excluded.title, amount=excluded.amount,
                   planned_price=excluded.planned_price, award_rate=excluded.award_rate,
                   procurement_method=excluded.procurement_method, agency=excluded.agency,
                   agency_department=excluded.agency_department, agency_location=excluded.agency_location,
                   domain_category_id=excluded.domain_category_id,
                   nature_category_id=excluded.nature_category_id,
                   domain_rule_matched=excluded.domain_rule_matched,
                   nature_rule_matched=excluded.nature_rule_matched,
                   suspected_duplicate=excluded.suspected_duplicate,
                   normalization_status=excluded.normalization_status,
                   normalization_flags=excluded.normalization_flags,
                   normalization_version=excluded.normalization_version""",
            (
                raw["id"], source_id, fy,
                d.isoformat() if d else None, company_id, title, amount, planned,
                rate, method, agency["organization"] or ORGANIZATION,
                agency["department"], agency["location"],
                dom_ids[dom_name], nat_ids[nat_name],
                int(dom_matched), int(nat_matched), raw["suspected_duplicate"],
                status, ";".join(flags) if flags else None, N.NORMALIZATION_VERSION,
            ),
        )
    return stats
