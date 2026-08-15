"""金額・日付・法人名などの正規化ロジック。

raw_contracts の原文を入力に、正規化済みの値を返す。
失敗時は None を返し、呼び出し側が normalization_flags に記録する
(黙って破棄・推測で補完はしない)。
"""
from __future__ import annotations

import datetime
import re
import unicodedata

NORMALIZATION_VERSION = "0.2.0"

# 元号 -> 開始西暦(元年=1)
_ERA_STARTS = {
    "令和": 2018, "R": 2018, "Ｒ": 2018,
    "平成": 1988, "H": 1988, "Ｈ": 1988,
    "昭和": 1925, "S": 1925, "Ｓ": 1925,
}

_WAREKI_RE = re.compile(
    r"(令和|平成|昭和|[RHSＲＨＳ])\s*(\d{1,2}|元)\s*[.．年/\-]\s*(\d{1,2})\s*[.．月/\-]\s*(\d{1,2})\s*日?"
)
_SEIREKI_RE = re.compile(r"(\d{4})\s*[./年\-]\s*(\d{1,2})\s*[./月\-]\s*(\d{1,2})\s*日?")


def nfkc(text: str) -> str:
    """全角英数・記号をNFKCで半角へ寄せる(カナはそのまま)。"""
    return unicodedata.normalize("NFKC", text)


def parse_date(value) -> datetime.date | None:
    """契約締結日をdateへ。Excelのdatetime、西暦/和暦文字列に対応。"""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = nfkc(str(value)).strip()
    if not text:
        return None
    m = _WAREKI_RE.search(text)
    if m:
        era, y, mo, d = m.groups()
        base = _ERA_STARTS.get(era)
        year = base + (1 if y == "元" else int(y))
        try:
            return datetime.date(year, int(mo), int(d))
        except ValueError:
            return None
    m = _SEIREKI_RE.search(text)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


_AMOUNT_UNIT = {"億": 100_000_000, "万": 10_000, "千": 1_000}


def parse_amount(value) -> int | None:
    """契約金額(円)を整数へ。int/float、カンマ・注記付き文字列、億/万表記に対応。

    「※」等の注記は無視するが、数値が読めない場合は None(推測しない)。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    text = nfkc(str(value))
    # 注記・空白・通貨記号を除去(数値部の手前後にあるもの)
    text = text.replace(",", "").replace("円", "").replace("¥", "")
    text = re.sub(r"[※#＃*].*", "", text, flags=re.S).strip()
    if not text or text in {"-", "―", "－", "—"}:
        return None
    # 億/万/千 混在表記 (例: 1億2000万)
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)億)?(?:(\d+(?:\.\d+)?)万)?(?:(\d+(?:\.\d+)?)千)?(\d+(?:\.\d+)?)?", text)
    if m and any(m.groups()):
        oku, man, sen, rest = m.groups()
        total = 0.0
        for part, unit in ((oku, 100_000_000), (man, 10_000), (sen, 1_000)):
            if part:
                total += float(part) * unit
        if rest:
            total += float(rest)
        return int(round(total))
    return None


def parse_rate(value) -> float | None:
    """落札率。0-1の小数または%表記文字列。"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        return v / 100.0 if v > 1.5 else v
    text = nfkc(str(value)).replace("%", "").replace("％", "").strip()
    if not text or text in {"-", "―", "－"}:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return v / 100.0 if v > 1.5 else v


def split_company_cell(value) -> tuple[str, str]:
    """「契約相手方の商号又は名称及び住所」セルを (名称, 住所) に分割。

    公表様式では 1行目=商号、2行目以降=住所 が通例。1行のみの場合は住所空。
    """
    if value is None:
        return "", ""
    lines = [ln.strip() for ln in str(value).splitlines() if ln.strip()]
    if not lines:
        return "", ""
    return lines[0], " ".join(lines[1:])


# 法人格の表記揺れ: （株）/(株) → 株式会社 等。前株・後株どちらでも会社名に接続。
_LEGAL_ABBREV = [
    (re.compile(r"[（(]\s*株\s*[)）]"), "株式会社"),
    (re.compile(r"[（(]\s*有\s*[)）]"), "有限会社"),
    (re.compile(r"[（(]\s*合\s*[)）]"), "合同会社"),
    (re.compile(r"[（(]\s*一社\s*[)）]"), "一般社団法人"),
    (re.compile(r"[（(]\s*公財\s*[)）]"), "公益財団法人"),
    (re.compile(r"[（(]\s*独\s*[)）]"), "独立行政法人"),
]


def normalize_company_name(name: str) -> str:
    """照合キー用の名称正規化。

    - NFKC(全角英数→半角)
    - 空白除去
    - （株）→株式会社 等の法人格展開
    表記正規化のみで、異なる法人の統合判断はしない(それは法人番号/aliasの仕事)。
    """
    if not name:
        return ""
    text = nfkc(name)
    for pat, repl in _LEGAL_ABBREV:
        text = pat.sub(repl, text)
    text = re.sub(r"\s+", "", text)
    # 公表ファイルの注記マーカー(※)は名称の一部ではない (例: 「米陸軍省※」)
    text = text.replace("※", "")
    return text


def clean_corporate_number(value) -> str | None:
    """法人番号: 13桁数字のみ有効。「―」等はNone。"""
    if value is None:
        return None
    text = nfkc(str(value)).strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) == 13:
        return digits
    return None


def fiscal_year_of(d: datetime.date) -> int:
    """日本の会計年度(4月開始)。返り値は年度開始年の西暦。"""
    return d.year if d.month >= 4 else d.year - 1


_LOCATION_RE = re.compile(r"^(東京都|北海道|(?:京都|大阪)府|.{2,3}県)")


def parse_agency(raw_agency: str | None) -> dict:
    """契約担当官セルを構造化する。

    実データの典型(99%超):
      5行: 官職 / 組織(防衛装備庁) / 部局・役職(調達事業部長 等) / 氏名 / 所在地
      4行: 官職 / 組織+役職(防衛装備庁長官) / 氏名 / 所在地
    氏名(個人名)は保存しない。原文は raw_contracts.raw_agency に全文残る。
    返り値: {organization, department, location}
    """
    out = {"organization": None, "department": None, "location": None}
    if not raw_agency:
        return out
    lines = [ln.strip() for ln in str(raw_agency).splitlines() if ln.strip()]
    if not lines:
        return out
    rest = []
    for ln in lines:
        if "担当官" in ln and out["organization"] is None and not rest:
            continue  # 官職行(支出負担行為担当官 等)
        if _LOCATION_RE.match(ln):
            out["location"] = ln
            continue
        rest.append(ln)
    # rest = [組織(+役職)], [部局・役職], [氏名...]
    if rest:
        m = re.match(r"^(.*?(?:庁|省))(.*)$", rest[0])
        if m:
            out["organization"] = m.group(1)
            trailing = m.group(2).strip()
            dept_parts = [trailing] if trailing else []
        else:
            out["organization"] = rest[0]
            dept_parts = []
        # 2行目以降のうち、氏名らしい行(組織語を含まない短行)を除いて部局とする
        for ln in rest[1:]:
            if re.search(r"[庁省部官課室隊局]", ln):
                dept_parts.append(ln)
        if dept_parts:
            dept = "".join(dept_parts)
            # 役職と氏名が同一行の場合(例:「長官　土 本  英 樹」)、空白以降の氏名を落とす
            dept = re.split(r"[\s　]+", dept)[0]
            out["department"] = dept or None
    return out
