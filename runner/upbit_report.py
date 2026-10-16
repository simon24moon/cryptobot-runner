"""Pure formatting and ranking of public Upbit quotes; no local CLI or credentials."""
from decimal import Decimal
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def rank_rows(tickers, markets, count=20, sort_by="signed_change_rate"):
    if sort_by not in ("signed_change_rate", "acc_trade_price_24h"):
        raise ValueError("Unsupported ranking field")
    names = {m['market']: m['korean_name'] for m in markets}
    filtered = [t for t in tickers if t['market'].startswith('KRW-')]
    if not filtered or len({t['market'] for t in filtered}) != len(filtered):
        raise ValueError('Empty or duplicate market data')
    for t in filtered:
        for key in ('trade_price', 'signed_change_rate', 'acc_trade_price_24h'):
            if not Decimal(str(t[key])).is_finite():
                raise ValueError('Non-finite quote')
    filtered.sort(key=lambda t: (-Decimal(str(t[sort_by])), t['market']))
    rows = []
    for i, t in enumerate(filtered[:count], 1):
        price = format(Decimal(str(t['trade_price'])), ',f')
        if '.' in price:
            price = price.rstrip('0').rstrip('.')
        rows.append([str(i), f"{names.get(t['market'], t['market'])} ({t['market'][4:]})", price,
                     f"{Decimal(str(t['signed_change_rate']))*100:+.2f}%",
                     f"{Decimal(str(t['acc_trade_price_24h']))/100000000:,.2f}억 원"])
    return rows


def build_message(rows, stamp, ranking='상승률'):
    emoji = '🚀' if ranking == '상승률' else '💰'
    title = f'{emoji} 업비트 원화마켓 {ranking} TOP {len(rows)}'
    note = f'조회: {stamp:%Y-%m-%d %H:%M:%S} KST\n상승률: 오전 9시 기준 전일 종가 대비 · 거래대금: 최근 24시간 누적'
    headers = ['순위', '종목명 (티커)', '현재가(원)', '상승률', '24시간 거래대금']
    table = {'type': 'table', 'rows': [[{'type': 'raw_text', 'text': value} for value in row] for row in [headers, *rows]],
             'column_settings': [{'align': 'right'}, {'align': 'left', 'is_wrapped': True}, {'align': 'right'}, {'align': 'right'}, {'align': 'right'}]}
    return {'text': title+'\n'+note+'\n'+' | '.join(headers)+'\n'+'\n'.join(' | '.join(r) for r in rows),
            'blocks': [{'type': 'header', 'text': {'type': 'plain_text', 'text': title}},
                       {'type': 'section', 'text': {'type': 'plain_text', 'text': note}}, table],
            'unfurl_links': False, 'unfurl_media': False}
