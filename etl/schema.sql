-- 防衛調達データベース PoC スキーマ
-- 方針: 政府公開の原データ(raw_contracts)と当サイト側で正規化したデータ(contracts)を分離する。
-- raw_contracts は原文を保持し、正規化ロジック改修後に contracts を全再生成できる。

PRAGMA journal_mode = WAL;

-- 取得した公表ファイル(=出典)単位のレコード
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    organization TEXT NOT NULL,            -- 例: 防衛装備庁
    title TEXT NOT NULL,                   -- 例: 契約に係る情報の公表（中央調達分） 令和7年度 随意契約 2月分
    url TEXT NOT NULL UNIQUE,              -- 取得元ファイルURL
    landing_page TEXT,                     -- 掲載元ページURL
    fiscal_year INTEGER NOT NULL,          -- 会計年度(西暦表記: 令和5年度=2023)
    method_group TEXT NOT NULL,            -- kyoso(競争入札) / zuikei(随意契約)
    month INTEGER,                         -- 公表対象月(1-12)
    file_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,            -- 取得日時(ISO8601)
    license TEXT NOT NULL,                 -- 公共データ利用規約(PDL1.0) 等
    row_count INTEGER NOT NULL DEFAULT 0
);

-- 原データ: 公表ファイルの行をそのまま保持
CREATE TABLE IF NOT EXISTS raw_contracts (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    row_index INTEGER NOT NULL,            -- ファイル内の行番号(0始まり、ヘッダ含む実行番号)
    fiscal_year INTEGER NOT NULL,
    raw_title TEXT,                        -- 物品役務等の名称
    raw_quantity TEXT,
    raw_unit TEXT,
    raw_agency TEXT,                       -- 契約担当官等(部局名・所在地含む原文)
    raw_contract_date TEXT,                -- 契約締結日(原文/ISO文字列化)
    raw_company TEXT,                      -- 契約相手方の商号又は名称及び住所(原文)
    raw_corporate_number TEXT,             -- 法人番号(原文)
    raw_planned_price TEXT,                -- 予定価格
    raw_amount TEXT,                       -- 契約金額
    raw_award_rate TEXT,                   -- 落札率
    raw_method_detail TEXT,                -- 競争区分 or 随契根拠条文・理由
    raw_remarks TEXT,                      -- 備考
    raw_row_json TEXT NOT NULL,            -- 行全体のJSON(列順そのまま)
    imported_at TEXT NOT NULL,
    UNIQUE (source_id, row_index)
);

-- 正規化済み企業
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    corporate_number TEXT UNIQUE,          -- 法人番号(13桁)。外国政府等はNULL
    name TEXT NOT NULL,                    -- 正規化後の正式名称
    normalized_name TEXT NOT NULL,         -- 照合用キー(NFKC等)
    slug TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL DEFAULT 'company'  -- company / foreign_government / gov_agency / other
);

-- 表記揺れ辞書
CREATE TABLE IF NOT EXISTS company_aliases (
    id INTEGER PRIMARY KEY,
    alias TEXT NOT NULL,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    source TEXT NOT NULL,                  -- seed / corporate_number / auto
    confidence REAL NOT NULL,
    UNIQUE (alias, company_id)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 999
);

-- 正規化済み契約
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY,
    raw_contract_id INTEGER NOT NULL UNIQUE REFERENCES raw_contracts(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    fiscal_year INTEGER NOT NULL,
    contract_date TEXT,                    -- ISO8601 date。parse失敗時NULL
    company_id INTEGER REFERENCES companies(id),  -- 未解決時NULL
    title TEXT NOT NULL,
    amount INTEGER,                        -- 契約金額(円)。parse失敗時NULL
    planned_price INTEGER,                 -- 予定価格(円)。非公表・parse不能はNULL
    award_rate REAL,                       -- 落札率
    procurement_method TEXT NOT NULL,      -- 一般競争入札 / 指名競争入札 / 随意契約 等
    agency TEXT NOT NULL,                  -- 調達機関(正規化: 防衛装備庁)
    agency_detail TEXT,                    -- 契約担当官部局(原文短縮)
    category_id INTEGER REFERENCES categories(id),
    classification_confidence REAL,        -- 分類確信度(ルール一致=1.0 / 未分類=0)
    normalization_status TEXT NOT NULL,    -- ok / partial
    normalization_flags TEXT,              -- amount_failed;date_failed;company_unresolved 等
    normalization_version TEXT NOT NULL
);

-- 取込ジョブのログ
CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    files_fetched INTEGER DEFAULT 0,
    files_cached INTEGER DEFAULT 0,
    files_failed INTEGER DEFAULT 0,
    rows_raw INTEGER DEFAULT 0,
    rows_inserted INTEGER DEFAULT 0,
    rows_updated INTEGER DEFAULT 0,
    parse_failures INTEGER DEFAULT 0,
    companies_unresolved INTEGER DEFAULT 0,
    uncategorized INTEGER DEFAULT 0,
    log TEXT
);

CREATE INDEX IF NOT EXISTS idx_contracts_fy ON contracts(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_contracts_company ON contracts(company_id);
CREATE INDEX IF NOT EXISTS idx_contracts_category ON contracts(category_id);
CREATE INDEX IF NOT EXISTS idx_contracts_date ON contracts(contract_date);
CREATE INDEX IF NOT EXISTS idx_contracts_amount ON contracts(amount);
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_contracts(source_id);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON company_aliases(alias);
