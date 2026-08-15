"""データソース定義。

現在の収録ソース系統:
  atla    — 防衛装備庁 中央調達「契約に係る情報の公表(中央調達分)」(xlsx, 月次)
  n-kanto — 北関東防衛局 地方調達「入札結果等(物品・役務)」(PDF, 月次) ※地方調達PoC

いずれも「公共調達の適正化について」(平成18年財計第2017号)に基づく公表様式で、
列構成(件名/契約担当官/締結日/相手方/法人番号/予定価格/契約金額/落札率)は共通。

掲載元HTMLはCloudflareのbot対策でデータセンターからの機械取得が403になるため、
ファイルURLは (1)機械的な命名規則 + (2)国立国会図書館WARPのアーカイブHTMLから
抽出した実在ファイル名(irregular分) で構成する。ファイル本体は直接取得できる。

命名規則(実ファイルで確認済み):
  atla:
    令和5-6年度: kohyo_r{YY}/{YY}_{kyoso|zuikei}_kijunijo-{MM}.xlsx
    令和7年度以降: kohyo_r{YY}/{YY}_{kyoso|zuikei}-{MM}.xlsx
    YY=令和年, MM=暦月
  n-kanto:
    nyusatsu-keiyaku/tyoutatu/kekka/{n|z}-b-{YY}{MM}.pdf
    n=競争入札 / z=随意契約, b=物品役務(工事系列 n-k/z-k は未収録),
    YY=令和年, MM=暦月。存在しない月あり(該当契約なし)。

利用可能年度: atlaは令和5年度(2023)以降(令和4年度以前は404)。
n-kantoは平成28年まで遡れるが、中央調達と揃えて令和5年度以降を収録対象とする。
"""
from __future__ import annotations

from dataclasses import dataclass

LICENSE = "公共データ利用規約(第1.0版)(PDL1.0) https://www.mod.go.jp/j/info/contents.html"

METHOD_GROUPS = {"kyoso": "競争入札", "zuikei": "随意契約"}

# 会計年度内の暦月の並び(4月始まり)
FY_MONTHS = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


# 会計年度(西暦, 令和5年度=2023) -> 令和年
def reiwa_of(fiscal_year: int) -> int:
    return fiscal_year - 2018


# ---- 防衛装備庁 中央調達 ----

ATLA_BASE_URL = "https://www.mod.go.jp/atla/souhon/supply/jisseki/rakusatu"


@dataclass(frozen=True)
class SourceFile:
    """中央調達(atla)の公表ファイル。"""
    fiscal_year: int      # 西暦表記の年度(令和5年度=2023)
    method_group: str     # kyoso / zuikei
    month: int            # 暦月

    source_key = "atla"
    organization = "防衛装備庁"
    landing_page = f"{ATLA_BASE_URL}/index.html"
    file_format = "xlsx"

    @property
    def reiwa(self) -> int:
        return reiwa_of(self.fiscal_year)

    @property
    def file_name(self) -> str:
        yy = f"{self.reiwa:02d}"
        mm = f"{self.month:02d}"
        if self.fiscal_year >= 2025:  # 令和7年度以降
            return f"{yy}_{self.method_group}-{mm}.xlsx"
        return f"{yy}_{self.method_group}_kijunijo-{mm}.xlsx"

    @property
    def url(self) -> str:
        return f"{ATLA_BASE_URL}/kohyo_r{self.reiwa:02d}/{self.file_name}"

    @property
    def title(self) -> str:
        return (
            f"契約に係る情報の公表(中央調達分) 令和{self.reiwa}年度 "
            f"{METHOD_GROUPS[self.method_group]} {self.month}月分"
        )

    @property
    def cache_name(self) -> str:
        return f"kohyo_r{self.reiwa:02d}/{self.file_name}"


# ---- 北関東防衛局 地方調達 (PoC) ----

NKANTO_BASE_URL = "https://www.mod.go.jp/rdb/n-kanto/nyusatsu-keiyaku/tyoutatu/kekka"

# 命名規則から外れる実在ファイル名(WARPアーカイブのインデックスHTMLで確認)。
# キー: (method_group, 令和年, 暦月) -> ファイル名
NKANTO_IRREGULAR = {
    ("kyoso", 6, 4): "n-b-0604-2.pdf",
}


@dataclass(frozen=True)
class NKantoSourceFile:
    """北関東防衛局(n-kanto)の公表ファイル(物品・役務)。

    ファイル名の {YY}{MM} は「令和YY年の暦月MM」(年度ではない)。
    """
    reiwa_year: int       # 令和年(暦年ベース: 令和7年=2025)
    method_group: str     # kyoso(n-) / zuikei(z-)
    month: int            # 暦月

    source_key = "n-kanto"
    organization = "北関東防衛局"
    landing_page = ("https://www.mod.go.jp/rdb/n-kanto/nyusatsu-keiyaku/"
                    "nyusatukekkasonota/nyusatukekkasonota.html")
    file_format = "pdf"

    @property
    def calendar_year(self) -> int:
        return 2018 + self.reiwa_year

    @property
    def fiscal_year(self) -> int:
        return self.calendar_year if self.month >= 4 else self.calendar_year - 1

    @property
    def file_name(self) -> str:
        irregular = NKANTO_IRREGULAR.get((self.method_group, self.reiwa_year, self.month))
        if irregular:
            return irregular
        prefix = "n" if self.method_group == "kyoso" else "z"
        return f"{prefix}-b-{self.reiwa_year:02d}{self.month:02d}.pdf"

    @property
    def url(self) -> str:
        return f"{NKANTO_BASE_URL}/{self.file_name}"

    @property
    def title(self) -> str:
        return (
            f"入札結果等(物品・役務) 北関東防衛局 令和{self.reiwa_year}年{self.month}月 "
            f"{METHOD_GROUPS[self.method_group]}分"
        )

    @property
    def cache_name(self) -> str:
        return f"rdb_n_kanto/{self.file_name}"


def list_source_files(fiscal_years: list[int], sources: list[str] | None = None) -> list:
    """指定年度の全ファイル候補(存在しない月は取得時に404として記録)。

    sources で系統を絞れる(例: ["n-kanto"])。省略時は全系統。
    """
    out: list = []
    for fy in fiscal_years:
        for m in FY_MONTHS:
            for g in METHOD_GROUPS:
                if sources is None or "atla" in sources:
                    out.append(SourceFile(fy, g, m))
                if sources is None or "n-kanto" in sources:
                    # 年度内の暦月 -> 令和年(1-3月は翌暦年)
                    reiwa = reiwa_of(fy) + (1 if m <= 3 else 0)
                    out.append(NKantoSourceFile(reiwa, g, m))
    return out
