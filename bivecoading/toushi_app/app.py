"""
app.py - Flask Webアプリケーション メインファイル
株式投資アシスタントのWebインターフェースを提供する
"""

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, flash, session, Response, stream_with_context
)
from datetime import datetime, timedelta
import json
import traceback
import threading

# ローカルモジュール
from database import (
    init_db, get_setting, set_setting,
    add_portfolio_entry, get_portfolio, get_portfolio_by_id,
    update_portfolio_status, add_nanpin, calculate_average_price,
    get_stock_cache, save_manual_info, get_all_manual_info, get_manual_info,
    get_simulation_cache, save_simulation_cache,
)
from stock_data import get_stock_info, get_all_topix_data, TOPIX_CORE30, TOPIX_LARGE70
from investment_logic import (
    screen_all_stocks, generate_portfolio_alerts,
    calculate_sell_target, calculate_nanpin_targets,
    calculate_long_term_buy_price, run_backtest
)

# =============================================================================
# Flaskアプリの設定
# =============================================================================
app = Flask(__name__)
app.secret_key = 'toushi_assistant_secret_key_2024'  # セッション用シークレットキー

# スクリーニング結果をキャッシュする（グローバル変数）
_screening_cache = {
    'data': None,
    'updated_at': None,
}
_screening_lock = threading.Lock()  # スレッドセーフなキャッシュアクセス

# シミュレーション用スクリーニング結果キャッシュ（日付×銘柄数 → 結果）
# Flask プロセスが生きている間は有効
_sim_cache = {}
_sim_cache_lock = threading.Lock()


def get_screening_results(force_refresh=False):
    """
    スクリーニング結果を取得する（キャッシュ利用）
    Args:
        force_refresh: キャッシュを無視して再取得するか
    Returns:
        スクリーニング結果の辞書
    """
    global _screening_cache

    with _screening_lock:
        # キャッシュが有効かチェック（1時間以内）
        if (not force_refresh and
            _screening_cache['data'] is not None and
            _screening_cache['updated_at'] is not None):
            elapsed = (datetime.now() - _screening_cache['updated_at']).total_seconds()
            if elapsed < 3600:  # 1時間
                return _screening_cache['data']

    # 新しいデータを取得
    try:
        screening_target = get_setting('screening_target') or 'core30_large70'
        include_large70 = screening_target != 'core30_only'

        # 全銘柄データを取得
        stock_data = get_all_topix_data(include_large70=include_large70)

        # 設定値を取得
        nikkei_pbr = float(get_setting('nikkei_pbr') or 1.3)
        settings = {
            'target_yield_min': float(get_setting('target_yield_min') or 3.0),
            'target_yield_max': float(get_setting('target_yield_max') or 5.0),
        }

        # スクリーニング実行
        results = screen_all_stocks(stock_data, nikkei_pbr, settings)

        # キャッシュを更新
        with _screening_lock:
            _screening_cache['data'] = results
            _screening_cache['updated_at'] = datetime.now()

        return results

    except Exception as e:
        print(f"スクリーニングエラー: {e}")
        traceback.print_exc()
        # エラー時は空の結果を返す
        return {
            'short_anshin_waribari': [],
            'short_normal': [],
            'medium_term': [],
            'long_term': [],
            'screened_at': datetime.now().isoformat(),
            'total_screened': 0,
            'error': str(e)
        }


# =============================================================================
# ルート: ダッシュボード
# =============================================================================

@app.route('/')
def dashboard():
    """
    メインダッシュボード
    全銘柄スクリーニング結果を表示（安心割安株を最優先表示）
    """
    try:
        # スクリーニング結果の取得（キャッシュ利用）
        results = get_screening_results()

        # スクリーニング実行時刻の整形
        screened_at = results.get('screened_at', '')
        if screened_at:
            try:
                dt = datetime.fromisoformat(screened_at)
                screened_at = dt.strftime('%Y年%m月%d日 %H:%M')
            except Exception:
                pass

        return render_template(
            'dashboard.html',
            anshin_stocks=results.get('short_anshin_waribari', []),
            normal_stocks=results.get('short_normal', []),
            medium_stocks=results.get('medium_term', []),
            long_stocks=results.get('long_term', []),
            screened_at=screened_at,
            total_screened=results.get('total_screened', 0),
            error=results.get('error'),
        )
    except Exception as e:
        flash(f'ダッシュボードの読み込みエラー: {str(e)}', 'danger')
        traceback.print_exc()
        return render_template(
            'dashboard.html',
            anshin_stocks=[],
            normal_stocks=[],
            medium_stocks=[],
            long_stocks=[],
            screened_at='',
            total_screened=0,
            error=str(e),
        )


# =============================================================================
# ルート: ポートフォリオ
# =============================================================================

@app.route('/portfolio')
def portfolio():
    """
    ポートフォリオ管理画面
    保有株一覧とアラートを表示する
    """
    try:
        # アクティブな保有株を取得
        portfolio_list = get_portfolio(status='active')

        # 現在の株価を取得
        current_prices = {}
        for entry in portfolio_list:
            ticker = entry['ticker']
            cached = get_stock_cache(ticker)
            if cached and cached.get('current_price'):
                current_prices[ticker] = cached['current_price']
            else:
                # キャッシュがなければ取得
                info = get_stock_info(ticker)
                if info:
                    current_prices[ticker] = info.get('current_price')

        # アラートを生成
        from database import DEFAULT_SETTINGS as _PF_DS
        _pf_settings = {k: get_setting(k) or _PF_DS.get(k, '') for k in _PF_DS}
        alerts = generate_portfolio_alerts(portfolio_list, current_prices, _pf_settings)

        # ポートフォリオエントリに追加情報を付加
        enriched_portfolio = []
        for entry in portfolio_list:
            ticker = entry['ticker']
            cp = current_prices.get(ticker)

            avg_price, total_shares = calculate_average_price(entry['id'])

            enriched = dict(entry)
            enriched['current_price'] = cp
            enriched['avg_price'] = avg_price
            enriched['total_shares'] = total_shares

            if cp and avg_price:
                enriched['pnl'] = round((cp - avg_price) * total_shares, 0)
                enriched['pnl_rate'] = round((cp - avg_price) / avg_price * 100, 2)
            else:
                enriched['pnl'] = None
                enriched['pnl_rate'] = None

            # 売り目標価格を計算
            has_nanpin = entry.get('nanpin_count', 0) > 0
            sell_targets = calculate_sell_target(
                entry['purchase_price'],
                nanpin_avg_price=avg_price if has_nanpin else None,
                has_nanpin=has_nanpin,
                investment_type=entry['investment_type']
            )
            enriched['sell_targets'] = sell_targets

            # 3ヶ月期限まで残り日数
            deadline_str = entry.get('three_month_deadline')
            if deadline_str:
                try:
                    deadline = datetime.strptime(deadline_str, '%Y-%m-%d').date()
                    enriched['days_to_deadline'] = (deadline - datetime.now().date()).days
                except Exception:
                    enriched['days_to_deadline'] = None
            else:
                enriched['days_to_deadline'] = None

            enriched_portfolio.append(enriched)

        # 売却済み銘柄も取得（最近10件）
        sold_portfolio = get_portfolio(status='sold')[:10]

        return render_template(
            'portfolio.html',
            portfolio=enriched_portfolio,
            sold_portfolio=sold_portfolio,
            alerts=alerts,
            current_prices=current_prices,
        )

    except Exception as e:
        flash(f'ポートフォリオ読み込みエラー: {str(e)}', 'danger')
        traceback.print_exc()
        return render_template(
            'portfolio.html',
            portfolio=[],
            sold_portfolio=[],
            alerts=[],
            current_prices={},
        )


@app.route('/portfolio/add', methods=['POST'])
def portfolio_add():
    """
    ポートフォリオに銘柄を追加する
    """
    try:
        # フォームデータの取得
        ticker = request.form.get('ticker', '').strip()
        stock_name = request.form.get('stock_name', '').strip()
        investment_type = request.form.get('investment_type', 'short')
        category = request.form.get('category', '通常割安株')
        purchase_date = request.form.get('purchase_date')
        purchase_price = float(request.form.get('purchase_price', 0))
        shares = int(request.form.get('shares', 0))
        notes = request.form.get('notes', '')

        # バリデーション
        if not ticker:
            flash('銘柄コードを入力してください', 'warning')
            return redirect(url_for('portfolio'))

        if purchase_price <= 0:
            flash('購入単価を正しく入力してください', 'warning')
            return redirect(url_for('portfolio'))

        if shares <= 0:
            flash('株数を正しく入力してください', 'warning')
            return redirect(url_for('portfolio'))

        if not purchase_date:
            purchase_date = datetime.now().strftime('%Y-%m-%d')

        # 銘柄名が空の場合はyfinanceから取得
        if not stock_name:
            info = get_stock_info(ticker)
            if info:
                stock_name = info.get('stock_name', ticker)
            else:
                stock_name = ticker

        # DBに追加
        entry_id = add_portfolio_entry(
            ticker=ticker,
            stock_name=stock_name,
            investment_type=investment_type,
            category=category,
            purchase_date=purchase_date,
            purchase_price=purchase_price,
            shares=shares,
            notes=notes
        )

        flash(f'{stock_name}({ticker})をポートフォリオに追加しました（ID: {entry_id}）', 'success')

    except ValueError as e:
        flash(f'入力値エラー: {str(e)}', 'danger')
    except Exception as e:
        flash(f'追加エラー: {str(e)}', 'danger')
        traceback.print_exc()

    return redirect(url_for('portfolio'))


@app.route('/portfolio/nanpin', methods=['POST'])
def portfolio_nanpin():
    """
    ナンピン（追加購入）を記録する
    """
    try:
        entry_id = int(request.form.get('entry_id', 0))
        nanpin_date = request.form.get('nanpin_date', datetime.now().strftime('%Y-%m-%d'))
        nanpin_price = float(request.form.get('nanpin_price', 0))
        nanpin_shares = int(request.form.get('nanpin_shares', 0))

        if entry_id <= 0:
            flash('ポートフォリオIDが不正です', 'warning')
            return redirect(url_for('portfolio'))

        if nanpin_price <= 0 or nanpin_shares <= 0:
            flash('ナンピン価格・株数を正しく入力してください', 'warning')
            return redirect(url_for('portfolio'))

        # ナンピン記録
        add_nanpin(entry_id, nanpin_date, nanpin_price, nanpin_shares)

        # ポートフォリオ情報を取得して確認メッセージ
        entry = get_portfolio_by_id(entry_id)
        if entry:
            avg_price, total_shares = calculate_average_price(entry_id)
            flash(
                f'{entry["stock_name"]}のナンピンを記録しました。'
                f'平均購入単価: {avg_price:,.0f}円、合計株数: {total_shares}株',
                'success'
            )

    except ValueError as e:
        flash(f'入力値エラー: {str(e)}', 'danger')
    except Exception as e:
        flash(f'ナンピン記録エラー: {str(e)}', 'danger')
        traceback.print_exc()

    return redirect(url_for('portfolio'))


@app.route('/portfolio/sell', methods=['POST'])
def portfolio_sell():
    """
    売却を記録する
    """
    try:
        entry_id = int(request.form.get('entry_id', 0))
        sell_price = float(request.form.get('sell_price', 0))
        sell_date = request.form.get('sell_date', datetime.now().strftime('%Y-%m-%d'))

        if entry_id <= 0:
            flash('ポートフォリオIDが不正です', 'warning')
            return redirect(url_for('portfolio'))

        if sell_price <= 0:
            flash('売却価格を正しく入力してください', 'warning')
            return redirect(url_for('portfolio'))

        # 売却記録
        update_portfolio_status(entry_id, 'sold', sell_price, sell_date)

        # 損益計算
        avg_price, total_shares = calculate_average_price(entry_id)
        if avg_price and total_shares:
            profit = (sell_price - avg_price) * total_shares
            profit_rate = (sell_price - avg_price) / avg_price * 100
            entry = get_portfolio_by_id(entry_id)
            stock_name = entry['stock_name'] if entry else str(entry_id)
            flash(
                f'{stock_name}の売却を記録しました。'
                f'損益: {profit:+,.0f}円 ({profit_rate:+.2f}%)',
                'success' if profit >= 0 else 'warning'
            )

    except ValueError as e:
        flash(f'入力値エラー: {str(e)}', 'danger')
    except Exception as e:
        flash(f'売却記録エラー: {str(e)}', 'danger')
        traceback.print_exc()

    return redirect(url_for('portfolio'))


# =============================================================================
# ルート: シミュレーション
# =============================================================================

@app.route('/simulation')
def simulation():
    """
    シミュレーション画面（過去時点の推奨銘柄 + トレード再現）
    """
    from stock_data import ALL_TOPIX_STOCKS
    from datetime import date, timedelta
    today = date.today()
    default_date = (today - timedelta(days=365)).strftime('%Y-%m-%d')
    return render_template(
        'simulation.html',
        all_stocks=ALL_TOPIX_STOCKS,
        now_date=today.strftime('%Y-%m-%d'),
        default_date=default_date,
    )


@app.route('/simulation/screen_at_date', methods=['POST'])
def simulation_screen_at_date():
    """
    指定日時点での推奨銘柄をスクリーニングしてJSONで返す

    リクエスト:
        { "date": "2022-01-15" }
    レスポンス:
        { "short_anshin": [...], "short_normal": [...], "medium": [...], "long": [...] }
    """
    try:
        from simulation import screen_stocks_at_date
        from datetime import date as date_type

        data = request.get_json() or {}
        date_str  = data.get('date', '')
        max_stocks = int(data.get('max_stocks', 30))

        if not date_str:
            return jsonify({'error': '日付を指定してください'}), 400

        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        # 未来日付はNG
        if target_date >= date_type.today():
            return jsonify({'error': '過去の日付を指定してください'}), 400

        # 設定値を読み込む
        from database import DEFAULT_SETTINGS
        sim_settings = {
            key: get_setting(key) or DEFAULT_SETTINGS.get(key, '')
            for key in DEFAULT_SETTINGS
        }
        nikkei_pbr       = float(sim_settings.get('nikkei_pbr', 1.3))
        target_yield_min = float(sim_settings.get('target_yield_min', 3.0))
        target_yield_max = float(sim_settings.get('target_yield_max', 5.0))

        # キャッシュを確認（メモリ → SQLite の順）
        cache_key = f"{date_str}_{max_stocks}"
        with _sim_cache_lock:
            if cache_key in _sim_cache:
                return jsonify({'status': 'ok', 'date': date_str,
                                'results': _sim_cache[cache_key], 'cached': True})

        db_cached = get_simulation_cache(cache_key)
        if db_cached is not None:
            with _sim_cache_lock:
                _sim_cache[cache_key] = db_cached
            return jsonify({'status': 'ok', 'date': date_str,
                            'results': db_cached, 'cached': True})

        results = screen_stocks_at_date(
            target_date=target_date,
            nikkei_pbr=nikkei_pbr,
            target_yield_min=target_yield_min,
            target_yield_max=target_yield_max,
            max_stocks=max_stocks,
            settings=sim_settings,
        )

        # メモリ・SQLite 両方にキャッシュ保存
        with _sim_cache_lock:
            _sim_cache[cache_key] = results
        save_simulation_cache(cache_key, results)

        return jsonify({'status': 'ok', 'date': date_str, 'results': results})

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/simulation/screen_stream')
def simulation_screen_stream():
    """
    SSE（Server-Sent Events）で推奨銘柄をリアルタイムにストリーミングする。
    銘柄の処理が完了するたびにイベントを送信するため、
    ユーザーは結果が出次第テーブルに追加されるのを確認できる。
    """
    import json as _json
    date_str   = request.args.get('date', '')
    max_stocks = int(request.args.get('max_stocks', 15))

    if not date_str:
        return Response("data: {\"type\":\"error\"}\n\n", mimetype='text/event-stream')

    from datetime import date as date_type
    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()

    from database import DEFAULT_SETTINGS as _DS
    _sse_settings = {k: get_setting(k) or _DS.get(k, '') for k in _DS}
    nikkei_pbr       = float(_sse_settings.get('nikkei_pbr', 1.3))
    target_yield_min = float(_sse_settings.get('target_yield_min', 3.0))
    target_yield_max = float(_sse_settings.get('target_yield_max', 5.0))

    cache_key = f"{date_str}_{max_stocks}"

    def generate():
        # ── キャッシュヒット: 全結果を一括送信して終了 ──
        cached = None
        with _sim_cache_lock:
            cached = _sim_cache.get(cache_key)
        if cached is None:
            cached = get_simulation_cache(cache_key)

        if cached is not None:
            yield f"data: {_json.dumps({'type':'cached','results':cached}, ensure_ascii=False)}\n\n"
            return

        # ── キャッシュなし: 1銘柄ずつ処理して逐次送信 ──
        from simulation import _screen_one_stock
        from stock_data import TOPIX_QUICK15, ALL_TOPIX_STOCKS
        from concurrent.futures import ThreadPoolExecutor, as_completed

        target_list = TOPIX_QUICK15 if max_stocks <= 15 else ALL_TOPIX_STOCKS[:max_stocks]
        total       = len(target_list)
        results_all = {'short_anshin': [], 'short_normal': [], 'medium': [], 'long': [],
                       'eps_trigger': []}
        done_count  = 0

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(
                    _screen_one_stock,
                    code, name, target_date,
                    nikkei_pbr, target_yield_min, target_yield_max,
                    _sse_settings
                ): (code, name)
                for code, name in target_list
            }
            for future in as_completed(futures):
                code, name = futures[future]
                done_count += 1
                entries = future.result()

                # 進捗イベント（何も結果がなくても送信）
                yield f"data: {_json.dumps({'type':'progress','done':done_count,'total':total,'name':name}, ensure_ascii=False)}\n\n"

                if entries:
                    for cat, entry_list in entries.items():
                        for entry in entry_list:
                            results_all[cat].append(entry)
                            yield f"data: {_json.dumps({'type':'stock','category':cat,'entry':entry}, ensure_ascii=False)}\n\n"

        # ソートして完了イベント
        results_all['short_anshin'].sort(key=lambda x: x.get('score', 0), reverse=True)
        results_all['short_normal'].sort(key=lambda x: x.get('score', 0), reverse=True)
        results_all['eps_trigger'].sort(
            key=lambda x: x.get('eps_revision_rate') or 0, reverse=True
        )

        with _sim_cache_lock:
            _sim_cache[cache_key] = results_all
        save_simulation_cache(cache_key, results_all)

        yield f"data: {_json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/simulation/run_trade', methods=['POST'])
def simulation_run_trade():
    """
    銘柄・投資種別・購入日を受け取りトレードシミュレーションを実行する

    リクエスト:
        {
            "ticker": "7203",
            "strategy": "short",          # short / medium / long
            "category": "安心割安株",
            "purchase_date": "2022-01-15",
            "purchase_price": 2800,
            "shares": 100,
            "annual_dividend": 120,       # 長期用
            "target_yield": 3.5           # 長期用（%）
        }
    """
    try:
        from simulation import (simulate_short_term,
                                simulate_medium_term,
                                simulate_long_term)
        from datetime import date as date_type

        data = request.get_json() or {}

        ticker         = data.get('ticker', '')
        strategy       = data.get('strategy', 'short')
        category       = data.get('category', '安心割安株')
        purchase_date_str = data.get('purchase_date', '')
        purchase_price = float(data.get('purchase_price', 0))
        shares         = int(data.get('shares', 100))

        if not ticker or not purchase_date_str or purchase_price <= 0:
            return jsonify({'error': '銘柄・購入日・購入価格を入力してください'}), 400

        purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()

        if strategy == 'short':
            result = simulate_short_term(
                ticker_code=ticker,
                purchase_date=purchase_date,
                purchase_price=purchase_price,
                category=category,
                shares=shares,
            )
        elif strategy == 'medium':
            result = simulate_medium_term(
                ticker_code=ticker,
                purchase_date=purchase_date,
                purchase_price=purchase_price,
                shares=shares,
            )
        else:  # long
            annual_dividend = float(data.get('annual_dividend', 0))
            target_yield    = float(data.get('target_yield', 3.5))
            result = simulate_long_term(
                ticker_code=ticker,
                purchase_date=purchase_date,
                purchase_price=purchase_price,
                annual_dividend=annual_dividend,
                target_yield=target_yield,
                shares=shares,
            )

        if 'error' in result:
            return jsonify({'error': result['error']}), 400

        return jsonify({'status': 'ok', 'simulation': result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# =============================================================================
# ルート: バックテスト結果
# =============================================================================

@app.route('/backtest')
def backtest():
    import json, os
    from collections import defaultdict

    progress_file = os.path.join(os.path.dirname(__file__), 'backtest_all_progress.json')
    if not os.path.exists(progress_file):
        return render_template('backtest.html', error='バックテストデータが見つかりません。')

    with open(progress_file) as f:
        p = json.load(f)

    results = p.get('results', [])

    strategies = ['短期', '中期', '長期']
    strategy_colors = {'短期': 'info', '中期': 'warning', '長期': 'success'}

    def calc_investment(r):
        return r['purchase_price'] * 100 * (1 + r.get('nanpin_count', 0))

    # Per-year per-strategy stats
    by_year_strategy = defaultdict(lambda: defaultdict(list))
    for r in results:
        by_year_strategy[r['year']][r['strategy']].append(r)

    years = sorted(by_year_strategy.keys())
    rows = []
    for yr in years:
        year_total_pnl = 0
        year_total_inv = 0
        strat_rows = []
        for st in strategies:
            recs = by_year_strategy[yr].get(st, [])
            if not recs:
                strat_rows.append(None)
                continue
            wins  = sum(1 for r in recs if r['pnl'] > 0)
            inv   = sum(calc_investment(r) for r in recs)
            pnl   = sum(r['pnl'] for r in recs)
            roi   = pnl / inv * 100 if inv else 0
            lc    = sum(1 for r in recs if 'ロスカット' in r['outcome'])
            profit_cnt = sum(1 for r in recs if r['outcome'] == '利益確定')
            transfer   = sum(1 for r in recs if '移行' in r['outcome'] or '期間終了' in r['outcome'])
            year_total_pnl += pnl
            year_total_inv += inv
            strat_rows.append({
                'strategy': st,
                'color': strategy_colors[st],
                'count': len(recs),
                'win_rate': wins / len(recs) * 100,
                'investment': inv,
                'pnl': pnl,
                'roi': roi,
                'profit_cnt': profit_cnt,
                'lc': lc,
                'transfer': transfer,
            })
        year_roi = year_total_pnl / year_total_inv * 100 if year_total_inv else 0
        rows.append({
            'year': yr,
            'strategies': strat_rows,
            'total_pnl': year_total_pnl,
            'total_inv': year_total_inv,
            'total_roi': year_roi,
        })

    # Summary per strategy
    summary = []
    for st in strategies:
        recs = [r for r in results if r['strategy'] == st]
        wins = sum(1 for r in recs if r['pnl'] > 0)
        inv  = sum(calc_investment(r) for r in recs)
        pnl  = sum(r['pnl'] for r in recs)
        summary.append({
            'strategy': st,
            'color': strategy_colors[st],
            'count': len(recs),
            'win_rate': wins / len(recs) * 100 if recs else 0,
            'investment': inv,
            'pnl': pnl,
            'roi': pnl / inv * 100 if inv else 0,
            'avg_pnl': pnl / len(recs) if recs else 0,
        })

    total_pnl = sum(r['pnl'] for r in results)
    total_inv = sum(calc_investment(r) for r in results)
    total_wins = sum(1 for r in results if r['pnl'] > 0)

    # Yearly chart data (cumulative pnl per strategy)
    chart_years = [str(yr) for yr in years]
    chart_data = {}
    for st in strategies:
        cumulative = 0
        vals = []
        for yr in years:
            pnl = sum(r['pnl'] for r in by_year_strategy[yr].get(st, []))
            cumulative += pnl
            vals.append(round(cumulative / 10000, 1))
        chart_data[st] = vals

    return render_template('backtest.html',
        rows=rows,
        summary=summary,
        total_pnl=total_pnl,
        total_inv=total_inv,
        total_wins=total_wins,
        total_count=len(results),
        chart_years=chart_years,
        chart_data=chart_data,
    )


@app.route('/backtest/short')
def backtest_short():
    import json, os
    from collections import defaultdict

    progress_file = os.path.join(os.path.dirname(__file__), 'backtest_all_progress.json')
    with open(progress_file) as f:
        p = json.load(f)

    short = [r for r in p['results'] if r['strategy'] == '短期']
    years = sorted(set(r['year'] for r in short))

    def inv(r):
        return r['purchase_price'] * 100 * (1 + r.get('nanpin_count', 0))

    # --- 年別 ---
    by_year = defaultdict(list)
    for r in short:
        by_year[r['year']].append(r)

    yearly = []
    for yr in years:
        recs = by_year[yr]
        wins  = sum(1 for r in recs if r['pnl'] > 0)
        total_inv_yr = sum(inv(r) for r in recs)
        pnl   = sum(r['pnl'] for r in recs)
        lc    = sum(1 for r in recs if 'ロスカット' in r['outcome'])
        profit_cnt = sum(1 for r in recs if r['outcome'] == '利益確定')
        transfer   = sum(1 for r in recs if '移行' in r['outcome'])
        avg_days   = sum(r['days_held'] for r in recs) / len(recs)
        yearly.append({
            'year': yr, 'count': len(recs),
            'win_rate': wins / len(recs) * 100,
            'investment': total_inv_yr,
            'pnl': pnl,
            'roi': pnl / total_inv_yr * 100 if total_inv_yr else 0,
            'profit_cnt': profit_cnt, 'lc': lc, 'transfer': transfer,
            'avg_days': avg_days,
        })

    # --- カテゴリ別 ---
    by_cat = defaultdict(list)
    for r in short:
        by_cat[r['category']].append(r)
    cat_order = ['安心割安株', '通常割安株', '成長株']
    cat_colors = {'安心割安株': 'success', '通常割安株': 'warning', '成長株': 'info'}
    categories = []
    for cat in cat_order:
        recs = by_cat.get(cat, [])
        if not recs:
            continue
        wins = sum(1 for r in recs if r['pnl'] > 0)
        pnl  = sum(r['pnl'] for r in recs)
        total_inv_cat = sum(inv(r) for r in recs)
        categories.append({
            'name': cat, 'color': cat_colors[cat],
            'count': len(recs),
            'win_rate': wins / len(recs) * 100,
            'pnl': pnl,
            'roi': pnl / total_inv_cat * 100 if total_inv_cat else 0,
            'avg_pnl': pnl / len(recs),
        })

    # --- ナンピン別 ---
    nanpin_stats = []
    for nc in range(5):
        recs = [r for r in short if r.get('nanpin_count', 0) == nc]
        if not recs:
            continue
        wins = sum(1 for r in recs if r['pnl'] > 0)
        pnl  = sum(r['pnl'] for r in recs)
        nanpin_stats.append({
            'count_n': nc, 'count': len(recs),
            'win_rate': wins / len(recs) * 100,
            'pnl': pnl,
            'avg_pnl': pnl / len(recs),
        })

    # --- アウトカム分布 ---
    outcome_counts = defaultdict(int)
    for r in short:
        outcome_counts[r['outcome']] += 1

    # --- 全トレード（テーブル用） ---
    trades = sorted(short, key=lambda r: r['screen_date'], reverse=True)

    # --- チャート用: 年別 損益バー ---
    chart_years   = [str(yr) for yr in years]
    chart_pnl     = [round(by_year[yr][0]['pnl'] if False else sum(r['pnl'] for r in by_year[yr]) / 10000, 1) for yr in years]
    chart_cumul   = []
    c = 0
    for yr in years:
        c += sum(r['pnl'] for r in by_year[yr])
        chart_cumul.append(round(c / 10000, 1))
    chart_win_rates = [round(sum(1 for r in by_year[yr] if r['pnl'] > 0) / len(by_year[yr]) * 100, 1) for yr in years]

    # --- 全体 ---
    total_pnl  = sum(r['pnl'] for r in short)
    total_inv  = sum(inv(r) for r in short)
    total_wins = sum(1 for r in short if r['pnl'] > 0)
    total_lc   = sum(1 for r in short if 'ロスカット' in r['outcome'])
    avg_days   = sum(r['days_held'] for r in short) / len(short)

    # --- 結果区分別（短期勝ち / 短期LC / 中長期移行）---
    g_profit = [r for r in short if r['outcome'] == '利益確定']
    g_lc     = [r for r in short if 'ロスカット' in r['outcome']]
    g_trans  = [r for r in short if '移行' in r['outcome']]
    g_hold   = [r for r in short if '保有中' in r['outcome']]

    def group_stats(recs):
        if not recs:
            return {}
        pnl_sum   = sum(r['pnl'] for r in recs)
        inv_sum   = sum(inv(r) for r in recs)
        avg_rate  = sum(r['pnl_rate'] for r in recs) / len(recs)
        avg_d     = sum(r['days_held'] for r in recs) / len(recs)
        wins      = sum(1 for r in recs if r['pnl'] > 0)
        return {
            'count': len(recs),
            'ratio': len(recs) / len(short) * 100,
            'pnl': pnl_sum,
            'inv': inv_sum,
            'roi': pnl_sum / inv_sum * 100 if inv_sum else 0,
            'avg_rate': avg_rate,
            'avg_days': avg_d,
            'win_rate': wins / len(recs) * 100,
        }

    outcome_groups = {
        '短期利益確定': group_stats(g_profit),
        'ロスカット':   group_stats(g_lc),
        '中長期移行':   group_stats(g_trans),
        '保有中':       group_stats(g_hold),
    }

    # 移行後の中期・長期成績を紐付け
    mid_long_all = [r for r in p['results'] if r['strategy'] in ('中期', '長期')]
    trans_keys   = set((r['screen_date'], r['ticker']) for r in g_trans)
    matched_ml   = [r for r in mid_long_all if (r['screen_date'], r['ticker']) in trans_keys]

    trans_detail = {}
    for st in ('中期', '長期'):
        recs2 = [r for r in matched_ml if r['strategy'] == st]
        if recs2:
            pnl2  = sum(r['pnl'] for r in recs2)
            inv2  = sum(inv(r) for r in recs2)
            avg2  = sum(r['pnl_rate'] for r in recs2) / len(recs2)
            wins2 = sum(1 for r in recs2 if r['pnl'] > 0)
            trans_detail[st] = {
                'count': len(recs2),
                'pnl': pnl2,
                'roi': pnl2 / inv2 * 100 if inv2 else 0,
                'avg_rate': avg2,
                'win_rate': wins2 / len(recs2) * 100,
            }

    # 全区分合算ROI（短期損益 + 移行後中長期損益 を 総投資額で割る）
    combined_pnl = (
        sum(r['pnl'] for r in g_profit) +
        sum(r['pnl'] for r in g_lc) +
        sum(r['pnl'] for r in g_hold) +
        sum(r['pnl'] for r in matched_ml)
    )
    combined_inv = total_inv
    combined_roi = combined_pnl / combined_inv * 100 if combined_inv else 0

    # 全区分合算 平均損益率
    ml_rates = defaultdict(list)
    for r in matched_ml:
        ml_rates[(r['screen_date'], r['ticker'])].append(r['pnl_rate'])
    ml_avg_per = {k: sum(v) / len(v) for k, v in ml_rates.items()}
    rates_combined = []
    for r in short:
        key = (r['screen_date'], r['ticker'])
        if '移行' in r['outcome'] and key in ml_avg_per:
            rates_combined.append(ml_avg_per[key])
        else:
            rates_combined.append(r['pnl_rate'])
    combined_avg_rate = sum(rates_combined) / len(rates_combined) if rates_combined else 0

    # --- 平均利確期間 ---
    def holding_dist(recs):
        days = [r['days_held'] for r in recs]
        if not days:
            return {}
        buckets = [
            ('〜7日',   lambda d: d <= 7),
            ('8〜30日', lambda d: 8 <= d <= 30),
            ('31〜60日',lambda d: 31 <= d <= 60),
            ('61〜90日',lambda d: 61 <= d <= 90),
            ('91日〜',  lambda d: d >= 91),
        ]
        dist = []
        for label, fn in buckets:
            cnt = sum(1 for d in days if fn(d))
            dist.append({'label': label, 'count': cnt, 'pct': cnt / len(days) * 100})
        return {
            'avg': sum(days) / len(days),
            'min': min(days),
            'max': max(days),
            'dist': dist,
        }

    holding = {
        '短期利確':   holding_dist(g_profit),
        'ロスカット': holding_dist(g_lc),
        '中長期移行': holding_dist(g_trans),
        '全件':       holding_dist(short),
    }

    # 移行後の中長期利確日数
    for st in ('中期', '長期'):
        recs2 = [r for r in matched_ml if r['strategy'] == st and r['outcome'] == '利益確定']
        if recs2:
            days2 = [r['days_held'] for r in recs2]
            trans_detail[st]['avg_days_profit'] = sum(days2) / len(days2)
            trans_detail[st]['min_days_profit'] = min(days2)
            trans_detail[st]['max_days_profit'] = max(days2)
            trans_detail[st]['profit_count'] = len(recs2)

    # --- 保有期間別 年利分析（利確のみ / 利確+LC）---
    period_buckets = [
        ('〜7日',    lambda d: d <= 7),
        ('8〜15日',  lambda d: 8  <= d <= 15),
        ('16〜30日', lambda d: 16 <= d <= 30),
        ('31〜45日', lambda d: 31 <= d <= 45),
        ('46〜60日', lambda d: 46 <= d <= 60),
        ('61〜75日', lambda d: 61 <= d <= 75),
        ('76〜90日', lambda d: 76 <= d <= 90),
        ('91日〜',   lambda d: d >= 91),
    ]
    decided = [r for r in short if r['outcome'] == '利益確定' or 'ロスカット' in r['outcome']]
    period_stats = []
    for label, fn in period_buckets:
        p_recs = [r for r in g_profit if fn(r['days_held'])]
        d_recs = [r for r in decided  if fn(r['days_held'])]
        if not p_recs and not d_recs:
            continue
        # 利確のみ
        p_avg_d = sum(r['days_held'] for r in p_recs) / len(p_recs) if p_recs else 0
        p_avg_r = sum(r['pnl_rate']  for r in p_recs) / len(p_recs) if p_recs else 0
        p_simple   = p_avg_r / p_avg_d * 365 if p_avg_d else 0
        p_compound = ((1 + p_avg_r / 100) ** (365 / p_avg_d) - 1) * 100 if p_avg_d else 0
        # 利確+LC込み
        d_avg_d = sum(r['days_held'] for r in d_recs) / len(d_recs) if d_recs else 0
        d_avg_r = sum(r['pnl_rate']  for r in d_recs) / len(d_recs) if d_recs else 0
        d_simple   = d_avg_r / d_avg_d * 365 if d_avg_d else 0
        d_compound = ((1 + d_avg_r / 100) ** (365 / d_avg_d) - 1) * 100 if d_avg_d else 0
        lc_cnt  = sum(1 for r in d_recs if 'ロスカット' in r['outcome'])
        lc_rate = lc_cnt / len(d_recs) * 100 if d_recs else 0
        period_stats.append({
            'label': label,
            # 利確のみ
            'p_count': len(p_recs),
            'p_avg_days': p_avg_d,
            'p_avg_rate': p_avg_r,
            'p_simple': p_simple,
            'p_compound': p_compound,
            # 利確+LC
            'd_count': len(d_recs),
            'd_avg_days': d_avg_d,
            'd_avg_rate': d_avg_r,
            'd_simple': d_simple,
            'd_compound': d_compound,
            'lc_count': lc_cnt,
            'lc_rate': lc_rate,
        })

    return render_template('backtest_short.html',
        yearly=yearly,
        categories=categories,
        nanpin_stats=nanpin_stats,
        outcome_counts=dict(outcome_counts),
        trades=trades,
        total_pnl=total_pnl,
        total_inv=total_inv,
        total_wins=total_wins,
        total_count=len(short),
        total_lc=total_lc,
        avg_days=avg_days,
        chart_years=chart_years,
        chart_pnl=chart_pnl,
        chart_cumul=chart_cumul,
        chart_win_rates=chart_win_rates,
        outcome_groups=outcome_groups,
        trans_detail=trans_detail,
        combined_pnl=combined_pnl,
        combined_roi=combined_roi,
        combined_avg_rate=combined_avg_rate,
        holding=holding,
        period_stats=period_stats,
    )


# =============================================================================
# ルート: 仕様書準拠確認
# =============================================================================

@app.route('/spec_check')
def spec_check():
    """仕様書準拠確認ページ"""
    return render_template('spec_check.html')


# =============================================================================
# ルート: 設定
# =============================================================================

@app.route('/settings')
def settings():
    """
    設定画面
    """
    # 現在の設定値を取得
    from database import DEFAULT_SETTINGS
    current_settings = {
        key: get_setting(key) or DEFAULT_SETTINGS.get(key, '')
        for key in DEFAULT_SETTINGS
    }

    # 手動設定情報を取得
    manual_info_list = get_all_manual_info()

    # TOPIX全銘柄リスト
    from stock_data import ALL_TOPIX_STOCKS
    all_stocks = ALL_TOPIX_STOCKS

    return render_template(
        'settings.html',
        settings=current_settings,
        manual_info_list=manual_info_list,
        all_stocks=all_stocks,
    )


@app.route('/settings/update', methods=['POST'])
def settings_update():
    """
    設定を更新する
    """
    try:
        # 基本設定の更新
        form_data = request.form

        # 日経平均PBRの更新
        nikkei_pbr = form_data.get('nikkei_pbr')
        if nikkei_pbr:
            try:
                val = float(nikkei_pbr)
                if 0.5 <= val <= 5.0:
                    set_setting('nikkei_pbr', val)
                else:
                    flash('日経平均PBRは0.5〜5.0の範囲で入力してください', 'warning')
            except ValueError:
                flash('日経平均PBRの値が不正です', 'warning')

        # 目標配当利回り範囲
        target_yield_min = form_data.get('target_yield_min')
        target_yield_max = form_data.get('target_yield_max')
        if target_yield_min and target_yield_max:
            try:
                min_val = float(target_yield_min)
                max_val = float(target_yield_max)
                if min_val < max_val and 0.5 <= min_val <= 10 and 0.5 <= max_val <= 10:
                    set_setting('target_yield_min', min_val)
                    set_setting('target_yield_max', max_val)
                else:
                    flash('目標配当利回りの範囲が不正です', 'warning')
            except ValueError:
                flash('目標配当利回りの値が不正です', 'warning')

        # スクリーニング対象
        screening_target = form_data.get('screening_target')
        if screening_target in ['core30_only', 'core30_large70']:
            set_setting('screening_target', screening_target)

        # データ更新頻度
        update_interval = form_data.get('update_interval_minutes')
        if update_interval:
            try:
                val = int(update_interval)
                if 15 <= val <= 1440:
                    set_setting('update_interval_minutes', val)
                else:
                    flash('更新頻度は15〜1440分の範囲で入力してください', 'warning')
            except ValueError:
                pass

        # 数値設定の一括保存（新規追加設定キー群）
        _num_settings = {
            'short_anshin_pbr':       (0.1,  5.0),
            'short_anshin_yield':     (0.0,  20.0),
            'short_nanpin1':          (1.0,  50.0),
            'short_nanpin2':          (1.0,  50.0),
            'short_nanpin3':          (1.0,  50.0),
            'short_profit_normal':    (1.0,  100.0),
            'short_profit_nanpin':    (1.0,  100.0),
            'short_loss_cut':         (1.0,  100.0),
            'short_holding_days':     (7,    365),
            'mid_intl_black_coeff':   (0.1,  2.0),
            'mid_intl_red_coeff':     (0.05, 2.0),
            'mid_fin_black_coeff':    (0.1,  2.0),
            'mid_fin_red_coeff':      (0.05, 2.0),
            'intl_bps_min':           (0,    100000),
            'intl_equity_ratio_min':  (0,    100),
            'fin_net_assets_min':     (0,    100000),
            'fin_bps_min':            (0,    100000),
            'fin_equity_ratio_min':   (0,    100),
            'eps_revision_threshold': (0.0,  100.0),
        }
        for key, (lo, hi) in _num_settings.items():
            raw = form_data.get(key)
            if raw is not None and raw != '':
                try:
                    val = float(raw)
                    if lo <= val <= hi:
                        set_setting(key, val)
                except ValueError:
                    flash(f'{key} の値が不正です', 'warning')

        # 銘柄ごとの手動設定を更新
        # ticker_XXXXの形式でフォームデータを取得
        tickers_set = set()
        for key in form_data.keys():
            if key.startswith('overseas_ratio_'):
                ticker = key.replace('overseas_ratio_', '')
                tickers_set.add(ticker)

        for ticker in tickers_set:
            overseas_ratio = form_data.get(f'overseas_ratio_{ticker}')
            stable_ratio = form_data.get(f'stable_ratio_{ticker}')
            notes = form_data.get(f'notes_{ticker}', '')

            try:
                overseas_val = float(overseas_ratio) if overseas_ratio else None
                stable_val = float(stable_ratio) if stable_ratio else None

                save_manual_info(
                    ticker=ticker,
                    overseas_sales_ratio=overseas_val,
                    stable_shareholder_ratio=stable_val,
                    notes=notes
                )
            except ValueError:
                flash(f'{ticker}の手動設定値が不正です', 'warning')

        flash('設定を保存しました', 'success')

        # スクリーニングキャッシュをクリア（設定変更後は再スクリーニング）
        with _screening_lock:
            _screening_cache['data'] = None
            _screening_cache['updated_at'] = None

    except Exception as e:
        flash(f'設定保存エラー: {str(e)}', 'danger')
        traceback.print_exc()

    return redirect(url_for('settings'))


# =============================================================================
# API: データ更新
# =============================================================================

@app.route('/api/refresh_data')
def api_refresh_data():
    """
    株価データを強制更新してスクリーニングを実行する
    """
    try:
        # バックグラウンドで実行（UIをブロックしない）
        def refresh_async():
            global _screening_cache
            results = get_screening_results(force_refresh=True)
            with _screening_lock:
                _screening_cache['data'] = results
                _screening_cache['updated_at'] = datetime.now()

        thread = threading.Thread(target=refresh_async, daemon=True)
        thread.start()

        return jsonify({
            'status': 'started',
            'message': 'データ更新を開始しました。数分後にページを再読み込みしてください。',
        })

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/portfolio_alerts')
def api_portfolio_alerts():
    """
    ポートフォリオのアラートを取得するAPI
    ポーリング用（5分ごとに呼び出される）
    """
    try:
        # アクティブなポートフォリオを取得
        portfolio_list = get_portfolio(status='active')

        # 現在の株価を取得
        current_prices = {}
        for entry in portfolio_list:
            ticker = entry['ticker']
            info = get_stock_info(ticker)  # キャッシュ利用
            if info:
                current_prices[ticker] = info.get('current_price')

        # アラート生成
        alerts = generate_portfolio_alerts(portfolio_list, current_prices)

        return jsonify({
            'status': 'success',
            'alerts': alerts,
            'count': len(alerts),
            'updated_at': datetime.now().isoformat(),
        })

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/stock_info/<ticker>')
def api_stock_info(ticker):
    """
    指定銘柄の最新情報をJSON返却する
    """
    try:
        info = get_stock_info(ticker)
        if info:
            return jsonify({'status': 'success', 'data': info})
        else:
            return jsonify({'status': 'error', 'error': '銘柄データが取得できませんでした'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/screening_status')
def api_screening_status():
    """
    スクリーニングの実行状態を返す
    """
    with _screening_lock:
        if _screening_cache['updated_at']:
            return jsonify({
                'status': 'ready',
                'updated_at': _screening_cache['updated_at'].isoformat(),
                'anshin_count': len(_screening_cache.get('data', {}).get('short_anshin_waribari', [])) if _screening_cache['data'] else 0,
            })
        else:
            return jsonify({'status': 'loading'})


# =============================================================================
# エラーハンドラ
# =============================================================================

@app.errorhandler(404)
def not_found(e):
    """404エラーページ"""
    return render_template('base.html', error_code=404,
                           error_message='ページが見つかりません'), 404


@app.errorhandler(500)
def server_error(e):
    """500エラーページ"""
    return render_template('base.html', error_code=500,
                           error_message='サーバーエラーが発生しました'), 500


# =============================================================================
# アプリ起動
# =============================================================================

def _prewarm_info_cache():
    """
    アプリ起動時にバックグラウンドで t.info キャッシュを温める。
    キャッシュがない銘柄だけ取得するため、2回目以降の起動は高速。
    """
    import yfinance as yf
    from stock_data import TOPIX_QUICK15
    from stock_data import _to_yfinance_ticker
    from database import get_fundamentals_cache, save_fundamentals_cache

    miss = [(c, nm) for c, nm in TOPIX_QUICK15
            if get_fundamentals_cache(c) is None]
    if not miss:
        print("[キャッシュ] Quick15 の財務指標は全てキャッシュ済みです")
        return

    print(f"[キャッシュ] 起動時プレウォーム: {len(miss)}銘柄の t.info を取得中...")
    for code, name in miss:
        try:
            info = yf.Ticker(_to_yfinance_ticker(code)).info
            save_fundamentals_cache(code, info)
            print(f"[キャッシュ] {code} {name} 保存完了")
        except Exception as e:
            print(f"[キャッシュ] {code} 取得失敗: {e}")


if __name__ == '__main__':
    # データベースの初期化
    print("データベースを初期化中...")
    init_db()

    # バックグラウンドで財務指標キャッシュを温める（初回リクエストを高速化）
    threading.Thread(target=_prewarm_info_cache, daemon=True).start()

    print("Flask サーバーを起動します...")
    print("アクセス URL: http://127.0.0.1:5001")
    print("Ctrl+C で停止")

    app.run(
        debug=True,
        host='0.0.0.0',
        port=5001,
        threaded=True,  # 非同期リクエスト処理
        use_reloader=False,  # reloaderを無効化（プレウォームを1回だけ実行）
    )
