"""Cloud job claim and delivery checkpoints. Errors deliberately exclude request bodies/secrets."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


class CloudError(RuntimeError):
    pass


class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudError('Service redirect rejected; credentials were not forwarded')


open_authenticated = urllib.request.build_opener(NoCredentialRedirect()).open
SERVICE_PATHS = frozenset(('/jobs/claim', '/jobs/progress', '/jobs/finish',
    '/integrations/tradingview/watchlist'))
SLACK_METHODS = frozenset(('auth.test', 'chat.postMessage', 'chat.update',
    'files.getUploadURLExternal', 'files.completeUploadExternal'))


def request(path, body):
    if path not in SERVICE_PATHS:
        raise CloudError('Unsupported executor endpoint')
    base = os.environ['CRYPTOBOT_WORKER_URL'].rstrip('/')
    url = urllib.parse.urlparse(base)
    if url.scheme != 'https' or not url.hostname or url.port not in (None, 443):
        raise CloudError('Worker URL must use HTTPS')
    if url.username or url.password or url.query or url.fragment or url.path:
        raise CloudError('Invalid Worker URL')
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + os.environ['RUNNER_TOKEN'], 'Content-Type': 'application/json',
            'User-Agent': 'cryptobot-phase2/0.2'})
    try:
        with open_authenticated(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise CloudError('Worker HTTP ' + str(exc.code)) from None
    except (OSError, ValueError):
        raise CloudError('Worker response unavailable; delivery stopped') from None


def reference():
    return {'id': os.environ['CLOUD_JOB_ID'], 'claimToken': os.environ['CLOUD_CLAIM_TOKEN']}


def upload_file(url, blob):
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'files.slack.com'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.fragment or not parsed.path.startswith('/upload/v1/')):
        raise CloudError('Unexpected Slack upload destination')
    req = urllib.request.Request(url, data=blob,
        headers={'Content-Type': 'application/octet-stream'}, method='POST')
    # A signed upload URL is also a credential. Never follow its redirects.
    with open_authenticated(req, timeout=60) as response:
        if response.status != 200:
            raise CloudError('File upload failed')


def checkpoint(phase, detail):
    if os.environ.get('CLOUD_JOB_ID'):
        request('/jobs/progress', {**reference(), 'phase': phase, 'detail': detail})


def slack(method, body):
    if method not in SLACK_METHODS:
        raise CloudError('Unsupported Slack method')
    # auth.test is read-only; all delivery methods must have durable intent first.
    mutating = method != 'auth.test'
    if mutating:
        checkpoint('slack:before', {'method': method, 'status': 'sending'})
    # Slack rejects JSON arguments for this upload-ticket endpoint in practice.
    # Other methods use JSON so nested blocks/files and booleans retain their types.
    form = method == 'files.getUploadURLExternal'
    data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
    req = urllib.request.Request('https://slack.com/api/' + method, data=data,
        headers={'Authorization': 'Bearer ' + os.environ['SLACK_BOT_TOKEN'],
            'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'})
    try:
        with open_authenticated(req, timeout=60) as response:
            value = json.load(response)
        if not value.get('ok'):
            code = value.get('error', 'unknown')
            if not isinstance(code, str) or not re.fullmatch(r'[a-z_]{1,64}', code):
                code = 'unknown'
            raise CloudError('Slack rejected ' + method + ': ' + code)
    except (OSError, ValueError):
        raise CloudError('Slack delivery response unavailable') from None
    if mutating:
        detail = {'method': method, 'status': 'accepted'}
        if value.get('ts'):
            detail['ts'] = value['ts']
        ids = [f['id'] for f in value.get('files', []) if 'id' in f]
        if value.get('file_id'):
            ids.append(value['file_id'])
        if ids:
            detail['fileIds'] = ids
        checkpoint('slack:after', detail)
    return value
