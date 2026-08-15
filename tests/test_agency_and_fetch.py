"""契約担当官の構造化・相手方区分・404マーカー期限のテスト。"""
import datetime
import json

from etl.companies import guess_entity_type_without_cn, US_GOV_RE
from etl.fetch import _missing_marker_expired, MISSING_TTL_CURRENT_FY
from etl.normalize import normalize_company_name, parse_agency
from etl.sources import SourceFile


class TestParseAgency:
    def test_five_line_pattern(self):
        # 実データの99%超を占める5行パターン(氏名は保存しない)
        raw = "分任支出負担行為担当官\n防衛装備庁\n調達事業部長\n柴　田　直　彦\n東京都新宿区市谷本村町５－１"
        a = parse_agency(raw)
        assert a["organization"] == "防衛装備庁"
        assert a["department"] == "調達事業部長"
        assert a["location"] == "東京都新宿区市谷本村町５－１"
        assert "柴" not in (a["department"] or "")

    def test_four_line_pattern(self):
        raw = "支出負担行為担当官\n防衛装備庁長官\n土 本  英 樹\n東京都新宿区市谷本村町５－１"
        a = parse_agency(raw)
        assert a["organization"] == "防衛装備庁"
        assert a["department"] == "長官"
        assert a["location"].startswith("東京都")

    def test_empty(self):
        a = parse_agency(None)
        assert a == {"organization": None, "department": None, "location": None}


class TestEntityType:
    def test_us_gov_rule(self):
        assert US_GOV_RE.match(normalize_company_name("米空軍省"))
        assert US_GOV_RE.match(normalize_company_name("米国家安全保障庁"))
        assert US_GOV_RE.match(normalize_company_name("米陸軍省※"))  # 注記付き表記
        assert not US_GOV_RE.match(normalize_company_name("米屋株式会社"))

    def test_foreign_company_by_latin_name(self):
        t = guess_entity_type_without_cn(
            normalize_company_name("Ｏａｋｗｏｏｄ　Ｃｏｎｔｒｏｌｓ　Ｃｏｒｐｏｒａｔｉｏｎ"), "")
        assert t == "foreign_company"

    def test_domestic_sole_proprietor(self):
        t = guess_entity_type_without_cn("高松匠店", "香川県高松市１－１")
        assert t == "company"

    def test_foreign_by_address(self):
        t = guess_entity_type_without_cn("サンプル商会", "米国カリフォルニア州")
        assert t == "foreign_company"


class TestXlsxValidation:
    def test_truncated_zip_rejected(self):
        # 先頭がPKでも途中で切れたZIPは拒否する(キャッシュを壊さない)
        import io, zipfile
        from etl.fetch import validate_xlsx_bytes
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<x/>")
            zf.writestr("xl/workbook.xml", "<w/>" * 1000)
        data = buf.getvalue()
        assert validate_xlsx_bytes(data) is True
        assert validate_xlsx_bytes(data[: len(data) // 2]) is False  # 途中切れ
        assert validate_xlsx_bytes(b"PK\x03\x04garbage") is False

    def test_non_xlsx_zip_rejected(self):
        import io, zipfile
        from etl.fetch import validate_xlsx_bytes
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "not xlsx")
        assert validate_xlsx_bytes(buf.getvalue()) is False


class TestMissingMarkerTTL:
    def test_expired_marker_is_retried(self, tmp_path):
        sf = SourceFile(2026, "zuikei", 7)  # 進行中年度
        marker = tmp_path / "x.404"
        old = datetime.datetime.now(datetime.timezone.utc) - MISSING_TTL_CURRENT_FY * 2
        marker.write_text(json.dumps({"checked_at": old.isoformat()}))
        assert _missing_marker_expired(marker, sf) is True

    def test_fresh_marker_not_retried(self, tmp_path):
        sf = SourceFile(2026, "zuikei", 7)
        marker = tmp_path / "x.404"
        marker.write_text(json.dumps(
            {"checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
        assert _missing_marker_expired(marker, sf) is False

    def test_legacy_marker_treated_as_expired(self, tmp_path):
        # 旧形式(日時なし)は期限切れ扱いで再確認する
        sf = SourceFile(2023, "zuikei", 1)
        marker = tmp_path / "x.404"
        marker.write_text("404")
        assert _missing_marker_expired(marker, sf) is True
