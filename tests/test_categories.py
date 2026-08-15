"""カテゴリ分類ルール(二軸)のテスト(件名は実データより)。

classify() は (domain名, domain_rule_matched, nature名, nature_rule_matched) を返す。
rule_matched はルール一致のブール値であり、確信度ではない。
"""
from etl.categories import DOMAIN_UNCLASSIFIED, NATURE_DEFAULT, classify


def test_two_axes_are_independent():
    # 装備分野と契約目的の両方が残る(単一軸ではどちらかが失われていた)
    d, dm, n, nm = classify("極超音速誘導弾等の生産準備役務（その２）")
    assert d == "誘導弾・ミサイル" and dm
    assert n == "役務・サービス" and nm


def test_rd_with_domain():
    d, dm, n, nm = classify("将来潜水艦用ソーナーシステムの研究試作")
    assert d == "艦船"
    assert n == "研究開発"


def test_maintenance_with_domain():
    d, _, n, _ = classify("護衛艦の定期修理")
    assert d == "艦船"
    assert n == "維持整備・修理"


def test_fuel_for_ships():
    # 「軽油2号(艦船用)」: 分野=艦船、目的=燃料・消耗品(旧単一軸では艦船のみだった)
    d, _, n, _ = classify("軽油２号（艦船用）")
    assert d == "艦船"
    assert n == "燃料・消耗品"


def test_tank_engine_is_land_not_aircraft():
    # 「エンジン」キーワードで航空機に誤爆しない(陸上装備を先に判定)
    d, _, n, _ = classify("１０式戦車用エンジン")
    assert d == "陸上装備"
    assert n == NATURE_DEFAULT


def test_aircraft_designator():
    d, _, n, nm = classify("Ｆ－１５緊急射出装置用部品（国産・その７）")
    assert d == "航空機"
    assert n == NATURE_DEFAULT and not nm  # 既定値適用


def test_jp8_fuel_not_aircraft():
    d, _, n, _ = classify("航空タービン燃料ＪＰ－８")
    # ＪＰ－８はＰ－８(哨戒機)ではない。航空タービンは航空機分野の燃料
    assert n == "燃料・消耗品"


def test_missile_development():
    d, _, n, _ = classify("島嶼防衛用高速滑空弾（能力向上型）の開発諸費")
    assert d == "誘導弾・ミサイル"
    assert n == "研究開発"


def test_ammunition():
    d, _, n, _ = classify("１５５ｍｍりゅう弾")
    assert d == "弾薬・火器"


def test_industrial_base_nature():
    d, dm, n, _ = classify("製造工程効率化に係る特定取組（Ｂ－２０２５－０１２２－００）")
    assert n == "産業基盤"
    assert d == DOMAIN_UNCLASSIFIED and not dm


def test_satellite():
    d, _, _, _ = classify("衛星幹線通信システム携帯局　ＪＰＲＣ－Ｂ１")
    assert d == "宇宙"


def test_unmanned_rd():
    d, _, n, _ = classify("戦闘支援型多目的ＵＳＶの研究試作")
    assert d == "無人機"
    assert n == "研究開発"


def test_general_equipment_is_common_domain():
    d, _, n, nm = classify("発動発電機セット")
    assert d == "共通・その他"
    assert n == NATURE_DEFAULT and not nm


def test_ship_gas_turbine():
    d, _, _, _ = classify("主機械ＭＴ３０型ガスタービン機関（減速装置を含む）")
    assert d == "艦船"


def test_it_lease_is_services():
    d, _, n, _ = classify("電子計算機借上　航空自衛隊クラウドシステム")
    assert d in ("C4ISR・電子機器", "IT・ソフトウェア")
    assert n == "役務・サービス"


def test_clothing():
    d, _, _, _ = classify("冬服，陸，男性，曹士，１６式")
    assert d == "共通・その他"


def test_unclassified():
    d, dm, n, nm = classify("マットレス（市販品）")
    assert d == DOMAIN_UNCLASSIFIED and not dm
    assert n == NATURE_DEFAULT and not nm


def test_empty_title():
    d, dm, n, nm = classify("")
    assert d == DOMAIN_UNCLASSIFIED and not dm
    assert n == NATURE_DEFAULT and not nm
