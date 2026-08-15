"""静的サイト書き出し(GitHub Pagesプレビュー用)。

FlaskアプリをSTATIC_BUILD=1で起動し、全ページをtest_clientで描画して
dist/ に保存する。クエリ付きURL(?year= 等)は静的ディレクトリへマップし、
HTML内のリンクを書き換える。

使い方:
  STATIC_BASE=/defense python3 -m web.freeze
  → dist/ に生成。gh-pagesブランチとして公開する。

注意: これはプレビュー用であり、本番構成(docs/deployment.md)はSSR。
キーワード検索・金額フィルタ等の動的機能は静的版では動かない(UIに明示)。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

os.environ["STATIC_BUILD"] = "1"

from web.app import ROOT, app, db  # noqa: E402

BASE = os.environ.get("STATIC_BASE", "/defense").rstrip("/")
OUT = ROOT / "dist"


def norm_url(url: str) -> str:
    """パス?クエリ を正規形(クエリキーでソート)に。"""
    s = urlsplit(url)
    q = sorted(parse_qsl(s.query))
    return s.path + ("?" + urlencode(q) if q else "")


class Freezer:
    def __init__(self):
        self.mapping: dict[str, str] = {}  # norm_url -> 出力相対パス(末尾/)
        self.queue: list[str] = []

    def add(self, url: str, outrel: str):
        key = norm_url(url)
        if key in self.mapping:
            return
        assert outrel == "" or outrel.endswith("/"), outrel
        self.mapping[key] = outrel
        self.queue.append(url)

    def alias(self, url: str, outrel: str):
        """生成済みページへの別URL(例: page=1)をマップのみ登録。"""
        self.mapping.setdefault(norm_url(url), outrel)

    def static_href(self, url: str) -> str:
        """href属性値を静的パスへ変換。"""
        s = urlsplit(url)
        if not s.path.startswith("/"):
            return url
        key = norm_url(url)
        if key in self.mapping:
            return f"{BASE}/{self.mapping[key]}"
        if s.path.startswith("/static/"):
            return BASE + s.path
        # 未生成のクエリ組合せはクエリを落として基底ページへ
        base_key = norm_url(s.path)
        if base_key in self.mapping:
            return f"{BASE}/{self.mapping[base_key]}"
        return BASE + s.path

    def rewrite(self, html: str) -> str:
        def repl(m):
            attr, url = m.group(1), m.group(2)
            return f'{attr}="{self.static_href(url)}"'
        return re.sub(r'(href|src|action)="(/[^"]*)"', repl, html)

    def run(self):
        if OUT.exists():
            shutil.rmtree(OUT)
        OUT.mkdir(parents=True)
        (OUT / ".nojekyll").write_text("")
        shutil.copytree(ROOT / "web" / "static", OUT / "static")

        client = app.test_client()
        done = 0
        while self.queue:
            url = self.queue.pop(0)
            outrel = self.mapping[norm_url(url)]
            resp = client.get(url)
            if resp.status_code != 200:
                print(f"!! {resp.status_code} {url}", file=sys.stderr)
                continue
            html = self.rewrite(resp.get_data(as_text=True))
            dest = OUT / outrel / "index.html"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(html, encoding="utf-8")
            done += 1
            if done % 1000 == 0:
                print(f"  {done} pages...")
        print(f"generated {done} pages -> {OUT}")


def main():
    fr = Freezer()
    with app.app_context():
        conn = db()
        years = [r[0] for r in conn.execute(
            "SELECT DISTINCT fiscal_year FROM contracts ORDER BY 1 DESC")]
        n_all = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
        per = 100

        fr.add("/", "")
        fr.add("/sources/", "sources/")
        fr.add("/methodology/", "methodology/")
        fr.add("/categories/", "categories/")

        # 契約一覧: 全件 + 年度別 (全ページ)
        fr.add("/contracts/", "contracts/")
        fr.alias("/contracts/?page=1", "contracts/")
        for p in range(2, -(-n_all // per) + 1):
            fr.add(f"/contracts/?page={p}", f"contracts/p{p}/")
        for y in years:
            ny = conn.execute(
                "SELECT COUNT(*) FROM contracts WHERE fiscal_year=?", (y,)).fetchone()[0]
            fr.add(f"/contracts/?year={y}", f"contracts/y{y}/")
            fr.alias(f"/contracts/?page=1&year={y}", f"contracts/y{y}/")
            for p in range(2, -(-ny // per) + 1):
                fr.add(f"/contracts/?page={p}&year={y}", f"contracts/y{y}/p{p}/")

        # 企業
        fr.add("/companies/", "companies/")
        for y in years:
            fr.add(f"/companies/?year={y}", f"companies/y{y}/")
        for (slug,) in conn.execute(
                """SELECT DISTINCT co.slug FROM companies co
                   JOIN contracts c ON c.company_id = co.id"""):
            fr.add(f"/companies/{slug}/", f"companies/{slug}/")

        # カテゴリ
        for (slug,) in conn.execute("SELECT slug FROM categories"):
            fr.add(f"/categories/{slug}/", f"categories/{slug}/")

        # ランキング
        for scope in ("companies", "counterparties"):
            fr.add(f"/rankings/{scope}/", f"rankings/{scope}/")
            for y in years:
                fr.add(f"/rankings/{scope}/?year={y}", f"rankings/{scope}/y{y}/")

        # 契約詳細(全件)
        for (cid,) in conn.execute("SELECT id FROM contracts"):
            fr.add(f"/contracts/{cid}/", f"contracts/{cid}/")

    print(f"planned pages: {len(fr.mapping)}")
    fr.run()


if __name__ == "__main__":
    main()
