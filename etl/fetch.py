"""公表ファイルのダウンロード(キャッシュ優先・低頻度アクセス)。

- サーバー負荷を避けるためリクエスト間に固定間隔を置く。
- 404(未公表月)はマーカーに確認日時を保存し、TTL内は再試行しない。
  進行中年度は短TTL(後日公表されるため)、終了年度は長TTL。
- 取得済みxlsxも定期的にETag/Last-Modifiedで更新確認する(公表元の訂正を検知)。
  メタ情報は <file>.meta.json に保存する。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from .sources import SourceFile

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; defense-procurement-db-poc/0.1; "
    "research use; contact: repository owner)"
)
REQUEST_INTERVAL_SEC = 3.0

# 404マーカーのTTL: 進行中(または未来)年度は未公表→公表への遷移があるため短く。
MISSING_TTL_CURRENT_FY = datetime.timedelta(days=3)
MISSING_TTL_PAST_FY = datetime.timedelta(days=90)
# 取得済みファイルの更新確認間隔(条件付きGET)
RECHECK_INTERVAL = datetime.timedelta(days=30)

_last_request_at = 0.0


def _throttle():
    global _last_request_at
    wait = REQUEST_INTERVAL_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _current_fiscal_year(today: datetime.date | None = None) -> int:
    d = today or datetime.date.today()
    return d.year if d.month >= 4 else d.year - 1


def _read_meta(path: Path) -> dict:
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_meta(path: Path, meta: dict):
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))


def _missing_marker_expired(marker: Path, sf: SourceFile) -> bool:
    try:
        data = json.loads(marker.read_text())
        checked = datetime.datetime.fromisoformat(data["checked_at"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
        return True  # 旧形式(日時なし)は期限切れ扱いで再確認
    ttl = MISSING_TTL_CURRENT_FY if sf.fiscal_year >= _current_fiscal_year() else MISSING_TTL_PAST_FY
    return _now() - checked > ttl


def _download(sf: SourceFile, dest: Path, prev_meta: dict | None = None):
    """条件付きGET。返り値: ('fetched'|'unchanged'|'missing'|'error', headers|None)"""
    _throttle()
    headers = {"User-Agent": USER_AGENT}
    if prev_meta:
        if prev_meta.get("etag"):
            headers["If-None-Match"] = prev_meta["etag"]
        if prev_meta.get("last_modified"):
            headers["If-Modified-Since"] = prev_meta["last_modified"]
    req = urllib.request.Request(sf.url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            resp_headers = dict(resp.headers)
        if data[:2] != b"PK":  # xlsx = zip
            log.warning("not xlsx content: %s", sf.url)
            return "error", None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return "fetched", resp_headers
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return "unchanged", None
        if e.code == 404:
            return "missing", None
        log.error("http error %s: %s", e.code, sf.url)
        return "error", None
    except Exception as e:
        log.error("fetch failed: %s (%s)", sf.url, e)
        return "error", None


def fetch_file(sf: SourceFile, raw_dir: Path) -> tuple[str, Path | None]:
    """1ファイル取得。返り値: (status, path)。

    status: cached(TTL内・変更なし) / fetched(新規または更新) / missing / error
    """
    dest = raw_dir / sf.cache_name
    miss_marker = dest.with_suffix(dest.suffix + ".404")

    # 取得済み: 再確認間隔内ならそのまま、超過なら条件付きGETで更新確認
    if dest.exists() and dest.stat().st_size > 0:
        meta = _read_meta(dest)
        checked = None
        if meta.get("checked_at"):
            try:
                checked = datetime.datetime.fromisoformat(meta["checked_at"])
            except ValueError:
                pass
        if checked and _now() - checked <= RECHECK_INTERVAL:
            return "cached", dest
        status, headers = _download(sf, dest, prev_meta=meta)
        if status == "fetched":
            _write_meta(dest, {
                "checked_at": _now().isoformat(),
                "etag": (headers or {}).get("ETag"),
                "last_modified": (headers or {}).get("Last-Modified"),
            })
            log.info("updated: %s", sf.url)
            return "fetched", dest
        if status == "unchanged":
            meta["checked_at"] = _now().isoformat()
            _write_meta(dest, meta)
            return "cached", dest
        # 再確認の失敗・404は既存キャッシュを維持(公表元の一時的な問題の可能性)
        log.warning("recheck %s for %s — keeping cached copy", status, sf.url)
        return "cached", dest

    # 未取得: 404マーカーがTTL内なら再試行しない
    if miss_marker.exists() and not _missing_marker_expired(miss_marker, sf):
        return "missing", None

    status, headers = _download(sf, dest)
    if status == "fetched":
        miss_marker.unlink(missing_ok=True)
        _write_meta(dest, {
            "checked_at": _now().isoformat(),
            "etag": (headers or {}).get("ETag"),
            "last_modified": (headers or {}).get("Last-Modified"),
        })
        log.info("fetched %s (%d bytes)", sf.url, dest.stat().st_size)
        return "fetched", dest
    if status == "missing":
        miss_marker.parent.mkdir(parents=True, exist_ok=True)
        miss_marker.write_text(json.dumps({"checked_at": _now().isoformat()}))
        log.info("missing (404): %s", sf.url)
        return "missing", None
    return "error", None
