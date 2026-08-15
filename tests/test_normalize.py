"""normalize.py のテスト。

ケースは実ファイル(令和5〜8年度 中央調達公表xlsx)で観測した値に基づく。
"""
import datetime

import pytest

from etl.normalize import (
    clean_corporate_number,
    fiscal_year_of,
    normalize_company_name,
    parse_amount,
    parse_date,
    parse_rate,
    split_company_cell,
)


class TestParseAmount:
    def test_int_passthrough(self):
        assert parse_amount(540320000) == 540320000

    def test_float(self):
        assert parse_amount(1234.0) == 1234

    def test_comma_string(self):
        assert parse_amount("10,296,000") == 10_296_000

    def test_comma_string_with_note(self):
        # 実データ: "10,296,000\n※" (kohyo_r07/07_zuikei-02.xlsx)
        assert parse_amount("10,296,000\n※") == 10_296_000

    def test_fullwidth_digits(self):
        assert parse_amount("１２３４５６") == 123456

    def test_oku_man_notation(self):
        assert parse_amount("1億2000万") == 120_000_000
        assert parse_amount("3億") == 300_000_000
        assert parse_amount("2500万") == 25_000_000

    def test_yen_suffix(self):
        assert parse_amount("1,000円") == 1000

    def test_empty_and_dash(self):
        assert parse_amount(None) is None
        assert parse_amount("") is None
        assert parse_amount("―") is None
        assert parse_amount("-") is None

    def test_garbage_returns_none(self):
        # 推測で埋めない
        assert parse_amount("非公表") is None
        assert parse_amount("後日公表") is None


class TestParseDate:
    def test_datetime_passthrough(self):
        assert parse_date(datetime.datetime(2026, 2, 2)) == datetime.date(2026, 2, 2)

    def test_wareki_with_note(self):
        # 実データ: "R7.7.28※" (kohyo_r07/07_zuikei-07.xlsx)
        assert parse_date("R7.7.28※") == datetime.date(2025, 7, 28)

    def test_wareki_kanji(self):
        assert parse_date("令和6年12月26日") == datetime.date(2024, 12, 26)

    def test_wareki_gannen(self):
        assert parse_date("令和元年5月1日") == datetime.date(2019, 5, 1)

    def test_seireki(self):
        assert parse_date("2024/10/01") == datetime.date(2024, 10, 1)
        assert parse_date("2024年10月1日") == datetime.date(2024, 10, 1)

    def test_invalid(self):
        assert parse_date("未定") is None
        assert parse_date(None) is None
        assert parse_date("R7.13.45") is None  # 存在しない月日


class TestCompanyName:
    def test_kabu_abbrev_expansion(self):
        assert normalize_company_name("三菱重工業（株）") == "三菱重工業株式会社"
        assert normalize_company_name("三菱重工業(株)") == "三菱重工業株式会社"
        assert normalize_company_name("三菱重工業株式会社") == "三菱重工業株式会社"

    def test_mae_kabu(self):
        assert normalize_company_name("（株）東芝") == "株式会社東芝"

    def test_fullwidth_alnum(self):
        # 全角英字は半角へ(ＩＨＩ→IHI)
        assert normalize_company_name("株式会社ＩＨＩ") == "株式会社IHI"
        assert normalize_company_name("株式会社IHI") == "株式会社IHI"

    def test_whitespace_removed(self):
        assert normalize_company_name("株式会社 ＳＵＢＡＲＵ") == "株式会社SUBARU"

    def test_different_companies_not_merged(self):
        # 表記正規化で別法人が同一キーにならないこと
        a = normalize_company_name("三菱重工業株式会社")
        b = normalize_company_name("三菱電機株式会社")
        c = normalize_company_name("三菱商事株式会社")
        assert len({a, b, c}) == 3


class TestSplitCompanyCell:
    def test_name_and_address(self):
        # 実データの形式: 1行目=商号 2行目=住所
        name, addr = split_company_cell("藤倉航装株式会社\n東京都品川区荏原２丁目４番４６号")
        assert name == "藤倉航装株式会社"
        assert addr == "東京都品川区荏原２丁目４番４６号"

    def test_name_only(self):
        name, addr = split_company_cell("米空軍省")
        assert name == "米空軍省"
        assert addr == ""

    def test_empty(self):
        assert split_company_cell(None) == ("", "")
        assert split_company_cell("") == ("", "")


class TestCorporateNumber:
    def test_valid(self):
        assert clean_corporate_number("4010701008683") == "4010701008683"

    def test_int_input(self):
        assert clean_corporate_number(4010701008683) == "4010701008683"

    def test_dash_is_none(self):
        # 実データ: FMS契約(米軍省)は「―」
        assert clean_corporate_number("―") is None

    def test_wrong_length(self):
        assert clean_corporate_number("123") is None


class TestFiscalYear:
    def test_april_starts_fy(self):
        assert fiscal_year_of(datetime.date(2026, 4, 1)) == 2026

    def test_march_belongs_to_previous_fy(self):
        assert fiscal_year_of(datetime.date(2026, 2, 2)) == 2025  # 令和7年度

    def test_reiwa5(self):
        assert fiscal_year_of(datetime.date(2024, 1, 10)) == 2023  # 令和5年度


class TestParseRate:
    def test_decimal(self):
        assert parse_rate(0.997) == pytest.approx(0.997)

    def test_percent_string(self):
        assert parse_rate("99.7%") == pytest.approx(0.997)

    def test_none(self):
        assert parse_rate("―") is None
