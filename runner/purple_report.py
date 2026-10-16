"""TradingView watchlist rankings. Keep exact instruments internally, omit exchange names in Slack."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import re
import time
import urllib.request

from cloud_client import CloudError, request
from upbit_report import KST

SCANNER = 'https://scanner.tradingview.com/crypto/scan'
COLUMNS = ['name', 'description', 'base_currency', 'close', 'change', '24h_vol|5', 'currency', 'type']
SYMBOL = re.compile(r'^[A-Z0-9_]+:[A-Za-z0-9_.!/-]+$')


def fetch_watchlist():
    for attempt in range(3):
        try:
            value = request('/integrations/tradingview/watchlist', {})
            symbols = value.get('symbols')
            if not isinstance(symbols, list) or not 1 <= len(symbols) <= 2000:
                raise CloudError('Empty or invalid purple watchlist')
            if any(not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in symbols):
                raise CloudError('Invalid watchlist symbol')
            return {**value, 'symbols': list(dict.fromkeys(symbols))}
        except CloudError as exc:
            if str(exc) != 'Worker HTTP 503' or attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def fetch_quotes(symbols):
    result = []
    # Small batches bound response size and keep the same exact symbol throughout.
    for start in range(0, len(symbols), 100):
        batch = symbols[start:start + 100]
        body = {'symbols': {'tickers': batch}, 'columns': COLUMNS, 'range': [0, 100]}
        for attempt in range(3):
            try:
                req = urllib.request.Request(SCANNER, data=json.dumps(body).encode(),
                    headers={'Content-Type': 'application/json', 'User-Agent': 'cryptobot-watchlist/1.0'}, method='POST')
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.load(response)
                if not isinstance(data.get('data'), list):
                    raise ValueError('Invalid scanner response')
                result.extend(data['data'])
                break
            except (OSError, ValueError):
                if attempt == 2:
                    raise CloudError('TradingView quotes unavailable') from None
                time.sleep(2 * (attempt + 1))
    return result


def number(value, *, nonnegative=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and (not nonnegative or n >= 0) else None
    except InvalidOperation:
        return None


def normalize(symbols, data):
    wanted, seen, quotes, missing = set(symbols), set(), [], []
    for item in data:
        symbol, values = item.get('s'), item.get('d')
        if symbol not in wanted or symbol in seen:
            raise CloudError('Unexpected or duplicate TradingView instrument')
        seen.add(symbol)
        if not isinstance(values, list) or len(values) != len(COLUMNS):
            raise CloudError('Invalid TradingView quote columns')
        q = dict(zip(COLUMNS, values))
        # Index/stock/list separator entries are not crypto quotes.
        if q['type'] not in ('spot', 'swap'):
            missing.append(symbol)
            continue
        price = number(q['close'], nonnegative=True)
        change = number(q['change'])
        if price is None or price == 0 or change is None:
            missing.append(symbol)
            continue
        base, currency = q['base_currency'], q['currency']
        if not isinstance(base, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', base):
            missing.append(symbol)
            continue
        if not isinstance(currency, str) or not re.fullmatch(r'[A-Z0-9]{2,12}', currency):
            missing.append(symbol)
            continue
        quotes.append({'symbol': symbol, 'base': base, 'currency': currency, 'kind': q['type'],
            'price': price, 'change': change, 'turnover': number(q['24h_vol|5'], nonnegative=True)})
    missing += [s for s in symbols if s not in seen]
    return quotes, missing


def price_text(value):
    text = format(value, ',f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def build_message(quotes, symbols, missing, stamp, ranking, count=20):
    field = 'change' if ranking == 'gainers' else 'turnover'
    available = [q for q in quotes if q[field] is not None]
    available.sort(key=lambda q: (-q[field], q['symbol']))
    if not available:
        raise CloudError('No valid purple ranking data')
    selected = available[:count]
    title = f"💜 {'🚀 상승률' if ranking == 'gainers' else '💰 24시간 거래대금'} TOP {len(selected)} · 트뷰 퍼플리스트"
    note = (f'조회: {stamp:%Y-%m-%d %H:%M:%S} KST · 관심항목 {len(symbols)}개\n'
        '상승률: TradingView 표시 변동률 · 거래대금: 해당 등록 상품의 최근 24시간 USD 환산값\n'
        '등록된 현물·무기한 선물 기준이며, 같은 코인의 다른 상품은 별도 항목입니다.')
    if missing:
        note += f'\n시세를 확인하지 못한 {len(missing)}개 항목은 순위에서 제외했습니다.'
    no_turnover = sum(q['turnover'] is None for q in quotes)
    if no_turnover:
        note += f'\n거래대금 미제공 {no_turnover}개 항목은 거래대금 순위에서 제외하며, 표에는 —로 표시합니다.'
    headers = ['순위', '관심종목', '현재가', '상승률', '24시간 거래대금(USD)']
    rows = []
    for i, q in enumerate(selected, 1):
        product = '무기한' if q['kind'] == 'swap' else '현물'
        rows.append([str(i), f"{q['base']} · {product}", f"{price_text(q['price'])} {q['currency']}",
            f"{q['change']:+.2f}%", '—' if q['turnover'] is None else f"${q['turnover']:,.0f}"])
    table = {'type': 'table', 'rows': [[{'type': 'raw_text', 'text': value} for value in row] for row in [headers, *rows]],
        'column_settings': [{'align': 'right'}, {'align': 'left', 'is_wrapped': True}, {'align': 'right'}, {'align': 'right'}, {'align': 'right'}]}
    return {'text': title + '\n' + note + '\n' + ' | '.join(headers) + '\n' + '\n'.join(' | '.join(r) for r in rows),
        'blocks': [{'type': 'header', 'text': {'type': 'plain_text', 'text': title}},
            {'type': 'section', 'text': {'type': 'plain_text', 'text': note}}, table],
        'unfurl_links': False, 'unfurl_media': False}


def payloads(count=20):
    watchlist = fetch_watchlist()
    symbols = watchlist['symbols']
    quotes, missing = normalize(symbols, fetch_quotes(symbols))
    stamp = datetime.now(KST)
    # Build both before sending either; no partial delivery after a validation error.
    return [build_message(quotes, symbols, missing, stamp, rank, count) for rank in ('gainers', 'turnover')]
