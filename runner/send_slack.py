"""Upload a verified capture batch once. Never prints credentials or upload URLs."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from cloud_client import slack, upload_file

ROOT = Path(__file__).resolve().parent
CATEGORIES = json.loads((ROOT/'bubble_categories.json').read_text())

def validate_batch(manifest):
    files=manifest.get('files',[])
    if manifest.get('status')!='captured':
        raise SystemExit('Requires an unattempted, complete capture batch.')
    if manifest.get('period') not in ('1H','1D','1W'):
        raise SystemExit('Unexpected chart period.')
    if manifest.get('kind')=='categories':
        if [f.get('category') for f in files]!=[c['name'] for c in CATEGORIES] or not manifest.get('thread_ts'):
            raise SystemExit('Requires five ordered categories and a parent thread.')
    elif manifest.get('kind','ranks')=='ranks':
        if [(f.get('start'),f.get('end')) for f in files]!=[(n+1,n+100) for n in range(0,1000,100)]:
            raise SystemExit('Requires ten ordered rank pages.')
    else:
        raise SystemExit('Unexpected capture kind.')
    if manifest.get('thread_ts') and not re.fullmatch(r'\d+\.\d+',manifest['thread_ts']):
        raise SystemExit('Invalid parent thread.')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+\.png', f.get('filename','')) for f in files):
        raise SystemExit('Invalid capture filename.')

def save(path, value):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2))
    os.replace(tmp,path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--test',action='store_true')
    args=parser.parse_args()
    if not os.environ.get('CLOUD_JOB_ID') or not os.environ.get('CLOUD_CLAIM_TOKEN'):
        raise SystemExit('A claimed service job is required.')
    if args.manifest.is_symlink():
        raise SystemExit('Capture manifest must not be a symbolic link.')
    file=args.manifest.resolve()
    if not file.is_relative_to((ROOT/'captures').resolve()):
        raise SystemExit('Capture manifest is outside the capture directory.')
    manifest=json.loads(file.read_text())
    validate_batch(manifest)
    category_mode=manifest.get('kind')=='categories'
    def api(method,payload):
        body=dict(payload)
        if body.get('reply_broadcast')=='true':body['reply_broadcast']=True
        return slack(method,body)
    identity=api('auth.test',{})
    expected=json.loads((ROOT/'slack-app-status.json').read_text())
    if identity.get('team_id')!=expected['teamId'] or identity.get('user_id')!=expected['botUserId'] or manifest['channel']!=expected['channelId']:
        raise SystemExit('Unexpected Slack identity or channel; stopped.')
    blobs=[]
    for item in manifest['files']:
        image=file.parent/item['filename']
        if image.is_symlink() or image.resolve().parent!=file.parent or image.stat().st_size>10*1024*1024:
            raise SystemExit('Invalid capture file.')
        blob=image.read_bytes()
        if not blob.startswith(b'\x89PNG\r\n\x1a\n'):
            raise SystemExit('Expected a PNG capture.')
        if hashlib.sha256(blob).hexdigest()!=item['sha256']:
            raise SystemExit('Capture checksum mismatch; stopped.')
        blobs.append(blob)
    lock=file.parent/'send.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    manifest['status']='uploading';save(file,manifest)
    uploads=[]
    try:
        for item,blob in zip(manifest['files'],blobs):
            ticket=api('files.getUploadURLExternal',{'filename':item['filename'],'length':len(blob)})
            upload_file(ticket['upload_url'],blob)
            label=item['category'] if category_mode else f"{item['start']}–{item['end']}"
            uploads.append({'id':ticket['file_id'],'title':f"Crypto Bubbles | {label} | {manifest['period']}"})
            print(f"Uploaded {label}",flush=True)
        stamp=lambda value:datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
        summary=(f"카테고리: {' → '.join(c['name'] for c in CATEGORIES)} · 총 5장 · 변동률: {manifest['period']}" if category_mode else f"범위: 1–1000 · 100개씩 총 10장 · 변동률: {manifest['period']}")
        comment=(('🧪 캡처 전송 테스트\n' if args.test else ('🫧 Crypto Bubbles · 카테고리\n' if category_mode else '🫧 Crypto Bubbles\n'))+f"{summary}\n캡처: {stamp(manifest['startedAt'])} ~ {stamp(manifest['endedAt'])} KST\n순차 캡처이므로 화면별 시각 차이가 있습니다.\nhttps://cryptobubbles.net/en\n실행 ID: {manifest['run']}")
        manifest['status']='delivery_pending';manifest['slackFiles']=uploads;save(file,manifest)
        # Completion is not retried on ambiguous errors: avoid duplicate messages.
        # Always create a known message ts. Scheduled rank captures use it as
        # the parent of their category reply without adding Slack read scopes.
        result=api('files.completeUploadExternal',{'files':uploads})
        manifest['status']='reply_pending';save(file,manifest)
        body={'channel':manifest['channel'],'text':comment,'unfurl_links':False,'unfurl_media':False}
        if manifest.get('thread_ts'): body['thread_ts']=manifest['thread_ts']
        reply=api('chat.postMessage',body)
        if not re.fullmatch(r'\d+\.\d+',reply.get('ts','')):
            raise RuntimeError('Slack message timestamp was not confirmed')
        manifest['slackMessageTs']=reply['ts'];manifest['status']='attachment_pending';save(file,manifest)
        attached=api('chat.update',{'channel':manifest['channel'],'ts':reply['ts'],'text':comment,'file_ids':[item['id'] for item in uploads]})
        markers=(summary, f"실행 ID: {manifest['run']}")
        updated=attached
        if manifest.get('thread_ts'):
            manifest['status']='broadcast_pending';save(file,manifest)
            # Slack requires broadcasting separately from editing reply content.
            broadcast=api('chat.update',{'channel':manifest['channel'],'ts':reply['ts'],'reply_broadcast':'true'})
            if broadcast.get('message',{}).get('subtype')!='thread_broadcast':
                raise RuntimeError('Slack reply broadcast was not confirmed')
            # Slack can clear text during a file/broadcast update. Restore the
            # caption on the same message; never upload or post the batch again.
            updated=broadcast
            manifest['replyBroadcast']=True
        text=updated.get('message',{}).get('text',updated.get('text',''))
        if not all(marker in text for marker in markers):
            restored=api('chat.update',{'channel':manifest['channel'],'ts':reply['ts'],'text':comment})
            text=restored.get('message',{}).get('text',restored.get('text',''))
            if not all(marker in text for marker in markers):
                raise RuntimeError('Slack capture caption was not confirmed')
        manifest['status']='sent';manifest['sentAt']=datetime.now().astimezone().isoformat();save(file,manifest)
        print('Slack accepted one batch with '+str(len(result.get('files',[])))+' files.')
    except Exception as exc:
        print('Delivery stopped: '+type(exc).__name__)
        raise SystemExit(1)

if __name__=='__main__': main()
