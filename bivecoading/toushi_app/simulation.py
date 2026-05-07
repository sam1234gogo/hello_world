"""
simulation.py - 歴史的シミュレーションモジュール

特定の過去時点における推奨銘柄の表示と、
ロジックに基づく売買シミュレーションを行う
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import threading as _threading
from stock_data import ALL_TOPIX_STOCKS, TOPIX_QUICK15, _to_yfinance_ticker
from database import (get_fundamentals_cache, save_fundamentals_cache,
                      get_balance_sheet_cache, save_balance_sheet_cache)
from investment_logic import (
    calculate_short_term_score,
    classify_short_term,
    check_international_excellent,
    check_financial_excellent,
    check_medium_term_buy,
    calculate_long_term_buy_price,
)


# =============================================================================
# 過去時点の株価取得
# =============================================================================

def get_price_at_date(ticker_code, target_date):
    """
    指定日の終値を取得する
    指定日が休場の場合は直後の営業日を使用

    Args:
        ticker_code: 銘柄コード（4桁）
        target_date: 取得したい日付（date型）
    Returns:
        (float価格, 実際の取得日) のタプル、取得失敗時は (None, None)
    """
    yf_ticker = _to_yfinance_ticker(ticker_code)
    try:
        t = yf.Ticker(yf_ticker)
        # 指定日から1週間分取得し最初のデータを使う
        end = target_date + timedelta(days=7)
        hist = t.history(start=target_date.strftime('%Y-%m-%d'),
                         end=end.strftime('%Y-%m-%d'))
        if hist.empty:
            return None, None
        actual_date = hist.index[0].date()
        price = float(hist['Close'].iloc[0])
        return price, actual_date
    except Exception as e:
        print(f"株価取得エラー {ticker_code}: {e}")
        return None, None


# =============================================================================
# 過去時点の財務指標を推定
# =============================================================================

def get_historical_fundamentals(ticker_code, target_date, settings=None):
    """
    指定日時点の財務データを高速取得する。

    【高速化の仕組み】
    1. t.history(1年分) → 株価・52週レンジ・配当履歴を一括取得
    2. t.info → BPS/EPS/ROE/配当利回り等を一括取得
    合計2回のAPI呼び出しで完結（旧実装は7〜9回）。

    Args:
        ticker_code: 銘柄コード
        target_date: 基準日（date型）
    Returns:
        財務指標の辞書、取得失敗時は None
    """
    yf_ticker = _to_yfinance_ticker(ticker_code)
    try:
        t = yf.Ticker(yf_ticker)

        # === API呼び出し1: 過去1年+7日分の日足データ（株価・配当含む）===
        one_year_ago = target_date - timedelta(days=365)
        end_fetch    = target_date + timedelta(days=7)
        hist = t.history(
            start=one_year_ago.strftime('%Y-%m-%d'),
            end=end_fetch.strftime('%Y-%m-%d'),
        )
        if hist.empty:
            return None

        # インデックスを date リストに変換（timezone 非依存）
        hist_dates = [d.date() for d in hist.index]

        # 指定日以前の最後の終値 = 指定日時点の株価
        before_idx = [i for i, d in enumerate(hist_dates) if d <= target_date]
        if not before_idx:
            return None
        last_i     = before_idx[-1]
        price      = float(hist['Close'].iloc[last_i])
        actual_date = hist_dates[last_i]

        # 52週高値安値（指定日以前1年分）
        hist_before = hist.iloc[:last_i + 1]
        week52_low   = float(hist_before['Low'].min())
        week52_high  = float(hist_before['High'].max())
        price_vs_52w_low = (price / week52_low) if week52_low > 0 else None

        # 配当（指定日以前1年分の合計）
        annual_dividend = dividend_yield = None
        try:
            if 'Dividends' in hist_before.columns:
                total_div = float(hist_before['Dividends'].sum())
                if total_div > 0:
                    annual_dividend = total_div
                    dividend_yield  = (total_div / price) * 100
        except Exception:
            pass

        # === API呼び出し2: 過去財務諸表（バランスシート・損益計算書）===
        # target_date 以前の最新決算期のデータを使用
        # yfinanceの制約: バランスシートは直近4年分のみ取得可能
        # → 4年超前の target_date は t.info（現在値）にフォールバック
        bs_data = get_balance_sheet_cache(ticker_code)
        if bs_data is None or 'quarterly_ni' not in bs_data:
            bs_data = _fetch_balance_sheet_data(ticker_code)
            if bs_data:
                save_balance_sheet_cache(ticker_code, bs_data)

        eq, ta, net_inc, shares = _get_financials_at_date(bs_data, target_date)

        # バランスシートで target_date をカバーできない場合は t.info で代替
        if eq is None:
            info = get_fundamentals_cache(ticker_code)
            if info is None:
                info = t.info
                save_fundamentals_cache(ticker_code, info)
            bps      = info.get('bookValue')
            eps      = info.get('trailingEps') or info.get('forwardEps')
            roe_raw  = info.get('returnOnEquity')
            roe      = (roe_raw * 100 if abs(roe_raw) <= 1 else roe_raw) if roe_raw else None
            eq_f     = info.get('totalStockholderEquity') or info.get('stockholdersEquity')
            ta_f     = info.get('totalAssets')
            equity_ratio   = ((eq_f / ta_f) * 100) if (eq_f and ta_f and ta_f > 0) else None
            net_assets_oku = (eq_f / 1e8) if eq_f else None
            pbr = (price / bps) if (bps and bps > 0) else None
            per = (price / eps) if (eps and eps > 0) else None
            data_source = 'info_fallback'   # 現在値フォールバック
        else:
            bps = (eq / shares)       if (eq and shares and shares > 0) else None
            eps = (net_inc / shares)  if (net_inc and shares and shares > 0) else None
            roe = (net_inc / eq * 100) if (net_inc and eq and eq > 0) else None
            pbr = (price / bps) if (bps and bps > 0) else None
            per = (price / eps) if (eps and eps > 0) else None
            equity_ratio   = ((eq / ta) * 100) if (eq and ta and ta > 0) else None
            net_assets_oku = (eq / 1e8) if eq else None
            data_source = 'balance_sheet'   # 当時の決算データ

        # 配当データが履歴から取れなかった場合は t.info から代替
        if dividend_yield is None:
            info = get_fundamentals_cache(ticker_code)
            if info is None:
                info = t.info
                save_fundamentals_cache(ticker_code, info)
            dy_raw = info.get('dividendYield')
            if dy_raw is not None:
                dividend_yield = dy_raw if dy_raw >= 0.5 else dy_raw * 100
            ad = info.get('dividendRate') or info.get('lastDividendValue')
            if ad:
                annual_dividend = float(ad)

        # EPS上方修正チェック（設定値の閾値を使用）
        # 1次: 株探スクレイピング（通期予想修正履歴、発表日時点比較）
        # 2次: 年次実績純利益 前期比（スクレイピングデータ不足時のフォールバック）
        eps_threshold = float((settings or {}).get('eps_revision_threshold', 10.0))
        eps_rev_signal, eps_rev_rate = check_eps_revision_from_forecast(
            ticker_code, target_date, threshold=eps_threshold
        )
        if eps_rev_signal is None:
            eps_rev_signal, eps_rev_rate = check_annual_eps_revision(
                bs_data, target_date, threshold=eps_threshold
            )
        if eps_rev_signal is None:
            eps_rev_signal = False

        return {
            'ticker':                ticker_code,
            'price':                 price,
            'actual_date':           str(actual_date),
            'pbr':                   pbr,
            'per':                   per,
            'roe':                   roe,
            'bps':                   bps,
            'eps':                   eps,
            'equity_ratio':          equity_ratio,
            'net_assets':            net_assets_oku,
            'dividend_yield':        dividend_yield,
            'annual_dividend':       annual_dividend,
            'week52_low':            week52_low,
            'week52_high':           week52_high,
            'price_vs_52w_low':      price_vs_52w_low,
            'data_source':           data_source,
            'eps_revision_signal':   eps_rev_signal,
            'eps_revision_rate':     eps_rev_rate,
        }

    except Exception as e:
        print(f"財務データ取得エラー {ticker_code}: {e}")
        return None


def _pick_value(df, col, keys):
    """データフレームから指定キー群のうち最初に見つかった有効な値を返す"""
    for k in keys:
        if k in df.index:
            v = df.loc[k, col]
            if v is not None and not pd.isna(v):
                return float(v)
    return None


# =============================================================================
# 過去財務諸表ヘルパー
# =============================================================================

def _fetch_balance_sheet_data(ticker_code):
    """
    yfinance の balance_sheet / financials から時系列財務データを取得し
    SQLite キャッシュに保存できる形式（dict）に変換して返す。

    返り値:
        {
          "columns":      ["2024-03-31", "2023-03-31", ...],  # 降順
          "equity":       [float|None, ...],
          "total_assets": [float|None, ...],
          "net_income":   [float|None, ...],
          "shares":       float|None   # 発行済株式数（t.info から、近似）
        }
    """
    yf_ticker = _to_yfinance_ticker(ticker_code)
    t = yf.Ticker(yf_ticker)

    def safe_series(df, keys):
        """複数のキー候補から最初に見つかった行を返す"""
        if df is None or df.empty:
            return {}
        for k in keys:
            if k in df.index:
                return df.loc[k]
        return {}

    try:
        bs   = t.balance_sheet
        fin  = t.financials
        info = t.info

        shares = (info.get('sharesOutstanding') or
                  info.get('impliedSharesOutstanding') or
                  info.get('floatShares'))

        # 決算期カラム（Timestamp → 日付文字列）
        def col_dates(df):
            if df is None or df.empty:
                return []
            return [c.strftime('%Y-%m-%d') for c in df.columns]

        bs_dates  = col_dates(bs)
        fin_dates = col_dates(fin)
        all_dates = sorted(set(bs_dates + fin_dates), reverse=True)

        equity_ser      = safe_series(bs, ['Stockholders Equity',
                                           'Total Stockholder Equity',
                                           'Common Stock Equity',
                                           'stockholdersEquity'])
        total_asset_ser = safe_series(bs, ['Total Assets', 'totalAssets'])
        net_income_ser  = safe_series(fin, ['Net Income',
                                            'Net Income Common Stockholders',
                                            'netIncome'])

        def extract_val(ser, date_str):
            if not hasattr(ser, 'index') or ser.empty:
                return None
            for ts in ser.index:
                if hasattr(ts, 'strftime') and ts.strftime('%Y-%m-%d') == date_str:
                    v = ser[ts]
                    return float(v) if v is not None and not pd.isna(v) else None
            return None

        # ── 四半期純利益（前年同期比 EPS シグナル用）──
        quarterly_ni = {}
        try:
            qf = t.quarterly_financials
            if qf is not None and not qf.empty:
                # Net Income 行を探す（銘柄によってキー名が異なる）
                ni_keys_q = ['Net Income', 'Net income', 'NetIncome']
                eps_keys  = ['Basic EPS', 'Diluted EPS']

                ni_q = None
                for k in ni_keys_q:
                    if k in qf.index:
                        ni_q = qf.loc[k]
                        break

                if ni_q is None:
                    # Net Incomeがない場合: EPS × 株数で代替
                    for k in eps_keys:
                        if k in qf.index:
                            eps_q = qf.loc[k]
                            if shares and shares > 0:
                                ni_q = eps_q * shares
                            break

                if ni_q is not None:
                    for ts, v in ni_q.items():
                        if v is not None and not pd.isna(v):
                            quarterly_ni[ts.strftime('%Y-%m-%d')] = float(v)
        except Exception:
            pass

        data = {
            'columns':      all_dates,
            'equity':       [extract_val(equity_ser, d)      for d in all_dates],
            'total_assets': [extract_val(total_asset_ser, d) for d in all_dates],
            'net_income':   [extract_val(net_income_ser, d)  for d in all_dates],
            'shares':       float(shares) if shares else None,
            'quarterly_ni': quarterly_ni,  # {日付文字列: 純利益} 降順
        }
        return data

    except Exception as e:
        print(f"バランスシート取得エラー {ticker_code}: {e}")
        return None


def _get_financials_at_date(bs_data, target_date):
    """
    bs_data から target_date 以前の最新決算期の財務数値を返す。

    返り値: (equity, total_assets, net_income, shares)
    """
    if not bs_data or not bs_data.get('columns'):
        return None, None, None, None

    cols = bs_data['columns']
    shares = bs_data.get('shares')

    # target_date 以前の最新カラムを探す
    chosen_idx = None
    for i, col_str in enumerate(cols):
        try:
            col_date = date.fromisoformat(col_str)
        except ValueError:
            continue
        if col_date <= target_date:
            chosen_idx = i
            break   # cols は降順なので最初にヒットしたものが最新

    if chosen_idx is None:
        return None, None, None, None

    equity       = bs_data['equity'][chosen_idx]
    total_assets = bs_data['total_assets'][chosen_idx]
    net_income   = bs_data['net_income'][chosen_idx]

    return equity, total_assets, net_income, shares


# =============================================================================
# EPS上方修正シグナル（株探スクレイピング + 年次実績フォールバック）
# =============================================================================

def _get_eps_forecast_history(ticker_code):
    """
    通期予想EPS修正履歴を取得する。
    DB キャッシュ（7日）優先、なければ株探をスクレイピングして保存。
    Returns: list of records（空リストの場合もある）
    """
    from database import get_eps_forecast_history, save_eps_forecast_history
    history = get_eps_forecast_history(ticker_code)
    if history is not None:
        return history
    try:
        from eps_scraper import scrape_eps_forecast_history
        records = scrape_eps_forecast_history(ticker_code)
        save_eps_forecast_history(ticker_code, records or [])
        return records or []
    except Exception as e:
        print(f'EPS forecast scrape failed {ticker_code}: {e}')
        return []


def check_eps_revision_from_forecast(ticker_code, target_date, threshold=10.0):
    """
    株探スクレイピングデータから通期予想EPS修正シグナルを検出する。

    target_date 以前の有効な（EPS非None・黒字）エントリを時系列で並べ、
    直近2エントリを比較して +threshold% 以上の上方修正があればシグナル。

    Returns:
        (True/False, rate)  : データあり・判定済み
        (None, None)        : データ不足 → 年次実績比較へフォールバック
    """
    history = _get_eps_forecast_history(ticker_code)
    if not history:
        return None, None

    target_str = target_date.isoformat()
    valid = [
        r for r in history
        if r['announcement_date'] <= target_str
        and r.get('eps') is not None and r['eps'] > 0
    ]
    if len(valid) < 2:
        return None, None

    latest = valid[-1]
    prev   = valid[-2]
    if prev['eps'] <= 0:
        return False, None

    rate       = ((latest['eps'] - prev['eps']) / abs(prev['eps'])) * 100
    has_signal = (latest['eps'] > 0) and (rate >= threshold)
    return has_signal, round(rate, 2)


def check_annual_eps_revision(bs_data, target_date, threshold=10.0):
    """
    年次純利益の前期比 +threshold% チェック（target_date 時点のデータのみ使用）。

    t.financials の年次純利益（bs_data['net_income']）を使用し、
    target_date 以前の最新2決算期を比較する。

    Returns: (has_signal: bool, revision_rate_percent: float|None)
    """
    if not bs_data:
        return False, None

    cols       = bs_data.get('columns', [])
    net_income = bs_data.get('net_income', [])

    valid_ni = []
    for i, col_str in enumerate(cols):
        try:
            col_date = date.fromisoformat(col_str)
        except ValueError:
            continue
        if col_date <= target_date:
            ni = net_income[i] if i < len(net_income) else None
            if ni is not None:
                valid_ni.append(ni)

    if len(valid_ni) < 2:
        return False, None

    latest_ni = valid_ni[0]
    prev_ni   = valid_ni[1]

    if prev_ni <= 0 or latest_ni is None:
        return False, None

    rate       = ((latest_ni - prev_ni) / abs(prev_ni)) * 100
    has_signal = (latest_ni > 0) and (rate >= threshold)
    return has_signal, round(rate, 2)


# =============================================================================
# 特定日時点でのスクリーニング（高速版）
# =============================================================================

def _screen_one_stock(ticker_code, stock_name, target_date, nikkei_pbr,
                      target_yield_min, target_yield_max, settings=None):
    """
    1銘柄を処理してカテゴリ別エントリーを返す。
    t.info は SQLite キャッシュ済みのため t.history の1回のみ API 呼び出し。
    SSE ストリーミングおよび screen_stocks_at_date から呼び出される。
    """
    if settings is None:
        settings = {}
    try:
        info = get_historical_fundamentals(ticker_code, target_date, settings)
        if info is None:
            return None

        price          = info.get('price')
        if not price:
            return None

        pbr            = info.get('pbr')
        per            = info.get('per')
        roe            = info.get('roe')
        dividend_yield = info.get('dividend_yield')
        price_vs_52w   = info.get('price_vs_52w_low')
        forward_eps    = info.get('eps')
        annual_div     = info.get('annual_dividend')

        int_check, _      = check_international_excellent(info, settings)
        market_type       = 'international' if int_check else 'financial'
        market_type_label = '国際優良企業'  if int_check else '財務優良企業'

        score, score_details = calculate_short_term_score(
            pbr, per, roe, dividend_yield, price_vs_52w
        )
        is_profit_increase = (
            (roe            is not None and roe            > 0) or
            (forward_eps    is not None and forward_eps    > 0) or
            (dividend_yield is not None and dividend_yield > 0)
        )
        category = classify_short_term(score, pbr, dividend_yield, is_profit_increase, settings)

        # EPS上方修正シグナル（get_historical_fundamentals で計算済み）
        eps_revision_signal = info.get('eps_revision_signal', False)
        eps_revision_rate   = info.get('eps_revision_rate')

        entry_base = {
            'ticker':              ticker_code,
            'stock_name':          stock_name,
            'price':               price,
            'pbr':                 round(pbr, 2)            if pbr            else None,
            'per':                 round(per, 1)            if per            else None,
            'roe':                 round(roe, 1)            if roe            else None,
            'dividend_yield':      round(dividend_yield, 2) if dividend_yield else None,
            'score':               score,
            'category':            category,
            'market_type':         market_type,
            'market_type_label':   market_type_label,
            'score_details':       {k: {'label': v['label'], 'score': v['score']}
                                    for k, v in score_details.items()} if score_details else {},
            'eps_revision_signal': eps_revision_signal,
            'eps_revision_rate':   eps_revision_rate,
        }

        entries = {'short_anshin': [], 'short_normal': [], 'medium': [], 'long': [],
                   'eps_trigger': []}

        if category == '安心割安株':
            entries['short_anshin'].append(entry_base)
        elif category in ('通常割安株', '成長株'):
            entries['short_normal'].append(entry_base)

        # EPS上方修正シグナルがあれば専用カテゴリにも追加
        if eps_revision_signal:
            et = dict(entry_base)
            entries['eps_trigger'].append(et)

        if pbr:
            eps_pos = (
                (forward_eps    is not None and forward_eps    > 0) or
                (roe            is not None and roe            > 0) or
                (dividend_yield is not None and dividend_yield > 0)
            )
            has_signal, threshold_pbr, _ = check_medium_term_buy(
                pbr, nikkei_pbr, market_type, eps_pos, settings
            )
            if has_signal:
                me = dict(entry_base)
                me['threshold_pbr'] = round(threshold_pbr, 3) if threshold_pbr else None
                entries['medium'].append(me)

        if annual_div:
            max_buy, min_buy = calculate_long_term_buy_price(
                annual_div, target_yield_min, target_yield_max
            )
            if max_buy and price <= max_buy:
                le = dict(entry_base)
                le['annual_dividend'] = round(annual_div, 1)
                le['max_buy_price']   = round(max_buy, 0)
                le['min_buy_price']   = round(min_buy, 0)
                entries['long'].append(le)

        return entries

    except Exception as e:
        print(f"スクリーニングエラー {ticker_code}: {e}")
        return None


def _extract_ticker_hist(hist_all, yf_ticker, single):
    """
    yf.download の結果から1銘柄分の DataFrame を取り出す。
    single=True: ダウンロード対象が1銘柄のみ（MultiIndex なし）
    """
    if single:
        return hist_all
    try:
        # group_by='ticker' → hist_all[ticker_symbol] でアクセス
        th = hist_all[yf_ticker]
        if th is None or th.empty:
            return None
        return th
    except (KeyError, TypeError):
        return None


def screen_stocks_at_date(target_date, nikkei_pbr=1.3, target_yield_min=3.0,
                           target_yield_max=5.0, max_stocks=30, settings=None):
    """
    指定日時点での推奨銘柄をスクリーニングする。

    【高速化の仕組み】
    1. yf.download() で全銘柄の株価・配当を一括取得（1回のHTTPリクエスト）
    2. t.info を全銘柄同時並列取得（max_workers=銘柄数）
    3. 1と2を別スレッドで並行実行 → 合計時間 = max(価格取得, info取得)

    Args:
        target_date: スクリーニング基準日（date型）
        nikkei_pbr: 日経平均PBR
        target_yield_min: 目標配当利回り下限（%）
        target_yield_max: 目標配当利回り上限（%）
        max_stocks: 処理する銘柄数の上限
    Returns:
        スクリーニング結果の辞書
    """
    if max_stocks <= 15:
        target_list = TOPIX_QUICK15
    else:
        target_list = ALL_TOPIX_STOCKS[:max_stocks]

    n = len(target_list)
    codes      = [c for c, _ in target_list]
    names_map  = {c: nm for c, nm in target_list}
    yf_tickers = [_to_yfinance_ticker(c) for c in codes]

    one_year_ago = target_date - timedelta(days=365)
    end_fetch    = target_date + timedelta(days=7)

    print(f"[シミュレーション] {target_date} 時点でのスクリーニング開始（{n}銘柄）")

    # ── 価格データ（共有変数） ──
    price_map = {}   # code → {price, week52_low, week52_high, price_vs_52w_low, annual_dividend, dividend_yield}
    # ── t.info データ（共有変数） ──
    info_map  = {}   # code → info dict

    # ─────────────────────────────────────────
    # THREAD A: yf.download で全銘柄の株価・配当を一括取得
    # ─────────────────────────────────────────
    def download_prices():
        try:
            tickers_str = " ".join(yf_tickers)
            hist_all = yf.download(
                tickers_str,
                start=one_year_ago.strftime('%Y-%m-%d'),
                end=end_fetch.strftime('%Y-%m-%d'),
                auto_adjust=True,
                actions=True,
                group_by='ticker',
                threads=True,
                progress=False,
            )
            if hist_all is None or hist_all.empty:
                return

            single = (n == 1)
            for code, yft in zip(codes, yf_tickers):
                try:
                    th = _extract_ticker_hist(hist_all, yft, single)
                    if th is None or th.empty:
                        continue

                    dates = [d.date() for d in th.index]
                    before_idx = [i for i, d in enumerate(dates) if d <= target_date]
                    if not before_idx:
                        continue

                    last_i      = before_idx[-1]
                    price       = float(th['Close'].iloc[last_i])
                    hist_before = th.iloc[:last_i + 1]

                    week52_low  = float(hist_before['Low'].min())
                    week52_high = float(hist_before['High'].max())
                    pv52 = (price / week52_low) if week52_low > 0 else None

                    annual_div = div_yield = None
                    if 'Dividends' in hist_before.columns:
                        td = float(hist_before['Dividends'].sum())
                        if td > 0:
                            annual_div = td
                            div_yield  = (td / price) * 100

                    price_map[code] = {
                        'price':            price,
                        'actual_date':      str(dates[last_i]),
                        'week52_low':       week52_low,
                        'week52_high':      week52_high,
                        'price_vs_52w_low': pv52,
                        'annual_dividend':  annual_div,
                        'dividend_yield':   div_yield,
                    }
                except Exception as e:
                    print(f"価格抽出エラー {code}: {e}")
        except Exception as e:
            print(f"yf.download エラー: {e}")
            # フォールバック: 個別取得
            for code in codes:
                try:
                    t = yf.Ticker(_to_yfinance_ticker(code))
                    hist = t.history(start=one_year_ago.strftime('%Y-%m-%d'),
                                     end=end_fetch.strftime('%Y-%m-%d'))
                    if hist.empty:
                        continue
                    dates = [d.date() for d in hist.index]
                    before_idx = [i for i, d in enumerate(dates) if d <= target_date]
                    if not before_idx:
                        continue
                    last_i = before_idx[-1]
                    price  = float(hist['Close'].iloc[last_i])
                    hb     = hist.iloc[:last_i + 1]
                    w52l   = float(hb['Low'].min())
                    w52h   = float(hb['High'].max())
                    ad = dy = None
                    if 'Dividends' in hb.columns:
                        td = float(hb['Dividends'].sum())
                        if td > 0:
                            ad = td
                            dy = (td / price) * 100
                    price_map[code] = {
                        'price': price, 'actual_date': str(dates[last_i]),
                        'week52_low': w52l, 'week52_high': w52h,
                        'price_vs_52w_low': (price / w52l) if w52l > 0 else None,
                        'annual_dividend': ad, 'dividend_yield': dy,
                    }
                except Exception:
                    pass

    # ─────────────────────────────────────────
    # THREAD B: t.info を取得（SQLiteキャッシュ優先、24時間有効）
    # ─────────────────────────────────────────
    def download_infos():
        # キャッシュヒット分は即座に設定、ミス分だけ並列フェッチ
        miss_codes = []
        for code in codes:
            cached = get_fundamentals_cache(code)
            if cached is not None:
                info_map[code] = cached
            else:
                miss_codes.append(code)

        if not miss_codes:
            return  # 全銘柄キャッシュヒット

        print(f"[info取得] キャッシュミス {len(miss_codes)}銘柄 → yfinanceから取得")

        def fetch_one(code):
            try:
                info = yf.Ticker(_to_yfinance_ticker(code)).info
                save_fundamentals_cache(code, info)
                return code, info
            except Exception:
                return code, {}

        with ThreadPoolExecutor(max_workers=len(miss_codes)) as ex:
            futs = {ex.submit(fetch_one, code): code for code in miss_codes}
            for fut in as_completed(futs):
                code, info = fut.result()
                info_map[code] = info

    # A と B を並行実行
    ta = _threading.Thread(target=download_prices, daemon=True)
    tb = _threading.Thread(target=download_infos,  daemon=True)
    ta.start(); tb.start()
    ta.join();  tb.join()

    # ─────────────────────────────────────────
    # STEP 3: スクリーニング
    # ─────────────────────────────────────────
    results = {'short_anshin': [], 'short_normal': [], 'medium': [], 'long': []}

    for code in codes:
        if code not in price_map:
            continue

        pd_data  = price_map[code]
        info     = info_map.get(code, {})
        price    = pd_data['price']
        name     = names_map[code]

        # t.info から財務指標
        bps     = info.get('bookValue')
        eps     = info.get('trailingEps') or info.get('forwardEps')
        roe_raw = info.get('returnOnEquity')
        roe     = None
        if roe_raw is not None:
            roe = roe_raw * 100 if abs(roe_raw) <= 1 else roe_raw

        pbr = (price / bps) if (bps and bps > 0) else None
        per = (price / eps) if (eps and eps > 0) else None

        eq = info.get('totalStockholderEquity') or info.get('stockholdersEquity')
        ta_ = info.get('totalAssets')
        equity_ratio   = ((eq / ta_) * 100) if (eq and ta_ and ta_ > 0) else None
        net_assets_oku = (eq / 1e8)          if eq                        else None

        dividend_yield = pd_data.get('dividend_yield')
        annual_div     = pd_data.get('annual_dividend')
        if dividend_yield is None:
            dy_raw = info.get('dividendYield')
            if dy_raw is not None:
                dividend_yield = dy_raw if dy_raw >= 0.5 else dy_raw * 100
            ad = info.get('dividendRate') or info.get('lastDividendValue')
            if ad:
                annual_div = float(ad)

        full_info = {
            **pd_data, 'ticker': code,
            'pbr': pbr, 'per': per, 'roe': roe,
            'bps': bps, 'eps': eps,
            'equity_ratio': equity_ratio,
            'net_assets':   net_assets_oku,
            'dividend_yield':  dividend_yield,
            'annual_dividend': annual_div,
        }

        # 企業種別
        int_check, _ = check_international_excellent(full_info, settings)
        market_type       = 'international' if int_check else 'financial'
        market_type_label = '国際優良企業'  if int_check else '財務優良企業'

        # 短期スコア
        score, score_details = calculate_short_term_score(
            pbr, per, roe, dividend_yield, pd_data.get('price_vs_52w_low')
        )
        is_profit_increase = (
            (roe           is not None and roe           > 0) or
            (eps           is not None and eps           > 0) or
            (dividend_yield is not None and dividend_yield > 0)
        )
        category = classify_short_term(score, pbr, dividend_yield, is_profit_increase, settings)

        entry_base = {
            'ticker':            code,
            'stock_name':        name,
            'price':             price,
            'pbr':               round(pbr, 2)            if pbr            else None,
            'per':               round(per, 1)            if per            else None,
            'roe':               round(roe, 1)            if roe            else None,
            'dividend_yield':    round(dividend_yield, 2) if dividend_yield else None,
            'score':             score,
            'category':          category,
            'market_type':       market_type,
            'market_type_label': market_type_label,
            'score_details': {k: {'label': v['label'], 'score': v['score']}
                              for k, v in score_details.items()} if score_details else {},
        }

        if category == '安心割安株':
            results['short_anshin'].append(entry_base)
        elif category in ('通常割安株', '成長株'):
            results['short_normal'].append(entry_base)

        if pbr:
            eps_pos = (
                (eps           is not None and eps           > 0) or
                (roe           is not None and roe           > 0) or
                (dividend_yield is not None and dividend_yield > 0)
            )
            has_signal, threshold_pbr, _ = check_medium_term_buy(
                pbr, nikkei_pbr, market_type, eps_pos, settings
            )
            if has_signal:
                me = dict(entry_base)
                me['threshold_pbr'] = round(threshold_pbr, 3) if threshold_pbr else None
                results['medium'].append(me)

        if annual_div:
            max_buy, min_buy = calculate_long_term_buy_price(
                annual_div, target_yield_min, target_yield_max
            )
            if max_buy and price <= max_buy:
                le = dict(entry_base)
                le['annual_dividend'] = round(annual_div, 1)
                le['max_buy_price']   = round(max_buy, 0)
                le['min_buy_price']   = round(min_buy, 0)
                results['long'].append(le)

        print(f"[完了] {code} {name}: pbr={pbr}, cat={category}, dy={dividend_yield}")

    results['short_anshin'].sort(key=lambda x: x.get('score', 0), reverse=True)
    results['short_normal'].sort(key=lambda x: x.get('score', 0), reverse=True)

    total = (len(results['short_anshin']) + len(results['short_normal']) +
             len(results['medium']) + len(results['long']))
    print(f"[シミュレーション] スクリーニング完了: {total}銘柄が条件に該当")
    return results


# =============================================================================
# 短期投資のトレードシミュレーション
# =============================================================================

def simulate_short_term(ticker_code, purchase_date, purchase_price, category,
                        shares=100):
    """
    短期投資ロジックに基づくトレードシミュレーション

    購入日から最大180日間（延長含む）の価格推移を追跡し、
    ナンピン・利確・ロスカット・期限切れを再現する

    Args:
        ticker_code: 銘柄コード
        purchase_date: 購入日（date型）
        purchase_price: 購入単価（円）
        category: 区分（安心割安株 / 通常割安株 / 成長株）
        shares: 初回購入株数
    Returns:
        シミュレーション結果の辞書
    """
    yf_ticker = _to_yfinance_ticker(ticker_code)
    t = yf.Ticker(yf_ticker)

    # 購入日から180日分の日足データ取得
    end_date = min(purchase_date + timedelta(days=180), date.today())
    hist = t.history(
        start=purchase_date.strftime('%Y-%m-%d'),
        end=end_date.strftime('%Y-%m-%d')
    )
    if hist.empty:
        return {'error': 'データ取得失敗'}

    # --- 初期設定 ---
    # ナンピン水準（-10%, -15%, -20%）
    nanpin_pcts  = [-0.10, -0.15, -0.20]
    # 区分ごとの買い増し株数倍率
    if category == '安心割安株':
        nanpin_mults = [1, 1, 1]      # 1-1-1-1
    else:
        nanpin_mults = [2, 3, 4]      # 1-2-3-4

    # ポジション管理
    holdings = [{'date': purchase_date, 'price': purchase_price, 'shares': shares}]
    nanpin_done = [False, False, False]

    # 期限（最初は3ヶ月）
    deadline = purchase_date + timedelta(days=90)
    extended = False   # 1回延長済みフラグ

    # 売り目標（初期はナンピンなしの+9%）
    sell_target = purchase_price * 1.09
    loss_cut    = purchase_price * 0.75

    # 取引ログ
    trade_log = [{
        'date': str(purchase_date),
        'action': '購入',
        'price': purchase_price,
        'shares': shares,
        'note': f'初回購入（{category}）',
    }]

    # チャート用データ
    chart_dates  = []
    chart_prices = []
    result = None

    for idx, row in hist.iterrows():
        current_date  = idx.date()
        current_price = float(row['Close'])

        chart_dates.append(str(current_date))
        chart_prices.append(current_price)

        # 平均取得単価・総保有株数を再計算
        avg_price   = (sum(h['price'] * h['shares'] for h in holdings) /
                       sum(h['shares'] for h in holdings))
        total_shares = sum(h['shares'] for h in holdings)

        # --- ナンピン判定 ---
        for i, pct in enumerate(nanpin_pcts):
            if not nanpin_done[i] and current_price <= purchase_price * (1 + pct):
                np_shares = shares * nanpin_mults[i]
                holdings.append({'date': current_date,
                                 'price': current_price,
                                 'shares': np_shares})
                nanpin_done[i] = True
                # ナンピン後は売り目標を平均取得×1.10に更新
                avg_price    = (sum(h['price'] * h['shares'] for h in holdings) /
                                sum(h['shares'] for h in holdings))
                sell_target  = avg_price * 1.10
                total_shares = sum(h['shares'] for h in holdings)
                trade_log.append({
                    'date': str(current_date),
                    'action': f'ナンピン {i+1}回目',
                    'price': current_price,
                    'shares': np_shares,
                    'note': f'{pct*100:.0f}%下落 → 売り目標を平均×1.10に更新',
                })

        # 再計算（ナンピン後）
        avg_price    = (sum(h['price'] * h['shares'] for h in holdings) /
                        sum(h['shares'] for h in holdings))
        total_shares = sum(h['shares'] for h in holdings)

        # --- 利確判定 ---
        if current_price >= sell_target:
            pnl      = (current_price - avg_price) * total_shares
            pnl_rate = (current_price - avg_price) / avg_price * 100
            result = {
                'outcome': '利益確定',
                'outcome_class': 'success',
                'sell_date': str(current_date),
                'sell_price': current_price,
                'avg_buy_price': avg_price,
                'pnl': pnl,
                'pnl_rate': pnl_rate,
                'total_shares': total_shares,
                'days_held': (current_date - purchase_date).days,
                'nanpin_count': sum(nanpin_done),
            }
            trade_log.append({
                'date': str(current_date),
                'action': '売却（利確）',
                'price': current_price,
                'shares': total_shares,
                'note': f'目標達成 +{pnl_rate:.1f}%',
            })
            break

        # --- ロスカット判定 ---
        if current_price <= loss_cut:
            pnl      = (current_price - avg_price) * total_shares
            pnl_rate = (current_price - avg_price) / avg_price * 100
            result = {
                'outcome': 'ロスカット',
                'outcome_class': 'danger',
                'sell_date': str(current_date),
                'sell_price': current_price,
                'avg_buy_price': avg_price,
                'pnl': pnl,
                'pnl_rate': pnl_rate,
                'total_shares': total_shares,
                'days_held': (current_date - purchase_date).days,
                'nanpin_count': sum(nanpin_done),
            }
            trade_log.append({
                'date': str(current_date),
                'action': '売却（ロスカット）',
                'price': current_price,
                'shares': total_shares,
                'note': f'損切り {pnl_rate:.1f}%',
            })
            break

        # --- 期限チェック ---
        if current_date >= deadline and result is None:
            if category == '安心割安株':
                # 安心割安株は中期/長期へ移行
                pnl      = (current_price - avg_price) * total_shares
                pnl_rate = (current_price - avg_price) / avg_price * 100
                result = {
                    'outcome': '中期/長期へ移行',
                    'outcome_class': 'warning',
                    'sell_date': str(current_date),
                    'sell_price': current_price,
                    'avg_buy_price': avg_price,
                    'pnl': pnl,
                    'pnl_rate': pnl_rate,
                    'total_shares': total_shares,
                    'days_held': (current_date - purchase_date).days,
                    'nanpin_count': sum(nanpin_done),
                }
                trade_log.append({
                    'date': str(current_date),
                    'action': '投資種別移行',
                    'price': current_price,
                    'shares': total_shares,
                    'note': '3ヶ月未達のため中期/長期へ自動移行',
                })
                break
            elif not extended:
                # 通常割安株・成長株は1四半期だけ延長
                deadline = deadline + timedelta(days=90)
                extended = True
                trade_log.append({
                    'date': str(current_date),
                    'action': '期限1回延長',
                    'price': current_price,
                    'shares': total_shares,
                    'note': 'もう1四半期（90日）延長',
                })

    # --- 期間終了時点まで売買シグナルが発生しなかった場合 ---
    if result is None:
        last_price   = chart_prices[-1] if chart_prices else purchase_price
        pnl          = (last_price - avg_price) * total_shares
        pnl_rate     = (last_price - avg_price) / avg_price * 100
        result = {
            'outcome': '保有中（未確定）',
            'outcome_class': 'info',
            'sell_date': chart_dates[-1] if chart_dates else str(purchase_date),
            'sell_price': last_price,
            'avg_buy_price': avg_price,
            'pnl': pnl,
            'pnl_rate': pnl_rate,
            'total_shares': total_shares,
            'days_held': len(chart_prices),
            'nanpin_count': sum(nanpin_done),
        }

    # --- チャート用の水平ライン ---
    sell_target_final = avg_price * (1.10 if any(nanpin_done) else 1.09)

    return {
        'ticker': ticker_code,
        'category': category,
        'purchase_date': str(purchase_date),
        'purchase_price': purchase_price,
        'result': result,
        'trade_log': trade_log,
        'chart': {
            'dates':  chart_dates,
            'prices': chart_prices,
            'lines': {
                'sell_target': round(sell_target_final, 0),
                'loss_cut':    round(purchase_price * 0.75, 0),
                'nanpin_10':   round(purchase_price * 0.90, 0),
                'nanpin_15':   round(purchase_price * 0.85, 0),
                'nanpin_20':   round(purchase_price * 0.80, 0),
            },
            'buy_points':  [{'date': h['date'], 'price': h['price'],
                             'shares': h['shares']} for h in holdings],
            'sell_point':  ({'date': result['sell_date'],
                             'price': result['sell_price']}
                            if result['outcome'] not in ('保有中（未確定）',) else None),
        },
    }


# =============================================================================
# 中期投資のトレードシミュレーション
# =============================================================================

def simulate_medium_term(ticker_code, purchase_date, purchase_price, shares=100):
    """
    中期投資ロジックに基づくトレードシミュレーション

    サポートライン・レジスタンスラインを計算し、
    利確・ナンピンの状態遷移を再現する

    Args:
        ticker_code: 銘柄コード
        purchase_date: 購入日（date型）
        purchase_price: 購入単価（円）
        shares: 初回購入株数
    Returns:
        シミュレーション結果の辞書
    """
    from stock_data import calculate_support_resistance
    yf_ticker = _to_yfinance_ticker(ticker_code)
    t = yf.Ticker(yf_ticker)

    # 購入日から最大2年間の日足データを取得
    end_date = min(purchase_date + timedelta(days=730), date.today())
    hist = t.history(
        start=purchase_date.strftime('%Y-%m-%d'),
        end=end_date.strftime('%Y-%m-%d')
    )
    if hist.empty:
        return {'error': 'データ取得失敗'}

    # 過去5年の週足データからサポート/レジスタンスを算出
    five_years_ago = purchase_date - timedelta(days=365 * 5)
    hist_5y = t.history(
        start=five_years_ago.strftime('%Y-%m-%d'),
        end=purchase_date.strftime('%Y-%m-%d'),
        interval='1wk'
    )
    sr = calculate_support_resistance(hist_5y) if not hist_5y.empty else {}

    support1    = sr.get('support1', purchase_price * 0.90)
    support2    = sr.get('support2', purchase_price * 0.80)
    resistance1 = sr.get('resistance1', purchase_price * 1.20)

    holdings   = [{'date': purchase_date, 'price': purchase_price, 'shares': shares}]
    nanpin_done = False
    trade_log  = [{
        'date': str(purchase_date),
        'action': '購入',
        'price': purchase_price,
        'shares': shares,
        'note': f'中期投資 初回購入（Support1: ¥{support1:,.0f}）',
    }]
    chart_dates  = []
    chart_prices = []
    result = None

    for idx, row in hist.iterrows():
        current_date  = idx.date()
        current_price = float(row['Close'])
        chart_dates.append(str(current_date))
        chart_prices.append(current_price)

        avg_price    = (sum(h['price'] * h['shares'] for h in holdings) /
                        sum(h['shares'] for h in holdings))
        total_shares = sum(h['shares'] for h in holdings)

        # ナンピン（Support2を下回ったとき）
        if not nanpin_done and current_price <= support2:
            holdings.append({'date': current_date, 'price': current_price, 'shares': shares})
            nanpin_done = True
            avg_price    = (sum(h['price'] * h['shares'] for h in holdings) /
                            sum(h['shares'] for h in holdings))
            total_shares = sum(h['shares'] for h in holdings)
            # ナンピン後は利確目標を上方修正
            resistance1 = resistance1 * 1.05
            trade_log.append({
                'date': str(current_date),
                'action': 'ナンピン',
                'price': current_price,
                'shares': shares,
                'note': f'Support2（¥{support2:,.0f}）割れ → 利確目標を5%上方修正',
            })

        # 利確（Resistance1以上）
        if current_price >= resistance1:
            pnl      = (current_price - avg_price) * total_shares
            pnl_rate = (current_price - avg_price) / avg_price * 100
            result = {
                'outcome': '利益確定',
                'outcome_class': 'success',
                'sell_date': str(current_date),
                'sell_price': current_price,
                'avg_buy_price': avg_price,
                'pnl': pnl,
                'pnl_rate': pnl_rate,
                'total_shares': total_shares,
                'days_held': (current_date - purchase_date).days,
                'nanpin_count': 1 if nanpin_done else 0,
            }
            trade_log.append({
                'date': str(current_date),
                'action': '売却（利確）',
                'price': current_price,
                'shares': total_shares,
                'note': f'Resistance1到達 +{pnl_rate:.1f}%',
            })
            break

    if result is None:
        last_price   = chart_prices[-1] if chart_prices else purchase_price
        avg_price    = (sum(h['price'] * h['shares'] for h in holdings) /
                        sum(h['shares'] for h in holdings))
        total_shares = sum(h['shares'] for h in holdings)
        pnl          = (last_price - avg_price) * total_shares
        pnl_rate     = (last_price - avg_price) / avg_price * 100
        result = {
            'outcome': '保有中（未確定）',
            'outcome_class': 'info',
            'sell_date': chart_dates[-1] if chart_dates else str(purchase_date),
            'sell_price': last_price,
            'avg_buy_price': avg_price,
            'pnl': pnl,
            'pnl_rate': pnl_rate,
            'total_shares': total_shares,
            'days_held': len(chart_prices),
            'nanpin_count': 1 if nanpin_done else 0,
        }

    return {
        'ticker': ticker_code,
        'category': '中期投資',
        'purchase_date': str(purchase_date),
        'purchase_price': purchase_price,
        'result': result,
        'trade_log': trade_log,
        'chart': {
            'dates':  chart_dates,
            'prices': chart_prices,
            'lines': {
                'support1':    round(support1, 0),
                'support2':    round(support2, 0),
                'resistance1': round(resistance1, 0),
            },
            'buy_points': [{'date': h['date'], 'price': h['price'],
                            'shares': h['shares']} for h in holdings],
            'sell_point': ({'date': result['sell_date'], 'price': result['sell_price']}
                           if result['outcome'] == '利益確定' else None),
        },
    }


# =============================================================================
# 長期投資のトレードシミュレーション
# =============================================================================

def simulate_long_term(ticker_code, purchase_date, purchase_price, annual_dividend,
                       target_yield, shares=100):
    """
    長期投資ロジックに基づくトレードシミュレーション

    配当利回り基準での買い・ナンピン・売りを再現する

    Args:
        ticker_code: 銘柄コード
        purchase_date: 購入日（date型）
        purchase_price: 購入単価（円）
        annual_dividend: 年間配当額（円）
        target_yield: 目標配当利回り（%）
        shares: 初回購入株数
    Returns:
        シミュレーション結果の辞書
    """
    yf_ticker = _to_yfinance_ticker(ticker_code)
    t = yf.Ticker(yf_ticker)

    end_date = min(purchase_date + timedelta(days=365 * 3), date.today())
    hist = t.history(
        start=purchase_date.strftime('%Y-%m-%d'),
        end=end_date.strftime('%Y-%m-%d')
    )
    if hist.empty:
        return {'error': 'データ取得失敗'}

    # 売り目標: 最安値 × (1 + 目標利回り×10)
    # 初期の最安値は購入価格
    lowest_price = purchase_price
    sell_target  = purchase_price * (1 + (target_yield / 100) * 10)

    nanpin1_price = purchase_price * 0.90  # -10%
    nanpin2_price = purchase_price * 0.80  # -20%

    holdings    = [{'date': purchase_date, 'price': purchase_price, 'shares': shares}]
    nanpin_done = [False, False]
    trade_log   = [{
        'date': str(purchase_date),
        'action': '購入',
        'price': purchase_price,
        'shares': shares,
        'note': f'長期投資 初回購入（目標利回り {target_yield}%、売り目標 ¥{sell_target:,.0f}）',
    }]
    chart_dates  = []
    chart_prices = []
    result = None

    for idx, row in hist.iterrows():
        current_date  = idx.date()
        current_price = float(row['Close'])
        chart_dates.append(str(current_date))
        chart_prices.append(current_price)

        # 最安値更新 → 売り目標を再計算
        if current_price < lowest_price:
            lowest_price = current_price
            sell_target  = lowest_price * (1 + (target_yield / 100) * 10)

        avg_price    = (sum(h['price'] * h['shares'] for h in holdings) /
                        sum(h['shares'] for h in holdings))
        total_shares = sum(h['shares'] for h in holdings)

        # ナンピン1回目（-10%）
        if not nanpin_done[0] and current_price <= nanpin1_price:
            holdings.append({'date': current_date, 'price': current_price, 'shares': shares})
            nanpin_done[0] = True
            trade_log.append({
                'date': str(current_date),
                'action': 'ナンピン1回目',
                'price': current_price,
                'shares': shares,
                'note': f'初回から-10%（¥{nanpin1_price:,.0f}）割れ',
            })

        # ナンピン2回目（-20%）
        if not nanpin_done[1] and current_price <= nanpin2_price:
            holdings.append({'date': current_date, 'price': current_price, 'shares': shares})
            nanpin_done[1] = True
            trade_log.append({
                'date': str(current_date),
                'action': 'ナンピン2回目',
                'price': current_price,
                'shares': shares,
                'note': f'初回から-20%（¥{nanpin2_price:,.0f}）割れ',
            })

        avg_price    = (sum(h['price'] * h['shares'] for h in holdings) /
                        sum(h['shares'] for h in holdings))
        total_shares = sum(h['shares'] for h in holdings)

        # 利確判定
        if current_price >= sell_target:
            pnl      = (current_price - avg_price) * total_shares
            pnl_rate = (current_price - avg_price) / avg_price * 100
            result = {
                'outcome': '利益確定',
                'outcome_class': 'success',
                'sell_date': str(current_date),
                'sell_price': current_price,
                'avg_buy_price': avg_price,
                'pnl': pnl,
                'pnl_rate': pnl_rate,
                'total_shares': total_shares,
                'days_held': (current_date - purchase_date).days,
                'nanpin_count': sum(nanpin_done),
            }
            trade_log.append({
                'date': str(current_date),
                'action': '売却（利確）',
                'price': current_price,
                'shares': total_shares,
                'note': f'売り目標達成（最安値×{(1+(target_yield/100)*10):.2f}倍） +{pnl_rate:.1f}%',
            })
            break

    if result is None:
        last_price   = chart_prices[-1] if chart_prices else purchase_price
        avg_price    = (sum(h['price'] * h['shares'] for h in holdings) /
                        sum(h['shares'] for h in holdings))
        total_shares = sum(h['shares'] for h in holdings)
        pnl          = (last_price - avg_price) * total_shares
        pnl_rate     = (last_price - avg_price) / avg_price * 100
        result = {
            'outcome': '保有中（未確定）',
            'outcome_class': 'info',
            'sell_date': chart_dates[-1] if chart_dates else str(purchase_date),
            'sell_price': last_price,
            'avg_buy_price': avg_price,
            'pnl': pnl,
            'pnl_rate': pnl_rate,
            'total_shares': total_shares,
            'days_held': len(chart_prices),
            'nanpin_count': sum(nanpin_done),
        }

    return {
        'ticker': ticker_code,
        'category': '長期投資',
        'purchase_date': str(purchase_date),
        'purchase_price': purchase_price,
        'result': result,
        'trade_log': trade_log,
        'chart': {
            'dates':  chart_dates,
            'prices': chart_prices,
            'lines': {
                'sell_target': round(sell_target, 0),
                'nanpin_10':   round(nanpin1_price, 0),
                'nanpin_20':   round(nanpin2_price, 0),
            },
            'buy_points': [{'date': h['date'], 'price': h['price'],
                            'shares': h['shares']} for h in holdings],
            'sell_point': ({'date': result['sell_date'], 'price': result['sell_price']}
                           if result['outcome'] == '利益確定' else None),
        },
    }
