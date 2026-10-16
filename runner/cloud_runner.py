"""GitHub Actions entry point. Authoritative job payload comes only from the authenticated Worker."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid

from cloud_client import CloudError, checkpoint, reference, request, slack
from upbit_report import rank_rows, build_message, KST
from help_text import help_message
import fear_greed
import purple_report

ROOT = Path(__file__).resolve().parent


def browser_environment():
    # The page/browser and browser installer never need service credentials.
    allowed = {'PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL', 'TZ',
        'SYSTEMROOT', 'DISPLAY', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR', 'CHROME_CHANNEL',
        'PLAYWRIGHT_MODULE', 'PLAYWRIGHT_BROWSERS_PATH', 'PLAYWRIGHT_DOWNLOAD_HOST',
        'PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST', 'PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT'}
    return {key: value for key, value in os.environ.items() if key in allowed}


def delivery_environment():
    # The uploader needs only these credentials and this job's claim, never
    # deployment, GitHub, signing or unrelated credentials from the parent.
    allowed = {'PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL', 'TZ',
        'SYSTEMROOT', 'CRYPTOBOT_WORKER_URL', 'RUNNER_TOKEN', 'SLACK_BOT_TOKEN',
        'CLOUD_JOB_ID', 'CLOUD_CLAIM_TOKEN'}
    return {key: value for key, value in os.environ.items() if key in allowed}


def validate_job(job):
    if job.get('channel') != os.environ['SLACK_CHANNEL_ID']:
        raise CloudError('Unexpected destination')
    if not re.fullmatch(r'[\w:.-]{1,200}', job.get('requestId', '')) or job.get('source') not in ('mention', 'schedule', 'manual'):
        raise CloudError('Invalid job identity')
    if job.get('thread_ts') and not re.fullmatch(r'\d+\.\d+', job['thread_ts']):
        raise CloudError('Invalid thread')
    if job.get('kind') == 'help':
        if not job.get('thread_ts'):
            raise CloudError('Help requires a thread')
    elif job.get('kind') == 'capture':
        if job.get('period') not in ('1H', '1D', '1W'):
            raise CloudError('Invalid capture period')
    elif job.get('kind') == 'report':
        count = job.get('count')
        if type(count) is not int or not 1 <= count <= 99 or job.get('report') not in ('all', 'gainers', 'turnover', 'sentiment', 'purple'):
            raise CloudError('Invalid report')
        if job.get('includeCapture') and job['report'] != 'all':
            raise CloudError('Invalid bundle')
        if job.get('includePurple') and job['report'] != 'all':
            raise CloudError('Invalid purple bundle')
    else:
        raise CloudError('Invalid job kind')


def public_query(path):
    # Fixed public endpoints only; there is no trading/account API or Upbit credential.
    if path not in ('/v1/market/all', '/v1/ticker/all?quote_currencies=KRW'):
        raise CloudError('Unsupported public endpoint')
    for attempt in range(3):
        try:
            req = urllib.request.Request('https://api.upbit.com' + path,
                headers={'User-Agent': 'cryptobot-phase2/1.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                value = json.load(response)
            if not isinstance(value, list) or not value:
                raise ValueError('Empty public data')
            return value
        except (OSError, ValueError):
            if attempt == 2:
                raise CloudError('Upbit quote lookup failed') from None
            time.sleep(2 * (attempt + 1))


def report_payloads(job):
    payloads = []
    if job['report'] != 'sentiment':
        markets = public_query('/v1/market/all')
        tickers = public_query('/v1/ticker/all?quote_currencies=KRW')
        stamp = datetime.now(KST)
        if job['report'] in ('all', 'gainers'):
            payloads.append(build_message(rank_rows(tickers, markets, job['count']), stamp))
        if job['report'] in ('all', 'turnover'):
            payloads.append(build_message(rank_rows(tickers, markets, job['count'], 'acc_trade_price_24h'), stamp, '24시간 거래대금'))
    if job['report'] in ('all', 'sentiment'):
        payloads.append(fear_greed.build_message(fear_greed.fetch(), datetime.now(KST)))
    return payloads


def send_reports(job):
    checkpoint('component:begin', {'component': 'report'})
    for index, payload in enumerate(report_payloads(job)):
        if index:
            time.sleep(1.1)
        payload['channel'] = job['channel']
        if job.get('thread_ts'):
            payload.update(thread_ts=job['thread_ts'], reply_broadcast=True)
        response = slack('chat.postMessage', payload)
        if job.get('thread_ts') and response.get('message', {}).get('subtype') != 'thread_broadcast':
            raise CloudError('Report broadcast unconfirmed')
    checkpoint('component:end', {'component': 'report', 'status': 'sent'})


def send_purple(job):
    checkpoint('component:begin', {'component': 'purple'})
    for index, payload in enumerate(purple_report.payloads(job['count'])):
        if index:
            time.sleep(1.1)
        payload['channel'] = job['channel']
        if job.get('thread_ts'):
            payload.update(thread_ts=job['thread_ts'], reply_broadcast=True)
        response = slack('chat.postMessage', payload)
        if job.get('thread_ts') and response.get('message', {}).get('subtype') != 'thread_broadcast':
            raise CloudError('Purple report broadcast unconfirmed')
    checkpoint('component:end', {'component': 'purple', 'status': 'sent'})


def capture_batch(job, run, capture_kind, deliver):
    state = ROOT / 'state'
    state.mkdir(exist_ok=True)
    spec = state / (run + '.json')
    spec.write_text(json.dumps({**job, 'run': run, 'captureKind': capture_kind}))
    spec.chmod(0o600)
    env = {**browser_environment(), 'CAPTURE_JOB': str(spec)}
    component = 'categories' if capture_kind == 'categories' else 'capture'
    checkpoint('component:begin', {'component': component})
    result = subprocess.run([os.environ.get('NODE_BIN', 'node'), str(ROOT / 'capture.cjs')], env=env, timeout=600)
    if result.returncode:
        raise CloudError('Capture failed; no incomplete batch sent')
    manifest = ROOT / 'captures' / run / 'manifest.json'
    if deliver:
        result = subprocess.run([sys.executable, str(ROOT / 'send_slack.py'), str(manifest)], env=delivery_environment(), timeout=600)
        if result.returncode:
            raise CloudError('Capture delivery requires review')
    checkpoint('component:end', {'component': component, 'status': 'sent' if deliver else 'captured'})
    return manifest


def capture(job, deliver=True):
    run = hashlib.sha256(job['requestId'].encode()).hexdigest()[:24]
    # Install the browser only after this execution owns a capture job. Report/help
    # jobs and duplicate Actions runs do not spend time downloading Chromium.
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        install = subprocess.run(['pnpm', 'exec', 'playwright', 'install', '--with-deps', 'chromium'],
            cwd=ROOT.parent, env=browser_environment(), timeout=300)
        if install.returncode:
            raise CloudError('Browser setup failed; no capture sent')
    manifest = capture_batch(job, run, 'ranks', deliver)
    parent = job.get('thread_ts')
    if deliver:
        saved = json.loads(manifest.read_text())
        if saved.get('status') != 'sent' or not re.fullmatch(r'\d+\.\d+', saved.get('slackMessageTs', '')):
            raise CloudError('Capture parent message unconfirmed; categories stopped')
        parent = parent or saved['slackMessageTs']
    # Slack has one thread level. Scheduled captures start a thread on their
    # own message; mention captures keep the original request's root thread.
    child = capture_batch({**job, 'requestId': job['requestId'] + ':categories', 'thread_ts': parent},
        run + '-categories', 'categories', deliver)
    if not deliver:
        print('Category preview manifest:', child)
    return manifest


def setup_files():
    expected = {'teamId': os.environ['SLACK_TEAM_ID'], 'botUserId': os.environ['SLACK_BOT_USER_ID'], 'channelId': os.environ['SLACK_CHANNEL_ID']}
    (ROOT / 'slack-app-status.json').write_text(json.dumps(expected))
    cfg = json.loads((ROOT / 'config.example.json').read_text())
    cfg['slackChannelId'] = expected['channelId']
    cfg['schedule']['enabled'] = False
    (ROOT / 'config.local.json').write_text(json.dumps(cfg))
    for name in ('slack-app-status.json', 'config.local.json'):
        (ROOT / name).chmod(0o600)
    return expected


def execute(job):
    if job['kind'] == 'help':
        slack('chat.postMessage', {'channel': job['channel'], 'thread_ts': job['thread_ts'],
            'text': help_message(purple_enabled=job.get('includePurple', False), categories_enabled=True), 'unfurl_links': False, 'unfurl_media': False})
        return
    if job['source'] == 'mention':
        slack('chat.postMessage', {'channel': job['channel'], 'thread_ts': job['thread_ts'],
            'text': '요청을 접수했습니다. 최신 조회·캡처 결과를 이 스레드와 채널에 함께 표시하겠습니다.'})
    if job['kind'] == 'capture':
        capture(job)
        return
    failures = []
    components = [] if job['report'] == 'purple' else [('report', lambda: send_reports(job))]
    if job['report'] == 'purple' or job.get('includePurple'):
        components.append(('purple', lambda: send_purple(job)))
    if job.get('includeCapture'):
        components.append(('capture', lambda: capture({**job, 'requestId': job['requestId'] + ':capture', 'kind': 'capture', 'period': '1D'})))
    for name, run in components:
        try:
            run()
        except Exception as exc:
            failures.append(name)
            # If the state service is unavailable, stop; do not make further Slack calls.
            checkpoint('component:failed', {'component': name, 'errorType': type(exc).__name__})
    if failures:
        raise CloudError('One or more components require review')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[\w:.-]{1,200}', args.job_id):
        parser.error('Invalid job ID')
    expected = setup_files()
    execution = os.environ.get('GITHUB_RUN_ID', 'local-' + uuid.uuid4().hex) + ':' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    job_id = args.job_id
    token = str(uuid.uuid4())
    try:
        job = request('/jobs/claim', {'id': job_id, 'claimToken': token, 'executionId': execution})['job']
    except CloudError as exc:
        if str(exc) == 'Worker HTTP 409':
            print('SKIPPED: job already claimed, completed or expired')
            return
        raise
    os.environ.update(CLOUD_JOB_ID=job_id, CLOUD_CLAIM_TOKEN=token)
    try:
        validate_job(job)
        if job['requestId'] != job_id:
            raise CloudError('Mismatched job ID')
        identity = slack('auth.test', {})
        if identity.get('team_id') != expected['teamId'] or identity.get('user_id') != expected['botUserId']:
            raise CloudError('Unexpected Slack identity')
        execute(job)
        request('/jobs/finish', {**reference(), 'status': 'sent'})
        print('SENT: claimed job completed')
    except Exception as exc:
        print('Job stopped:', type(exc).__name__)
        # No automatic Slack retry or status notice after an ambiguous send.
        try:
            request('/jobs/finish', {**reference(), 'status': 'needs_review'})
        except CloudError:
            print('State unavailable; stale running job will require review')
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except (CloudError, OSError, KeyError, ValueError) as exc:
        print('Runner stopped:', type(exc).__name__)
        raise SystemExit(1)
