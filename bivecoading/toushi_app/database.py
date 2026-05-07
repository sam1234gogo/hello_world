"""
database.py - SQLite3データベース管理モジュール
投資アシスタントアプリのデータ永続化を担当する
"""

import sqlite3
import os
from datetime import datetime

# データベースファイルのパス
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'toushi.db')

# デフォルト設定値
DEFAULT_SETTINGS = {
    # 基本設定
    'nikkei_pbr': '1.3',             # 日経平均PBR（手動設定）
    'target_yield_min': '3.0',       # 目標配当利回り最小（%）
    'target_yield_max': '5.0',       # 目標配当利回り最大（%）
    'screening_target': 'core30_large70',  # スクリーニング対象
    'update_interval_minutes': '60', # データ更新頻度（分）
    # 短期売買ルール
    'short_anshin_pbr': '0.75',      # 安心割安株 PBR上限
    'short_anshin_yield': '2.4',     # 安心割安株 配当利回り下限（%）
    'short_nanpin1': '10',           # ナンピン1回目 下落率（%）
    'short_nanpin2': '15',           # ナンピン2回目 下落率（%）
    'short_nanpin3': '20',           # ナンピン3回目 下落率（%）
    'short_profit_normal': '9.0',    # 通常売り目標利益率（%）
    'short_profit_nanpin': '10.0',   # ナンピン後売り目標利益率（%）
    'short_loss_cut': '25.0',        # ロスカット下落率（%）
    'short_holding_days': '90',      # 保有期間上限（日）
    # 中期売買ルール（PBR係数）
    'mid_intl_black_coeff': '0.6',   # 国際優良企業（黒字）係数
    'mid_intl_red_coeff': '0.3',     # 国際優良企業（赤字）係数
    'mid_fin_black_coeff': '0.5',    # 財務優良企業（黒字）係数
    'mid_fin_red_coeff': '0.25',     # 財務優良企業（赤字）係数
    # 銘柄フィルタ条件
    'intl_bps_min': '500',           # 国際優良企業 BPS下限（円）
    'intl_equity_ratio_min': '30',   # 国際優良企業 自己資本比率下限（%）
    'fin_net_assets_min': '500',     # 財務優良企業 純資産下限（億円）
    'fin_bps_min': '1000',           # 財務優良企業 BPS下限（円）
    'fin_equity_ratio_min': '60',    # 財務優良企業 自己資本比率下限（%）
    'eps_revision_threshold': '10.0', # EPS上方修正判定閾値（%）
}


def get_db():
    """データベース接続を取得する（Row形式で返す）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 辞書形式でアクセス可能にする
    conn.execute("PRAGMA journal_mode=WAL")  # 並列アクセス性能向上
    conn.execute("PRAGMA foreign_keys=ON")   # 外部キー制約を有効化
    return conn


def init_db():
    """
    データベースの初期化
    テーブルが存在しない場合のみ作成する
    """
    conn = get_db()
    cursor = conn.cursor()

    # ポートフォリオテーブル（保有株管理）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,                    -- 銘柄コード（例: 7203）
            stock_name TEXT NOT NULL,                -- 銘柄名
            investment_type TEXT NOT NULL,           -- 投資種別: short/medium/long
            category TEXT,                           -- 区分: 安心割安株/通常割安株/成長株
            purchase_date TEXT NOT NULL,             -- 購入日（YYYY-MM-DD形式）
            purchase_price REAL NOT NULL,            -- 購入単価（円）
            shares INTEGER NOT NULL,                 -- 保有株数
            status TEXT DEFAULT 'active',            -- 状態: active/sold
            nanpin_count INTEGER DEFAULT 0,          -- ナンピン回数
            three_month_deadline TEXT,               -- 3ヶ月期限（YYYY-MM-DD形式）
            sell_price REAL,                         -- 売却価格（売却後に記録）
            sell_date TEXT,                          -- 売却日
            notes TEXT,                              -- メモ・備考
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ナンピン履歴テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nanpin_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,           -- portfolioテーブルのID
            date TEXT NOT NULL,                      -- ナンピン日（YYYY-MM-DD形式）
            price REAL NOT NULL,                     -- ナンピン時の購入価格
            shares INTEGER NOT NULL,                 -- ナンピンで購入した株数
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (portfolio_id) REFERENCES portfolio(id)
        )
    ''')

    # 設定テーブル（キーバリュー形式）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,                    -- 設定キー
            value TEXT NOT NULL,                     -- 設定値
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 株価キャッシュテーブル（yfinanceのデータを1時間キャッシュ）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_cache (
            ticker TEXT PRIMARY KEY,                 -- 銘柄コード（例: 7203）
            stock_name TEXT,                         -- 銘柄名
            current_price REAL,                      -- 現在株価
            pbr REAL,                                -- PBR（株価純資産倍率）
            per REAL,                                -- PER（株価収益率）
            roe REAL,                                -- ROE（自己資本利益率）
            dividend_yield REAL,                     -- 配当利回り（%）
            bps REAL,                                -- BPS（1株純資産）
            equity_ratio REAL,                       -- 自己資本比率（%）
            forward_eps REAL,                        -- 予想EPS
            market_cap REAL,                         -- 時価総額
            week52_low REAL,                         -- 52週安値
            week52_high REAL,                        -- 52週高値
            net_assets REAL,                         -- 純資産（億円）
            annual_dividend REAL,                    -- 年間配当（1株当たり）
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # EPS履歴テーブル（EPS改訂率の追跡用）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS eps_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,                    -- 銘柄コード
            recorded_at TEXT NOT NULL,               -- 記録日時
            forward_eps REAL,                        -- 予想EPS
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 財務指標キャッシュ（t.info を24時間キャッシュ）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fundamentals_cache (
            code TEXT PRIMARY KEY,
            info_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    # シミュレーションスクリーニング結果キャッシュ（再起動後も有効）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS simulation_cache (
            cache_key TEXT PRIMARY KEY,
            results_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    # 過去財務諸表キャッシュ（バランスシート・損益計算書の時系列データ）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS balance_sheet_cache (
            code TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    # 通期予想EPS修正履歴（株探スクレイピング）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS eps_forecast_history (
            ticker           TEXT NOT NULL,
            fiscal_year_end  TEXT NOT NULL,   -- 例: '2026-03-31'
            announcement_date TEXT NOT NULL,  -- 例: '2025-05-08' (発表日)
            revision_type    TEXT NOT NULL,   -- 'initial' / 'revision' / 'actual'
            net_income_m     REAL,            -- 最終益（百万円）
            eps              REAL,            -- 通期予想EPS（円）
            scraped_at       TEXT NOT NULL,
            PRIMARY KEY (ticker, fiscal_year_end, announcement_date)
        )
    ''')

    # 銘柄追加情報テーブル（手動設定項目）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_manual_info (
            ticker TEXT PRIMARY KEY,                 -- 銘柄コード
            overseas_sales_ratio REAL,               -- 海外売上高比率（%）
            stable_shareholder_ratio REAL,           -- 安定株主率（%）
            is_topix_core30 INTEGER DEFAULT 0,       -- TOPIX Core30採用フラグ
            is_topix_large70 INTEGER DEFAULT 0,      -- TOPIX Large70採用フラグ
            notes TEXT,                              -- メモ
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()

    # デフォルト設定を挿入（存在しない場合のみ）
    for key, value in DEFAULT_SETTINGS.items():
        cursor.execute('''
            INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)
        ''', (key, value))

    conn.commit()
    conn.close()
    print(f"データベースを初期化しました: {DB_PATH}")


def get_simulation_cache(cache_key):
    """スクリーニング結果キャッシュを取得する（過去データは永続、当日分は24h有効）"""
    import json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT results_json, created_at FROM simulation_cache WHERE cache_key = ?', (cache_key,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    # 当日分（cache_keyが今日の日付を含む）は24h制限
    from datetime import datetime, timedelta, date
    today_str = date.today().strftime('%Y-%m-%d')
    if today_str in cache_key:
        created = datetime.fromisoformat(row['created_at'])
        if datetime.now() - created > timedelta(hours=24):
            return None
    return json.loads(row['results_json'])


def save_simulation_cache(cache_key, results):
    """スクリーニング結果をSQLiteに保存する"""
    import json
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        'INSERT OR REPLACE INTO simulation_cache (cache_key, results_json, created_at) VALUES (?, ?, ?)',
        (cache_key, json.dumps(results, ensure_ascii=False), now)
    )
    conn.commit()
    conn.close()


def get_fundamentals_cache(code, max_age_hours=24):
    """
    t.info キャッシュを取得する（max_age_hours 時間以内のみ有効）
    Returns: info dict または None
    """
    import json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT info_json, updated_at FROM fundamentals_cache WHERE code = ?', (code,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    # 有効期限チェック
    from datetime import datetime, timedelta
    updated = datetime.fromisoformat(row['updated_at'])
    if datetime.now() - updated > timedelta(hours=max_age_hours):
        return None
    return json.loads(row['info_json'])


def save_fundamentals_cache(code, info_dict):
    """t.info データを SQLite にキャッシュする"""
    import json
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        'INSERT OR REPLACE INTO fundamentals_cache (code, info_json, updated_at) VALUES (?, ?, ?)',
        (code, json.dumps(info_dict), now)
    )
    conn.commit()
    conn.close()


def get_balance_sheet_cache(code, max_age_days=30):
    """
    過去財務諸表キャッシュを取得する（30日間有効）
    Returns: data dict または None
      data = {
        "columns":     ["2024-03-31", ...],   # 決算期日（降順）
        "equity":      [float|None, ...],     # 自己資本
        "total_assets":[float|None, ...],     # 総資産
        "net_income":  [float|None, ...],     # 純利益
        "shares":      float|None             # 発行済株式数（最新・近似）
      }
    """
    import json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT data_json, updated_at FROM balance_sheet_cache WHERE code = ?', (code,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    from datetime import datetime, timedelta
    updated = datetime.fromisoformat(row['updated_at'])
    if datetime.now() - updated > timedelta(days=max_age_days):
        return None
    return json.loads(row['data_json'])


def save_balance_sheet_cache(code, data):
    """過去財務諸表データを SQLite にキャッシュする"""
    import json
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        'INSERT OR REPLACE INTO balance_sheet_cache (code, data_json, updated_at) VALUES (?, ?, ?)',
        (code, json.dumps(data, ensure_ascii=False), now)
    )
    conn.commit()
    conn.close()


def save_eps_forecast_history(ticker, records):
    """
    通期予想EPS修正履歴をDBに保存する。
    records: list of {fiscal_year_end, announcement_date, revision_type, net_income_m, eps}
    """
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    for r in records:
        cursor.execute('''
            INSERT OR REPLACE INTO eps_forecast_history
            (ticker, fiscal_year_end, announcement_date, revision_type,
             net_income_m, eps, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (ticker, r['fiscal_year_end'], r['announcement_date'],
              r['revision_type'], r.get('net_income_m'), r.get('eps'), now))
    conn.commit()
    conn.close()


def get_eps_forecast_history(ticker, max_age_days=7):
    """
    通期予想EPS修正履歴をDBから取得する（7日キャッシュ）。
    Returns: list of dict（announcement_date 昇順）
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT fiscal_year_end, announcement_date, revision_type,
               net_income_m, eps, scraped_at
        FROM eps_forecast_history
        WHERE ticker = ?
        ORDER BY fiscal_year_end ASC, announcement_date ASC
    ''', (ticker,))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return None
    from datetime import timedelta
    latest = max(r['scraped_at'] for r in rows)
    if datetime.now() - datetime.fromisoformat(latest) > timedelta(days=max_age_days):
        return None
    return [dict(r) for r in rows]


def get_setting(key):
    """
    設定値を取得する
    Args:
        key: 設定キー
    Returns:
        設定値（文字列）またはNone
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row['value']
    return DEFAULT_SETTINGS.get(key)


def set_setting(key, value):
    """
    設定値を更新する
    Args:
        key: 設定キー
        value: 設定値
    """
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute('''
        INSERT OR REPLACE INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
    ''', (key, str(value), now))
    conn.commit()
    conn.close()


def add_portfolio_entry(ticker, stock_name, investment_type, category,
                         purchase_date, purchase_price, shares, notes=''):
    """
    ポートフォリオに新しい銘柄を追加する
    Args:
        ticker: 銘柄コード
        stock_name: 銘柄名
        investment_type: 投資種別（short/medium/long）
        category: 区分（安心割安株/通常割安株/成長株）
        purchase_date: 購入日
        purchase_price: 購入単価
        shares: 株数
        notes: メモ
    Returns:
        新しく追加されたエントリのID
    """
    from datetime import datetime, timedelta

    conn = get_db()
    cursor = conn.cursor()

    # 短期投資の場合は3ヶ月期限を設定
    three_month_deadline = None
    if investment_type == 'short':
        purchase_dt = datetime.strptime(purchase_date, '%Y-%m-%d')
        deadline_dt = purchase_dt + timedelta(days=90)  # 3ヶ月後
        three_month_deadline = deadline_dt.strftime('%Y-%m-%d')

    now = datetime.now().isoformat()
    cursor.execute('''
        INSERT INTO portfolio
        (ticker, stock_name, investment_type, category, purchase_date,
         purchase_price, shares, status, nanpin_count, three_month_deadline,
         notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?, ?)
    ''', (ticker, stock_name, investment_type, category, purchase_date,
          purchase_price, shares, three_month_deadline, notes, now, now))

    entry_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return entry_id


def get_portfolio(status=None, investment_type=None):
    """
    ポートフォリオ一覧を取得する
    Args:
        status: フィルタするステータス（active/sold/None=全件）
        investment_type: 投資種別フィルタ
    Returns:
        ポートフォリオエントリのリスト
    """
    conn = get_db()
    cursor = conn.cursor()

    query = 'SELECT * FROM portfolio WHERE 1=1'
    params = []

    if status:
        query += ' AND status = ?'
        params.append(status)

    if investment_type:
        query += ' AND investment_type = ?'
        params.append(investment_type)

    query += ' ORDER BY purchase_date DESC'

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    # Row オブジェクトを辞書に変換
    return [dict(row) for row in rows]


def get_portfolio_by_id(entry_id):
    """指定IDのポートフォリオエントリを取得する"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM portfolio WHERE id = ?', (entry_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_portfolio_status(entry_id, status, sell_price=None, sell_date=None):
    """
    ポートフォリオのステータスを更新する（売却時など）
    Args:
        entry_id: ポートフォリオID
        status: 新しいステータス
        sell_price: 売却価格（売却時）
        sell_date: 売却日（売却時）
    """
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    if sell_price and sell_date:
        cursor.execute('''
            UPDATE portfolio
            SET status = ?, sell_price = ?, sell_date = ?, updated_at = ?
            WHERE id = ?
        ''', (status, sell_price, sell_date, now, entry_id))
    else:
        cursor.execute('''
            UPDATE portfolio SET status = ?, updated_at = ? WHERE id = ?
        ''', (status, now, entry_id))

    conn.commit()
    conn.close()


def add_nanpin(portfolio_id, date, price, shares):
    """
    ナンピン履歴を記録する
    Args:
        portfolio_id: ポートフォリオID
        date: ナンピン日
        price: ナンピン時の購入価格
        shares: ナンピンで購入した株数
    """
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    # ナンピン履歴を追加
    cursor.execute('''
        INSERT INTO nanpin_history (portfolio_id, date, price, shares, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (portfolio_id, date, price, shares, now))

    # ポートフォリオのナンピン回数を更新
    cursor.execute('''
        UPDATE portfolio SET nanpin_count = nanpin_count + 1, updated_at = ?
        WHERE id = ?
    ''', (now, portfolio_id))

    conn.commit()
    conn.close()


def get_nanpin_history(portfolio_id):
    """指定ポートフォリオIDのナンピン履歴を取得する"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM nanpin_history WHERE portfolio_id = ? ORDER BY date
    ''', (portfolio_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def calculate_average_price(portfolio_id):
    """
    ナンピンを含めた平均購入価格を計算する
    Args:
        portfolio_id: ポートフォリオID
    Returns:
        平均購入価格, 合計株数
    """
    entry = get_portfolio_by_id(portfolio_id)
    if not entry:
        return None, None

    # 初回購入
    total_cost = entry['purchase_price'] * entry['shares']
    total_shares = entry['shares']

    # ナンピン分を加算
    nanpin_history = get_nanpin_history(portfolio_id)
    for np_entry in nanpin_history:
        total_cost += np_entry['price'] * np_entry['shares']
        total_shares += np_entry['shares']

    avg_price = total_cost / total_shares if total_shares > 0 else 0
    return avg_price, total_shares


def save_stock_cache(ticker, data_dict):
    """
    株価データをキャッシュに保存する
    Args:
        ticker: 銘柄コード
        data_dict: 株価データの辞書
    """
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute('''
        INSERT OR REPLACE INTO stock_cache
        (ticker, stock_name, current_price, pbr, per, roe, dividend_yield,
         bps, equity_ratio, forward_eps, market_cap, week52_low, week52_high,
         net_assets, annual_dividend, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        ticker,
        data_dict.get('stock_name'),
        data_dict.get('current_price'),
        data_dict.get('pbr'),
        data_dict.get('per'),
        data_dict.get('roe'),
        data_dict.get('dividend_yield'),
        data_dict.get('bps'),
        data_dict.get('equity_ratio'),
        data_dict.get('forward_eps'),
        data_dict.get('market_cap'),
        data_dict.get('week52_low'),
        data_dict.get('week52_high'),
        data_dict.get('net_assets'),
        data_dict.get('annual_dividend'),
        now
    ))
    conn.commit()
    conn.close()


def get_stock_cache(ticker):
    """
    キャッシュから株価データを取得する（1時間以内のデータのみ有効）
    Args:
        ticker: 銘柄コード
    Returns:
        株価データの辞書またはNone（キャッシュ切れ・未登録の場合）
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_cache WHERE ticker = ?', (ticker,))
    row = cursor.fetchone()
    conn.close()

    if row:
        # キャッシュの有効期限チェック（1時間）
        updated_at = datetime.fromisoformat(row['updated_at'])
        elapsed = (datetime.now() - updated_at).total_seconds()
        if elapsed < 3600:  # 3600秒 = 1時間
            return dict(row)

    return None  # キャッシュ切れまたは未登録


def save_eps_history(ticker, forward_eps):
    """
    EPS履歴を保存する（EPS改訂率追跡のため）
    Args:
        ticker: 銘柄コード
        forward_eps: 予想EPS
    """
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute('''
        INSERT INTO eps_history (ticker, recorded_at, forward_eps, created_at)
        VALUES (?, ?, ?, ?)
    ''', (ticker, now, forward_eps, now))

    conn.commit()
    conn.close()


def get_latest_eps(ticker, exclude_latest=False):
    """
    指定銘柄の最新EPS履歴を取得する
    Args:
        ticker: 銘柄コード
        exclude_latest: 最新を除いた直前のEPSを取得するか
    Returns:
        EPS値またはNone
    """
    conn = get_db()
    cursor = conn.cursor()

    if exclude_latest:
        cursor.execute('''
            SELECT forward_eps FROM eps_history
            WHERE ticker = ?
            ORDER BY recorded_at DESC
            LIMIT 1 OFFSET 1
        ''', (ticker,))
    else:
        cursor.execute('''
            SELECT forward_eps FROM eps_history
            WHERE ticker = ?
            ORDER BY recorded_at DESC
            LIMIT 1
        ''', (ticker,))

    row = cursor.fetchone()
    conn.close()
    return row['forward_eps'] if row else None


def get_prev_eps(ticker, min_days_ago=30):
    """
    min_days_ago日以上前に記録されたEPSのうち最新のものを返す。
    四半期サイクルでの上方修正検出に使用（デフォルト30日）。
    Returns: EPS値 または None
    """
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=min_days_ago)).isoformat()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT forward_eps FROM eps_history
        WHERE ticker = ? AND recorded_at <= ?
        ORDER BY recorded_at DESC
        LIMIT 1
    ''', (ticker, cutoff))
    row = cursor.fetchone()
    conn.close()
    return row['forward_eps'] if row else None


def get_manual_info(ticker):
    """銘柄の手動設定情報を取得する"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_manual_info WHERE ticker = ?', (ticker,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}


def save_manual_info(ticker, overseas_sales_ratio=None, stable_shareholder_ratio=None,
                      is_topix_core30=None, is_topix_large70=None, notes=None):
    """銘柄の手動設定情報を保存する"""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute('''
        INSERT OR REPLACE INTO stock_manual_info
        (ticker, overseas_sales_ratio, stable_shareholder_ratio,
         is_topix_core30, is_topix_large70, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (ticker, overseas_sales_ratio, stable_shareholder_ratio,
          is_topix_core30, is_topix_large70, notes, now))

    conn.commit()
    conn.close()


def get_all_manual_info():
    """全銘柄の手動設定情報を取得する"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM stock_manual_info ORDER BY ticker')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
