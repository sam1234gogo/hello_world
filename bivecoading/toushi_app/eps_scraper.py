"""
eps_scraper.py - 株探から通期予想EPS修正履歴を取得する

無料で取得できるデータ:
  - 現在の決算期: 全修正履歴（発表日・最終益・修正方向）
  - 過去実績: 年次EPS（発表日付き）
  - 古い決算期(FY2021以前): 全修正履歴

ペイウォール: FY2022〜前期の中間修正値（発表日のみ取得可）
"""

import re
import time
import calendar
import logging
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
    ),
    'Accept-Language': 'ja,en;q=0.9',
}
_SCRAPE_INTERVAL = 1.0   # リクエスト間隔（秒）
_last_fetch_time = {}


def _throttle(key='kabutan'):
    elapsed = time.time() - _last_fetch_time.get(key, 0)
    if elapsed < _SCRAPE_INTERVAL:
        time.sleep(_SCRAPE_INTERVAL - elapsed)
    _last_fetch_time[key] = time.time()


def _to_float(text):
    if not text:
        return None
    t = re.sub(r'[,，\s]', '', text.strip())
    if t in ('---', '－', '-', '—', '−', '', 'N/A', '‐'):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_ym_to_date(text):
    """
    '2026.03' → '2026-03-31'
    '22.03'   → '2022-03-31'（年2桁は2000年代）
    失敗時 None
    """
    text = text.strip()
    m = re.match(r'(\d{2,4})[./](\d{1,2})', text)
    if not m:
        return None
    yr, mo = int(m.group(1)), int(m.group(2))
    if yr < 100:
        yr += 2000
    last_day = calendar.monthrange(yr, mo)[1]
    return f'{yr:04d}-{mo:02d}-{last_day:02d}'


def _parse_announce_date(text):
    """
    '25/05/08' or '2025/05/08' → '2025-05-08'
    失敗時 None
    """
    text = text.strip()
    m = re.match(r'(\d{2,4})[/](\d{2})[/](\d{2})', text)
    if not m:
        return None
    yr, mo, dy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if yr < 100:
        yr += 2000
    return f'{yr:04d}-{mo:02d}-{dy:02d}'


def _compute_eps(net_income_m, shares_m):
    """最終益（百万円）÷ 発行済株式数（百万株）→ EPS（円）"""
    if net_income_m and shares_m and shares_m > 0:
        return round(net_income_m / shares_m, 2)
    return None


def scrape_eps_forecast_history(ticker_code):
    """
    株探から通期予想EPS修正履歴を取得する。

    Returns: list of {
        'fiscal_year_end':    '2026-03-31',
        'announcement_date':  '2025-05-08',
        'revision_type':      'initial' | 'revision' | 'actual',
        'net_income_m':       3100000.0,   # 百万円（不明時 None）
        'eps':                233.9,        # 円（不明時 None）
    }

    取得可能範囲:
      - 現在進行中の決算期: 全修正エントリ（値あり）
      - 旧決算期(FY2021以前): 全修正エントリ（値あり）
      - 旧決算期(FY2022〜前期): 発表日のみ（値はペイウォール）
      - 年次実績: 過去実績EPS（発表日付き）
    """
    _throttle()
    url = f'https://kabutan.jp/stock/finance?code={ticker_code}&oto=2'
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or 'utf-8'
    except Exception as e:
        raise RuntimeError(f'kabutan fetch failed for {ticker_code}: {e}')

    soup = BeautifulSoup(resp.text, 'html.parser')
    records = []

    # ── 1. 年次実績テーブルから shares を推定 ──────────────────────────
    # shares_m = 最終益(百万円) / EPS(円) → 株式数（百万株）
    shares_m = None
    result_div = soup.find('div', class_='fin_year_result_d')
    annual_eps_records = []
    if result_div:
        tbl = result_div.find('table')
        if tbl:
            rows = tbl.find_all('tr')
            # ヘッダー行から列インデックスを特定
            hdr = [th.get_text(strip=True) for th in rows[0].find_all(['th', 'td'])] if rows else []
            ni_idx  = next((i for i, h in enumerate(hdr) if '最終益' in h), None)
            eps_idx = next((i for i, h in enumerate(hdr) if '1株益' in h or 'EPS' in h), None)
            ann_idx = next((i for i, h in enumerate(hdr) if '発表日' in h), None)
            per_idx = 0   # 決算期は0列目

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
                if len(cells) <= max(filter(None, [ni_idx, eps_idx, ann_idx, per_idx]), default=0):
                    continue

                period_text = cells[per_idx] if per_idx < len(cells) else ''
                is_forecast = '予' in period_text
                fiscal_end  = _parse_ym_to_date(re.sub(r'[^\d./]', '', period_text))
                if not fiscal_end:
                    continue

                ni_val  = _to_float(cells[ni_idx])  if ni_idx  is not None and ni_idx  < len(cells) else None
                eps_val = _to_float(cells[eps_idx]) if eps_idx is not None and eps_idx < len(cells) else None
                ann_dt  = _parse_announce_date(cells[ann_idx]) if ann_idx is not None and ann_idx < len(cells) else None

                if not ann_dt:
                    continue

                # shares の推定（最終益とEPSが両方あれば）
                if ni_val and eps_val and eps_val != 0 and shares_m is None:
                    shares_m = ni_val / eps_val   # 百万円 / 円 = 百万株

                rev_type = 'initial' if is_forecast else 'actual'
                annual_eps_records.append({
                    'fiscal_year_end':   fiscal_end,
                    'announcement_date': ann_dt,
                    'revision_type':     rev_type,
                    'net_income_m':      ni_val,
                    'eps':               eps_val,
                })

    records.extend(annual_eps_records)

    # ── 2. 通期予想修正履歴テーブルをパース ──────────────────────────────
    forecast_div = soup.find('div', class_='fin_year_forecast_d')
    if forecast_div:
        tbl = forecast_div.find('table')
        if tbl:
            rows = tbl.find_all('tr')
            current_fy = None

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
                if not cells or all(c == '' for c in cells):
                    continue

                # ペイウォールメッセージが含まれる行はスキップ
                if any('プレミアム' in c or 'ログイン' in c for c in cells):
                    continue

                # 決算期の更新（'2026.03' のような4桁年/月 パターン）
                fy_match = re.search(r'(\d{4}[./]\d{2})', ' '.join(cells[:3]))
                if fy_match:
                    current_fy = _parse_ym_to_date(fy_match.group(1))

                if not current_fy:
                    continue

                # 修正日を全セルから探す（YY/MM/DD or YYYY/MM/DD）
                ann_dt = None
                for cell in cells:
                    dt = _parse_announce_date(cell)
                    if dt:
                        ann_dt = dt
                        break

                # 修正種別
                rev_type = None
                for cell in cells:
                    if cell in ('初', '初回'):
                        rev_type = 'initial'
                        break
                    if cell in ('実', '実績'):
                        rev_type = 'actual'
                        break
                    if cell == '修':
                        rev_type = 'revision'
                        break

                if not ann_dt or not rev_type:
                    continue

                # 最終益は末尾から2番目のセル
                # HTML構造: [..., 売上高, 営業益, 経常益, 最終益, 配当]
                # 初 row (15 cells) も 修/実 row (14 cells) も cells[-2] = 最終益
                ni_val  = _to_float(cells[-2]) if len(cells) >= 5 else None
                eps_val = _compute_eps(ni_val, shares_m) if ni_val else None

                # 既に annual_eps_records に同じエントリがあれば重複追加しない
                is_dup = any(
                    r['fiscal_year_end'] == current_fy and r['announcement_date'] == ann_dt
                    for r in records
                )
                if not is_dup:
                    records.append({
                        'fiscal_year_end':   current_fy,
                        'announcement_date': ann_dt,
                        'revision_type':     rev_type,
                        'net_income_m':      ni_val,
                        'eps':               eps_val,
                    })

    # announcement_date 昇順でソート
    records.sort(key=lambda x: (x['fiscal_year_end'], x['announcement_date']))
    return records
