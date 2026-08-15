"""カテゴリ分類(ルールベース・二軸)。

単一階層に「装備分野」「契約目的」「品目種別」が混在すると、
「何を調達したか」と「何のための契約か」のどちらかが失われるため、
以下の二軸で独立に分類する。

  domain (装備分野): 何に関する調達か — 航空機 / 艦船 / 陸上装備 / 誘導弾 / ...
  nature (契約目的): 何のための契約か — 新規取得 / 研究開発 / 維持整備 / 役務 / ...

例: 「極超音速誘導弾等の生産準備役務」 → domain=誘導弾・ミサイル, nature=役務・サービス
    「軽油2号(艦船用)」            → domain=艦船, nature=燃料・消耗品
    「10式戦車用エンジン」          → domain=陸上装備, nature=新規取得

方針(PoC):
- 再現性を最優先し、キーワードルールのみで分類する(LLMは使わない)。
- 各軸内はルールを上から順に評価し、最初に一致したものを採用する(順序は本ファイルが仕様)。
- domainが一致しない契約は「未分類」とし、無理に分類しない。
- natureが一致しない契約は既定値「新規取得」(物品調達の既定)とし、
  rule_matched=False で既定値適用であることを記録する。
- rule_matched は「ルールに一致したか」のブール値であり、分類精度の確信度ではない。

キーワードは令和5〜8年度の実ファイル(約21,000件)の件名を確認して作成。
"""
from __future__ import annotations

import re

from .normalize import nfkc

DOMAIN_UNCLASSIFIED = "未分類"
# nature未一致時の既定値。「新規取得と確定した」のではなく
# 「目的ルールに一致しない物品調達等」の受け皿(nature_rule_matched=0で記録)。
NATURE_DEFAULT = "物品取得・その他"

# ---- nature軸(契約目的): 先に契約の性質を判定する ----
NATURE_RULES: list[tuple[str, str, list[str | re.Pattern]]] = [
    ("産業基盤", "industrial-base", [
        "特定取組", "製造工程効率化", "供給網強靱化", "事業承継",
    ]),
    ("研究開発", "rd", [
        "研究試作", "試験研究", "調査研究", "研究用", "技術実証", "実証研究",
        "性能確認試験", "の研究", "開発諸費", "の開発", "共同開発", "供試器材",
        "発射試験", "試験支援", "研究に係る", "に関する研究", "装備認定試験",
        "概念実証", "実証事業",
    ]),
    ("改修・能力向上", "upgrade", [
        "能力向上", "量産改修", "改修", "試改修", "機齢延伸", "延命", "機能付加",
        "近代化",
    ]),
    ("調査・検討", "study", [
        "検討", "調査", "予備設計",
    ]),
    ("維持整備・修理", "maintenance", [
        "定期修理", "オーバーホール", "修理", "整備", "点検", "補修", "換装",
        "定期検査", "部品交換", "維持",
    ]),
    ("役務・サービス", "services", [
        "役務", "技術支援", "委託教育", "後方支援", "ＩＬＳ", "講習", "教育訓練",
        "借上", "賃貸借", "運航・管理", "業務委託", "輸送", "廃棄", "保管",
    ]),
    ("燃料・消耗品", "fuel-consumables", [
        "燃料", "軽油", "灯油", "ガソリン", "重油", "潤滑油", "作動油",
        "オイル", "グリース", "航空タービン",
    ]),
]

# ---- domain軸(装備分野) ----
# 特異性の高い分野(誘導弾・無人機・宇宙)を、広い分野(航空機・艦船・C4ISR)より先に判定。
# 「エンジン」等の汎用語による誤爆を避けるため、陸上装備(戦車等)を航空機より先に置く。
DOMAIN_RULES: list[tuple[str, str, list[str | re.Pattern]]] = [
    ("誘導弾・ミサイル", "missiles", [
        "誘導弾", "ミサイル", "ペトリオット", "ＰＡＣ－３", "PAC-3",
        "スタンド・オフ", "誘導装置", "シーカ", "滑空弾", "ＪＳＭ", "ＪＡＳＳＭ",
        "トマホーク", "ＳＭ－３", "ＳＭ－６", "ＥＳＳＭ", "ＶＬＳ", "垂直発射装置",
        "ＢＭＤ", "ＧＰＩ", "キャニスタ", "迎撃", "ＳＡＭ", "ＨＧＶ",
        re.compile(r"(ＡＡＭ|AAM|ＳＳＭ|SSM|ＡＳＭ|ASM)[-－]?\d"),
    ]),
    ("無人機", "unmanned", [
        "無人機", "無人航空機", "無人水上", "無人水中", "無人車両", "ＵＡＶ", "UAV",
        "ＵＳＶ", "USV", "ＵＵＶ", "UUV", "ＵＧＶ", "UGV", "ドローン", "遊弋",
        "グローバルホーク", "シーガーディアン", "ＲＯＶ", "オートノミー",
        re.compile(r"(ＲＱ|RQ|ＭＱ|MQ)[-－]\d+"),
    ]),
    ("宇宙", "space", [
        "宇宙", "衛星", "コンステレーション", "Ｘバンド", "測位",
    ]),
    ("弾薬・火器", "ammunition-firearms", [
        "弾薬", "実包", "空包", "信管", "火薬", "装薬", "りゅう弾", "擲弾",
        "迫撃砲", "魚雷", "機雷", "爆雷", "小銃", "機関銃", "けん銃", "拳銃",
        "火器", "砲弾", "発射薬", "弾倉", "ロケット弾", "チャフ", "フレア",
        "ＳＤＢ", "爆弾", "口径", "ＧＢＵ", "レールガン", "弾頭",
        re.compile(r"\d+ｍｍ|\d+mm"), re.compile(r"弾[（(，,]|弾$"),
        re.compile(r"砲[（(，,]|砲$"),
    ]),
    ("艦船", "ships", [
        "護衛艦", "潜水艦", "掃海", "舶用", "船体", "ソーナー", "ソナー",
        "推進器", "水中発射", "揚収", "艦", "艇", "船舶", "主機械",
        "ガスタービン主機", "ガスタービン機関", "ＭＴ３０", "洋上", "イージス",
        "水中", "潜望鏡",
    ]),
    ("陸上装備", "land-systems", [
        "戦車", "装甲", "車両", "トラック", "けん引車", "自動車", "ダンプ",
        "クレーン車", "車体", "装輪", "装軌", "機動車", "タイヤ", "浮橋",
        "野外", "地雷", "施設器材", "ドーザ", "除染", "ＮＢＣ",
        re.compile(r"車[（(，,]|車$"),
    ]),
    ("航空機", "aircraft", [
        "航空機", "飛行機", "ヘリコプタ", "回転翼", "固定翼", "戦闘機", "哨戒機",
        "輸送機", "練習機", "救難飛行艇", "救難機", "作戦機", "警戒管制機",
        "射出装置", "降着装置", "オスプレイ", "エンジン", "プロペラ", "機体",
        "ＦＴＢ", "落下傘", "航空タービン", "ティルト・ローター", "ローター",
        "機齢延伸", "滑走路",
        re.compile(r"(ＵＰ|UP|ＥＰ|EP)[-－]\d"),
        re.compile(r"(?<![Ａ-ＺA-Z０-９0-9])[ＦｆFf][-－]?\d+"),
        re.compile(r"(?<![Ａ-ＺA-Z０-９0-9])[ＴT][-－]\d+"),
        re.compile(r"(?<![Ａ-ＺA-Z０-９0-9])[ＰP][-－]\d+"),
        re.compile(r"(?<![Ａ-ＺA-Z０-９0-9])[ＣC][-－]\d+"),
        re.compile(r"(?<![Ａ-ＺA-Z０-９0-9])[ＥE][-－]\d+"),
        re.compile(r"(ＫＣ|KC)[-－]\d+"),
        re.compile(r"(ＵＨ|UH|ＣＨ|CH|ＳＨ|SH|ＡＨ|AH|ＯＨ|OH|ＲＣ|RC|ＭＣＨ|MCH)[-－]\d+"),
        re.compile(r"[ＶV][-－]22"),
    ]),
    ("C4ISR・電子機器", "c4isr", [
        "レーダ", "通信", "無線", "電波", "電子戦", "アンテナ", "暗号",
        "ネットワーク", "交換機", "端末", "情報処理", "電子計算機", "表示装置",
        "受信", "送信", "指揮", "電測", "センサ", "監視装置", "暗視装置",
        "測定装置", "評価システム", "収集システム", "変換器", "タカン",
        "ソノブイ", "ブイ", "音響", "光学", "標定", "電子", "空中線", "秘匿",
        "信号発生", "スペクトラム", "航法", "レーザ", "赤外線", "探知",
        "妨害", "電磁波", "収集", "模擬", "ＩＦＦ", "変復調", "信号分析",
        "ＥＳＭ", "ＥＣＭ", "ＨＰＭ", "伝送", "計器着陸",
        re.compile(r"(ＡＬＲ|ALR|ＡＬＱ|ALQ)[-－]?\d"),
    ]),
    ("IT・ソフトウェア", "software-it", [
        "サイバー", "ＡＩ", "人工知能", "機械学習", "ソフトウェア", "ライセンス",
        "クラウド", "電算機", "セキュリティ", "データ管理", "スレットハンティング",
        "共通基盤", "サービス基盤", "システム",
    ]),
    # 分野横断の共通品目(被服・需品・医療・汎用機材・燃料類)
    ("共通・その他", "common", [
        "被服", "作業服", "制服", "戦闘服", "靴", "半長靴", "糧食", "需品",
        "天幕", "毛布", "寝袋", "外とう", "手袋", "帽", "防弾", "耐弾",
        "コンテナ", "浄水", "炊事", "夏服", "冬服", "階級章", "精勤章",
        "略章", "記章", "雨衣", "防護", "医療", "衛生", "医薬", "ワクチン", "血液",
        "包帯", "救急", "発動発電", "発電機", "電源装置", "無停電", "蓄電池",
        "電池", "充電", "フォークリフト", "クレーン", "照明", "空気調和",
        "冷凍", "冷蔵", "洗濯", "印刷", "カートリッジ", "事務", "工具",
        "除雪", "燃料", "軽油", "灯油", "ガソリン", "重油", "潤滑油",
        "作動油", "オイル", "グリース",
        re.compile(r"服[，,（(]|服$"),
    ]),
]

DOMAIN_SLUGS = {name: slug for name, slug, _ in DOMAIN_RULES}
DOMAIN_SLUGS[DOMAIN_UNCLASSIFIED] = "uncategorized"
NATURE_SLUGS = {name: slug for name, slug, _ in NATURE_RULES}
NATURE_SLUGS[NATURE_DEFAULT] = "acquisition"


def _match(rules, text, text_nfkc):
    for name, _slug, keywords in rules:
        for kw in keywords:
            if isinstance(kw, re.Pattern):
                if kw.search(text) or kw.search(text_nfkc):
                    return name
            else:
                if kw in text or nfkc(kw) in text_nfkc:
                    return name
    return None


def classify(title: str) -> tuple[str, bool, str, bool]:
    """契約件名を二軸で分類する。

    返り値: (domain名, domain_rule_matched, nature名, nature_rule_matched)
    rule_matched はキーワードルールに一致したかのブール値(精度の確信度ではない)。
    """
    if not title:
        return DOMAIN_UNCLASSIFIED, False, NATURE_DEFAULT, False
    text = title
    text_nfkc = nfkc(title)
    domain = _match(DOMAIN_RULES, text, text_nfkc)
    nature = _match(NATURE_RULES, text, text_nfkc)
    return (
        domain or DOMAIN_UNCLASSIFIED, domain is not None,
        nature or NATURE_DEFAULT, nature is not None,
    )


# ---- domain未分類の理由コード ----
# 未分類を一括りにせず、改善対象(manual_review_pending≒rule gap候補)と
# 本質的に分類困難なもの(insufficient_context / cross_domain)を区別する。
UNMATCHED_INSUFFICIENT = "insufficient_context"   # 件名だけでは分野が特定できない
UNMATCHED_CROSS_DOMAIN = "cross_domain"           # 試験設備等の分野横断
UNMATCHED_REVIEW = "manual_review_pending"        # 未レビュー(rule gap候補を含む)

_INSUFFICIENT_KW = ["ＦＭＳ", "装備認定試験", "特定取組", "委託教育", "米軍", "支援プログラム"]
_CROSS_DOMAIN_KW = ["風洞", "試験装置", "試験用器材", "供試器材", "実験装置",
                    "発射試験", "試験の実施", "計測システム"]


def domain_unmatched_reason(title: str, nature_name: str) -> str:
    """domainが未分類の契約に理由コードを付ける(未分類時のみ呼ぶ)。"""
    if not title:
        return UNMATCHED_INSUFFICIENT
    text_nfkc = nfkc(title)
    if nature_name == "産業基盤":
        return UNMATCHED_INSUFFICIENT  # 件名が取組番号のみで装備分野を持たない
    for kw in _INSUFFICIENT_KW:
        if kw in title or nfkc(kw) in text_nfkc:
            return UNMATCHED_INSUFFICIENT
    for kw in _CROSS_DOMAIN_KW:
        if kw in title:
            return UNMATCHED_CROSS_DOMAIN
    return UNMATCHED_REVIEW
