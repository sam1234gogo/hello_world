#!/usr/bin/env python3
"""Comprehensive analysis of 10-year backtest results for all 3 strategies."""

import json
from collections import defaultdict

PROGRESS_FILE = '/Users/sam/bivecoading/toushi_app/backtest_all_progress.json'

with open(PROGRESS_FILE) as f:
    p = json.load(f)

results = p['results']

strategies = ['短期', '中期', '長期']

def analyze(results, label):
    if not results:
        print(f'{label}: No results')
        return

    outcomes = defaultdict(int)
    for r in results:
        outcomes[r['outcome']] += 1

    wins = [r for r in results if r['pnl'] > 0]
    losses = [r for r in results if r['pnl'] < 0]
    breakeven = [r for r in results if r['pnl'] == 0]
    total_pnl = sum(r['pnl'] for r in results)
    avg_pnl = total_pnl / len(results)
    avg_win = sum(r['pnl'] for r in wins) / len(wins) if wins else 0
    avg_loss = sum(r['pnl'] for r in losses) / len(losses) if losses else 0
    avg_days = sum(r['days_held'] for r in results) / len(results)
    avg_pnl_rate = sum(r['pnl_rate'] for r in results) / len(results)
    max_win = max(results, key=lambda r: r['pnl'])
    max_loss = min(results, key=lambda r: r['pnl'])

    print(f'=== {label} ===')
    print(f'  総取引数: {len(results):,}')
    print(f'  勝ち: {len(wins):,} ({len(wins)/len(results)*100:.1f}%)')
    print(f'  負け: {len(losses):,} ({len(losses)/len(results)*100:.1f}%)')
    print(f'  引き分け: {len(breakeven):,}')
    print(f'  勝率: {len(wins)/len(results)*100:.1f}%')
    print(f'  累計損益: {total_pnl:+,.0f}円')
    print(f'  平均損益/取引: {avg_pnl:+,.0f}円')
    print(f'  平均利益率: {avg_pnl_rate:+.2f}%')
    print(f'  平均勝利時: {avg_win:+,.0f}円')
    print(f'  平均損失時: {avg_loss:+,.0f}円')
    if avg_loss != 0:
        print(f'  損益比: {abs(avg_win/avg_loss):.2f}')
    print(f'  平均保有日数: {avg_days:.0f}日')
    print(f'  最大利益: {max_win["pnl"]:+,.0f}円 ({max_win["ticker"]} {max_win["stock_name"]} {max_win["screen_date"]})')
    print(f'  最大損失: {max_loss["pnl"]:+,.0f}円 ({max_loss["ticker"]} {max_loss["stock_name"]} {max_loss["screen_date"]})')
    print(f'  アウトカム内訳:')
    for o, cnt in sorted(outcomes.items(), key=lambda x: -x[1]):
        print(f'    {o}: {cnt} ({cnt/len(results)*100:.1f}%)')

    # Yearly breakdown
    by_year = defaultdict(list)
    for r in results:
        by_year[r['year']].append(r)
    print(f'  年別損益:')
    for yr in sorted(by_year.keys()):
        yr_results = by_year[yr]
        yr_pnl = sum(r['pnl'] for r in yr_results)
        yr_wins = sum(1 for r in yr_results if r['pnl'] > 0)
        yr_wr = yr_wins / len(yr_results) * 100
        print(f'    {yr}: {len(yr_results):3d}件 勝率{yr_wr:.0f}% 損益{yr_pnl:+,.0f}円')
    print()

print('10年バックテスト結果サマリー (2015-2025)')
print('=' * 60)
print()

all_short  = [r for r in results if r['strategy'] == '短期']
all_medium = [r for r in results if r['strategy'] == '中期']
all_long   = [r for r in results if r['strategy'] == '長期']

analyze(all_short, '短期投資')
analyze(all_medium, '中期投資')
analyze(all_long, '長期投資')

# Overall summary
print('=== 総合サマリー ===')
all_pnl = sum(r['pnl'] for r in results)
all_wins = sum(1 for r in results if r['pnl'] > 0)
print(f'  総取引数: {len(results):,}')
print(f'  総勝率: {all_wins/len(results)*100:.1f}%')
print(f'  3戦略合計損益: {all_pnl:+,.0f}円 ({all_pnl/10000:.1f}万円)')
print()
print(f'  短期: {sum(r["pnl"] for r in all_short):+,.0f}円')
print(f'  中期: {sum(r["pnl"] for r in all_medium):+,.0f}円')
print(f'  長期: {sum(r["pnl"] for r in all_long):+,.0f}円')
