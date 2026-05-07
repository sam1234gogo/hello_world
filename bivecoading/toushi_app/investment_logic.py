"""
investment_logic.py - 投資ロジック実装モジュール
短期・中期・長期の投資判断ロジックを実装する
"""

import math
from datetime import datetime, timedelta
from database import get_setting, get_nanpin_history, calculate_average_price


# =============================================================================
# 銘柄フィルタ条件チェック
# =============================================================================

def check_international_excellent(info, settings=None):
    """
    国際優良企業かどうかチェックする
    条件:
    - TOPIX Core30/Large70採用銘柄（別途チェック）
    - 海外売上高比率30%以上（手動設定）
    - 日平均売買代金30億円以上（別途チェック）
    - BPS≥設定値（デフォルト500円）
    - 自己資本比率≥設定値（デフォルト30%）

    Args:
        info: 銘柄情報の辞書
        settings: 設定辞書
    Returns:
        (bool, list): チェック結果と理由のリスト
    """
    if settings is None:
        settings = {}
    bps_min    = float(settings.get('intl_bps_min', 500))
    eq_min     = float(settings.get('intl_equity_ratio_min', 30))

    reasons = []
    passed = True

    bps = info.get('bps')
    if bps is not None:
        if bps >= bps_min:
            reasons.append(f"BPS {bps:.0f}円 ≥ {bps_min:.0f}円 ✓")
        else:
            reasons.append(f"BPS {bps:.0f}円 < {bps_min:.0f}円 ✗")
            passed = False
    else:
        reasons.append("BPS データなし")

    equity_ratio = info.get('equity_ratio')
    if equity_ratio is not None:
        if equity_ratio >= eq_min:
            reasons.append(f"自己資本比率 {equity_ratio:.1f}% ≥ {eq_min:.0f}% ✓")
        else:
            reasons.append(f"自己資本比率 {equity_ratio:.1f}% < {eq_min:.0f}% ✗")
            passed = False
    else:
        reasons.append("自己資本比率 データなし")

    return passed, reasons


def check_financial_excellent(info, settings=None):
    """
    財務優良企業かどうかチェックする
    条件:
    - プライム市場上場（別途チェック）
    - 純資産≥設定値（デフォルト500億円）
    - BPS≥設定値（デフォルト1000円）
    - 自己資本比率≥設定値（デフォルト60%）

    Args:
        info: 銘柄情報の辞書
        settings: 設定辞書
    Returns:
        (bool, list): チェック結果と理由のリスト
    """
    if settings is None:
        settings = {}
    na_min  = float(settings.get('fin_net_assets_min', 500))
    bps_min = float(settings.get('fin_bps_min', 1000))
    eq_min  = float(settings.get('fin_equity_ratio_min', 60))

    reasons = []
    passed = True

    net_assets = info.get('net_assets')
    if net_assets is not None:
        if net_assets >= na_min:
            reasons.append(f"純資産 {net_assets:.0f}億円 ≥ {na_min:.0f}億円 ✓")
        else:
            reasons.append(f"純資産 {net_assets:.0f}億円 < {na_min:.0f}億円 ✗")
            passed = False
    else:
        reasons.append("純資産 データなし")

    bps = info.get('bps')
    if bps is not None:
        if bps >= bps_min:
            reasons.append(f"BPS {bps:.0f}円 ≥ {bps_min:.0f}円 ✓")
        else:
            reasons.append(f"BPS {bps:.0f}円 < {bps_min:.0f}円 ✗")
            passed = False
    else:
        reasons.append("BPS データなし")

    equity_ratio = info.get('equity_ratio')
    if equity_ratio is not None:
        if equity_ratio >= eq_min:
            reasons.append(f"自己資本比率 {equity_ratio:.1f}% ≥ {eq_min:.0f}% ✓")
        else:
            reasons.append(f"自己資本比率 {equity_ratio:.1f}% < {eq_min:.0f}% ✗")
            passed = False
    else:
        reasons.append("自己資本比率 データなし")

    return passed, reasons


# =============================================================================
# 短期投資ロジック
# =============================================================================

def calculate_short_term_score(pbr, per, roe, dividend_yield, price_vs_52w_low):
    """
    短期投資のスコアリングを行う（5項目）

    スコアリングルール:
    - PBR: ≤0.8倍→+2、≤0.5倍→さらに+2（計+4）、≥1.0倍→-3
    - PER予想: ≤15倍→+2、≤10倍→さらに+2（計+4）、≥20倍→-3
    - ROE予想: ≥8%→+2、≥16%→さらに+2（計+4）、≤2%→-3
    - 配当利回り: ≥3%→+2、≥4%→さらに+2（計+4）、≤1.5%→-3
    - 年初来安値からの上昇率: ≤1.2倍→+2、≥1.5倍→-3

    Args:
        pbr: PBR（株価純資産倍率）
        per: PER（予想）
        roe: ROE（予想、%）
        dividend_yield: 配当利回り（%）
        price_vs_52w_low: 現在価格÷52週安値（倍率）
    Returns:
        (合計スコア, 各項目の詳細辞書)
    """
    total_score = 0
    details = {}

    # --- PBRスコア ---
    pbr_score = 0
    if pbr is not None:
        if pbr <= 0.5:
            pbr_score = 4   # ≤0.8で+2、≤0.5でさらに+2
        elif pbr <= 0.8:
            pbr_score = 2   # ≤0.8で+2
        elif pbr >= 1.0:
            pbr_score = -3  # ≥1.0で-3
        total_score += pbr_score
    details['pbr'] = {
        'value': pbr,
        'score': pbr_score,
        'label': f"PBR {pbr:.2f}倍" if pbr is not None else "PBR N/A"
    }

    # --- PER予想スコア ---
    per_score = 0
    if per is not None and per > 0:
        if per <= 10:
            per_score = 4   # ≤15で+2、≤10でさらに+2
        elif per <= 15:
            per_score = 2   # ≤15で+2
        elif per >= 20:
            per_score = -3  # ≥20で-3
        total_score += per_score
    details['per'] = {
        'value': per,
        'score': per_score,
        'label': f"PER {per:.1f}倍" if per is not None else "PER N/A"
    }

    # --- ROE予想スコア ---
    roe_score = 0
    if roe is not None:
        if roe >= 16:
            roe_score = 4   # ≥8で+2、≥16でさらに+2
        elif roe >= 8:
            roe_score = 2   # ≥8で+2
        elif roe <= 2:
            roe_score = -3  # ≤2で-3
        total_score += roe_score
    details['roe'] = {
        'value': roe,
        'score': roe_score,
        'label': f"ROE {roe:.1f}%" if roe is not None else "ROE N/A"
    }

    # --- 配当利回りスコア ---
    div_score = 0
    if dividend_yield is not None:
        if dividend_yield >= 4:
            div_score = 4   # ≥3で+2、≥4でさらに+2
        elif dividend_yield >= 3:
            div_score = 2   # ≥3で+2
        elif dividend_yield <= 1.5:
            div_score = -3  # ≤1.5で-3
        total_score += div_score
    details['dividend_yield'] = {
        'value': dividend_yield,
        'score': div_score,
        'label': f"配当利回り {dividend_yield:.2f}%" if dividend_yield is not None else "配当 N/A"
    }

    # --- 52週安値からの上昇率スコア ---
    price_score = 0
    if price_vs_52w_low is not None:
        if price_vs_52w_low <= 1.2:
            price_score = 2   # 52週安値から20%以内→+2（割安圏）
        elif price_vs_52w_low >= 1.5:
            price_score = -3  # 52週安値から50%以上→-3（高値圏）
        total_score += price_score
    details['price_vs_52w_low'] = {
        'value': price_vs_52w_low,
        'score': price_score,
        'label': f"52週安値比 {price_vs_52w_low:.2f}倍" if price_vs_52w_low is not None else "52週安値比 N/A"
    }

    return total_score, details


def classify_short_term(score, pbr, dividend_yield, is_profit_increase, settings=None):
    """
    短期投資の区分を判定する
    Args:
        score: スコアリング合計点
        pbr: PBR
        dividend_yield: 配当利回り（%）
        is_profit_increase: 経常利益が増益かどうか
        settings: 設定辞書
    Returns:
        区分文字列: "安心割安株" / "通常割安株" / "成長株" / "スキップ"
    """
    if settings is None:
        settings = {}
    anshin_pbr   = float(settings.get('short_anshin_pbr', 0.75))
    anshin_yield = float(settings.get('short_anshin_yield', 2.4))

    if score >= 2 and is_profit_increase:
        if (pbr is not None and pbr <= anshin_pbr) or \
           (dividend_yield is not None and dividend_yield >= anshin_yield):
            return "安心割安株"
        else:
            return "通常割安株"
    elif score <= -10:
        return "成長株"
    else:
        return "スキップ"


def calculate_nanpin_targets(purchase_price, category, settings=None):
    """
    ナンピン目標価格を計算する

    安心割安株: 1-1-1（同数量）
    通常割安株/成長株: 2-3-4（下がるほど多く）

    Args:
        purchase_price: 購入価格
        category: 区分（安心割安株/通常割安株/成長株）
        settings: 設定辞書
    Returns:
        ナンピン情報のリスト [{price, ratio, multiplier}, ...]
    """
    if settings is None:
        settings = {}
    n1 = float(settings.get('short_nanpin1', 10))
    n2 = float(settings.get('short_nanpin2', 15))
    n3 = float(settings.get('short_nanpin3', 20))

    nanpin_levels = [
        {'ratio': -n1 / 100, 'label': f'-{n1:.0f}%'},
        {'ratio': -n2 / 100, 'label': f'-{n2:.0f}%'},
        {'ratio': -n3 / 100, 'label': f'-{n3:.0f}%'},
    ]

    if category == "安心割安株":
        multipliers = [1, 1, 1]
    else:
        multipliers = [2, 3, 4]

    result = []
    for i, level in enumerate(nanpin_levels):
        target_price = purchase_price * (1 + level['ratio'])
        result.append({
            'price': round(target_price, 0),
            'ratio': level['ratio'] * 100,
            'label': level['label'],
            'multiplier': multipliers[i]
        })

    return result


def calculate_sell_target(purchase_price, nanpin_avg_price=None,
                           has_nanpin=False, investment_type='short', settings=None):
    """
    売り目標価格を計算する

    通常: 買い値×(1+short_profit_normal/100)
    ナンピン後: 買い平均×(1+short_profit_nanpin/100)
    ロスカット: 買い値×(1-short_loss_cut/100)

    Args:
        purchase_price: 購入価格（初回）
        nanpin_avg_price: ナンピン後の平均価格
        has_nanpin: ナンピンをしたかどうか
        investment_type: 投資種別
        settings: 設定辞書
    Returns:
        売り目標情報の辞書
    """
    if settings is None:
        settings = {}
    profit_normal = float(settings.get('short_profit_normal', 9.0))
    profit_nanpin = float(settings.get('short_profit_nanpin', 10.0))
    loss_cut_rate = float(settings.get('short_loss_cut', 25.0))

    if has_nanpin and nanpin_avg_price is not None:
        profit_target = round(nanpin_avg_price * (1 + profit_nanpin / 100), 0)
        base_price = nanpin_avg_price
        target_rate = profit_nanpin
    else:
        profit_target = round(purchase_price * (1 + profit_normal / 100), 0)
        base_price = purchase_price
        target_rate = profit_normal

    loss_cut = round(purchase_price * (1 - loss_cut_rate / 100), 0)

    return {
        'profit_target': profit_target,
        'loss_cut': loss_cut,
        'base_price': base_price,
        'target_rate': target_rate,
    }


def check_eps_revision_signal(ticker_code, current_eps, settings=None):
    """
    EPS上方修正シグナルをチェックする
    買いトリガー: EPS年間予想が30日以上前の記録値より+閾値%以上引き上げ

    Args:
        ticker_code: 銘柄コード
        current_eps: 現在の予想EPS（正の値のみ対象）
        settings: 設定辞書
    Returns:
        (bool, float): シグナルあり/なし、改訂率（%）
    """
    from database import get_prev_eps

    if settings is None:
        settings = {}
    threshold = float(settings.get('eps_revision_threshold', 10.0))

    if current_eps is None or current_eps <= 0:
        return False, None

    prev_eps = get_prev_eps(ticker_code, min_days_ago=30)

    if prev_eps is None or prev_eps <= 0:
        return False, None

    revision_rate = ((current_eps - prev_eps) / prev_eps) * 100
    has_signal = revision_rate >= threshold

    return has_signal, round(revision_rate, 2)


# =============================================================================
# 中期投資ロジック
# =============================================================================

def check_medium_term_buy(pbr, nikkei_pbr, market_type='international', eps_positive=True,
                          settings=None):
    """
    中期投資の買いシグナルをチェックする
    買いトリガー: 個別銘柄PBR ≤ 日経平均PBR × 係数

    Args:
        pbr: 個別銘柄のPBR
        nikkei_pbr: 日経平均PBR
        market_type: 企業種別（'international'/'financial'）
        eps_positive: EPSが黒字かどうか
        settings: 設定辞書
    Returns:
        (bool, float, float): シグナルあり/なし、閾値PBR、現在PBR
    """
    if pbr is None or nikkei_pbr is None:
        return False, None, pbr

    if settings is None:
        settings = {}
    intl_black = float(settings.get('mid_intl_black_coeff', 0.6))
    intl_red   = float(settings.get('mid_intl_red_coeff',   0.3))
    fin_black  = float(settings.get('mid_fin_black_coeff',  0.5))
    fin_red    = float(settings.get('mid_fin_red_coeff',    0.25))

    if market_type == 'international':
        coefficient = intl_black if eps_positive else intl_red
    else:
        coefficient = fin_black  if eps_positive else fin_red

    threshold_pbr = nikkei_pbr * coefficient
    has_signal    = pbr <= threshold_pbr

    return has_signal, round(threshold_pbr, 3), pbr


def calculate_medium_term_status(current_price, support1, support2, resistance1,
                                  status='monitoring'):
    """
    中期投資の状態と通知を判定する

    状態管理:
    - 監視中: CurrentPrice≤Support1 → 第1購入通知
    - 保有中(1次): CurrentPrice≥Resistance1 → 利確通知
    - 保有中(1次): CurrentPrice≤Support2 → ナンピン通知
    - 保有中(ナンピン済): 目標をResistance1+αに上方修正

    Args:
        current_price: 現在価格
        support1: サポート1水準
        support2: サポート2水準
        resistance1: レジスタンス1水準
        status: 現在の保有状態
    Returns:
        アクション通知の辞書
    """
    notifications = []
    action = 'hold'  # hold, buy, sell, nanpin

    if status == 'monitoring':
        if support1 and current_price <= support1:
            notifications.append({
                'type': 'buy',
                'message': f'現在価格({current_price:,.0f}円)がサポート1({support1:,.0f}円)に達しました。第1購入検討してください。',
                'level': 'info'
            })
            action = 'buy'

    elif status == 'holding_primary':
        if resistance1 and current_price >= resistance1:
            notifications.append({
                'type': 'sell',
                'message': f'現在価格({current_price:,.0f}円)がレジスタンス1({resistance1:,.0f}円)に達しました。利確を検討してください。',
                'level': 'success'
            })
            action = 'sell'

        elif support2 and current_price <= support2:
            notifications.append({
                'type': 'nanpin',
                'message': f'現在価格({current_price:,.0f}円)がサポート2({support2:,.0f}円)に達しました。ナンピンを検討してください。',
                'level': 'warning'
            })
            action = 'nanpin'

    elif status == 'holding_nanpin':
        # ナンピン済みの場合: 目標をResistance1+α（10%上乗せ）
        if resistance1:
            enhanced_target = resistance1 * 1.1
            if current_price >= enhanced_target:
                notifications.append({
                    'type': 'sell',
                    'message': f'現在価格({current_price:,.0f}円)がナンピン後目標({enhanced_target:,.0f}円)に達しました。',
                    'level': 'success'
                })
                action = 'sell'

    return {
        'action': action,
        'notifications': notifications,
        'support1': support1,
        'support2': support2,
        'resistance1': resistance1
    }


# =============================================================================
# 長期投資ロジック
# =============================================================================

def calculate_long_term_buy_price(annual_dividend, target_yield_min=3.0, target_yield_max=5.0):
    """
    長期投資の買い目標株価を計算する
    買い目標株価 = 予想年間配当 ÷ 目標配当利回り

    Args:
        annual_dividend: 予想年間配当（1株あたり）
        target_yield_min: 目標配当利回り最小（%）例: 3.0
        target_yield_max: 目標配当利回り最大（%）例: 5.0
    Returns:
        (買い目標株価の最大値, 買い目標株価の最小値)
        ※利回り最小 → 株価最大（高く買える）、利回り最大 → 株価最小
    """
    if annual_dividend is None or annual_dividend <= 0:
        return None, None

    # 目標利回り最小（3%）の場合 → 許容できる最高株価
    max_buy_price = annual_dividend / (target_yield_min / 100)

    # 目標利回り最大（5%）の場合 → 理想的な最低購入価格
    min_buy_price = annual_dividend / (target_yield_max / 100)

    return round(max_buy_price, 0), round(min_buy_price, 0)


def calculate_long_term_sell_price(lowest_price, target_yield=4.0):
    """
    長期投資の売り目標株価を計算する
    売り時: 最安値 × (1 + 目標利回り×10)

    Args:
        lowest_price: 過去最安値
        target_yield: 目標利回り（%）
    Returns:
        売り目標株価
    """
    if lowest_price is None:
        return None

    # 売り目標 = 最安値 × (1 + 目標利回り × 10)
    # 目標利回り4%の場合: 最安値 × 1.4 （+40%）
    sell_price = lowest_price * (1 + target_yield / 100 * 10)
    return round(sell_price, 0)


def check_long_term_conditions(info, manual_info=None):
    """
    長期投資の条件チェック
    条件:
    - EPSが過去20年で赤字2回以下（手動確認が必要）
    - 安定株主率40%以上（手動設定）
    - 配当1株30円以上
    - 過去15-20年で減配なし（手動確認が必要）
    - 日平均50,000株以上

    Args:
        info: 銘柄情報
        manual_info: 手動設定情報（安定株主率等）
    Returns:
        (bool, list): 条件クリア/否か、理由リスト
    """
    reasons = []
    passed = True

    # 配当1株30円以上チェック
    annual_dividend = info.get('annual_dividend')
    if annual_dividend is not None:
        if annual_dividend >= 30:
            reasons.append(f"配当 {annual_dividend:.0f}円/株 ≥ 30円 ✓")
        else:
            reasons.append(f"配当 {annual_dividend:.0f}円/株 < 30円 ✗")
            passed = False
    else:
        reasons.append("配当データなし（要確認）")

    # 安定株主率チェック（手動設定が必要）
    if manual_info:
        stable_ratio = manual_info.get('stable_shareholder_ratio')
        if stable_ratio is not None:
            if stable_ratio >= 40:
                reasons.append(f"安定株主率 {stable_ratio:.1f}% ≥ 40% ✓")
            else:
                reasons.append(f"安定株主率 {stable_ratio:.1f}% < 40% ✗")
                passed = False
        else:
            reasons.append("安定株主率 未設定（手動設定が必要）")

    # 配当利回りチェック（参考）
    div_yield = info.get('dividend_yield')
    if div_yield is not None:
        reasons.append(f"配当利回り {div_yield:.2f}%")

    return passed, reasons


# =============================================================================
# 全銘柄スクリーニング
# =============================================================================

def screen_all_stocks(stock_data_list, nikkei_pbr=1.3, settings=None):
    """
    全銘柄のスクリーニングを実行する
    短期・中期・長期の各ロジックで銘柄を分類する

    Args:
        stock_data_list: 銘柄データのリスト（get_all_topix_data()の結果）
        nikkei_pbr: 日経平均PBR
        settings: 設定辞書
    Returns:
        スクリーニング結果の辞書
        {
            'short_anshin_waribari': [...],  # 安心割安株
            'short_normal': [...],            # 通常割安株・成長株
            'medium_term': [...],             # 中期候補
            'long_term': [...],               # 長期候補
        }
    """
    if settings is None:
        settings = {}

    # 設定値の取得
    target_yield_min = float(settings.get('target_yield_min', 3.0))
    target_yield_max = float(settings.get('target_yield_max', 5.0))

    # 結果格納用リスト
    anshin_waribari = []    # 安心割安株
    normal_short = []       # 通常割安株・成長株
    medium_candidates = []  # 中期候補
    long_candidates = []    # 長期候補

    for ticker, info in stock_data_list.items():
        if not info:
            continue

        try:
            # 基本データの取得
            current_price = info.get('current_price')
            pbr = info.get('pbr')
            per = info.get('per')
            roe = info.get('roe')
            dividend_yield = info.get('dividend_yield')
            week52_low = info.get('week52_low')
            forward_eps = info.get('forward_eps')

            # 52週安値からの上昇率計算
            price_vs_52w_low = None
            if current_price and week52_low and week52_low > 0:
                price_vs_52w_low = current_price / week52_low

            # =========================================
            # 企業種別の判定（短期・中期・長期共通）
            # =========================================
            int_check, _ = check_international_excellent(info, settings)
            fin_check, _ = check_financial_excellent(info, settings)
            market_type = 'international' if int_check else 'financial'
            market_type_label = '国際優良企業' if int_check else '財務優良企業'

            # =========================================
            # 短期スクリーニング
            # =========================================

            # EPS上方修正シグナルチェック（買いトリガー）
            has_eps_signal, eps_revision = check_eps_revision_signal(ticker, forward_eps, settings)

            # スコアリング
            score, score_details = calculate_short_term_score(
                pbr, per, roe, dividend_yield, price_vs_52w_low
            )

            # 経常利益増益判断（ROE>0かつEPS>0を簡易チェック）
            is_profit_increase = (roe is not None and roe > 0 and
                                   forward_eps is not None and forward_eps > 0)

            # 短期区分判定
            category = classify_short_term(score, pbr, dividend_yield, is_profit_increase, settings)

            # 売り目標・ナンピン水準の計算
            nanpin_targets = None
            sell_targets = None
            if current_price and category != "スキップ":
                nanpin_targets = calculate_nanpin_targets(current_price, category, settings)
                sell_targets = calculate_sell_target(current_price, settings=settings)

            # 短期候補リストに追加（企業種別を含む）
            short_entry = {
                'ticker': ticker,
                'stock_name': info.get('stock_name', ticker),
                'current_price': current_price,
                'pbr': pbr,
                'per': per,
                'roe': roe,
                'dividend_yield': dividend_yield,
                'score': score,
                'score_details': score_details,
                'category': category,
                'eps_revision': eps_revision,
                'has_eps_signal': has_eps_signal,
                'nanpin_targets': nanpin_targets,
                'sell_targets': sell_targets,
                'week52_low': week52_low,
                'price_vs_52w_low': price_vs_52w_low,
                'market_type': market_type,
                'market_type_label': market_type_label,
            }

            if category == "安心割安株":
                anshin_waribari.append(short_entry)
            elif category in ("通常割安株", "成長株"):
                normal_short.append(short_entry)

            # =========================================
            # 中期スクリーニング
            # =========================================

            # market_type は企業種別判定セクションで設定済み
            eps_positive = forward_eps is not None and forward_eps > 0

            has_medium_signal, threshold_pbr, _ = check_medium_term_buy(
                pbr, nikkei_pbr, market_type, eps_positive, settings
            )

            if has_medium_signal:
                medium_entry = {
                    'ticker': ticker,
                    'stock_name': info.get('stock_name', ticker),
                    'current_price': current_price,
                    'pbr': pbr,
                    'threshold_pbr': threshold_pbr,
                    'nikkei_pbr': nikkei_pbr,
                    'market_type': market_type,
                    'market_type_label': market_type_label,
                    'roe': roe,
                    'dividend_yield': dividend_yield,
                }
                medium_candidates.append(medium_entry)

            # =========================================
            # 長期スクリーニング（配当利回りベース）
            # =========================================

            annual_dividend = info.get('annual_dividend')
            max_buy, min_buy = calculate_long_term_buy_price(
                annual_dividend, target_yield_min, target_yield_max
            )

            # 現在価格が買い目標範囲内または以下であれば候補に追加（企業種別を含む）
            if max_buy and current_price and current_price <= max_buy:
                long_entry = {
                    'ticker': ticker,
                    'stock_name': info.get('stock_name', ticker),
                    'current_price': current_price,
                    'annual_dividend': annual_dividend,
                    'dividend_yield': dividend_yield,
                    'max_buy_price': max_buy,
                    'min_buy_price': min_buy,
                    'pbr': pbr,
                    'roe': roe,
                    'market_type': market_type,
                    'market_type_label': market_type_label,
                }
                long_candidates.append(long_entry)

        except Exception as e:
            print(f"スクリーニングエラー {ticker}: {e}")
            continue

    # スコア順（降順）でソート
    anshin_waribari.sort(key=lambda x: x.get('score', 0), reverse=True)
    normal_short.sort(key=lambda x: x.get('score', 0), reverse=True)

    # 中期: PBR/閾値PBRの乖離が大きい順
    medium_candidates.sort(
        key=lambda x: (x.get('threshold_pbr', 0) - x.get('pbr', 0))
        if x.get('pbr') and x.get('threshold_pbr') else 0,
        reverse=True
    )

    # 長期: 現在価格が理想購入価格（利回り最大）に近い順
    long_candidates.sort(
        key=lambda x: x.get('current_price', 0) / x.get('min_buy_price', 1)
        if x.get('min_buy_price') else 0
    )

    return {
        'short_anshin_waribari': anshin_waribari,
        'short_normal': normal_short,
        'medium_term': medium_candidates,
        'long_term': long_candidates,
        'screened_at': datetime.now().isoformat(),
        'total_screened': len(stock_data_list),
    }


# =============================================================================
# ポートフォリオアラート生成
# =============================================================================

def generate_portfolio_alerts(portfolio_list, current_prices, settings=None):
    """
    ポートフォリオのナンピン/売りアラートを生成する

    Args:
        portfolio_list: ポートフォリオエントリのリスト
        current_prices: {ticker: price}の辞書
        settings: 設定辞書
    Returns:
        アラートのリスト
    """
    if settings is None:
        settings = {}
    loss_cut_rate = float(settings.get('short_loss_cut', 25.0)) / 100

    alerts = []
    today = datetime.now().date()

    for entry in portfolio_list:
        if entry.get('status') != 'active':
            continue

        ticker = entry['ticker']
        current_price = current_prices.get(ticker)

        if current_price is None:
            continue

        purchase_price = entry['purchase_price']
        investment_type = entry['investment_type']
        category = entry.get('category', '通常割安株')
        nanpin_count = entry.get('nanpin_count', 0)
        entry_id = entry['id']

        # 平均購入価格の計算（ナンピン含む）
        avg_price, total_shares = calculate_average_price(entry_id)
        if avg_price is None:
            avg_price = purchase_price

        # 損益率の計算
        pnl_rate = (current_price - avg_price) / avg_price * 100

        # =========================================
        # 短期投資のアラート
        # =========================================
        if investment_type == 'short':

            # --- ロスカットアラート（最優先） ---
            loss_cut_price = purchase_price * (1 - loss_cut_rate)
            if current_price <= loss_cut_price:
                alerts.append({
                    'type': 'loss_cut',
                    'level': 'danger',
                    'ticker': ticker,
                    'stock_name': entry['stock_name'],
                    'message': f'【ロスカット】{entry["stock_name"]}({ticker}): '
                               f'現在価格({current_price:,.0f}円)がロスカット水準({loss_cut_price:,.0f}円)を下回っています。即売却を検討してください。',
                    'current_price': current_price,
                    'trigger_price': loss_cut_price,
                    'pnl_rate': pnl_rate,
                    'entry_id': entry_id,
                })

            # --- 売り目標達成アラート ---
            sell_targets = calculate_sell_target(
                purchase_price,
                nanpin_avg_price=avg_price if nanpin_count > 0 else None,
                has_nanpin=nanpin_count > 0,
                investment_type='short',
                settings=settings,
            )
            profit_target = sell_targets['profit_target']

            if current_price >= profit_target:
                alerts.append({
                    'type': 'sell_target',
                    'level': 'success',
                    'ticker': ticker,
                    'stock_name': entry['stock_name'],
                    'message': f'【売り時】{entry["stock_name"]}({ticker}): '
                               f'現在価格({current_price:,.0f}円)が売り目標({profit_target:,.0f}円)に達しました。',
                    'current_price': current_price,
                    'trigger_price': profit_target,
                    'pnl_rate': pnl_rate,
                    'entry_id': entry_id,
                })

            # --- ナンピンアラート ---
            if nanpin_count < 3:  # 最大3回まで
                nanpin_targets = calculate_nanpin_targets(purchase_price, category, settings)
                if nanpin_count < len(nanpin_targets):
                    next_nanpin = nanpin_targets[nanpin_count]
                    if current_price <= next_nanpin['price']:
                        alerts.append({
                            'type': 'nanpin',
                            'level': 'warning',
                            'ticker': ticker,
                            'stock_name': entry['stock_name'],
                            'message': f'【ナンピン推奨】{entry["stock_name"]}({ticker}): '
                                       f'現在価格({current_price:,.0f}円)がナンピン水準({next_nanpin["price"]:,.0f}円)に達しました。'
                                       f'（第{nanpin_count+1}回目、{next_nanpin["multiplier"]}倍量）',
                            'current_price': current_price,
                            'trigger_price': next_nanpin['price'],
                            'pnl_rate': pnl_rate,
                            'entry_id': entry_id,
                            'nanpin_multiplier': next_nanpin['multiplier'],
                        })

            # --- 3ヶ月期限アラート ---
            deadline_str = entry.get('three_month_deadline')
            if deadline_str:
                deadline = datetime.strptime(deadline_str, '%Y-%m-%d').date()
                days_remaining = (deadline - today).days

                if days_remaining <= 0:
                    # 期限切れ → 強制売却または移行
                    if category == '安心割安株':
                        alerts.append({
                            'type': 'period_expired',
                            'level': 'warning',
                            'ticker': ticker,
                            'stock_name': entry['stock_name'],
                            'message': f'【期限到来】{entry["stock_name"]}({ticker}): '
                                       f'3ヶ月期限が到来しました。安心割安株のため中期/長期投資に移行を検討してください。',
                            'current_price': current_price,
                            'pnl_rate': pnl_rate,
                            'entry_id': entry_id,
                        })
                    else:
                        alerts.append({
                            'type': 'period_expired',
                            'level': 'danger',
                            'ticker': ticker,
                            'stock_name': entry['stock_name'],
                            'message': f'【強制売却】{entry["stock_name"]}({ticker}): '
                                       f'3ヶ月期限が到来しました。売却を検討してください。',
                            'current_price': current_price,
                            'pnl_rate': pnl_rate,
                            'entry_id': entry_id,
                        })
                elif days_remaining <= 7:
                    alerts.append({
                        'type': 'period_warning',
                        'level': 'info',
                        'ticker': ticker,
                        'stock_name': entry['stock_name'],
                        'message': f'【期限警告】{entry["stock_name"]}({ticker}): '
                                   f'3ヶ月期限まであと{days_remaining}日です。',
                        'current_price': current_price,
                        'pnl_rate': pnl_rate,
                        'entry_id': entry_id,
                    })

        # =========================================
        # 長期投資のアラート（減配チェック）
        # =========================================
        elif investment_type == 'long':
            # 大幅下落アラート（-10%/-20%のナンピン水準）
            if nanpin_count == 0 and pnl_rate <= -10:
                alerts.append({
                    'type': 'nanpin',
                    'level': 'warning',
                    'ticker': ticker,
                    'stock_name': entry['stock_name'],
                    'message': f'【ナンピン推奨】{entry["stock_name"]}({ticker}): '
                               f'初回購入から{pnl_rate:.1f}%下落。第1回ナンピンを検討してください。',
                    'current_price': current_price,
                    'pnl_rate': pnl_rate,
                    'entry_id': entry_id,
                })
            elif nanpin_count == 1 and pnl_rate <= -20:
                alerts.append({
                    'type': 'nanpin',
                    'level': 'warning',
                    'ticker': ticker,
                    'stock_name': entry['stock_name'],
                    'message': f'【ナンピン推奨】{entry["stock_name"]}({ticker}): '
                               f'初回購入から{pnl_rate:.1f}%下落。第2回ナンピンを検討してください。',
                    'current_price': current_price,
                    'pnl_rate': pnl_rate,
                    'entry_id': entry_id,
                })

    # アラートの優先度でソート（danger > warning > success > info）
    priority = {'danger': 0, 'warning': 1, 'success': 2, 'info': 3}
    alerts.sort(key=lambda x: priority.get(x.get('level', 'info'), 3))

    return alerts


# =============================================================================
# バックテスト（シミュレーション）
# =============================================================================

def run_backtest(ticker_code, start_date, end_date, initial_capital=1000000,
                 strategy='short', category='安心割安株'):
    """
    過去データを使ったバックテストを実行する

    Args:
        ticker_code: 銘柄コード
        start_date: 開始日（YYYY-MM-DD）
        end_date: 終了日（YYYY-MM-DD）
        initial_capital: 初期資金（円）
        strategy: 戦略（short/medium/long）
        category: 短期区分
    Returns:
        バックテスト結果の辞書
    """
    from stock_data import get_historical_prices

    # 日足データを取得
    period = '5y'  # 5年分
    hist = get_historical_prices(ticker_code, period=period, interval='1d')

    if hist is None:
        return {'error': 'データ取得失敗'}

    # 指定期間のデータにフィルタ
    hist.index = pd.to_datetime(hist.index) if not isinstance(hist.index, pd.DatetimeIndex) else hist.index
    hist = hist.loc[start_date:end_date]

    if hist.empty:
        return {'error': '指定期間のデータなし'}

    # バックテスト実行
    trades = []
    capital = initial_capital
    position = None  # 現在ポジション
    equity_curve = []

    for date, row in hist.iterrows():
        current_price = row['Close']

        if position is None:
            # ポジションなし → 買い判断
            # 簡易的にシグナルをチェック（実際はより複雑）
            # ここでは最初の日に買い
            if len(equity_curve) == 0:
                shares = int(initial_capital * 0.3 / current_price)  # 30%投資
                if shares > 0:
                    position = {
                        'entry_date': date,
                        'entry_price': current_price,
                        'shares': shares,
                        'cost': current_price * shares,
                        'nanpin_count': 0,
                        'avg_price': current_price,
                    }
                    capital -= position['cost']

        else:
            # ポジションあり → 売り/ナンピン判断
            pnl_rate = (current_price - position['avg_price']) / position['avg_price']

            # 売りチェック（短期の場合）
            if strategy == 'short':
                if position['nanpin_count'] > 0:
                    sell_target_rate = 1.10
                else:
                    sell_target_rate = 1.09

                # 売り条件
                if (current_price >= position['avg_price'] * sell_target_rate or
                    current_price <= position['entry_price'] * 0.75):
                    # 売却
                    proceeds = current_price * position['shares']
                    profit = proceeds - position['cost']
                    capital += proceeds

                    trades.append({
                        'entry_date': position['entry_date'].strftime('%Y-%m-%d'),
                        'exit_date': date.strftime('%Y-%m-%d'),
                        'ticker': ticker_code,
                        'entry_price': position['entry_price'],
                        'exit_price': current_price,
                        'shares': position['shares'],
                        'profit': profit,
                        'profit_rate': pnl_rate * 100,
                        'result': 'WIN' if profit > 0 else 'LOSS',
                    })
                    position = None

                # ナンピンチェック（最大3回）
                elif position['nanpin_count'] < 3:
                    nanpin_targets = calculate_nanpin_targets(
                        position['entry_price'], category
                    )
                    np_idx = position['nanpin_count']
                    if np_idx < len(nanpin_targets):
                        np_target = nanpin_targets[np_idx]
                        if current_price <= np_target['price']:
                            # ナンピン実行
                            np_shares = int(position['shares'] * np_target['multiplier'])
                            np_cost = current_price * np_shares
                            if capital >= np_cost:
                                total_shares = position['shares'] + np_shares
                                total_cost = position['cost'] + np_cost
                                position['avg_price'] = total_cost / total_shares
                                position['shares'] = total_shares
                                position['cost'] = total_cost
                                position['nanpin_count'] += 1
                                capital -= np_cost

        # エクイティカーブの記録
        portfolio_value = capital
        if position:
            portfolio_value += current_price * position['shares']
        equity_curve.append({
            'date': date.strftime('%Y-%m-%d'),
            'value': portfolio_value,
        })

    # 最終ポジションをクローズ
    if position and len(hist) > 0:
        last_price = hist['Close'].iloc[-1]
        proceeds = last_price * position['shares']
        profit = proceeds - position['cost']
        capital += proceeds
        trades.append({
            'entry_date': position['entry_date'].strftime('%Y-%m-%d'),
            'exit_date': hist.index[-1].strftime('%Y-%m-%d'),
            'ticker': ticker_code,
            'entry_price': position['entry_price'],
            'exit_price': last_price,
            'shares': position['shares'],
            'profit': profit,
            'profit_rate': (last_price - position['avg_price']) / position['avg_price'] * 100,
            'result': 'WIN' if profit > 0 else 'LOSS',
        })

    # 統計の計算
    total_trades = len(trades)
    winning_trades = [t for t in trades if t['result'] == 'WIN']
    losing_trades = [t for t in trades if t['result'] == 'LOSS']

    win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
    avg_win = sum(t['profit_rate'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
    avg_loss = sum(t['profit_rate'] for t in losing_trades) / len(losing_trades) if losing_trades else 0

    total_profit = sum(t['profit'] for t in trades)
    final_capital = initial_capital + total_profit

    # 最大ドローダウンの計算
    max_value = initial_capital
    max_drawdown = 0
    for point in equity_curve:
        if point['value'] > max_value:
            max_value = point['value']
        dd = (max_value - point['value']) / max_value * 100
        if dd > max_drawdown:
            max_drawdown = dd

    return {
        'ticker': ticker_code,
        'strategy': strategy,
        'start_date': start_date,
        'end_date': end_date,
        'initial_capital': initial_capital,
        'final_capital': round(final_capital, 0),
        'total_profit': round(total_profit, 0),
        'total_return_rate': round((final_capital - initial_capital) / initial_capital * 100, 2),
        'total_trades': total_trades,
        'win_rate': round(win_rate, 1),
        'avg_win_rate': round(avg_win, 2),
        'avg_loss_rate': round(avg_loss, 2),
        'max_drawdown': round(max_drawdown, 2),
        'trades': trades,
        'equity_curve': equity_curve,
    }


# pandas importを追加
try:
    import pandas as pd
except ImportError:
    pass
