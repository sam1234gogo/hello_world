"""
stock_data.py - 株価データ取得モジュール
yfinanceを使って日本株のデータを取得し、DBにキャッシュする
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import time
import traceback

from database import (
    save_stock_cache, get_stock_cache,
    save_eps_history, get_latest_eps
)

# =============================================================================
# TOPIX Core30銘柄リスト（定数）
# =============================================================================
TOPIX_CORE30 = [
    ("7203", "トヨタ自動車"),
    ("6758", "ソニーグループ"),
    ("8306", "三菱UFJFG"),
    ("8035", "東京エレクトロン"),
    ("4063", "信越化学工業"),
    ("6861", "キーエンス"),
    ("8058", "三菱商事"),
    ("9432", "NTT"),
    ("7974", "任天堂"),
    ("7267", "本田技研工業"),
    ("8316", "三井住友FG"),
    ("6981", "村田製作所"),
    ("7011", "三菱重工業"),
    ("4519", "中外製薬"),
    ("9984", "ソフトバンクG"),
    ("6367", "ダイキン工業"),
    ("8031", "三井物産"),
    ("7751", "キヤノン"),
    ("6954", "ファナック"),
    ("9433", "KDDI"),
    ("4543", "テルモ"),
    ("4568", "第一三共"),
    ("6902", "デンソー"),
    ("6098", "リクルートHD"),
    ("8766", "東京海上HD"),
    ("4901", "富士フイルムHD"),
    ("4502", "武田薬品"),
    ("2802", "味の素"),
    ("9022", "JR東海"),
    ("8411", "みずほFG"),
]

# =============================================================================
# TOPIX Large70銘柄リスト（定数）（上位40銘柄程度）
# =============================================================================
TOPIX_LARGE70 = [
    ("2914", "JT"),
    ("3382", "セブン&アイHD"),
    ("4452", "花王"),
    ("5108", "ブリヂストン"),
    ("5401", "日本製鉄"),
    ("6503", "三菱電機"),
    ("6702", "富士通"),
    ("6752", "パナソニックHD"),
    ("6857", "アドバンテスト"),
    ("7270", "SUBARU"),
    ("7741", "HOYA"),
    ("8001", "伊藤忠商事"),
    ("8002", "丸紅"),
    ("8053", "住友商事"),
    ("8113", "ユニ・チャーム"),
    ("8591", "オリックス"),
    ("8604", "野村HD"),
    ("8801", "三井不動産"),
    ("8802", "三菱地所"),
    ("9020", "JR東日本"),
    ("9101", "日本郵船"),
    ("9107", "川崎汽船"),
    ("9104", "商船三井"),
    ("9735", "セコム"),
    ("9843", "ニトリHD"),
    ("4911", "資生堂"),
    ("4523", "エーザイ"),
    ("2503", "キリンHD"),
    ("2502", "アサヒグループHD"),
    ("1925", "大和ハウス工業"),
    ("1928", "積水ハウス"),
    ("3407", "旭化成"),
    ("5802", "住友電気工業"),
    ("6326", "クボタ"),
    ("6506", "安川電機"),
    ("6762", "TDK"),
    ("7733", "オリンパス"),
    ("8309", "三井住友トラストHD"),
    ("8630", "SOMPO HD"),
    ("8750", "第一生命HD"),
]

# 全銘柄リスト（Core30 + Large70）
ALL_TOPIX_STOCKS = TOPIX_CORE30 + TOPIX_LARGE70

# クイック確認用：時価総額・流動性の高い代表15銘柄
TOPIX_QUICK15 = [
    ("7203", "トヨタ自動車"),
    ("8306", "三菱UFJFG"),
    ("8316", "三井住友FG"),
    ("7267", "本田技研工業"),
    ("9432", "NTT"),
    ("9984", "ソフトバンクG"),
    ("8031", "三井物産"),
    ("8058", "三菱商事"),
    ("8411", "みずほFG"),
    ("8766", "東京海上HD"),
    ("7751", "キヤノン"),
    ("6902", "デンソー"),
    ("9022", "JR東海"),
    ("8591", "オリックス"),
    ("5401", "日本製鉄"),
]


def _to_yfinance_ticker(ticker_code):
    """
    銘柄コードをyfinance形式に変換する
    例: "7203" → "7203.T"
    Args:
        ticker_code: 銘柄コード（数字のみ）
    Returns:
        yfinance形式のティッカーシンボル
    """
    return f"{ticker_code}.T"


def _safe_get(info_dict, key, default=None):
    """
    辞書から安全に値を取得する（NaN/None対応）
    Args:
        info_dict: データ辞書
        key: キー
        default: デフォルト値
    Returns:
        値またはデフォルト値
    """
    value = info_dict.get(key, default)
    if value is None:
        return default
    # NaN チェック
    if isinstance(value, float) and np.isnan(value):
        return default
    return value


def get_stock_info(ticker_code):
    """
    個別銘柄の情報をyfinanceで取得する（キャッシュ利用、1時間有効）
    Args:
        ticker_code: 銘柄コード（例: "7203"）
    Returns:
        銘柄情報の辞書またはNone（取得失敗時）
    """
    # キャッシュチェック（1時間以内のデータがあれば返す）
    cached = get_stock_cache(ticker_code)
    if cached:
        return cached

    # キャッシュがないのでyfinanceから取得
    yf_ticker = _to_yfinance_ticker(ticker_code)

    try:
        ticker = yf.Ticker(yf_ticker)
        info = ticker.info

        # データが空の場合はスキップ
        if not info or 'regularMarketPrice' not in info and 'currentPrice' not in info:
            print(f"警告: {ticker_code} のデータが取得できませんでした")
            return None

        # 現在株価（複数フィールドから取得を試みる）
        current_price = (
            _safe_get(info, 'currentPrice') or
            _safe_get(info, 'regularMarketPrice') or
            _safe_get(info, 'previousClose')
        )

        # 時価総額（億円に変換）
        market_cap_raw = _safe_get(info, 'marketCap', 0)
        market_cap_oku = market_cap_raw / 1e8 if market_cap_raw else None

        # 純資産（億円に変換）
        total_stockholder_equity = _safe_get(info, 'totalStockholderEquity', 0)
        net_assets_oku = total_stockholder_equity / 1e8 if total_stockholder_equity else None

        # ROE計算（予想）
        # yfinanceではreturnOnEquityとして取得（小数形式）
        roe_raw = _safe_get(info, 'returnOnEquity')
        roe = roe_raw * 100 if roe_raw is not None else None  # パーセンテージに変換

        # 配当利回り
        # yfinanceの日本株（.T銘柄）はdividendYieldをすでにパーセント形式で返す
        # 例: トヨタ → 3.17（= 3.17%）、×100は不要
        div_yield_raw = _safe_get(info, 'dividendYield')
        dividend_yield = div_yield_raw  # そのまま使用（例: 3.17 → 3.17%）

        # 自己資本比率の計算
        # yfinanceから直接取得できないため、総資産と自己資本から計算
        total_assets = _safe_get(info, 'totalAssets', 0)
        equity_ratio = None
        if total_assets and total_stockholder_equity and total_assets > 0:
            equity_ratio = (total_stockholder_equity / total_assets) * 100

        # BPS（1株純資産）
        bps = _safe_get(info, 'bookValue')

        # 年間配当（1株あたり）
        annual_dividend = _safe_get(info, 'dividendRate')

        # 予想EPS
        forward_eps = _safe_get(info, 'forwardEps')

        # 銘柄名（日本語名は事前リストから、英語名はyfinanceから）
        stock_name_from_list = _get_stock_name_from_list(ticker_code)
        stock_name = stock_name_from_list or _safe_get(info, 'longName', ticker_code)

        # データを辞書にまとめる
        stock_data = {
            'ticker': ticker_code,
            'stock_name': stock_name,
            'current_price': current_price,
            'pbr': _safe_get(info, 'priceToBook'),
            'per': _safe_get(info, 'forwardPE') or _safe_get(info, 'trailingPE'),
            'roe': roe,
            'dividend_yield': dividend_yield,
            'bps': bps,
            'equity_ratio': equity_ratio,
            'forward_eps': forward_eps,
            'market_cap': market_cap_oku,
            'week52_low': _safe_get(info, 'fiftyTwoWeekLow'),
            'week52_high': _safe_get(info, 'fiftyTwoWeekHigh'),
            'net_assets': net_assets_oku,
            'annual_dividend': annual_dividend,
        }

        # キャッシュに保存
        save_stock_cache(ticker_code, stock_data)

        # EPS履歴を記録（前回と異なる場合）
        if forward_eps is not None:
            prev_eps = get_latest_eps(ticker_code)
            if prev_eps != forward_eps:
                save_eps_history(ticker_code, forward_eps)

        return stock_data

    except Exception as e:
        print(f"エラー: {ticker_code} のデータ取得に失敗しました: {e}")
        traceback.print_exc()
        return None


def _get_stock_name_from_list(ticker_code):
    """
    銘柄コードからTOPIXリストで日本語名を検索する
    Args:
        ticker_code: 銘柄コード
    Returns:
        銘柄名またはNone
    """
    for code, name in ALL_TOPIX_STOCKS:
        if code == ticker_code:
            return name
    return None


def get_historical_prices(ticker_code, period='5y', interval='1wk'):
    """
    過去の価格データを取得する（週足）
    Args:
        ticker_code: 銘柄コード
        period: 取得期間（'5y', '10y', '1y'等）
        interval: 間隔（'1wk', '1d', '1mo'）
    Returns:
        pandas DataFrameまたはNone
    """
    yf_ticker = _to_yfinance_ticker(ticker_code)

    try:
        ticker = yf.Ticker(yf_ticker)
        hist = ticker.history(period=period, interval=interval)

        if hist.empty:
            print(f"警告: {ticker_code} の過去データが取得できませんでした")
            return None

        return hist

    except Exception as e:
        print(f"エラー: {ticker_code} の過去データ取得に失敗しました: {e}")
        return None


def calculate_support_resistance(prices_df):
    """
    サポート/レジスタンス水準を計算する
    過去の価格データから、頻繁に反発した水準を特定する

    アルゴリズム:
    1. 価格を一定のビン（範囲）に分割
    2. 各ビンでの出来高や反発回数を集計
    3. 最も頻繁に反発した価格帯をサポート/レジスタンスとする

    Args:
        prices_df: yfinanceから取得した価格DataFrame
    Returns:
        support_resistance辞書 {support1, support2, resistance1}
    """
    if prices_df is None or prices_df.empty:
        return {'support1': None, 'support2': None, 'resistance1': None}

    try:
        close_prices = prices_df['Close'].dropna().values
        low_prices = prices_df['Low'].dropna().values
        high_prices = prices_df['High'].dropna().values

        # 価格範囲を設定
        price_min = np.min(low_prices)
        price_max = np.max(high_prices)
        price_range = price_max - price_min

        if price_range == 0:
            return {'support1': None, 'support2': None, 'resistance1': None}

        # 価格を20のビンに分割してヒストグラム作成
        n_bins = 20
        bin_edges = np.linspace(price_min, price_max, n_bins + 1)
        bin_width = (price_max - price_min) / n_bins

        # 各価格帯でのローソク足の数をカウント（価格が滞留した期間）
        price_counts = np.zeros(n_bins)
        for price in close_prices:
            bin_idx = min(int((price - price_min) / bin_width), n_bins - 1)
            price_counts[bin_idx] += 1

        # 下位30%の価格帯 → サポートゾーン候補
        lower_third = int(n_bins * 0.3)
        support_zone = price_counts[:lower_third]

        # 上位30%の価格帯 → レジスタンスゾーン候補
        upper_third = int(n_bins * 0.7)
        resistance_zone = price_counts[upper_third:]

        # サポート1: 下位30%で最も頻繁に滞留した価格帯の中心
        if len(support_zone) > 0 and np.max(support_zone) > 0:
            s1_bin = np.argmax(support_zone)
            support1 = bin_edges[s1_bin] + bin_width / 2
        else:
            # データが少ない場合は52週安値を使用
            support1 = np.percentile(low_prices, 20)

        # サポート2: サポート1より低い価格帯で2番目に多い滞留（大底）
        support2 = price_min + bin_width  # デフォルトは最安値付近

        # レジスタンス1: 上位30%で最も頻繁に滞留した価格帯
        if len(resistance_zone) > 0 and np.max(resistance_zone) > 0:
            r1_bin = np.argmax(resistance_zone) + upper_third
            resistance1 = bin_edges[r1_bin] + bin_width / 2
        else:
            resistance1 = np.percentile(high_prices, 80)

        return {
            'support1': round(support1, 0),
            'support2': round(support2, 0),
            'resistance1': round(resistance1, 0)
        }

    except Exception as e:
        print(f"サポート/レジスタンス計算エラー: {e}")
        return {'support1': None, 'support2': None, 'resistance1': None}


def get_all_topix_data(include_large70=True):
    """
    全TOPIX Core30（+Large70）銘柄のデータを一括取得する
    バッチ処理でAPIコールを最小化する
    Args:
        include_large70: Large70銘柄も含めるか
    Returns:
        銘柄コードをキー、データ辞書を値とする辞書
    """
    # 対象銘柄リストを決定
    if include_large70:
        target_stocks = ALL_TOPIX_STOCKS
    else:
        target_stocks = TOPIX_CORE30

    results = {}
    failed = []

    print(f"スクリーニング対象: {len(target_stocks)}銘柄")

    for i, (code, name) in enumerate(target_stocks):
        try:
            print(f"取得中 ({i+1}/{len(target_stocks)}): {code} {name}")
            stock_info = get_stock_info(code)

            if stock_info:
                results[code] = stock_info
            else:
                failed.append((code, name))

            # API制限対策として少し待機（キャッシュがある場合は不要）
            if i % 10 == 9:  # 10件ごとに1秒待機
                time.sleep(1)

        except Exception as e:
            print(f"エラー: {code} {name}: {e}")
            failed.append((code, name))

    if failed:
        print(f"取得失敗: {len(failed)}銘柄: {[f[0] for f in failed]}")

    print(f"データ取得完了: {len(results)}銘柄")
    return results


def check_eps_revision(ticker_code, current_eps):
    """
    EPS改訂率をチェックする
    前回記録されたEPSと比較して改訂率を計算する

    Args:
        ticker_code: 銘柄コード
        current_eps: 現在の予想EPS
    Returns:
        改訂率（%）またはNone（前回データなし）
        正の値 → 上方修正、負の値 → 下方修正
    """
    if current_eps is None:
        return None

    # 1つ前のEPS記録を取得
    prev_eps = get_latest_eps(ticker_code, exclude_latest=True)

    if prev_eps is None or prev_eps == 0:
        return None

    # 改訂率計算（%）
    revision_rate = ((current_eps - prev_eps) / abs(prev_eps)) * 100
    return round(revision_rate, 2)


def get_nikkei_average_info():
    """
    日経平均株価のPBRを取得する
    ^N225（日経平均）のデータからPBRを推定する
    Returns:
        日経平均PBR（取得できない場合はNone）
    """
    try:
        nikkei = yf.Ticker("^N225")
        info = nikkei.info
        # 日経平均のPBRはyfinanceから直接取得できないため
        # 設定値を使用する（settings.pyのnikkei_pbrを参照）
        return _safe_get(info, 'priceToBook')
    except Exception as e:
        print(f"日経平均PBR取得エラー: {e}")
        return None


def get_stock_volume_data(ticker_code, period='3mo'):
    """
    銘柄の売買代金データを取得する
    日平均売買代金の計算に使用する
    Args:
        ticker_code: 銘柄コード
        period: 取得期間
    Returns:
        日平均売買代金（億円）またはNone
    """
    yf_ticker = _to_yfinance_ticker(ticker_code)

    try:
        ticker = yf.Ticker(yf_ticker)
        hist = ticker.history(period=period, interval='1d')

        if hist.empty:
            return None

        # 売買代金 = 終値 × 出来高
        hist['turnover'] = hist['Close'] * hist['Volume']
        avg_daily_turnover = hist['turnover'].mean()

        # 億円に変換
        return avg_daily_turnover / 1e8

    except Exception as e:
        print(f"エラー: {ticker_code} の売買代金取得に失敗: {e}")
        return None
