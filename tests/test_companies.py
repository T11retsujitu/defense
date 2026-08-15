"""企業マスタ(シード・slug導出)のテスト。"""
from etl.companies import SEED_COMPANIES, derive_slug_base


def test_trisat_seeded_with_readable_slug():
    # 令和7年度ランキング上位のSPC(衛星コンステレーション事業)。
    # 法人番号フォールバック(c6011...)ではなく可読slugになること。
    assert SEED_COMPANIES["6011101114814"] == (
        "株式会社トライサット・コンステレーション", "trisat-constellation")


def test_derive_slug_from_latin_name():
    assert derive_slug_base("ＥＮＥＯＳ株式会社") == "eneos"  # 全角→NFKC
    assert derive_slug_base("SkyDrive株式会社") == "skydrive"
    assert derive_slug_base("Ｂａｅ Ｓｙｓｔｅｍｓ Ｌｔｄ．") == "bae-systems"


def test_derive_slug_japanese_only_returns_none():
    # 日本語のみの名称は読み推定をしない(呼び出し側が c+法人番号 に退避)
    assert derive_slug_base("株式会社トライサット・コンステレーション") is None
    assert derive_slug_base("三菱重工業株式会社") is None
    assert derive_slug_base("") is None


def test_derive_slug_stopwords_and_short():
    assert derive_slug_base("Co., Ltd.") is None  # 法人格語のみ→不成立
    assert derive_slug_base("ＡＢ商事") is None    # 2文字未満は不成立(len<3)
