"""
backtest.py - 短期投資ロジック 勝率バックテスト（10年対応版）

指定期間の月次日付で Quick15 銘柄をスクリーニングし、
推奨銘柄ごとに simulate_short_term を実行して勝率を集計する。

実行方法:
    cd /Users/sam/bivecoading/toushi_app
    python3 backtest.py                          # デフォルト: 過去10年
    python3 backtest.py --start 2020-01-01       # 開始日を指定
    python3 backtest.py --months 24              # 月数を指定
    python3 backtest.py --resume                 # 途中から再開

機能:
    - 途中保存/再開（--resume）: 中断しても続きから実行可能
    - 年度別・カテゴリ別勝率の集計
    - データソース表示（過去決算 vs 現在値フォールバック）
    - CSV出力で詳細分析可能
"""
import sys
import os
import argparse
import csv
import json
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db, get_setting
init_db()

from stock_data import TOPIX_QUICK15
from simulation import _screen_one_stock, simulate_short_term

PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'backtest_progress.json')


# =============================================================================
# 月次日付リスト生成
# =============================================================================
def generate_monthly_dates(start: date, end: date) -> list:
    """start から end まで月次の第1営業日リストを生成"""
    dates = []
    y, m = start.year, start.month
    cutoff = date.today() - timedelta(days=180)  # 180日分のデータが揃う日のみ

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
            m = 1
            y += 1
    return dates


# =============================================================================
# 進捗保存・読み込み
# =============================================================================
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False)


# =============================================================================
# 1日付のスクリーニング
# =============================================================================
def screen_one_date(target_date, nikkei_pbr, yield_min, yield_max):
    recommended = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            ex.submit(_screen_one_stock, code, name, target_date,
                      nikkei_pbr, yield_min, yield_max): (code, name)
            for code, name in TOPIX_QUICK15
        }
        for fut in as_completed(futures):
            code, name = futures[fut]
            try:
                entries = fut.result()
                if not entries:
                    continue
                for cat in ('short_anshin', 'short_normal'):
                    for e in entries.get(cat, []):
                        recommended.append({
                            'screen_date':    str(target_date),
                            'ticker':         code,
                            'stock_name':     name,
                            'category':       '安心割安株' if cat == 'short_anshin' else e.get('category', '通常割安株'),
                            'price':          e.get('price'),
                            'score':          e.get('score'),
                            'pbr':            e.get('pbr'),
                            'per':            e.get('per'),
                            'roe':            e.get('roe'),
                            'dividend_yield': e.get('dividend_yield'),
                            'data_source':    e.get('data_source', 'unknown'),
                        })
            except Exception as exc:
                pass  # 個別エラーは無視して継続
    return recommended


# =============================================================================
# 1銘柄シミュレーション
# =============================================================================
def run_one_sim(rec, max_retries=2):
    purchase_price = rec.get('price')
    if not purchase_price or purchase_price <= 0:
        return None

    screen_date = date.fromisoformat(rec['screen_date'])

    for attempt in range(max_retries + 1):
        try:
            sim = simulate_short_term(
                ticker_code    = rec['ticker'],
                purchase_date  = screen_date,
                purchase_price = float(purchase_price),
                category       = rec['category'],
                shares         = 100,
            )
            result = sim.get('result', {})
            return {
                'screen_date':    rec['screen_date'],
                'year':           screen_date.year,
                'ticker':         rec['ticker'],
                'stock_name':     rec['stock_name'],
                'category':       rec['category'],
                'score':          rec.get('score'),
                'pbr':            rec.get('pbr'),
                'per':            rec.get('per'),
                'roe':            rec.get('roe'),
                'dividend_yield': rec.get('dividend_yield'),
                'data_source':    rec.get('data_source', 'unknown'),
                'purchase_price': round(purchase_price, 0),
                'outcome':        result.get('outcome', '不明'),
                'pnl':            round(result.get('pnl', 0), 0),
                'pnl_rate':       round(result.get('pnl_rate', 0), 2),
                'sell_price':     result.get('sell_price'),
                'days_held':      result.get('days_held'),
                'nanpin_count':   result.get('nanpin_count', 0),
            }
        except Exception as exc:
            if attempt < max_retries:
                time.sleep(1)
            else:
                return None


# =============================================================================
# 統計集計
# =============================================================================
def summarize(rows, label='全期間'):
    if not rows:
        return None

    wins   = [r for r in rows if r['outcome'] == '利益確定']
    losses = [r for r in rows if r['outcome'] == 'ロスカット']
    medium = [r for r in rows if '中期' in r['outcome'] or '長期' in r['outcome']]
    hold   = [r for r in rows if '保有中' in r['outcome']]
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
    }


def print_stats_row(s, indent=''):
    if s is None:
        return
    wr  = f"{s['win_rate']}%" if s['win_rate'] is not None else 'N/A'
    awr = f"+{s['avg_win_rate']}%" if s['avg_win_rate'] is not None else 'N/A'
    alr = f"{s['avg_loss_rate']}%" if s['avg_loss_rate'] is not None else 'N/A'
    ad  = f"{s['avg_days']}日" if s['avg_days'] is not None else 'N/A'
    print(f"{indent}{s['label']:12s}  "
          f"計{s['total']:4d}件  勝{s['wins']:4d}  負{s['losses']:3d}  "
          f"移行{s['medium_long']:3d}  保有{s['hold']:3d}  "
          f"勝率={wr:>7s}  勝avg={awr:>8s}  負avg={alr:>8s}  保有日={ad}")


def print_full_summary(rows):
    print("\n" + "="*100)
    print("  バックテスト 集計結果")
    print("="*100)
    print(f"  {'期間/区分':<12}  {'計':>5}  {'勝':>5}  {'負':>4}  {'移行':>5}  {'保有':>5}  "
          f"{'勝率':>8}  {'勝avg':>9}  {'負avg':>9}  {'保有日':>7}")
    print("-"*100)

    # 全期間
    total_stats = summarize(rows, '全期間')
    print_stats_row(total_stats)
    print("-"*100)

    # 年度別
    years = sorted(set(r['year'] for r in rows))
    for y in years:
        yr = [r for r in rows if r['year'] == y]
        print_stats_row(summarize(yr, str(y)+'年'), indent='  ')

    print("-"*100)

    # カテゴリ別
    for cat in ('安心割安株', '通常割安株', '成長株'):
        cat_rows = [r for r in rows if r['category'] == cat]
        if cat_rows:
            print_stats_row(summarize(cat_rows, cat))

    print("="*100)

    # データソース内訳
    bs_rows   = [r for r in rows if r.get('data_source') == 'balance_sheet']
    info_rows = [r for r in rows if r.get('data_source') == 'info_fallback']
    print(f"\n  データソース内訳:")
    print(f"    過去決算データ（高精度）: {len(bs_rows)}件")
    print(f"    現在値フォールバック（近似・yfinance制約による）: {len(info_rows)}件")
    print()


# =============================================================================
# メイン
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description='短期投資ロジック バックテスト（10年対応）')
    parser.add_argument('--start',  type=str, default=None,
                        help='開始日 YYYY-MM-DD（デフォルト: 10年前）')
    parser.add_argument('--end',    type=str, default=None,
                        help='終了日 YYYY-MM-DD（デフォルト: 今日から180日前）')
    parser.add_argument('--months', type=int, default=None,
                        help='月数（--start から何ヶ月か。--end と排他）')
    parser.add_argument('--resume', action='store_true',
                        help='前回の途中保存から再開する')
    parser.add_argument('--csv',    type=str, default='backtest_results_10y.csv',
                        help='CSV出力ファイル名')
    args = parser.parse_args()

    # 期間設定
    cutoff = date.today() - timedelta(days=180)
    if args.start:
        start_date = date.fromisoformat(args.start)
    else:
        start_date = date(cutoff.year - 10, cutoff.month, 1)

    if args.end:
        end_date = date.fromisoformat(args.end)
    elif args.months:
        from dateutil.relativedelta import relativedelta
        end_date = start_date + relativedelta(months=args.months)
    else:
        end_date = cutoff

    dates = generate_monthly_dates(start_date, end_date)
    if not dates:
        print("有効な日付がありません（データ取得可能期間外）")
        sys.exit(1)

    nikkei_pbr = float(get_setting('nikkei_pbr') or 1.3)
    yield_min  = float(get_setting('target_yield_min') or 3.0)
    yield_max  = float(get_setting('target_yield_max') or 5.0)

    print(f"\n{'='*70}")
    print(f"  バックテスト開始")
    print(f"  期間: {dates[0]} 〜 {dates[-1]}  ({len(dates)}ヶ月)")
    print(f"  銘柄: Quick15（{len(TOPIX_QUICK15)}銘柄）")
    print(f"  日経PBR={nikkei_pbr}  目標利回り={yield_min}〜{yield_max}%")
    print(f"  注意: yfinanceの制約によりバランスシートは直近4年分のみ")
    print(f"        → 2021年以前のデータは現在の財務指標値で近似")
    print(f"{'='*70}\n")

    # 進捗読み込み
    progress = load_progress() if args.resume else {}
    already_done = set(progress.keys())

    all_sim_results = []
    # 再開時は既存結果を読み込む
    for date_str, month_results in progress.items():
        all_sim_results.extend(month_results)

    # ── スクリーニングフェーズ ──
    all_recommended = []
    dates_to_process = [d for d in dates if str(d) not in already_done]

    print(f"スクリーニング: {len(dates_to_process)}ヶ月分"
          + (f"（{len(already_done)}ヶ月は保存済みスキップ）" if already_done else ""))

    for i, d in enumerate(dates_to_process, 1):
        print(f"  [{i:03d}/{len(dates_to_process)}] {d} スクリーニング中...", end=' ', flush=True)
        recs = screen_one_date(d, nikkei_pbr, yield_min, yield_max)
        print(f"推奨: {len(recs)}銘柄", flush=True)
        all_recommended.extend(recs)

    if not all_recommended and not all_sim_results:
        print("推奨銘柄なし（スクリーニング結果ゼロ）")
        sys.exit(0)

    print(f"\n新規: {len(all_recommended)}件 → シミュレーション実行中...\n")

    # ── シミュレーションフェーズ ──
    # 月ごとにグルーピングして順番に処理（月単位で途中保存）
    from collections import defaultdict
    monthly_recs = defaultdict(list)
    for rec in all_recommended:
        monthly_recs[rec['screen_date']].append(rec)

    month_keys = sorted(monthly_recs.keys())
    total_new  = len(all_recommended)
    done_count = 0

    for month_key in month_keys:
        month_recs   = monthly_recs[month_key]
        month_results = []

        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(run_one_sim, rec): rec for rec in month_recs}
            for fut in as_completed(futures):
                done_count += 1
                result = fut.result()
                if result:
                    month_results.append(result)
                print(f"  シミュレーション進捗: {done_count:4d}/{total_new}", end='\r', flush=True)

        # 月単位で保存
        all_sim_results.extend(month_results)
        progress[month_key] = month_results
        save_progress(progress)

    print(f"\n\nシミュレーション完了: {len(all_sim_results)}件\n")

    # ── 集計・表示 ──
    print_full_summary(all_sim_results)

    # ── CSV保存 ──
    if all_sim_results:
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.csv)
        fieldnames = ['screen_date', 'year', 'ticker', 'stock_name', 'category',
                      'score', 'pbr', 'per', 'roe', 'dividend_yield', 'data_source',
                      'purchase_price', 'outcome', 'pnl', 'pnl_rate',
                      'sell_price', 'days_held', 'nanpin_count']
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in sorted(all_sim_results, key=lambda x: x['screen_date']):
                writer.writerow({k: row.get(k, '') for k in fieldnames})
        print(f"詳細CSV: {csv_path}")
        print(f"進捗ファイル（再開用）: {PROGRESS_FILE}")


if __name__ == '__main__':
    main()
