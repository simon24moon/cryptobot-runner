"""Alternative.me daily Bitcoin sentiment, with explicit source timestamps."""
from datetime import datetime
import json
import time
import urllib.request
from zoneinfo import ZoneInfo

SOURCE = 'https://alternative.me/crypto/fear-and-greed-index/'
URL = 'https://api.alternative.me/fng/?limit=2'
LABELS = {'Extreme Fear': '극도의 공포', 'Fear': '공포', 'Neutral': '중립',
          'Greed': '탐욕', 'Extreme Greed': '극도의 탐욕'}
KST = ZoneInfo('Asia/Seoul')


def parse(data, now):
    if data.get('metadata', {}).get('error') or not data.get('data'):
        raise ValueError('Missing sentiment data')
    entries = data['data']
    latest = entries[0]
    value, timestamp = int(latest['value']), int(latest['timestamp'])
    classification = latest['value_classification']
    if not 0 <= value <= 100 or classification not in LABELS or timestamp > now.timestamp()+300:
        raise ValueError('Invalid sentiment data')
    stamp = datetime.fromtimestamp(timestamp, KST)
    result = {'value': value, 'classification': LABELS[classification], 'timestamp': stamp.isoformat(),
              'stale': now.timestamp()-timestamp > 48*3600, 'delta': None, 'source': SOURCE}
    if len(entries) > 1:
        prev = entries[1]
        previous_value, previous_timestamp = int(prev['value']), int(prev['timestamp'])
        if 0 <= previous_value <= 100 and 20*3600 <= timestamp-previous_timestamp <= 28*3600:
            result['delta'] = value-previous_value
    return result


def fetch():
    for attempt in range(3):
        try:
            request = urllib.request.Request(URL, headers={'User-Agent': 'CryptoSnapshot/1.0'})
            with urllib.request.urlopen(request, timeout=15) as response:
                return parse(json.load(response), datetime.now(KST))
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            if attempt == 2:
                return {'error': '조회 실패', 'source': SOURCE}
            time.sleep(2)


def build_message(data, now):
    title = '🧭 크립토 공포·탐욕지수'
    if data.get('error'):
        detail = '⚠️ 지수를 조회하지 못했습니다. 다음 정기 실행에서 다시 확인합니다.'
    else:
        stamp = datetime.fromisoformat(data['timestamp'])
        delta = f"{data['delta']:+d}점" if data['delta'] is not None else '비교 데이터 없음'
        detail = f"{data['value']} / 100 · {data['classification']}\n전일 대비: {delta}\n데이터 기준: {stamp:%Y-%m-%d %H:%M} KST"
        if data['stale']:
            detail = '⚠️ 48시간 이상 지난 데이터입니다.\n'+detail
    note = f'조회: {now:%Y-%m-%d %H:%M:%S} KST\n비트코인 중심 지표 · 일 단위 갱신으로 시간대별 수치가 같을 수 있습니다.\n0 = 극도의 공포 · 100 = 극도의 탐욕'
    return {'text': f'{title}\n{detail}\n{note}\n출처: Alternative.me {SOURCE}',
            'blocks': [{'type': 'header', 'text': {'type': 'plain_text', 'text': title}},
                       {'type': 'section', 'text': {'type': 'plain_text', 'text': detail}},
                       {'type': 'context', 'elements': [{'type': 'plain_text', 'text': note}]},
                       {'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': f'출처: <{SOURCE}|Alternative.me>'}]}],
            'unfurl_links': False, 'unfurl_media': False}
