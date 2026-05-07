#!/usr/bin/env python3
"""Fill in missing 504 medium-term simulation results using batch yf.download."""

import json
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, '/Users/sam/bivecoading/toushi_app')

import yfinance as yf
import pandas as pd

PROGRESS_FILE = '/Users/sam/bivecoading/toushi_app/backtest_all_progress.json'

def load_progress():
    with open(PROGRESS_FILE) as f:
        return json.load(f)

def save_progress(p):
    with open(PROGRESS_FILE + '.tmp', 'w') as f:
        json.dump(p, f, ensure_ascii=False)
    import os
    os.replace(PROGRESS_FILE + '.tmp', PROGRESS_FILE)

def simulate_medium_simple(rec, price_df):
    """
    Medium-term rules:
    - Buy at signal price (rec['price'])
    - Sell target: +20% profit
    - Nanpin at -15% (add once)
    - Loss cut at -30%
    - Max hold: 2 years (504 trading days)
    Returns result dict.
    """
    screen_date = rec['screen_date']
    ticker = rec['ticker']
    stock_name = rec['stock_name']
    purchase_price = rec['price']
    threshold_pbr = rec.get('threshold_pbr')
    pbr = rec.get('pbr')

    if ticker not in price_df.columns:
        return None

    col = price_df[ticker].dropna()
    if col.empty:
        return None

    # Start from day after screening date
    start = pd.Timestamp(screen_date)
    future = col[col.index > start]
    if future.empty:
        return None

    nanpin_done = False
    nanpin_price = None
    avg_price = purchase_price
    nanpin_count = 0

    # Targets
    profit_target = purchase_price * 1.20
    nanpin_trigger = purchase_price * 0.85   # -15%
    loss_cut_price = purchase_price * 0.70   # -30%
    max_end = start + timedelta(days=730)    # 2 years

    outcome = '保有中（未確定）'
    sell_price = None
    sell_date = None

    for dt, price in future.items():
        if dt > max_end:
            # Time's up
            sell_price = price
            sell_date = dt
            outcome = '期間終了'
            break

        # After nanpin, recalculate targets
        if nanpin_done:
            current_profit_target = avg_price * 1.20
            current_loss_cut = avg_price * 0.70
        else:
            current_profit_target = profit_target
            current_loss_cut = loss_cut_price

        # Sell: profit target
        if price >= current_profit_target:
            sell_price = price
            sell_date = dt
            outcome = '利益確定'
            break

        # Loss cut
        if price <= current_loss_cut:
            sell_price = price
            sell_date = dt
            outcome = 'ロスカット'
            break

        # Nanpin (once only)
        if not nanpin_done and price <= nanpin_trigger:
            nanpin_done = True
            nanpin_price = price
            nanpin_count = 1
            avg_price = (purchase_price + nanpin_price) / 2
            # Recalculate targets with avg_price
            profit_target = avg_price * 1.20
            loss_cut_price = avg_price * 0.70

    if sell_price is None:
        # Use last known price
        if not future.empty:
            sell_price = float(future.iloc[-1])
            sell_date = future.index[-1]
        else:
            sell_price = purchase_price
        outcome = '保有中（未確定）'

    days_held = (sell_date - start).days if sell_date else 0
    # PnL per 100 shares (1単元)
    shares = 100
    pnl = (sell_price - avg_price) * shares
    if nanpin_done:
        # We bought 100 shares initially + 100 nanpin = 200 shares total
        # But original pnl is just on the average
        pnl = (sell_price - avg_price) * shares * 2
    else:
        pnl = (sell_price - purchase_price) * shares
    pnl_rate = (sell_price - avg_price) / avg_price * 100

    return {
        'strategy': '中期',
        'screen_date': screen_date,
        'year': int(screen_date[:4]),
        'ticker': ticker,
        'stock_name': stock_name,
        'category': '中期投資',
        'pbr': pbr,
        'threshold_pbr': threshold_pbr,
        'purchase_price': purchase_price,
        'outcome': outcome,
        'pnl': round(pnl, 1),
        'pnl_rate': round(pnl_rate, 2),
        'sell_price': sell_price,
        'days_held': days_held,
        'nanpin_count': nanpin_count,
    }

def main():
    p = load_progress()
    results = p['results']
    medium_recs = p['medium_recs']

    # Find missing
    done = set((r['screen_date'], r['ticker']) for r in results if r.get('strategy') == '中期')
    missing = [r for r in medium_recs if (r['screen_date'], r['ticker']) not in done]
    print(f'Missing medium sims: {len(missing)}')
    if not missing:
        print('Nothing to do.')
        return

    # Get unique tickers
    tickers = list(set(r['ticker'] for r in missing))
    print(f'Unique tickers: {len(tickers)}')

    # Date range needed: from earliest screen_date - 1 day to latest + 2 years + buffer
    dates = sorted(set(r['screen_date'] for r in missing))
    start_dl = (pd.Timestamp(dates[0]) - timedelta(days=1)).strftime('%Y-%m-%d')
    end_dl   = (pd.Timestamp(dates[-1]) + timedelta(days=800)).strftime('%Y-%m-%d')
    print(f'Download range: {start_dl} to {end_dl}')
    print(f'Tickers: {tickers}')

    # Build Yahoo Finance tickers (add .T for Japanese)
    yf_tickers = [t + '.T' for t in tickers]
    print(f'\nDownloading price data...')
    time.sleep(2)

    raw = yf.download(
        yf_tickers,
        start=start_dl,
        end=end_dl,
        auto_adjust=True,
        progress=True,
    )

    if isinstance(raw.columns, pd.MultiIndex):
        close = raw['Close'].copy()
    else:
        close = raw[['Close']].copy()
        close.columns = [yf_tickers[0]]

    # Rename columns back to code only
    close.columns = [c.replace('.T', '') for c in close.columns]
    print(f'Price data shape: {close.shape}')
    print(f'Tickers with data: {list(close.columns)}')

    new_results = []
    for i, rec in enumerate(missing):
        res = simulate_medium_simple(rec, close)
        if res:
            new_results.append(res)
        if (i + 1) % 50 == 0:
            print(f'  {i+1}/{len(missing)} simulated, {len(new_results)} successful so far')

    print(f'\nNew results: {len(new_results)}')

    # Add to progress
    results.extend(new_results)
    p['results'] = results
    save_progress(p)
    print(f'Saved. Total results: {len(results)}')

    # Quick summary
    outcomes = {}
    for r in new_results:
        o = r['outcome']
        outcomes[o] = outcomes.get(o, 0) + 1
    print('Outcome distribution:', outcomes)
    wins = sum(1 for r in new_results if r['pnl'] > 0)
    print(f'Win rate: {wins}/{len(new_results)} = {wins/len(new_results)*100:.1f}%')
    total_pnl = sum(r['pnl'] for r in new_results)
    print(f'Total PnL: {total_pnl:,.0f}円')

if __name__ == '__main__':
    main()
