"""カテゴリ分類ルールのテスト(件名は実データより)。"""
from etl.categories import UNCLASSIFIED, classify


def test_rd_before_domain():
    # 装備分野より契約性質(研究試作)を優先
    name, conf = classify("将来潜水艦用ソーナーシステムの研究試作")
    assert name == "研究開発"
    assert conf == 1.0


def test_maintenance_before_domain():
    name, _ = classify("護衛艦の定期修理")
    assert name == "維持整備・役務"


def test_aircraft_designator():
    name, _ = classify("Ｆ－１５緊急射出装置用部品（国産・その７）")
    assert name == "航空機"


def test_missile():
    assert classify("島嶼防衛用高速滑空弾（能力向上型）の開発諸費")[0] == "研究開発"
    assert classify("１２式地対艦誘導弾")[0] == "誘導弾・ミサイル"


def test_ammunition():
    assert classify("１５５ｍｍりゅう弾")[0] == "弾薬・火器"


def test_fuel():
    assert classify("航空タービン燃料ＪＰ－８")[0] == "燃料・油脂"


def test_satellite():
    assert classify("衛星幹線通信システム携帯局　ＪＰＲＣ－Ｂ１")[0] == "宇宙"


def test_unmanned():
    assert classify("戦闘支援型多目的ＵＳＶの研究試作")[0] == "研究開発"  # 性質優先
    assert classify("遊弋型ＵＡＶ対処器材")[0] == "無人機"


def test_industrial_base():
    # 令和7年度から頻出する防衛生産基盤強化関連の契約
    assert classify("製造工程効率化に係る特定取組（Ｂ－２０２５－０１２２－００）")[0] == "産業基盤・特定取組"


def test_general_equipment():
    assert classify("発動発電機セット")[0] == "汎用機材"
    assert classify("無停電電源装置")[0] == "汎用機材"


def test_ship_gas_turbine():
    assert classify("主機械ＭＴ３０型ガスタービン機関（減速装置を含む）")[0] == "艦船"


def test_fuel_not_hijacked_by_vehicle_keyword():
    # 「ドーザ等用」の作動油が車両に誤分類されないこと(ドーザは意図的に除外)
    assert classify("作動油，中型ドーザ等用，１０Ｗ")[0] == "燃料・油脂"


def test_clothing_suffix():
    assert classify("冬服，陸，男性，曹士，１６式")[0] == "需品・被服"


def test_unclassified_returns_zero_confidence():
    name, conf = classify("マットレス（市販品）")
    assert name == UNCLASSIFIED
    assert conf == 0.0


def test_empty_title():
    name, conf = classify("")
    assert name == UNCLASSIFIED
    assert conf == 0.0
