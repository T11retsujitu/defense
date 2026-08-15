"""データソース定義: 防衛装備庁 中央調達 契約に係る情報の公表。

掲載元ページ:
  https://www.mod.go.jp/atla/souhon/supply/jisseki/rakusatu/index.html
  (注: 同ページHTMLはCloudflareのbot対策で機械取得不可。ファイル本体は直接取得可能。
   URLは年度・月・契約区分から機械的に構成できる)

命名規則(実ファイルで確認済み):
  令和5-6年度: kohyo_r{YY}/{YY}_{kyoso|zuikei}_kijunijo-{MM}.xlsx
  令和7年度以降: kohyo_r{YY}/{YY}_{kyoso|zuikei}-{MM}.xlsx
   (令和7年4月1日の予決令改正に伴う公表基準変更でファイル名から「基準以上」が消えた)

  YY = 令和年(2桁ゼロ埋め), MM = 暦月(01-12)
  kyoso = 競争入札(一般競争・指名競争), zuikei = 随意契約

利用可能年度: 令和5年度(2023)以降。令和4年度以前は旧URL体系で、現在は404。
"""
from __future__ import annotations

from dataclasses import dataclass

BASE_URL = "https://www.mod.go.jp/atla/souhon/supply/jisseki/rakusatu"
LANDING_PAGE = f"{BASE_URL}/index.html"
ORGANIZATION = "防衛装備庁"
LICENSE = "公共データ利用規約(第1.0版)(PDL1.0) https://www.mod.go.jp/j/info/contents.html"

# 会計年度(西暦, 令和5年度=2023) -> 令和年
def reiwa_of(fiscal_year: int) -> int:
    return fiscal_year - 2018

METHOD_GROUPS = {"kyoso": "競争入札", "zuikei": "随意契約"}

# 会計年度内の暦月の並び(4月始まり)
FY_MONTHS = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


@dataclass(frozen=True)
class SourceFile:
    fiscal_year: int      # 西暦表記の年度(令和5年度=2023)
    method_group: str     # kyoso / zuikei
    month: int            # 暦月

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
        return f"{BASE_URL}/kohyo_r{self.reiwa:02d}/{self.file_name}"

    @property
    def title(self) -> str:
        return (
            f"契約に係る情報の公表(中央調達分) 令和{self.reiwa}年度 "
            f"{METHOD_GROUPS[self.method_group]} {self.month}月分"
        )

    @property
    def cache_name(self) -> str:
        return f"kohyo_r{self.reiwa:02d}/{self.file_name}"


def list_source_files(fiscal_years: list[int]) -> list[SourceFile]:
    """指定年度の全ファイル候補(存在しない月は取得時に404として記録)。"""
    out = []
    for fy in fiscal_years:
        for m in FY_MONTHS:
            for g in METHOD_GROUPS:
                out.append(SourceFile(fy, g, m))
    return out
