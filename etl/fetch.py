"""公表ファイルのダウンロード(キャッシュ優先・低頻度アクセス)。

- data/raw/ に保存済みのファイルは再ダウンロードしない。
- サーバー負荷を避けるためリクエスト間に固定間隔を置く。
- 404(未公表月)は missing として記録し、リトライしない(404マーカーを保存)。
"""
from __future__ import annotations

import hashlib
import logging
import time
import urllib.request
from pathlib import Path

from .sources import SourceFile

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; defense-procurement-db-poc/0.1; "
    "research use; contact: repository owner)"
)
REQUEST_INTERVAL_SEC = 3.0
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


def fetch_file(sf: SourceFile, raw_dir: Path) -> tuple[str, Path | None]:
    """1ファイル取得。返り値: (status, path)。status: cached/fetched/missing/error"""
    dest = raw_dir / sf.cache_name
    miss_marker = dest.with_suffix(dest.suffix + ".404")
    if dest.exists() and dest.stat().st_size > 0:
        return "cached", dest
    if miss_marker.exists():
        return "missing", None
    dest.parent.mkdir(parents=True, exist_ok=True)
    _throttle()
    req = urllib.request.Request(sf.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        ctype_ok = data[:2] == b"PK"  # xlsx = zip
        if not ctype_ok:
            log.warning("not xlsx content: %s", sf.url)
            return "error", None
        dest.write_bytes(data)
        log.info("fetched %s (%d bytes)", sf.url, len(data))
        return "fetched", dest
    except urllib.error.HTTPError as e:
        if e.code == 404:
            miss_marker.parent.mkdir(parents=True, exist_ok=True)
            miss_marker.write_text("404")
            log.info("missing (404): %s", sf.url)
            return "missing", None
        log.error("http error %s: %s", e.code, sf.url)
        return "error", None
    except Exception as e:  # ネットワーク断など
        log.error("fetch failed: %s (%s)", sf.url, e)
        return "error", None
