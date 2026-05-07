"""
backtest_all.py - 短期・中期・長期 全戦略 10年バックテスト

Quick15 銘柄を対象に、月次スクリーニングで各戦略の候補を特定し、
各戦略のトレードシミュレーションを実行して勝率・損益を集計する。

実行:
    cd /Users/sam/bivecoading/toushi_app
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 backtest_all.py
"""
import sys, os, csv, json, time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db, get_setting, DEFAULT_SETTINGS
init_db()

from stock_data import TOPIX_QUICK15
from simulation import (
    _screen_one_stock,
    simulate_short_term,
    simulate_medium_term,
    simulate_long_term,
)

# ─────────────────────────────────────────
# 設定読み込み
# ─────────────────────────────────────────
settings = {k: get_setting(k) or DEFAULT_SETTINGS.get(k, '') for k in DEFAULT_SETTINGS}
nikkei_pbr       = float(settings.get('nikkei_pbr', 1.3))
target_yield_min = float(settings.get('target_yield_min', 3.0))
target_yield_max = float(settings.get('target_yield_max', 5.0))

# ─────────────────────────────────────────
# 月次日付リスト生成
# ─────────────────────────────────────────
def generate_monthly_dates(start: date, end: date) -> list:
    dates = []
    y, m = start.year, start.month
    cutoff = date.today() - timedelta(days=180)
    while True:
        d = date(y, m, 1)
        if d > end:
            break
        if d.weekday() == 5:
            d += timedelta(days=2)
        elif d.weekday() == 6:
            d += timedelta(days=1)
        if d <= cutoff:
            dates.append(d)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return dates


# ─────────────────────────────────────────
# 1日付 × 全銘柄スクリーニング
# ─────────────────────────────────────────
def screen_one_date(target_date):
    short_recs  = []   # {screen_date, ticker, stock_name, category, price, ...}
    medium_recs = []   # {screen_date, ticker, stock_name, price, threshold_pbr, ...}
    long_recs   = []   # {screen_date, ticker, stock_name, price, annual_div, max_buy, ...}

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            ex.submit(
                _screen_one_stock,
                code, name, target_date,
                nikkei_pbr, target_yield_min, target_yield_max,
                settings
            ): (code, name)
            for code, name in TOPIX_QUICK15
        }
        for fut in as_completed(futures):
            code, name = futures[fut]
            try:
                entries = fut.result()
                if not entries:
                    continue
                # 短期
                for cat in ('short_anshin', 'short_normal'):
                    for e in entries.get(cat, []):
                        short_recs.append({
                            'screen_date': str(target_date),
                            'ticker': code, 'stock_name': name,
                            'category': '安心割安株' if cat == 'short_anshin' else e.get('category', '通常割安株'),
                            'price': e.get('price'),
                            'score': e.get('score'),
                            'pbr':   e.get('pbr'),
                            'per':   e.get('per'),
                            'roe':   e.get('roe'),
                            'dividend_yield': e.get('dividend_yield'),
                        })
                # 中期
                for e in entries.get('medium', []):
                    medium_recs.append({
                        'screen_date':   str(target_date),
                        'ticker':        code, 'stock_name': name,
                        'price':         e.get('price'),
                        'pbr':           e.get('pbr'),
                        'threshold_pbr': e.get('threshold_pbr'),
                    })
                # 長期
                for e in entries.get('long', []):
                    medium_recs.append({        # 長期は medium_recs と別に
                        'screen_date':   str(target_date),
                        'ticker':        code, 'stock_name': name,
                        'price':         e.get('price'),
                        'annual_div':    e.get('annual_dividend'),
                        'max_buy_price': e.get('max_buy_price'),
                        'min_buy_price': e.get('min_buy_price'),
                    })
                    long_recs.append({
                        'screen_date':   str(target_date),
                        'ticker':        code, 'stock_name': name,
                        'price':         e.get('price'),
                        'annual_div':    e.get('annual_dividend'),
                        'max_buy_price': e.get('max_buy_price'),
                    })
            except Exception:
                pass
    return short_recs, medium_recs, long_recs


# ─────────────────────────────────────────
# シミュレーション実行
# ─────────────────────────────────────────
def run_short_sim(rec):
    price = rec.get('price')
    if not price or price <= 0:
        return None
    d = date.fromisoformat(rec['screen_date'])
    for _ in range(2):
        try:
            sim = simulate_short_term(rec['ticker'], d, float(price), rec['category'], shares=100)
            r = sim.get('result', {})
            return {
                'strategy': '短期',
                'screen_date': rec['screen_date'],
                'year': d.year,
                'ticker': rec['ticker'], 'stock_name': rec['stock_name'],
                'category': rec['category'],
                'score': rec.get('score'), 'pbr': rec.get('pbr'),
                'per': rec.get('per'), 'roe': rec.get('roe'),
                'dividend_yield': rec.get('dividend_yield'),
                'purchase_price': round(price, 0),
                'outcome': r.get('outcome', '不明'),
                'pnl': round(r.get('pnl', 0), 0),
                'pnl_rate': round(r.get('pnl_rate', 0), 2),
                'sell_price': r.get('sell_price'),
                'days_held': r.get('days_held'),
                'nanpin_count': r.get('nanpin_count', 0),
            }
        except Exception:
            time.sleep(1)
    return None


def run_medium_sim(rec):
    price = rec.get('price')
    if not price or price <= 0:
        return None
    d = date.fromisoformat(rec['screen_date'])
    for _ in range(2):
        try:
            sim = simulate_medium_term(rec['ticker'], d, float(price), shares=100)
            r = sim.get('result', {})
            return {
                'strategy': '中期',
                'screen_date': rec['screen_date'],
                'year': d.year,
                'ticker': rec['ticker'], 'stock_name': rec['stock_name'],
                'category': '中期投資',
                'pbr': rec.get('pbr'), 'threshold_pbr': rec.get('threshold_pbr'),
                'purchase_price': round(price, 0),
                'outcome': r.get('outcome', '不明'),
                'pnl': round(r.get('pnl', 0), 0),
                'pnl_rate': round(r.get('pnl_rate', 0), 2),
                'sell_price': r.get('sell_price'),
                'days_held': r.get('days_held'),
                'nanpin_count': r.get('nanpin_count', 0),
            }
        except Exception:
            time.sleep(1)
    return None


def run_long_sim(rec):
    price = rec.get('price')
    annual_div = rec.get('annual_div')
    if not price or price <= 0 or not annual_div or annual_div <= 0:
        return None
    d = date.fromisoformat(rec['screen_date'])
    target_yield = (target_yield_min + target_yield_max) / 2  # 中央値
    for _ in range(2):
        try:
            sim = simulate_long_term(rec['ticker'], d, float(price),
                                     float(annual_div), target_yield, shares=100)
            r = sim.get('result', {})
            return {
                'strategy': '長期',
                'screen_date': rec['screen_date'],
                'year': d.year,
                'ticker': rec['ticker'], 'stock_name': rec['stock_name'],
                'category': '長期投資',
                'annual_div': annual_div,
                'purchase_price': round(price, 0),
                'outcome': r.get('outcome', '不明'),
                'pnl': round(r.get('pnl', 0), 0),
                'pnl_rate': round(r.get('pnl_rate', 0), 2),
                'sell_price': r.get('sell_price'),
                'days_held': r.get('days_held'),
                'nanpin_count': r.get('nanpin_count', 0),
            }
        except Exception:
            time.sleep(1)
    return None


# ─────────────────────────────────────────
# 統計集計
# ─────────────────────────────────────────
def summarize(rows, label=''):
    wins    = [r for r in rows if r['outcome'] == '利益確定']
    losses  = [r for r in rows if r['outcome'] == 'ロスカット']
    medium  = [r for r in rows if '中期' in r['outcome'] or '長期' in r['outcome']]
    hold    = [r for r in rows if '保有中' in r['outcome']]
    decided = wins + losses
    win_rate = len(wins) / len(decided) * 100 if decided else None

    def avg(lst, key):
        vals = [r[key] for r in lst if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        'label':         label,
        'total':         len(rows),
        'wins':          len(wins),
        'losses':        len(losses),
        'medium_long':   len(medium),
        'hold':          len(hold),
        'decided':       len(decided),
        'win_rate':      round(win_rate, 1) if win_rate is not None else None,
        'avg_pnl_rate':  avg(rows, 'pnl_rate'),
        'avg_win_rate':  avg(wins, 'pnl_rate'),
        'avg_loss_rate': avg(losses, 'pnl_rate'),
        'avg_days':      avg(rows, 'days_held'),
        'avg_nanpin':    avg(rows, 'nanpin_count'),
        'total_pnl':     round(sum(r['pnl'] for r in rows)),
    }


def print_row(s, indent=''):
    if not s:
        return
    wr  = f"{s['win_rate']}%" if s['win_rate'] is not None else ' N/A  '
    aw  = f"+{s['avg_win_rate']}%" if s['avg_win_rate'] else '  N/A  '
    al  = f"{s['avg_loss_rate']}%" if s['avg_loss_rate'] else '  N/A  '
    ad  = f"{s['avg_days']}日"    if s['avg_days']      else '  N/A '
    pnl = f"{s['total_pnl']:+,}" if s['total_pnl'] else 'N/A'
    print(
        f"{indent}{s['label']:<14} "
        f"計{s['total']:4d}件 勝{s['wins']:4d} 負{s['losses']:3d} "
        f"移行{s['medium_long']:3d} 保有{s['hold']:3d} | "
        f"勝率{wr:>7} 勝avg{aw:>8} 負avg{al:>8} | "
        f"累積損益{pnl:>12}円"
    )


def print_full_summary(rows):
    print("\n" + "="*130)
    print("  全戦略 バックテスト集計（短期 / 中期 / 長期）")
    print("="*130)

    # 全体
    print_row(summarize(rows, '■ 全戦略合計'))
    print("-"*130)

    # 戦略別
    for strat in ('短期', '中期', '長期'):
        sr = [r for r in rows if r['strategy'] == strat]
        if sr:
            print_row(summarize(sr, f'  [{strat}] 合計'), indent='')
    print("-"*130)

    # 戦略×年度別
    for strat in ('短期', '中期', '長期'):
        sr = [r for r in rows if r['strategy'] == strat]
        if not sr:
            continue
        print(f"\n  ── {strat}投資 年度別 ──")
        for y in sorted(set(r['year'] for r in sr)):
            yr = [r for r in sr if r['year'] == y]
            print_row(summarize(yr, f'    {y}年'), indent='')

    print("-"*130)

    # 短期カテゴリ別
    short_rows = [r for r in rows if r['strategy'] == '短期']
    if short_rows:
        print("\n  ── 短期投資 カテゴリ別 ──")
        for cat in ('安心割安株', '通常割安株', '成長株'):
            cr = [r for r in short_rows if r['category'] == cat]
            if cr:
                print_row(summarize(cr, f'    {cat}'), indent='')

    print("="*130)


# ─────────────────────────────────────────
# メイン
# ─────────────────────────────────────────
def main():
    cutoff     = date.today() - timedelta(days=180)
    start_date = date(cutoff.year - 10, cutoff.month, 1)
    end_date   = cutoff

    dates = generate_monthly_dates(start_date, end_date)
    print(f"\n{'='*70}")
    print(f"  全戦略バックテスト開始")
    print(f"  期間: {dates[0]} 〜 {dates[-1]}  ({len(dates)}ヶ月)")
    print(f"  銘柄: Quick15（{len(TOPIX_QUICK15)}銘柄）")
    print(f"  戦略: 短期 / 中期 / 長期")
    print(f"  日経PBR={nikkei_pbr}  目標利回り={target_yield_min}〜{target_yield_max}%")
    print(f"{'='*70}\n")

    all_short_recs  = []
    all_medium_recs = []
    all_long_recs   = []

    # ── Phase 1: スクリーニング ──
    PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'backtest_all_progress.json')
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            progress = json.load(f)
        done_dates = set(progress.get('done_dates', []))
        all_short_recs  = progress.get('short_recs',  [])
        all_medium_recs = progress.get('medium_recs', [])
        all_long_recs   = progress.get('long_recs',   [])
        all_results     = progress.get('results',     [])
        print(f"[再開] 処理済み: {len(done_dates)}ヶ月, 既存結果: {len(all_results)}件")
    else:
        done_dates  = set()
        all_results = []

    pending_dates = [d for d in dates if str(d) not in done_dates]
    print(f"スクリーニング: {len(pending_dates)}ヶ月分...")

    for i, d in enumerate(pending_dates, 1):
        print(f"  [{i:03d}/{len(pending_dates)}] {d}  スクリーニング中...", end=' ', flush=True)
        try:
            s_recs, m_recs, l_recs = screen_one_date(d)
        except Exception as e:
            print(f"エラー: {e}")
            continue
        print(f"短期:{len(s_recs)} 中期:{len(m_recs)} 長期:{len(l_recs)}")
        all_short_recs.extend(s_recs)
        all_medium_recs.extend(m_recs)
        all_long_recs.extend(l_recs)
        done_dates.add(str(d))

        # 都度保存
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'done_dates':  list(done_dates),
                'short_recs':  all_short_recs,
                'medium_recs': all_medium_recs,
                'long_recs':   all_long_recs,
                'results':     all_results,
            }, f, ensure_ascii=False)

    print(f"\nスクリーニング完了 — 短期:{len(all_short_recs)} 中期:{len(all_medium_recs)} 長期:{len(all_long_recs)}")

    # ── Phase 2: シミュレーション ──
    # 既に処理済みの ticker×screen_date をスキップ
    done_keys = set(
        (r['strategy'], r['screen_date'], r['ticker'])
        for r in all_results
    )

    def pending(recs, strat):
        return [r for r in recs
                if (strat, r['screen_date'], r['ticker']) not in done_keys]

    short_pending  = pending(all_short_recs,  '短期')
    medium_pending = pending(all_medium_recs, '中期')
    long_pending   = pending(all_long_recs,   '長期')

    total_pending = len(short_pending) + len(medium_pending) + len(long_pending)
    print(f"\nシミュレーション: {total_pending}件 実行中 "
          f"(短期:{len(short_pending)} 中期:{len(medium_pending)} 長期:{len(long_pending)})")

    done_count = 0

    def run_batch(recs, run_fn, strat_label):
        nonlocal done_count
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(run_fn, rec): rec for rec in recs}
            for fut in as_completed(futures):
                done_count += 1
                result = fut.result()
                if result:
                    all_results.append(result)
                print(f"  進捗 {done_count:4d}/{total_pending} [{strat_label}]", end='\r', flush=True)
                # 100件ごとに保存
                if done_count % 100 == 0:
                    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                        json.dump({
                            'done_dates': list(done_dates),
                            'short_recs': all_short_recs,
                            'medium_recs': all_medium_recs,
                            'long_recs': all_long_recs,
                            'results': all_results,
                        }, f, ensure_ascii=False)

    run_batch(short_pending,  run_short_sim,  '短期')
    run_batch(medium_pending, run_medium_sim, '中期')
    run_batch(long_pending,   run_long_sim,   '長期')

    print(f"\n\nシミュレーション完了: {len(all_results)}件\n")

    # 最終保存
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'done_dates': list(done_dates),
            'short_recs': all_short_recs,
            'medium_recs': all_medium_recs,
            'long_recs': all_long_recs,
            'results': all_results,
        }, f, ensure_ascii=False)

    # ── 集計・表示 ──
    print_full_summary(all_results)

    # ── CSV出力 ──
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'backtest_all_results.csv')
    fieldnames = ['strategy', 'screen_date', 'year', 'ticker', 'stock_name',
                  'category', 'pbr', 'per', 'roe', 'dividend_yield',
                  'purchase_price', 'outcome', 'pnl', 'pnl_rate',
                  'sell_price', 'days_held', 'nanpin_count']
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in sorted(all_results, key=lambda x: (x['strategy'], x['screen_date'])):
            writer.writerow({k: row.get(k, '') for k in fieldnames})
    print(f"\nCSV出力: {csv_path}")


if __name__ == '__main__':
    main()
