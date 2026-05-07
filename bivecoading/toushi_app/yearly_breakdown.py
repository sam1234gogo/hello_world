#!/usr/bin/env python3
"""Yearly breakdown with investment amounts for all 3 strategies."""

import json
from collections import defaultdict

with open('/Users/sam/bivecoading/toushi_app/backtest_all_progress.json') as f:
    p = json.load(f)

results = p['results']

# Investment amount calculation:
# Short: 100 shares base + 100 per nanpin level
# Medium: 100 shares base + 100 per nanpin count (nanpin is 1 extra lot)
# Long: 100 shares base + 100 per nanpin count (up to 2 extra)
def calc_investment(r):
    pp = r['purchase_price']
    nc = r.get('nanpin_count', 0)
    shares = 100 * (1 + nc)
    return pp * shares

by_year_strategy = defaultdict(lambda: defaultdict(list))
for r in results:
    by_year_strategy[r['year']][r['strategy']].append(r)

years = sorted(by_year_strategy.keys())
strategies = ['短期', '中期', '長期']

# --- Print table ---
header = f"{'年':>4}  {'戦略':>4}  {'件数':>5}  {'勝率':>6}  {'投資額合計':>14}  {'損益合計':>14}  {'ROI':>7}  {'利確':>5}  {'LC':>4}  {'移行/期終':>8}"
print('=' * len(header))
print(header)
print('=' * len(header))

yearly_totals = {}
for yr in years:
    strats = by_year_strategy[yr]
    yr_rows = []
    yr_pnl_total = 0
    yr_inv_total = 0

    for st in strategies:
        recs = strats.get(st, [])
        if not recs:
            continue
        wins = sum(1 for r in recs if r['pnl'] > 0)
        win_rate = wins / len(recs) * 100
        inv = sum(calc_investment(r) for r in recs)
        pnl = sum(r['pnl'] for r in recs)
        roi = pnl / inv * 100 if inv else 0

        # Outcome counts
        lc = sum(1 for r in recs if 'ロスカット' in r['outcome'])
        profit = sum(1 for r in recs if r['outcome'] == '利益確定')
        transfer = sum(1 for r in recs if '移行' in r['outcome'] or '期間終了' in r['outcome'])

        yr_rows.append((st, len(recs), win_rate, inv, pnl, roi, profit, lc, transfer))
        yr_pnl_total += pnl
        yr_inv_total += inv

    yearly_totals[yr] = (yr_pnl_total, yr_inv_total)
    for i, (st, cnt, wr, inv, pnl, roi, profit, lc, trans) in enumerate(yr_rows):
        yr_label = str(yr) if i == 0 else ''
        sign = '+' if pnl >= 0 else ''
        print(f"{yr_label:>4}  {st:>4}  {cnt:>5}  {wr:>5.1f}%  {inv:>13,.0f}  {sign}{pnl:>13,.0f}  {roi:>+6.1f}%  {profit:>5}  {lc:>4}  {trans:>8}")

    # Year subtotal
    yr_roi = yr_pnl_total / yr_inv_total * 100 if yr_inv_total else 0
    yr_sign = '+' if yr_pnl_total >= 0 else ''
    print(f"{'':>4}  {'合計':>4}  {'':>5}  {'':>6}  {yr_inv_total:>13,.0f}  {yr_sign}{yr_pnl_total:>13,.0f}  {yr_roi:>+6.1f}%")
    print('-' * len(header))

# Overall totals per strategy
print()
print('=== 戦略別10年合計 ===')
print(f"{'戦略':>4}  {'件数':>5}  {'勝率':>6}  {'投資額合計':>16}  {'損益合計':>14}  {'ROI':>7}  {'平均損益/件':>12}")
for st in strategies:
    recs = [r for r in results if r['strategy'] == st]
    wins = sum(1 for r in recs if r['pnl'] > 0)
    wr = wins / len(recs) * 100
    inv = sum(calc_investment(r) for r in recs)
    pnl = sum(r['pnl'] for r in recs)
    roi = pnl / inv * 100 if inv else 0
    avg = pnl / len(recs)
    print(f"  {st}  {len(recs):>5}  {wr:>5.1f}%  {inv:>15,.0f}  {pnl:>+14,.0f}  {roi:>+6.2f}%  {avg:>+11,.0f}円")

total_inv = sum(calc_investment(r) for r in results)
total_pnl = sum(r['pnl'] for r in results)
total_wins = sum(1 for r in results if r['pnl'] > 0)
total_roi = total_pnl / total_inv * 100
print(f"\n  合計  {len(results):>5}  {total_wins/len(results)*100:>5.1f}%  {total_inv:>15,.0f}  {total_pnl:>+14,.0f}  {total_roi:>+6.2f}%  {total_pnl/len(results):>+11,.0f}円")
print(f"\n  ※ 投資額は purchase_price × 100株 × (1+ナンピン回数)")
