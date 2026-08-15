# デプロイ構成 (決定)

## 方針

個人運営・低維持費・ロックイン回避を前提に、**「小型VPS 1台 + Docker + Caddy」** を採用する。

```
[ユーザー] ── HTTPS ── [Caddy (自動TLS)] ── [gunicorn + Flask] ── [SQLite (読み取り)]
                                              ↑
                        [cron: 月次 etl.run_import → DB差し替え]
```

## 選定理由

| 観点 | 判断 |
|---|---|
| データ規模 | 全期間でも数万契約・DB数十MB。SQLite読み取り専用で十分。RDSやマネージドDBは過剰 |
| 更新頻度 | 公表元が月次。リアルタイム性不要。cronで `etl.run_import` を月次実行し、生成済みDBをアトミックに差し替える |
| コスト | VPS 1台(月500〜1,000円程度)で完結。無料枠依存(Fly.io/Render等)はポリシー変更リスクがあるため主構成にしない |
| SEO | FlaskのSSRがそのままHTMLを返す。将来トラフィックが増えたらCaddy/CDNのキャッシュを前段に足す |
| ロックイン | Docker Compose一式で任意のVPS/自宅サーバへ移設可能 |
| 将来のAPI | 同一FlaskアプリにJSONエンドポイントを追加するだけ |

代替案として検討し、見送った構成:
- **静的サイト生成(SSG)+オブジェクトストレージ**: 契約検索(自由なフィルタ組合せ)が
  静的化できず、検索だけ別途サーバが必要になり構成が二重化するため見送り。
  ただし企業・カテゴリ・ランキング等の主要SEOページのみ事前生成してCDNに置く
  ハイブリッドは、負荷が問題になった段階の有力な選択肢。
- **PaaS(Fly.io/Render等)**: 手軽だが無料枠・料金体系の変動リスク。VPSより高くつく場合がある。

## 手順 (VPS上)

```bash
docker compose up -d          # web(gunicorn) + caddy
# 月次更新 (cron例: 毎月5日 04:00)
0 4 5 * * cd /srv/defense && docker compose run --rm web python -m etl.run_import && docker compose restart web
```

`Dockerfile` と `docker-compose.yml` はリポジトリ同梱。ローカル検証:

```bash
docker compose up --build   # http://localhost:8000
```

## 運用メモ

- DBは読み取り専用で配信し、更新はETLコンテナ側で生成→完了後に置き換え(WALのままコピーしない)。
- `import_jobs` の files_failed / parse_failures が0でない場合は差し替えを中止する
  (Makefileの `make db` は失敗時に非0を返す)。
- バックアップは `data/raw/`(原本)と生成スクリプトがあれば再構築できるため、
  rawディレクトリのみ定期退避すれば足りる。
- robots.txt / sitemap.xml は Flask 側で配信(未実装、公開前に追加)。
  index対象はトップ・主要企業・カテゴリ・年度・ランキングに限定し、
  検索結果のフィルタ組合せはnoindexとする(仕様19条)。
