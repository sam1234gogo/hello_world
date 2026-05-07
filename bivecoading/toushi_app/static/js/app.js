/**
 * app.js - 株式投資アシスタント フロントエンドJavaScript
 * データ更新、アラートポーリング、グラフ描画、フォームバリデーション
 */

'use strict';

// =============================================================================
// グローバル設定
// =============================================================================
const CONFIG = {
    ALERT_POLL_INTERVAL: 5 * 60 * 1000,  // 5分ごとのアラートポーリング（ミリ秒）
    REFRESH_TIMEOUT: 30000,              // データ更新のタイムアウト（30秒）
    STOCK_INFO_DEBOUNCE: 800,            // 株価情報取得のデバウンス時間（ms）
};

// デバウンス用タイマー
let _debounceTimers = {};

// アラートポーリングのインターバルID
let _alertPollingInterval = null;


// =============================================================================
// データ更新
// =============================================================================

/**
 * データ更新ボタンのクリックハンドラ
 * 非同期でスクリーニングを実行し、完了後にページをリロードする
 */
async function refreshData() {
    const btn = document.getElementById('btn-refresh') ||
                document.getElementById('btn-refresh-main');

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>更新中...';
    }

    try {
        const response = await fetch('/api/refresh_data', {
            method: 'GET',
            headers: {'X-Requested-With': 'XMLHttpRequest'},
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.status === 'started') {
            // 更新開始を通知
            showToast('データ更新を開始しました。しばらくお待ちください...', 'info');

            // 30秒後にページをリロード（スクリーニング完了待ち）
            setTimeout(() => {
                showToast('スクリーニング結果を更新しています...', 'success');
                window.location.reload();
            }, CONFIG.REFRESH_TIMEOUT);

        } else if (data.status === 'error') {
            throw new Error(data.error || 'データ更新に失敗しました');
        }

    } catch (error) {
        console.error('データ更新エラー:', error);
        showToast('データ更新に失敗しました: ' + error.message, 'danger');

        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>データ更新';
        }
    }
}


// =============================================================================
// ポートフォリオアラートのポーリング
// =============================================================================

/**
 * ポートフォリオアラートを定期的にチェックする（5分ごと）
 * ポートフォリオページでのみ実行される
 */
function startAlertPolling() {
    // ポートフォリオページでのみポーリングを開始
    if (!document.querySelector('.portfolio-page')) {
        return;
    }

    console.log('アラートポーリングを開始します（5分ごと）');

    // 即座に1回チェック
    checkPortfolioAlerts();

    // 5分ごとにポーリング
    _alertPollingInterval = setInterval(checkPortfolioAlerts, CONFIG.ALERT_POLL_INTERVAL);
}

/**
 * ポートフォリオアラートを取得してUIを更新する
 */
async function checkPortfolioAlerts() {
    try {
        const response = await fetch('/api/portfolio_alerts', {
            method: 'GET',
            headers: {'X-Requested-With': 'XMLHttpRequest'},
        });

        if (!response.ok) {
            console.warn('アラート取得失敗:', response.status);
            return;
        }

        const data = await response.json();

        if (data.status === 'success' && data.count > 0) {
            // 新しいアラートがあれば通知
            updateAlertCount(data.count);

            // ブラウザ通知（許可されている場合）
            if (data.count > 0 && Notification.permission === 'granted') {
                const dangerAlerts = data.alerts.filter(a => a.level === 'danger');
                if (dangerAlerts.length > 0) {
                    new Notification('【重要】投資アシスタント', {
                        body: dangerAlerts[0].message,
                        icon: '/static/favicon.ico',
                    });
                }
            }
        }

        // 最終更新時刻を表示
        const updateTimeEl = document.getElementById('last-update-time');
        if (updateTimeEl) {
            const now = new Date();
            updateTimeEl.textContent = now.toLocaleTimeString('ja-JP');
        }

    } catch (error) {
        console.error('アラートポーリングエラー:', error);
    }
}

/**
 * ナビゲーションバーのアラートカウントを更新する
 */
function updateAlertCount(count) {
    const alertBadge = document.getElementById('alert-count-badge');
    if (alertBadge) {
        alertBadge.textContent = count;
        alertBadge.classList.toggle('d-none', count === 0);
    }
}


// =============================================================================
// 株価情報の取得（フォーム入力補助）
// =============================================================================

/**
 * 銘柄コードから株価情報を取得してフォームに自動入力する
 * デバウンス処理で連続入力時のAPIコールを防ぐ
 * @param {string} ticker - 銘柄コード
 * @param {string} formId - 対象フォームのID（省略可）
 */
function fetchStockInfoForForm(ticker, formId = '') {
    const key = `stock-info-${formId}`;

    // デバウンス処理
    clearTimeout(_debounceTimers[key]);
    _debounceTimers[key] = setTimeout(async () => {
        if (!ticker || ticker.length < 4) return;

        try {
            const response = await fetch(`/api/stock_info/${ticker.trim()}`);
            if (!response.ok) return;

            const data = await response.json();
            if (data.status !== 'success' || !data.data) return;

            const info = data.data;

            // フォームに自動入力
            const prefix = formId ? `${formId}-` : '';
            autoFillField(`${prefix}stock-name`, info.stock_name);
            autoFillField(`${prefix}price`, info.current_price);

            // プレビューパネルを更新
            updateStockPreview(info);

        } catch (error) {
            console.error('株価情報取得エラー:', error);
        }
    }, CONFIG.STOCK_INFO_DEBOUNCE);
}

/**
 * フォームフィールドに値を自動入力する（既存値がある場合は上書きしない）
 * @param {string} fieldId - フィールドID
 * @param {*} value - 入力する値
 */
function autoFillField(fieldId, value) {
    const el = document.getElementById(fieldId);
    if (el && value !== null && value !== undefined) {
        if (!el.value || el.dataset.autoFilled) {
            el.value = value;
            el.dataset.autoFilled = 'true';
        }
    }
}

/**
 * 株価情報プレビューパネルを更新する
 * @param {Object} info - 株価情報オブジェクト
 */
function updateStockPreview(info) {
    const preview = document.getElementById('stock-info-preview');
    if (!preview) return;

    // 各フィールドを更新
    updatePreviewField('preview-price', info.current_price, v => `¥${v.toLocaleString()}`);
    updatePreviewField('preview-pbr', info.pbr, v => `${v.toFixed(2)}倍`);
    updatePreviewField('preview-per', info.per, v => `${v.toFixed(1)}倍`);
    updatePreviewField('preview-div', info.dividend_yield, v => `${v.toFixed(2)}%`);

    preview.classList.remove('d-none');
}

/**
 * プレビューフィールドの値を更新する
 */
function updatePreviewField(elementId, value, formatter) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.textContent = (value !== null && value !== undefined)
        ? formatter(value) : 'N/A';
}


// =============================================================================
// シミュレーション（Plotlyグラフ描画）
// =============================================================================

/**
 * 損益曲線をPlotlyで描画する
 * @param {Array} equityCurve - [{date, value}, ...] の配列
 * @param {number} initialCapital - 初期資金
 * @param {string} containerId - グラフコンテナのID
 */
function drawEquityCurveChart(equityCurve, initialCapital, containerId = 'equity-chart') {
    if (!equityCurve || equityCurve.length === 0) return;

    const dates = equityCurve.map(p => p.date);
    const values = equityCurve.map(p => p.value);
    const finalValue = values[values.length - 1];
    const isProfit = finalValue >= initialCapital;

    // 参照線（初期資金）
    const refLine = Array(dates.length).fill(initialCapital);

    // エクイティカーブのトレース
    const traceEquity = {
        x: dates,
        y: values,
        type: 'scatter',
        mode: 'lines',
        name: 'ポートフォリオ価値',
        line: {
            color: isProfit ? '#28a745' : '#dc3545',
            width: 2.5,
        },
        fill: 'tonexty',
        fillcolor: isProfit ? 'rgba(40, 167, 69, 0.12)' : 'rgba(220, 53, 69, 0.12)',
        hovertemplate: '%{x}<br>¥%{y:,.0f}<extra></extra>',
    };

    // 初期資金の参照線
    const traceRef = {
        x: dates,
        y: refLine,
        type: 'scatter',
        mode: 'lines',
        name: `初期資金 ¥${initialCapital.toLocaleString()}`,
        line: {
            color: '#6c757d',
            width: 1.5,
            dash: 'dash',
        },
        hoverinfo: 'skip',
    };

    // グラフレイアウト（ダークテーマ）
    const layout = {
        paper_bgcolor: '#1a1e21',
        plot_bgcolor: '#1a1e21',
        font: {color: '#dee2e6', family: 'sans-serif'},
        xaxis: {
            gridcolor: '#2d3238',
            tickfont: {color: '#adb5bd', size: 11},
            showline: true,
            linecolor: '#495057',
            tickformat: '%Y/%m',
        },
        yaxis: {
            gridcolor: '#2d3238',
            tickfont: {color: '#adb5bd', size: 11},
            tickformat: ',.0f',
            showline: true,
            linecolor: '#495057',
            hoverformat: '¥,.0f',
        },
        legend: {
            bgcolor: 'rgba(0,0,0,0)',
            font: {color: '#dee2e6', size: 12},
            x: 0,
            y: 1.05,
            orientation: 'h',
        },
        margin: {t: 30, b: 50, l: 90, r: 20},
        hovermode: 'x unified',
    };

    const config = {
        responsive: true,
        displayModeBar: true,
        modeBarButtonsToRemove: ['autoScale2d', 'lasso2d', 'select2d'],
        displaylogo: false,
    };

    Plotly.newPlot(containerId, [traceRef, traceEquity], layout, config);
}

/**
 * トレード分布グラフ（勝ち負けの損益率分布）を描画する
 * @param {Array} trades - トレードデータの配列
 * @param {string} containerId - グラフコンテナID
 */
function drawTradeProfitDistribution(trades, containerId) {
    if (!trades || trades.length === 0) return;

    const winRates = trades.filter(t => t.result === 'WIN').map(t => t.profit_rate);
    const lossRates = trades.filter(t => t.result === 'LOSS').map(t => t.profit_rate);

    const traceWin = {
        x: winRates,
        type: 'histogram',
        name: '利益トレード',
        marker: {color: 'rgba(40, 167, 69, 0.7)'},
        nbinsx: 10,
    };

    const traceLoss = {
        x: lossRates,
        type: 'histogram',
        name: '損失トレード',
        marker: {color: 'rgba(220, 53, 69, 0.7)'},
        nbinsx: 10,
    };

    const layout = {
        paper_bgcolor: '#1a1e21',
        plot_bgcolor: '#1a1e21',
        font: {color: '#dee2e6'},
        barmode: 'overlay',
        xaxis: {
            title: '損益率 (%)',
            gridcolor: '#2d3238',
            tickfont: {color: '#adb5bd'},
        },
        yaxis: {
            title: 'トレード数',
            gridcolor: '#2d3238',
            tickfont: {color: '#adb5bd'},
        },
        legend: {
            bgcolor: 'rgba(0,0,0,0)',
            font: {color: '#dee2e6'},
        },
        margin: {t: 20, b: 50, l: 60, r: 20},
    };

    Plotly.newPlot(containerId, [traceLoss, traceWin], layout, {responsive: true});
}


// =============================================================================
// トースト通知
// =============================================================================

/**
 * トースト通知を表示する
 * @param {string} message - メッセージ
 * @param {string} type - タイプ（success/danger/warning/info）
 * @param {number} duration - 表示時間（ms）
 */
function showToast(message, type = 'info', duration = 4000) {
    // トーストコンテナを作成または取得
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'position-fixed top-0 end-0 p-3';
        container.style.zIndex = '9999';
        document.body.appendChild(container);
    }

    // トースト要素を作成
    const toastId = `toast-${Date.now()}`;
    const iconMap = {
        success: 'bi-check-circle-fill',
        danger: 'bi-exclamation-triangle-fill',
        warning: 'bi-exclamation-circle-fill',
        info: 'bi-info-circle-fill',
    };
    const icon = iconMap[type] || iconMap.info;

    const toastHtml = `
        <div id="${toastId}" class="toast align-items-center text-bg-${type} border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body">
                    <i class="bi ${icon} me-2"></i>${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto"
                        data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', toastHtml);

    // Bootstrap Toastを初期化して表示
    const toastEl = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastEl, {delay: duration, autohide: true});
    toast.show();

    // 非表示後に要素を削除
    toastEl.addEventListener('hidden.bs.toast', () => {
        toastEl.remove();
    });
}


// =============================================================================
// フォームバリデーション
// =============================================================================

/**
 * ポートフォリオ追加フォームのバリデーション
 * @param {HTMLFormElement} form - フォーム要素
 * @returns {boolean} バリデーション結果
 */
function validatePortfolioForm(form) {
    const ticker = form.querySelector('[name="ticker"]')?.value?.trim();
    const price = parseFloat(form.querySelector('[name="purchase_price"]')?.value || 0);
    const shares = parseInt(form.querySelector('[name="shares"]')?.value || 0);
    const date = form.querySelector('[name="purchase_date"]')?.value;

    const errors = [];

    if (!ticker || ticker.length < 1) {
        errors.push('銘柄コードを入力してください');
    }
    if (price <= 0) {
        errors.push('購入単価は0より大きい値を入力してください');
    }
    if (shares <= 0) {
        errors.push('株数は0より大きい値を入力してください');
    }
    if (!date) {
        errors.push('購入日を入力してください');
    } else {
        const d = new Date(date);
        const today = new Date();
        if (d > today) {
            errors.push('購入日は今日以前の日付を入力してください');
        }
    }

    if (errors.length > 0) {
        showToast(errors.join('\n'), 'warning');
        return false;
    }
    return true;
}

/**
 * ナンピンフォームのバリデーション
 * @param {HTMLFormElement} form - フォーム要素
 * @returns {boolean} バリデーション結果
 */
function validateNanpinForm(form) {
    const price = parseFloat(form.querySelector('[name="nanpin_price"]')?.value || 0);
    const shares = parseInt(form.querySelector('[name="nanpin_shares"]')?.value || 0);

    if (price <= 0) {
        showToast('ナンピン価格は0より大きい値を入力してください', 'warning');
        return false;
    }
    if (shares <= 0) {
        showToast('購入株数は0より大きい値を入力してください', 'warning');
        return false;
    }
    return true;
}

/**
 * 売却フォームのバリデーション
 * @param {HTMLFormElement} form - フォーム要素
 * @returns {boolean} バリデーション結果
 */
function validateSellForm(form) {
    const price = parseFloat(form.querySelector('[name="sell_price"]')?.value || 0);

    if (price <= 0) {
        showToast('売却価格は0より大きい値を入力してください', 'warning');
        return false;
    }
    return true;
}


// =============================================================================
// ブラウザ通知の許可リクエスト
// =============================================================================

/**
 * ブラウザプッシュ通知の許可をリクエストする
 */
async function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        const permission = await Notification.requestPermission();
        if (permission === 'granted') {
            showToast('ブラウザ通知を有効にしました', 'success');
        }
    }
}


// =============================================================================
// ユーティリティ関数
// =============================================================================

/**
 * 数値を日本円フォーマットで表示する
 * @param {number} value - 金額
 * @returns {string} フォーマットされた文字列
 */
function formatYen(value) {
    if (value === null || value === undefined) return 'N/A';
    return `¥${Math.round(value).toLocaleString('ja-JP')}`;
}

/**
 * 損益率をフォーマットして色付きHTMLを返す
 * @param {number} rate - 損益率（%）
 * @returns {string} HTML文字列
 */
function formatPnlRate(rate) {
    if (rate === null || rate === undefined) return '-';
    const color = rate >= 0 ? 'text-success' : 'text-danger';
    const sign = rate >= 0 ? '+' : '';
    return `<span class="${color} fw-bold">${sign}${rate.toFixed(2)}%</span>`;
}

/**
 * 日付文字列を日本語フォーマットに変換する
 * @param {string} dateStr - YYYY-MM-DD形式の日付文字列
 * @returns {string} 日本語形式の日付文字列
 */
function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleDateString('ja-JP', {year: 'numeric', month: 'long', day: 'numeric'});
}

/**
 * デバウンス関数
 * @param {Function} fn - 実行する関数
 * @param {number} delay - 遅延時間（ms）
 * @returns {Function} デバウンスされた関数
 */
function debounce(fn, delay) {
    let timer;
    return function(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}


// =============================================================================
// 初期化処理
// =============================================================================

/**
 * ページ読み込み完了時の初期化
 */
document.addEventListener('DOMContentLoaded', function() {
    // アラートポーリングの開始（ポートフォリオページのみ）
    startAlertPolling();

    // フォームバリデーションのセットアップ
    setupFormValidation();

    // ツールチップの初期化
    const tooltipTriggerList = [].slice.call(
        document.querySelectorAll('[data-bs-toggle="tooltip"]')
    );
    tooltipTriggerList.forEach(el => new bootstrap.Tooltip(el));

    // 最終更新時刻の表示
    const updateTimeEl = document.getElementById('last-update-time');
    if (updateTimeEl) {
        updateTimeEl.textContent = new Date().toLocaleTimeString('ja-JP');
    }

    // ブラウザ通知の許可リクエスト（ポートフォリオページのみ）
    if (document.querySelector('.portfolio-page') && 'Notification' in window) {
        requestNotificationPermission();
    }

    console.log('株式投資アシスタント 初期化完了');
});

/**
 * フォームバリデーションをセットアップする
 */
function setupFormValidation() {
    // ポートフォリオ追加フォーム
    const addForm = document.getElementById('addPortfolioForm');
    if (addForm) {
        addForm.addEventListener('submit', function(e) {
            if (!validatePortfolioForm(this)) {
                e.preventDefault();
            }
        });
    }

    // ナンピンフォーム
    const nanpinForm = document.querySelector('form[action*="nanpin"]');
    if (nanpinForm) {
        nanpinForm.addEventListener('submit', function(e) {
            if (!validateNanpinForm(this)) {
                e.preventDefault();
            }
        });
    }

    // 売却フォーム
    const sellForm = document.querySelector('form[action*="sell"]');
    if (sellForm) {
        sellForm.addEventListener('submit', function(e) {
            if (!validateSellForm(this)) {
                e.preventDefault();
            }
        });
    }
}

/**
 * ページ離脱時のクリーンアップ
 */
window.addEventListener('beforeunload', function() {
    if (_alertPollingInterval) {
        clearInterval(_alertPollingInterval);
    }
});
