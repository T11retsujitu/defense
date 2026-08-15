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
    row_index INTEGER NOT NULL,            -- ファイル内の行番号
    fiscal_year INTEGER NOT NULL,          -- ファイル名由来の年度
    raw_title TEXT,
    raw_quantity TEXT,
    raw_unit TEXT,
    raw_agency TEXT,
    raw_contract_date TEXT,
    raw_company TEXT,
    raw_corporate_number TEXT,
    raw_planned_price TEXT,
    raw_amount TEXT,
    raw_award_rate TEXT,
    raw_method_detail TEXT,
    raw_remarks TEXT,
    raw_row_json TEXT NOT NULL,            -- 行全体のJSON(列順そのまま)
    dup_group TEXT,                        -- 同一ファイル内で行内容が完全一致するグループのハッシュ
    suspected_duplicate INTEGER NOT NULL DEFAULT 0,  -- グループ内2行目以降=1(削除はしない)
    imported_at TEXT NOT NULL,
    UNIQUE (source_id, row_index)
);

-- 正規化済み企業・契約相手方
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    corporate_number TEXT UNIQUE,          -- 法人番号(13桁)。外国政府等はNULL
    name TEXT NOT NULL,                    -- 正規化後の正式名称
    normalized_name TEXT NOT NULL,         -- 照合用キー(NFKC等)
    slug TEXT NOT NULL UNIQUE,             -- 決定的(再構築で不変)
    entity_type TEXT NOT NULL DEFAULT 'company'
    -- company: 国内民間(法人・個人事業者) / foreign_company: 外国企業
    -- foreign_government: 外国政府等(FMS) / gov_agency: 公的機関 / other: 不明
);

-- 表記揺れ辞書
CREATE TABLE IF NOT EXISTS company_aliases (
    id INTEGER PRIMARY KEY,
    alias TEXT NOT NULL,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    source TEXT NOT NULL,                  -- seed / corporate_number / rule_us_gov / auto
    confidence REAL NOT NULL,
    UNIQUE (alias, company_id)
);

-- カテゴリ(二軸)
-- axis='domain': 装備分野(何を調達したか) / axis='nature': 契約目的(何のための契約か)
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    axis TEXT NOT NULL,                    -- domain / nature
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 999,
    UNIQUE (axis, name)
);

-- 正規化済み契約
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY,
    raw_contract_id INTEGER NOT NULL UNIQUE REFERENCES raw_contracts(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    fiscal_year INTEGER NOT NULL,          -- 契約締結日から導出(日付不明時はファイル年度)
    contract_date TEXT,
    company_id INTEGER REFERENCES companies(id),
    title TEXT NOT NULL,
    amount INTEGER,
    planned_price INTEGER,
    award_rate REAL,
    procurement_method TEXT NOT NULL,
    agency TEXT NOT NULL,                  -- 調達機関(防衛装備庁)
    agency_department TEXT,                -- 部局・官職(例: 調達事業部長)
    agency_location TEXT,                  -- 所在地
    domain_category_id INTEGER REFERENCES categories(id),   -- 装備分野
    nature_category_id INTEGER REFERENCES categories(id),   -- 契約目的
    domain_rule_matched INTEGER NOT NULL DEFAULT 0,  -- 1=ルール一致 / 0=未分類
    domain_unmatched_reason TEXT,          -- 未分類の理由(insufficient_context/cross_domain/manual_review_pending)
    nature_rule_matched INTEGER NOT NULL DEFAULT 0,  -- 1=ルール一致 / 0=既定値(物品取得・その他)適用
    suspected_duplicate INTEGER NOT NULL DEFAULT 0,  -- raw層の完全一致重複フラグを引き継ぐ
    normalization_status TEXT NOT NULL,    -- ok / partial
    normalization_flags TEXT,              -- amount_failed;date_failed;company_unresolved;company_ambiguous 等
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
    rows_deleted INTEGER DEFAULT 0,        -- 差し替えで消えた行の削除数
    parse_failures INTEGER DEFAULT 0,
    companies_unresolved INTEGER DEFAULT 0,
    uncategorized INTEGER DEFAULT 0,       -- domain未分類
    suspected_duplicates INTEGER DEFAULT 0,
    log TEXT
);

CREATE INDEX IF NOT EXISTS idx_contracts_fy ON contracts(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_contracts_company ON contracts(company_id);
CREATE INDEX IF NOT EXISTS idx_contracts_domain ON contracts(domain_category_id);
CREATE INDEX IF NOT EXISTS idx_contracts_nature ON contracts(nature_category_id);
CREATE INDEX IF NOT EXISTS idx_contracts_date ON contracts(contract_date);
CREATE INDEX IF NOT EXISTS idx_contracts_amount ON contracts(amount);
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_contracts(source_id);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON company_aliases(alias);
