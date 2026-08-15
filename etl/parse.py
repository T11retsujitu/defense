"""公表xlsxのパース(原文保持)。

様式(公共調達の適正化 平成18年財計第2017号 に基づく公表様式):
  行0: 表題 / 行1: 列見出し / 行2: 副見出し / 行3以降: データ
  列見出しはファイル間でほぼ共通だが、列順は見出し名で解決する(年度による列ずれ対応)。
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

import openpyxl

# 見出し(部分一致) -> 内部フィールド名
HEADER_MAP = [
    ("物品役務等の名称", "title"),
    ("数量", "quantity"),
    ("単位", "unit"),
    ("契約担当官", "agency"),
    ("契約締結日", "contract_date"),
    ("契約相手方", "company"),
    ("法人番号", "corporate_number"),
    ("予定価格", "planned_price"),
    ("契約金額", "amount"),
    ("落札率", "award_rate"),
    ("一般競争入札・指名競争入札の別", "method_detail"),
    ("随意契約によることとした", "method_detail"),
    ("備考", "remarks"),
]


@dataclass
class RawRow:
    row_index: int
    values: dict          # field -> 原文値(str化前)
    row_json: str         # 行全体(列順のまま)のJSON


class ParseError(Exception):
    pass


def _cell_to_jsonable(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return v


def parse_xlsx(path: Path) -> list[RawRow]:
    """1ファイルをRawRow列へ。ヘッダ行が見つからない場合はParseError。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    header_idx = None
    for i, row in enumerate(rows[:10]):
        if row and any(c and "物品役務" in str(c) for c in row):
            header_idx = i
            break
    if header_idx is None:
        raise ParseError(f"header row not found: {path}")

    header = rows[header_idx]
    col_map: dict[int, str] = {}
    for ci, cell in enumerate(header):
        if cell is None:
            continue
        text = str(cell)
        for needle, field in HEADER_MAP:
            if needle in text:
                col_map[ci] = field
                break

    required = {"title", "contract_date", "company", "amount"}
    if not required.issubset(set(col_map.values())):
        raise ParseError(f"missing columns {required - set(col_map.values())}: {path}")

    out: list[RawRow] = []
    # ヘッダ直後の副見出し行(公益法人の場合の内訳)をスキップするため、
    # データ行は「件名列が非空」で判定する
    for ri in range(header_idx + 1, len(rows)):
        row = rows[ri]
        if not row:
            continue
        values = {}
        for ci, field in col_map.items():
            if ci < len(row):
                values.setdefault(field, row[ci])
        title = values.get("title")
        if title is None or str(title).strip() == "":
            continue
        if "物品役務" in str(title):  # 重複ヘッダ
            continue
        row_json = json.dumps([_cell_to_jsonable(c) for c in row], ensure_ascii=False)
        out.append(RawRow(row_index=ri, values=values, row_json=row_json))
    return out
