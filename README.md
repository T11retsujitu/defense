# 防衛調達データベース (PoC)

防衛装備庁が公表する**中央調達の契約情報**を自動取得・正規化し、
企業・年度・カテゴリ横断で検索・集計できる産業データベースのProof of Concept。

軍事ファンサイトではなく、EDINET系データサービス・政府統計ダッシュボードに近い
ニュートラルな産業データベースを目指す。

## 現状 (PoC完了時点)

- 実データ収録: 令和5〜8年度の公表ファイル**78件(公表済み全月)** / 契約**21,208件(約16.3兆円)** / 企業・団体1,203
- parse失敗 0件(注記行2件はrawのみ保持)、企業未解決 9件(小規模事業者のみ)、
  カテゴリ未分類 件数28% / **金額ベース3.6%**
- 未公表月(令和8年度7月以降)は404マーカーで管理し、次回実行時に自動追随
- 一次データ調査の詳細: [docs/data-survey.md](docs/data-survey.md)

## 技術構成

| 層 | 技術 | 選定理由 |
|---|---|---|
| ETL | Python 3 + openpyxl | 公表データがxlsx。標準的で長期維持しやすい |
| DB | SQLite | 全期間でも数万行規模。ファイル1個で運用でき、維持費ゼロ。将来Postgresへ移行可能なSQL |
| Web | Flask + Jinja2 (SSR) | ページ数が少なくSSRで十分。SEOに必要なHTMLを直接返す。将来は静的書き出し/CDN前段も容易 |
| テスト | pytest | パーサ・正規化の回帰防止 |

フロントエンドフレームワーク・ビルドチェーンは不使用(依存最小・表示速度優先)。

## セットアップ

```bash
pip install -r requirements.txt
```

## データ取得・更新

```bash
# リポジトリ同梱の取得済みファイル(data/raw/)だけでDBを構築(ネットワーク不要)
make db

# 公表元から新規ファイルを取得して更新(リクエスト間隔3秒、取得済みはキャッシュ利用)
python3 -m etl.run_import --years 2023 2024 2025 2026
```

- 取得結果(成功/失敗/parse失敗/未解決企業/未分類件数)は標準出力と `import_jobs` テーブルに記録される。
- 未公表月の404は `.404` マーカーで記録し、再アクセスしない。
- **防衛省サーバーへの高頻度アクセスは行わないこと。**

## Web起動

```bash
make web   # = python3 -m web.app → http://127.0.0.1:5000
```

ページ: `/` `/contracts/` `/contracts/<id>/` `/companies/<slug>/` `/categories/<slug>/`
`/rankings/companies/` `/sources/` `/methodology/`

## テスト

```bash
make test  # = python3 -m pytest tests/
```

金額(カンマ・注記・億万表記・全角)、日付(Excel型・和暦・注記)、法人名(（株）展開・全角半角)、
列順入れ替え・列欠落などのケースを含む。

## DB構造

`etl/schema.sql` 参照。要点:

- **raw層とnormalized層の分離**: `raw_contracts` は公表ファイルの原文を全列保持。
  `contracts` は正規化済み。正規化ロジック改修後は raw から全再生成できる
  (`normalization_version` で世代管理)。
- **企業同定**: `companies` + `company_aliases`。第一キーは公表ファイル記載の**法人番号**。
  法人番号のないFMS(米軍各省)等はシード辞書で解決し `entity_type` で区別。
  文字列類似だけで別法人を統合しない。
- **出典追跡**: 全契約が `sources` (取得元URL・SHA-256・取得日時・ライセンス)に紐づく。
- **品質可視化**: parse失敗・未解決・未分類は `normalization_status` / `normalization_flags`
  に記録し、破棄しない。

## データソースと利用規約

- 出典: 防衛装備庁「[契約に係る情報の公表（中央調達分）](https://www.mod.go.jp/atla/souhon/supply/jisseki/rakusatu/index.html)」を加工して作成
- ライセンス: [公共データ利用規約(第1.0版)(PDL1.0)](https://www.mod.go.jp/j/info/contents.html) — CC BY 4.0互換、出典記載必須
- 収録範囲は**中央調達のみ**(地方調達・非公表契約を含まない)。
- **契約金額は企業の防衛関連売上高ではない。** この注意はサイト全ページに表示している。

## デプロイ

小型VPS + Docker(gunicorn) + Caddy を採用。詳細と運用手順は
[docs/deployment.md](docs/deployment.md) 参照。ローカル検証:

```bash
docker compose up --build   # http://localhost:8000
```

## 関連ドキュメント

- [docs/data-survey.md](docs/data-survey.md) — 一次データ調査(中央調達xlsx)
- [docs/honbu-pdf-survey.md](docs/honbu-pdf-survey.md) — 本部一般調達PDFの調査と対応方針(次期スコープ)
- [docs/deployment.md](docs/deployment.md) — デプロイ構成

## ディレクトリ

```
etl/        取得・パース・正規化・投入 (schema.sql含む)
web/        Flaskアプリ + テンプレート
data/raw/   取得した公表ファイル(原本キャッシュ、コミット対象)
data/db/    SQLite (生成物、gitignore)
tests/      パーサ・正規化・分類のテスト
docs/       一次データ調査結果
```
