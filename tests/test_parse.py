"""parse.py のテスト。

fixtureは実ファイルと同じレイアウト(表題行/見出し行/副見出し行/データ行)を
openpyxlで再現した合成データ。実在の契約データは含まない。
列名・列順の年度差(列の入れ替え・欠落)への耐性を確認する。
"""
import datetime

import openpyxl
import pytest

from etl.parse import ParseError, parse_xlsx

HEADERS_ZUIKEI = [
    "物品役務等の名称", "数量", "単位",
    "契約担当官等の氏名並びにその所属する部局の名称及び所在地",
    "契約締結日", "契約相手方の商号又は名称及び住所", "法人番号",
    "予定価格\n（円）", "契約金額\n（円）", "落札率",
    "随意契約によることとした会計法令の根拠条文及び理由",
    "再就職の役員の数", "公益法人の場合", None, None, "備考",
]


def make_fixture(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["公共調達の適正化について（平成18年8月25日付財計第2017号）に基づく公表"])
    ws.append(headers)
    ws.append(["", "", "", "", "", "", "", "", "", "", "", "", "区分", "所管", ""])
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


@pytest.fixture
def zuikei_file(tmp_path):
    rows = [
        ["テスト装置用部品", 16, "ＥＡ",
         "分任支出負担行為担当官\n防衛装備庁\n調達事業部",
         datetime.datetime(2026, 2, 2), "テスト工業株式会社\n東京都千代田区1-1",
         "1234567890123", None, 540320000, None, "根拠条文", None, None, None, None, None],
        ["別の装置", 1, "式",
         "分任支出負担行為担当官\n防衛装備庁",
         "R7.7.28※", "（株）サンプル\n大阪府大阪市2-2",
         "―", None, "10,296,000\n※", None, "理由", None, None, None, None, None],
        # 空行(件名なし)は無視される
        [None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None],
    ]
    return make_fixture(tmp_path / "zuikei.xlsx", HEADERS_ZUIKEI, rows)


def test_parse_basic(zuikei_file):
    rows = parse_xlsx(zuikei_file)
    assert len(rows) == 2
    v = rows[0].values
    assert v["title"] == "テスト装置用部品"
    assert v["amount"] == 540320000
    assert v["corporate_number"] == "1234567890123"
    assert isinstance(v["contract_date"], datetime.datetime)


def test_parse_preserves_raw_strings(zuikei_file):
    rows = parse_xlsx(zuikei_file)
    v = rows[1].values
    # 原文を保持(注記付き金額・和暦日付を変換しない)
    assert v["amount"] == "10,296,000\n※"
    assert v["contract_date"] == "R7.7.28※"
    assert v["corporate_number"] == "―"


def test_parse_row_json_keeps_column_order(zuikei_file):
    rows = parse_xlsx(zuikei_file)
    import json
    arr = json.loads(rows[0].row_json)
    assert arr[0] == "テスト装置用部品"
    assert arr[8] == 540320000


def test_column_reorder_tolerated(tmp_path):
    # 年度により列順が変わっても見出し名で解決できること
    headers = ["契約締結日", "物品役務等の名称", "契約金額\n（円）",
               "契約相手方の商号又は名称及び住所", "法人番号"]
    rows = [[datetime.datetime(2024, 5, 1), "入れ替えテスト", 1000, "会社A\n住所A", "1111111111111"]]
    path = make_fixture(tmp_path / "reorder.xlsx", headers, rows)
    parsed = parse_xlsx(path)
    assert parsed[0].values["title"] == "入れ替えテスト"
    assert parsed[0].values["amount"] == 1000


def test_missing_required_column_raises(tmp_path):
    headers = ["物品役務等の名称", "数量"]  # 金額列がない
    path = make_fixture(tmp_path / "broken.xlsx", headers, [["x", 1]])
    with pytest.raises(ParseError):
        parse_xlsx(path)


def test_header_not_found_raises(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["まったく別の表"])
    p = tmp_path / "nothdr.xlsx"
    wb.save(p)
    with pytest.raises(ParseError):
        parse_xlsx(p)
