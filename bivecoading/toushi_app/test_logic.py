"""
test_logic.py - investment_logic.py の仕様書準拠 単体テスト

実行方法:
    cd /Users/sam/bivecoading/toushi_app
    python test_logic.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest
from investment_logic import (
    calculate_short_term_score,
    classify_short_term,
    check_medium_term_buy,
    calculate_long_term_buy_price,
    calculate_sell_target,
    calculate_nanpin_targets,
)


# =============================================================================
# 短期スコアリング: calculate_short_term_score
# =============================================================================
class TestShortTermScore(unittest.TestCase):

    # --- PBR ---
    def test_pbr_le05_score4(self):
        """PBR=0.4倍(≤0.5) → +4点"""
        _, d = calculate_short_term_score(0.4, None, None, None, None)
        self.assertEqual(d['pbr']['score'], 4, "PBR≤0.5 は +4点のはず")

    def test_pbr_le08_score2(self):
        """PBR=0.7倍(0.5<x≤0.8) → +2点"""
        _, d = calculate_short_term_score(0.7, None, None, None, None)
        self.assertEqual(d['pbr']['score'], 2, "PBR≤0.8 は +2点のはず")

    def test_pbr_ge10_scoreMinus3(self):
        """PBR=1.2倍(≥1.0) → -3点"""
        _, d = calculate_short_term_score(1.2, None, None, None, None)
        self.assertEqual(d['pbr']['score'], -3, "PBR≥1.0 は -3点のはず")

    def test_pbr_09_score0(self):
        """PBR=0.9倍(0.8<x<1.0) → 0点（中間）"""
        _, d = calculate_short_term_score(0.9, None, None, None, None)
        self.assertEqual(d['pbr']['score'], 0, "PBR 0.8<x<1.0 は 0点のはず")

    # --- PER ---
    def test_per_le10_score4(self):
        """PER=8倍(≤10) → +4点"""
        _, d = calculate_short_term_score(None, 8, None, None, None)
        self.assertEqual(d['per']['score'], 4, "PER≤10 は +4点のはず")

    def test_per_le15_score2(self):
        """PER=12倍(10<x≤15) → +2点"""
        _, d = calculate_short_term_score(None, 12, None, None, None)
        self.assertEqual(d['per']['score'], 2, "PER≤15 は +2点のはず")

    def test_per_ge20_scoreMinus3(self):
        """PER=25倍(≥20) → -3点"""
        _, d = calculate_short_term_score(None, 25, None, None, None)
        self.assertEqual(d['per']['score'], -3, "PER≥20 は -3点のはず")

    def test_per_16_score0(self):
        """PER=16倍(15<x<20) → 0点（中間）"""
        _, d = calculate_short_term_score(None, 16, None, None, None)
        self.assertEqual(d['per']['score'], 0, "PER 15<x<20 は 0点のはず")

    # --- ROE ---
    def test_roe_ge16_score4(self):
        """ROE=20%(≥16%) → +4点"""
        _, d = calculate_short_term_score(None, None, 20, None, None)
        self.assertEqual(d['roe']['score'], 4, "ROE≥16% は +4点のはず")

    def test_roe_ge8_score2(self):
        """ROE=10%(8%≤x<16%) → +2点"""
        _, d = calculate_short_term_score(None, None, 10, None, None)
        self.assertEqual(d['roe']['score'], 2, "ROE≥8% は +2点のはず")

    def test_roe_le2_scoreMinus3(self):
        """ROE=1%(≤2%) → -3点"""
        _, d = calculate_short_term_score(None, None, 1, None, None)
        self.assertEqual(d['roe']['score'], -3, "ROE≤2% は -3点のはず")

    def test_roe_5_score0(self):
        """ROE=5%(2%<x<8%) → 0点（中間）"""
        _, d = calculate_short_term_score(None, None, 5, None, None)
        self.assertEqual(d['roe']['score'], 0, "ROE 2%<x<8% は 0点のはず")

    # --- 配当利回り ---
    def test_div_ge4_score4(self):
        """配当利回り=5%(≥4%) → +4点"""
        _, d = calculate_short_term_score(None, None, None, 5.0, None)
        self.assertEqual(d['dividend_yield']['score'], 4, "配当≥4% は +4点のはず")

    def test_div_ge3_score2(self):
        """配当利回り=3.5%(3%≤x<4%) → +2点"""
        _, d = calculate_short_term_score(None, None, None, 3.5, None)
        self.assertEqual(d['dividend_yield']['score'], 2, "配当≥3% は +2点のはず")

    def test_div_le15_scoreMinus3(self):
        """配当利回り=1.0%(≤1.5%) → -3点"""
        _, d = calculate_short_term_score(None, None, None, 1.0, None)
        self.assertEqual(d['dividend_yield']['score'], -3, "配当≤1.5% は -3点のはず")

    def test_div_20_score0(self):
        """配当利回り=2.0%(1.5%<x<3%) → 0点（中間）"""
        _, d = calculate_short_term_score(None, None, None, 2.0, None)
        self.assertEqual(d['dividend_yield']['score'], 0, "配当 1.5%<x<3% は 0点のはず")

    # --- 52週安値比 ---
    def test_price52w_le12_score2(self):
        """52週安値比=1.1倍(≤1.2) → +2点"""
        _, d = calculate_short_term_score(None, None, None, None, 1.1)
        self.assertEqual(d['price_vs_52w_low']['score'], 2, "52週安値比≤1.2 は +2点のはず")

    def test_price52w_ge15_scoreMinus3(self):
        """52週安値比=1.6倍(≥1.5) → -3点"""
        _, d = calculate_short_term_score(None, None, None, None, 1.6)
        self.assertEqual(d['price_vs_52w_low']['score'], -3, "52週安値比≥1.5 は -3点のはず")

    def test_price52w_13_score0(self):
        """52週安値比=1.3倍(1.2<x<1.5) → 0点（中間）"""
        _, d = calculate_short_term_score(None, None, None, None, 1.3)
        self.assertEqual(d['price_vs_52w_low']['score'], 0, "52週安値比 1.2<x<1.5 は 0点のはず")

    # --- 合計点 ---
    def test_total_all_max(self):
        """全指標が最高値: PBR=0.4, PER=8, ROE=20%, 配当=5%, 安値比=1.1 → +18点"""
        # PBR:+4, PER:+4, ROE:+4, 配当:+4, 安値比:+2 = +18
        score, _ = calculate_short_term_score(0.4, 8, 20, 5.0, 1.1)
        self.assertEqual(score, 18, f"最大スコアは18点のはず（実際: {score}）")

    def test_total_all_worst(self):
        """全指標が最低値: PBR=1.5, PER=25, ROE=1%, 配当=1%, 安値比=1.6 → -15点"""
        score, _ = calculate_short_term_score(1.5, 25, 1.0, 1.0, 1.6)
        self.assertEqual(score, -15, f"最低スコアは-15点のはず（実際: {score}）")

    def test_none_values_score0(self):
        """全指標がNone → 0点（データなし扱い）"""
        score, _ = calculate_short_term_score(None, None, None, None, None)
        self.assertEqual(score, 0)


# =============================================================================
# 短期区分判定: classify_short_term
# =============================================================================
class TestClassifyShortTerm(unittest.TestCase):

    def test_anshin_by_low_pbr(self):
        """スコア≥2・増益・PBR=0.6(≤0.75) → 安心割安株"""
        cat = classify_short_term(4, 0.6, 2.0, True)
        self.assertEqual(cat, '安心割安株', "PBR≤0.75 かつ増益 → 安心割安株のはず")

    def test_anshin_by_high_dividend(self):
        """スコア≥2・増益・PBR=1.0・配当=3.0%(≥2.4%) → 安心割安株"""
        cat = classify_short_term(4, 1.0, 3.0, True)
        self.assertEqual(cat, '安心割安株', "配当≥2.4% かつ増益 → 安心割安株のはず")

    def test_anshin_both_conditions(self):
        """PBR≤0.75かつ配当≥2.4% → 安心割安株"""
        cat = classify_short_term(4, 0.5, 4.0, True)
        self.assertEqual(cat, '安心割安株')

    def test_normal_undervalue(self):
        """スコア≥2・増益・PBR=0.9(>0.75)・配当=2.0%(<2.4%) → 通常割安株"""
        cat = classify_short_term(4, 0.9, 2.0, True)
        self.assertEqual(cat, '通常割安株', "条件を満たさない場合は通常割安株のはず")

    def test_skip_score_1(self):
        """スコア=1(<2) → スキップ（増益でも）"""
        cat = classify_short_term(1, 0.5, 5.0, True)
        self.assertEqual(cat, 'スキップ', "スコア<2 は スキップのはず")

    def test_skip_no_profit_increase(self):
        """スコア=10・増益でない → スキップ"""
        cat = classify_short_term(10, 0.5, 5.0, False)
        self.assertEqual(cat, 'スキップ', "増益でない場合はスキップのはず")

    def test_growth_score_minus10(self):
        """スコア=-10(≤-10) → 成長株"""
        cat = classify_short_term(-10, None, None, False)
        self.assertEqual(cat, '成長株', "スコア≤-10 は 成長株のはず")

    def test_growth_score_minus12(self):
        """スコア=-12 → 成長株"""
        cat = classify_short_term(-12, 2.0, 0.5, False)
        self.assertEqual(cat, '成長株')

    def test_skip_score_minus5(self):
        """スコア=-5(-10<x<2)・増益でない → スキップ"""
        cat = classify_short_term(-5, 0.8, 3.0, False)
        self.assertEqual(cat, 'スキップ')


# =============================================================================
# 中期買いシグナル: check_medium_term_buy
# =============================================================================
class TestMediumTermBuy(unittest.TestCase):

    def test_international_black_threshold(self):
        """国際優良企業・黒字 → 閾値 = 日経PBR × 0.6"""
        nikkei_pbr = 1.3
        _, thresh, _ = check_medium_term_buy(0.5, nikkei_pbr, 'international', True)
        self.assertAlmostEqual(thresh, nikkei_pbr * 0.6, places=3,
                               msg="国際優良・黒字の閾値は日経PBR×0.6のはず")

    def test_international_black_signal_true(self):
        """PBR < 日経PBR×0.6 → シグナルあり"""
        signal, thresh, _ = check_medium_term_buy(0.7, 1.3, 'international', True)
        # 閾値=0.78, PBR=0.7 < 0.78 → シグナルあり
        self.assertTrue(signal)

    def test_international_black_signal_false(self):
        """PBR > 日経PBR×0.6 → シグナルなし"""
        signal, _, _ = check_medium_term_buy(1.0, 1.3, 'international', True)
        # 閾値=0.78, PBR=1.0 > 0.78 → シグナルなし
        self.assertFalse(signal)

    def test_international_red_threshold(self):
        """国際優良企業・赤字 → 閾値 = 日経PBR × 0.3"""
        nikkei_pbr = 1.3
        _, thresh, _ = check_medium_term_buy(0.1, nikkei_pbr, 'international', False)
        self.assertAlmostEqual(thresh, nikkei_pbr * 0.3, places=3,
                               msg="国際優良・赤字の閾値は日経PBR×0.3のはず")

    def test_financial_black_threshold(self):
        """財務優良企業・黒字 → 閾値 = 日経PBR × 0.5"""
        nikkei_pbr = 1.3
        _, thresh, _ = check_medium_term_buy(0.5, nikkei_pbr, 'financial', True)
        self.assertAlmostEqual(thresh, nikkei_pbr * 0.5, places=3,
                               msg="財務優良・黒字の閾値は日経PBR×0.5のはず")

    def test_financial_red_threshold(self):
        """財務優良企業・赤字 → 閾値 = 日経PBR × 0.25"""
        nikkei_pbr = 1.3
        _, thresh, _ = check_medium_term_buy(0.1, nikkei_pbr, 'financial', False)
        self.assertAlmostEqual(thresh, nikkei_pbr * 0.25, places=3,
                               msg="財務優良・赤字の閾値は日経PBR×0.25のはず")

    def test_none_pbr_no_signal(self):
        """PBR=None → シグナルなし"""
        signal, _, _ = check_medium_term_buy(None, 1.3, 'international', True)
        self.assertFalse(signal)


# =============================================================================
# 長期買い目標株価: calculate_long_term_buy_price
# =============================================================================
class TestLongTermBuyPrice(unittest.TestCase):

    def test_dividend_120_yield_3to5(self):
        """配当120円・利回り3〜5% → 最大4000円・最小2400円"""
        max_p, min_p = calculate_long_term_buy_price(120, 3.0, 5.0)
        self.assertEqual(max_p, 4000.0,
                         "配当120÷0.03=4000円 が最大買い価格のはず")
        self.assertEqual(min_p, 2400.0,
                         "配当120÷0.05=2400円 が最小買い価格のはず")

    def test_dividend_80_yield_35(self):
        """配当80円・利回り3.5% → 最大≒2286円"""
        max_p, _ = calculate_long_term_buy_price(80, 3.5, 5.0)
        self.assertAlmostEqual(max_p, round(80 / 0.035, 0), places=0)

    def test_zero_dividend(self):
        """配当0円 → (None, None)"""
        max_p, min_p = calculate_long_term_buy_price(0, 3.0, 5.0)
        self.assertIsNone(max_p)
        self.assertIsNone(min_p)

    def test_none_dividend(self):
        """配当None → (None, None)"""
        max_p, min_p = calculate_long_term_buy_price(None, 3.0, 5.0)
        self.assertIsNone(max_p)
        self.assertIsNone(min_p)

    def test_max_gt_min(self):
        """最大買い価格 > 最小買い価格（利回り最小→株価最大）"""
        max_p, min_p = calculate_long_term_buy_price(100, 3.0, 5.0)
        self.assertGreater(max_p, min_p,
                           "利回り最小の方が許容最高株価が高くなるはず")


# =============================================================================
# 売り目標価格: calculate_sell_target
# =============================================================================
class TestSellTarget(unittest.TestCase):

    def test_normal_profit_target(self):
        """ナンピンなし → 利確目標 = 買い値 × 1.09"""
        result = calculate_sell_target(1000)
        self.assertEqual(result['profit_target'], 1090.0,
                         "ナンピンなしの利確目標は買い値×1.09のはず")

    def test_nanpin_profit_target(self):
        """ナンピン後 → 利確目標 = 平均価格 × 1.10"""
        result = calculate_sell_target(1000, nanpin_avg_price=900, has_nanpin=True)
        self.assertEqual(result['profit_target'], 990.0,
                         "ナンピン後の利確目標は平均価格×1.10のはず")  # 900×1.10=990

    def test_loss_cut_price(self):
        """ロスカット = 買い値 × 0.75（-25%）"""
        result = calculate_sell_target(1000)
        self.assertEqual(result['loss_cut'], 750.0,
                         "ロスカットは買い値×0.75のはず")

    def test_loss_cut_unchanged_after_nanpin(self):
        """ロスカットはナンピン後も初回購入価格基準"""
        result = calculate_sell_target(2000, nanpin_avg_price=1600, has_nanpin=True)
        self.assertEqual(result['loss_cut'], 1500.0,
                         "ロスカットは初回価格×0.75のはず（平均価格ではない）")


# =============================================================================
# ナンピン目標: calculate_nanpin_targets
# =============================================================================
class TestNanpinTargets(unittest.TestCase):

    def test_anshin_multipliers_all_1(self):
        """安心割安株: 全3水準で ×1（同数量）"""
        targets = calculate_nanpin_targets(1000, '安心割安株')
        self.assertEqual(len(targets), 3)
        for t in targets:
            self.assertEqual(t['multiplier'], 1, "安心割安株はすべて×1のはず")

    def test_normal_multipliers_2_3_4(self):
        """通常割安株: -10%→×2, -15%→×3, -20%→×4"""
        targets = calculate_nanpin_targets(1000, '通常割安株')
        self.assertEqual(targets[0]['multiplier'], 2)
        self.assertEqual(targets[1]['multiplier'], 3)
        self.assertEqual(targets[2]['multiplier'], 4)

    def test_growth_multipliers_same_as_normal(self):
        """成長株: 通常割安株と同じ 2-3-4"""
        targets = calculate_nanpin_targets(1000, '成長株')
        self.assertEqual(targets[0]['multiplier'], 2)
        self.assertEqual(targets[1]['multiplier'], 3)
        self.assertEqual(targets[2]['multiplier'], 4)

    def test_nanpin_price_levels(self):
        """ナンピン水準: 購入価格の -10%, -15%, -20%"""
        targets = calculate_nanpin_targets(1000, '安心割安株')
        self.assertAlmostEqual(targets[0]['price'], 900.0, places=0,
                               msg="-10%水準は900円のはず")
        self.assertAlmostEqual(targets[1]['price'], 850.0, places=0,
                               msg="-15%水準は850円のはず")
        self.assertAlmostEqual(targets[2]['price'], 800.0, places=0,
                               msg="-20%水準は800円のはず")


# =============================================================================
# テスト実行
# =============================================================================
if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    test_classes = [
        TestShortTermScore,
        TestClassifyShortTerm,
        TestMediumTermBuy,
        TestLongTermBuyPrice,
        TestSellTarget,
        TestNanpinTargets,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total  = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print("\n" + "="*60)
    print(f"テスト結果: {passed}/{total} 合格  {'✓ 全テスト通過' if failed==0 else f'✗ {failed}件失敗'}")
    print("="*60)

    sys.exit(0 if failed == 0 else 1)
