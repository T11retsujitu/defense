"""企業マスタのシード定義と解決ロジック。

同定の優先順位:
  1. 法人番号(13桁) — 公表ファイル自体に含まれ、表記揺れ・社名変更に対して最も頑健
  2. シード辞書(SEED_COMPANIES / FOREIGN_ENTITIES)の別名一致
  3. 上記で解決できない場合は「表記正規化名」単位で自動登録(auto、confidence低)

異なる法人を文字列類似だけで統合することはしない。
シードは実データ(令和5〜8年度 中央調達公表ファイル)の上位企業から作成。
"""
from __future__ import annotations

from .normalize import normalize_company_name

# corporate_number -> (正式名称, slug)
# 名称・法人番号とも公表ファイル記載値に基づく。
SEED_COMPANIES: dict[str, tuple[str, str]] = {
    "8010401050387": ("三菱重工業株式会社", "mitsubishi-heavy-industries"),
    "1140001005719": ("川崎重工業株式会社", "kawasaki-heavy-industries"),
    "7010401022916": ("日本電気株式会社", "nec"),
    "4010001008772": ("三菱電機株式会社", "mitsubishi-electric"),
    "1020001071491": ("富士通株式会社", "fujitsu"),
    "1010401002840": ("伊藤忠アビエーション株式会社", "itochu-aviation"),
    "4010601031604": ("株式会社IHI", "ihi"),
    "8020001076641": ("ジャパンマリンユナイテッド株式会社", "japan-marine-united"),
    "7010401006126": ("沖電気工業株式会社", "oki-electric"),
    "4010001133876": ("ENEOS株式会社", "eneos"),
    "2010401044997": ("株式会社東芝", "toshiba"),
    "5010701019531": ("株式会社日本製鋼所", "japan-steel-works"),
    "7010001008844": ("株式会社日立製作所", "hitachi"),
    "6010601062093": ("株式会社NTTデータ", "ntt-data"),
    "1010001012983": ("株式会社大塚商会", "otsuka-shokai"),
    "7180001047999": ("中川物産株式会社", "nakagawa-bussan"),
    "4130001041539": ("株式会社ジーエス・ユアサテクノロジー", "gs-yuasa-technology"),
    "1010001020185": ("住商エアロシステム株式会社", "sumisho-aero-systems"),
    "5010001008771": ("三菱商事株式会社", "mitsubishi-corporation"),
    "5010701000904": ("いすゞ自動車株式会社", "isuzu-motors"),
    "8120001059660": ("ダイキン工業株式会社", "daikin-industries"),
    "1010001008692": ("住友商事株式会社", "sumitomo-corporation"),
    "7140001005647": ("兼松株式会社", "kanematsu"),
    "9010001011318": ("出光興産株式会社", "idemitsu-kosan"),
    "3180001018624": ("リコーエレメックス株式会社", "ricoh-elemex"),
    "5011101019196": ("株式会社SUBARU", "subaru"),
    "1010401010455": ("株式会社小松製作所", "komatsu"),
    "1180301018771": ("トヨタ自動車株式会社", "toyota-motor"),
    "2010001098064": ("株式会社国際電気", "kokusai-electric"),
    "9012405001241": ("国立研究開発法人宇宙航空研究開発機構", "jaxa"),
}

# 法人番号を持たない契約相手方(FMS=対外有償軍事援助の米軍各省 等)
# 正規化名 -> (正式名称, slug, entity_type)
FOREIGN_ENTITIES: dict[str, tuple[str, str, str]] = {
    "米空軍省": ("米空軍省(FMS)", "us-air-force-fms", "foreign_government"),
    "米海軍省": ("米海軍省(FMS)", "us-navy-fms", "foreign_government"),
    "米陸軍省": ("米陸軍省(FMS)", "us-army-fms", "foreign_government"),
    "米国防省": ("米国防省(FMS)", "us-dod-fms", "foreign_government"),
    "米国防兵站局": ("米国防兵站局(FMS)", "us-dla-fms", "foreign_government"),
}

SEED_ENTITY_TYPES = {"独立行政法人": "gov_agency", "国立研究開発法人": "gov_agency"}


def seed_entity_type(name: str) -> str:
    for prefix, etype in SEED_ENTITY_TYPES.items():
        if name.startswith(prefix):
            return etype
    return "company"


def lookup_foreign_entity(raw_name: str):
    """法人番号なしの外国政府等をシードから解決。"""
    key = normalize_company_name(raw_name)
    return FOREIGN_ENTITIES.get(key)
