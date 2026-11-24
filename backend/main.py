import os,json,threading,shlex,subprocess,hashlib,urllib.request,uuid
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Request,Body
from fastapi.responses import FileResponse
from . import store,media,contracts
MAX_BYTES=250*1024*1024
@asynccontextmanager
async def lifespan(app):
    store.init();stop=threading.Event();t=threading.Thread(target=media.worker,args=(stop,),daemon=True);t.start();yield;stop.set();t.join(timeout=2)
app=FastAPI(title='InterVue local API',lifespan=lifespan)
def fail(code,msg):raise HTTPException(code,msg)
def get_s(sid):
    s=store.session(sid)
    if not s:fail(404,'Session not found')
    return s
def get_a(aid):
    a=store.answer(aid)
    if not a:fail(404,'Answer not found')
    return a
@app.get('/api/health')
def health():return {'status':'ok','name':os.getenv('APP_NAME','InterVue')}
@app.get('/api/workspace')
def workspace():return store.get_setting('workspace',{})
@app.put('/api/workspace')
def save_workspace(x:dict):
    if len(json.dumps(x))>5_000_000:fail(413,'Workspace too large')
    days=x.get('settings',{}).get('retentionDays',4)
    if type(days) not in (int,float) or not 0<=days<=3650:fail(400,'Retention must be 0–3650 days (0 disables)')
    store.set_setting('workspace',x);return {'ok':True}
@app.get('/api/sessions')
def list_sessions():return [dict(s,answers=store.answers(s['id'])) for s in store.sessions()]
@app.post('/api/sessions')
def new_session(x:dict):
    qs=x.get('questions');settings=x.get('settings',{})
    if not isinstance(qs,list) or not 1<=len(qs)<=100 or any(not isinstance(q,dict) or not isinstance(q.get('text'),str) or not q['text'].strip() or len(q['text'])>4000 for q in qs):fail(400,'Choose 1–100 valid questions')
    for k in ['prep','answer']:
        if type(settings.get(k)) is not int or not 1<=settings[k]<=900:fail(400,'Timings must be 1–900 seconds')
    if len(json.dumps(x))>1_000_000:fail(413,'Session too large')
    s={'id':store.uid(),'created':store.now(),'profile':x.get('profile',{}),'settings':settings,'questions':qs,'finished':False,'assessment':None};store.save_session(s);return s
@app.get('/api/sessions/{sid}')
def session(sid:str):return dict(get_s(sid),answers=store.answers(sid))
@app.post('/api/sessions/{sid}/finish')
def finish(sid:str):
    s=get_s(sid);s['finished']=True;store.save_session(s);media.prepare_export(sid);return s
@app.post('/api/sessions/{sid}/answers')
def create_answer(sid:str,x:dict):
    s=get_s(sid);idx=x.get('index');ext=x.get('extension');client=x.get('client_id')
    if type(idx) is not int or not 0<=idx<len(s['questions']) or ext not in ['webm','mp4'] or not isinstance(client,str):fail(400,'Invalid answer metadata')
    try:uuid.UUID(client)
    except:fail(400,'Invalid client ID')
    existing=next((a for a in store.answers(sid) if a.get('client_id')==client),None)
    if existing:return existing
    if s['finished']:fail(409,'Session already finished')
    q=s['questions'][idx];a={'id':store.uid(),'session':sid,'client_id':client,'index':idx,'question_id':q.get('id'),'question':q['text'],'tags':q.get('tags',[]),'extension':ext,'answer_limit':s['settings']['answer'],'duration':x.get('duration',0),'recorded_at':store.now()};store.save_answer(a);return a
@app.put('/api/answers/{aid}/chunk')
async def chunk(aid:str,offset:int,request:Request):
    a=get_a(aid)
    if a['status']!='uploading':return {'offset':media.original(a).stat().st_size if media.original(a).exists() else 0,'complete':True}
    data=await request.body()
    if not 0<len(data)<=2*1024*1024 or offset<0 or offset+len(data)>MAX_BYTES:fail(413,'Invalid chunk or recording too large')
    p=media.original(a);size=p.stat().st_size if p.exists() else 0
    if offset<size:
        with p.open('rb') as f:f.seek(offset);same=f.read(len(data))==data
        if same:return {'offset':size}
    if offset!=size:fail(409,f'Expected offset {size}')
    with p.open('ab') as f:f.write(data)
    return {'offset':size+len(data)}
@app.post('/api/answers/{aid}/complete')
def complete(aid:str,x:dict):
    a=get_a(aid)
    if a['status']!='uploading':return a
    p=media.original(a)
    if not p.exists() or p.stat().st_size!=x.get('size') or not p.stat().st_size:fail(400,'Recording upload incomplete')
    # Queue first; recovery covers a crash between state update and enqueue.
    store.save_answer(a,'queued');store.queue(aid,'analyse');return get_a(aid)
@app.post('/api/answers/{aid}/retry')
def retry(aid:str):
    a=get_a(aid)
    if a['status']!='failed' or not media.original(a).exists():fail(409,'No failed recording to retry')
    store.save_answer(a,'queued');store.queue(aid,'analyse');return {'ok':True}
@app.patch('/api/answers/{aid}/transcript')
def edit_transcript(aid:str,x:dict):
    a=get_a(aid);t=x.get('transcript')
    if a['status']!='ready' or not isinstance(t,str) or len(t)>100000:fail(400,'Invalid transcript')
    with media.media_lock:
        a=get_a(aid);a['transcript']=t;a['transcript_edited']=True;a['segments_original']=a.get('segments_original',a.get('segments',[]));a['segments']=[] # edited text cannot honestly retain old alignment
        m=media.metrics(t,a['segments_original'],a['metrics']['duration'],a['answer_limit']);m['gaze']=a['metrics'].get('gaze');a['metrics']=m;store.save_answer(a,'ready');media.prepare_export(a['session'])
    return a
@app.get('/api/sessions/{sid}/assessment-request')
def export(sid:str):
    s=get_s(sid);aa=store.answers(sid)
    if not s['finished'] or not aa or any(a['status']!='ready' for a in aa):fail(409,'Finish session and wait for all answer transcriptions')
    return contracts.request_payload(s,sorted(aa,key=lambda a:a['index']))
@app.post('/api/sessions/{sid}/assessment')
def import_assessment(sid:str,x:dict):
    s=get_s(sid);aa=store.answers(sid)
    if not s['finished'] or not aa or any(a['status']!='ready' for a in aa):fail(409,'Session is not ready')
    try:contracts.validate_result(x,s,aa)
    except (ValueError,TypeError,KeyError) as e:fail(400,str(e))
    s['assessment']=x;store.save_session(s);return s
@app.get('/api/answers/{aid}/video')
def video(aid:str):
    a=get_a(aid);p=media.playable(a)
    if a.get('video_deleted') or not p.exists() or a['status']=='uploading':fail(404,'Video unavailable')
    return FileResponse(p,media_type='video/mp4' if p.suffix=='.mp4' else 'video/webm',headers={'Cache-Control':'private, no-store'})
@app.get('/api/answers/{aid}/thumb/{idx}')
def thumb(aid:str,idx:int):
    a=get_a(aid);p=store.media_dir(aid)/f'thumb-{idx}.jpg'
    if a.get('video_deleted') or not p.exists():fail(404,'Thumbnail unavailable')
    return FileResponse(p,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})
@app.delete('/api/answers/{aid}/video')
def delete_video(aid:str):
    a=get_a(aid)
    if a['status']!='ready':fail(409,'Wait for transcription before deleting')
    media.delete_video(a);return {'ok':True}
@app.delete('/api/videos')
def bulk_delete():
    n=0
    for s in store.sessions():
        for a in store.answers(s['id']):
            if a['status']=='ready' and not a.get('video_deleted'):media.delete_video(a);n+=1
    return {'deleted':n}
@app.delete('/api/sessions/{sid}')
def delete_session(sid:str):
    get_s(sid)
    with media.media_lock:
        for a in store.answers(sid):media.delete_video(a)
        with store.connect() as c:
            c.execute('DELETE FROM jobs WHERE answer IN (SELECT id FROM answers WHERE session=?)',(sid,));c.execute('DELETE FROM answers WHERE session=?',(sid,));c.execute('DELETE FROM sessions WHERE id=?',(sid,));c.execute('DELETE FROM kv WHERE key=?',('request:'+sid,))
    return {'ok':True}
def voices():
    p=Path(os.getenv('VOICES_FILE',str(store.ROOT/'voices.local.json')))
    if not p.exists():return []
    return [v for v in json.loads(p.read_text()) if v.get('approved') is True]
@app.get('/api/voices')
def get_voices():return [{'id':v['id'],'label':v['label']} for v in voices()]
@app.post('/api/voice')
def speak(x:dict):
    text=x.get('text');v=next((v for v in voices() if v['id']==x.get('voice')),None)
    if not v or not isinstance(text,str) or not 0<len(text)<=4000:fail(400,'Select an approved voice and valid question')
    folder=store.ROOT/'voice-cache';folder.mkdir(exist_ok=True);p=folder/(hashlib.sha256(json.dumps([v,text],sort_keys=True).encode()).hexdigest()+'.wav')
    with media.voice_lock:
        if not p.exists():
            tmp=p.with_suffix('.tmp.wav');url=os.getenv('CHATTERBOX_URL');cmd=os.getenv('CHATTERBOX_COMMAND')
            try:
                if url:
                    req=urllib.request.Request(url,data=json.dumps({'text':text,'voice_id':v['id']}).encode(),headers={'Content-Type':'application/json'},method='POST')
                    with urllib.request.urlopen(req,timeout=180) as r:tmp.write_bytes(r.read(30*1024*1024))
                elif cmd:
                    args=shlex.split(cmd)+['--text',text,'--reference',v['reference'],'--output',str(tmp)];media.run(args,180)
                else:raise RuntimeError('Chatterbox adapter not configured')
                if not any(s['codec_type']=='audio' for s in media.probe(tmp)['streams']):raise RuntimeError('Invalid audio')
                tmp.replace(p)
            except Exception as e:tmp.unlink(missing_ok=True);fail(503,str(e)[:500])
    return FileResponse(p,media_type='audio/wav',headers={'Cache-Control':'private, no-store'})
