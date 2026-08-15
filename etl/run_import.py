"""取込CLI: 取得(キャッシュ優先) → パース → raw投入 → 正規化。

使い方:
  python -m etl.run_import --years 2023 2024 2025 2026          # ネットワーク取得あり
  python -m etl.run_import --years 2023 2024 2025 2026 --offline # data/raw のみ使用

サイレント失敗を避けるため、件数・失敗数を必ずstdoutとimport_jobsに記録する。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys
from pathlib import Path

from .fetch import fetch_file, sha256_of
from .load import init_schema, insert_raw_rows, normalize_contracts, open_db, upsert_source
from .parse import ParseError, parse_source_file
from .sources import list_source_files

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "db" / "procurement.db"
SCHEMA = Path(__file__).resolve().parent / "schema.sql"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("import")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025, 2026],
                    help="対象会計年度(西暦, 令和5年度=2023)")
    ap.add_argument("--offline", action="store_true", help="ダウンロードせずdata/rawのみ使用")
    ap.add_argument("--sources", nargs="+", default=None,
                    help="対象ソース系統(atla / n-kanto)。省略時は全系統")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args(argv)

    conn = open_db(args.db)
    init_schema(conn, SCHEMA)

    job = {
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "files_fetched": 0, "files_cached": 0, "files_failed": 0,
        "rows_raw": 0, "rows_inserted": 0, "rows_updated": 0, "rows_deleted": 0,
        "parse_failures": 0, "companies_unresolved": 0, "uncategorized": 0,
        "suspected_duplicates": 0,
    }
    lines = []

    for sf in list_source_files(args.years, sources=args.sources):
        path = RAW_DIR / sf.cache_name
        if args.offline:
            if not path.exists() or path.stat().st_size == 0:
                continue
            status = "cached"
        else:
            status, path = fetch_file(sf, RAW_DIR)
        if status == "missing":
            continue
        if status == "error":
            job["files_failed"] += 1
            lines.append(f"FETCH_ERROR {sf.url}")
            continue
        job["files_fetched" if status == "fetched" else "files_cached"] += 1

        try:
            rows = parse_source_file(path, sf.file_format)
        except Exception as e:  # ParseError / BadZipFile / openpyxl例外
            # 1ファイルの破損で取込全体を止めない(ファイル単位で失敗記録)
            job["files_failed"] += 1
            lines.append(f"PARSE_ERROR {path.name}: {type(e).__name__}: {e}")
            log.error("parse error in %s: %s: %s", path.name, type(e).__name__, e)
            continue

        source_id = upsert_source(conn, sf, sha256_of(path), len(rows))
        ins, upd, deleted = insert_raw_rows(conn, source_id, sf.fiscal_year, rows)
        stats = normalize_contracts(conn, source_id, sf.method_group)
        conn.commit()

        job["rows_raw"] += len(rows)
        job["rows_inserted"] += ins
        job["rows_updated"] += upd
        job["rows_deleted"] += deleted
        job["parse_failures"] += stats["parse_failures"]
        job["companies_unresolved"] += stats["company_unresolved"]
        job["uncategorized"] += stats["uncategorized"]
        job["suspected_duplicates"] += stats["suspected_duplicates"]
        log.info("%s: rows=%d ins=%d upd=%d del=%d parse_fail=%d comp_unres=%d uncat=%d dup=%d",
                 sf.cache_name, len(rows), ins, upd, deleted,
                 stats["parse_failures"], stats["company_unresolved"],
                 stats["uncategorized"], stats["suspected_duplicates"])

    job["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO import_jobs(started_at, finished_at, files_fetched, files_cached, files_failed,
               rows_raw, rows_inserted, rows_updated, rows_deleted, parse_failures,
               companies_unresolved, uncategorized, suspected_duplicates, log)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (job["started_at"], job["finished_at"], job["files_fetched"], job["files_cached"],
         job["files_failed"], job["rows_raw"], job["rows_inserted"], job["rows_updated"],
         job["rows_deleted"], job["parse_failures"], job["companies_unresolved"],
         job["uncategorized"], job["suspected_duplicates"], "\n".join(lines)),
    )
    conn.commit()

    print("=== import summary ===")
    for k, v in job.items():
        print(f"  {k}: {v}")
    n = conn.execute("SELECT COUNT(*) c FROM contracts").fetchone()["c"]
    nc = conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
    print(f"  contracts total: {n}\n  companies total: {nc}")
    if job["files_failed"]:
        print("!! files_failed > 0 — ログを確認してください", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
