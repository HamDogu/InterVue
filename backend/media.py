import os, json, subprocess, re, math, shutil, time, threading, tempfile, logging
from collections import Counter
from pathlib import Path
from . import store, contracts
log=logging.getLogger('intervue'); model=None; media_lock=threading.RLock(); voice_lock=threading.Lock()
def run(args,timeout=1800):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError(f'{args[0]} failed: {p.stderr[-1400:]}')
    return p.stdout,p.stderr

def probe(p):return json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(p)],60)[0])
def duration_of(p,info=None):
    info=info or probe(p)
    try:
        d=float(info['format']['duration'])
        if math.isfinite(d) and d>0:return d
    except (KeyError,ValueError,TypeError):pass
    # MediaRecorder WebM commonly omits container duration. Derive from packet timestamps.
    raw=run(['ffprobe','-v','error','-show_entries','packet=pts_time,duration_time','-of','json',str(p)],120)[0]
    ends=[]
    for packet in json.loads(raw).get('packets',[]):
        try:ends.append(float(packet['pts_time'])+float(packet.get('duration_time') or 0))
        except (ValueError,KeyError,TypeError):pass
    if not ends or max(ends)<=0:raise RuntimeError('Cannot determine recording duration')
    return max(ends)
def original(a):return store.media_dir(a['id'])/('original.'+a['extension'])
def playable(a):
    d=store.media_dir(a['id']); p=d/'compressed.mp4'
    return p if p.exists() else original(a)
def gaze(path):
    # Approximate optical alignment only. Exclude unusable frames and disclose coverage.
    import cv2, mediapipe as mp
    cap=cv2.VideoCapture(str(path)); fps=cap.get(cv2.CAP_PROP_FPS) or 30; step=max(1,round(fps/2)); sampled=valid=looking=0; index=0
    try:
        with mp.solutions.face_mesh.FaceMesh(static_image_mode=True,max_num_faces=1,refine_landmarks=True,min_detection_confidence=.6) as mesh:
            while cap.grab():
                index+=1
                if (index-1)%step:continue
                ok,frame=cap.retrieve(); sampled+=1
                if not ok:continue
                result=mesh.process(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
                if not result.multi_face_landmarks:continue
                l=result.multi_face_landmarks[0].landmark
                def ratio(a,b,c):
                    lo,hi=sorted([l[a].x,l[b].x]);return (l[c].x-lo)/max(hi-lo,1e-6)
                def vertical(a,b,c):
                    lo,hi=sorted([l[a].y,l[b].y]);return (l[c].y-lo)/max(hi-lo,1e-6)
                left=ratio(33,133,468); right=ratio(362,263,473); ly=vertical(159,145,468); ry=vertical(386,374,473)
                if abs(l[159].y-l[145].y)<.004 or abs(l[386].y-l[374].y)<.004:continue
                yaw=ratio(234,454,1);valid+=1
                if .25<=left<=.75 and .25<=right<=.75 and .12<=ly<=.88 and .12<=ry<=.88 and .32<=yaw<=.68:looking+=1
    finally:cap.release()
    return {'percent':round(100*looking/valid,1) if valid>=5 and valid>=sampled*.35 else None,'coverage':round(100*valid/max(1,sampled),1),'samples':sampled,'valid_samples':valid,'note':'Approximate eye/head alignment in usable frames; 80% is a practice target, not an employer score. Camera placement, glasses, lighting and accessibility affect accuracy.'}

def metrics(text,segments,duration,limit):
    words=re.findall(r"\b[\w’'-]+\b",text); fillers=re.findall(r'\b(?:um+|uh+|erm+|er+|you know|sort of|kind of)\b',text,re.I)
    gaps=[{'start':round(float(a['end']),2),'end':round(float(b['start']),2)} for a,b in zip(segments,segments[1:]) if b['start']-a['end']>=1.5]
    counts=Counter(' '.join(x.lower() for x in words[i:i+3]) for i in range(max(0,len(words)-2)))
    repeats=[{'phrase':p,'count':n} for p,n in counts.most_common(5) if n>=2]
    return {'repeated_phrases':repeats,'duration':round(duration,2),'words':len(words),'words_per_minute':round(len(words)*60/max(duration,1)),'filler_words':len(fillers),'time_utilised':round(100*duration/max(limit,1),1),'pauses':gaps}

def analyse(a):
    global model
    p=original(a); info=probe(p);duration=duration_of(p,info);
    if not any(s['codec_type']=='audio' for s in info['streams']):raise RuntimeError('Recording has no audio track')
    if model is None:
        from faster_whisper import WhisperModel
        model=WhisperModel(os.getenv('WHISPER_MODEL','small.en'),device=os.getenv('WHISPER_DEVICE','cpu'),compute_type=os.getenv('WHISPER_COMPUTE_TYPE','int8'))
    segs,_=model.transcribe(str(p),vad_filter=True,word_timestamps=True)
    segs=[{'start':round(s.start,2),'end':round(s.end,2),'text':s.text.strip(),'words':[{'start':w.start,'end':w.end,'text':w.word} for w in (s.words or [])]} for s in segs]
    text=' '.join(s['text'] for s in segs); m=metrics(text,segs,duration,a['answer_limit'])
    try:m['gaze']=gaze(p) if store.session(a['session']).get('settings',{}).get('gaze',True) else {'percent':None,'note':'Eye alignment analysis disabled for this session.'}
    except Exception as e:log.warning('Gaze unavailable: %s',e);m['gaze']={'percent':None,'note':'Gaze analysis unavailable on this server.'}
    d=store.media_dir(a['id']); thumbs=[]
    for i in range(min(12,max(1,math.ceil(duration/5)))):
        t=round(min(max(0,duration-.15),duration*i/min(12,max(1,math.ceil(duration/5)))),2);name=f'thumb-{i}.jpg'
        try:run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(p),'-frames:v','1','-vf','scale=160:-2',str(d/name)],60);thumbs.append({'seconds':t,'file':name})
        except Exception:pass
    a.update(transcript=text,segments=segs,metrics=m,thumbs=thumbs,analysis_error=None,compression='queued');store.save_answer(a,'ready');prepare_export(a['session'])

def prepare_export(sid):
    s=store.session(sid); aa=store.answers(sid)
    if not s or not s['finished'] or not aa or any(a['status']!='ready' for a in aa):return
    # Persist a complete assessment request before scheduling any compression.
    store.set_setting('request:'+sid,contracts.request_payload(s,sorted(aa,key=lambda a:a['index'])))
    for a in aa:
        if a.get('compression')=='queued' and not a.get('video_deleted'):store.queue(a['id'],'compress')
def encode(src,dst,crf):run(['ffmpeg','-v','error','-y','-i',str(src),'-map','0:v:0','-map','0:a:0?','-c:v','libx264','-preset','medium','-crf',str(crf),'-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',str(dst)])
def vmaf(dist,ref,logpath):
    # temp directory has a fixed local filename; run with its cwd to avoid filter-path escaping.
    graph='[0:v]settb=AVTB,setpts=PTS-STARTPTS[dist];[1:v]settb=AVTB,setpts=PTS-STARTPTS[ref];[dist][ref]libvmaf=log_fmt=json:log_path=score.json'
    p=subprocess.run(['ffmpeg','-v','error','-i',str(dist),'-i',str(ref),'-lavfi',graph,'-f','null','-'],cwd=logpath.parent,capture_output=True,text=True,timeout=300)
    if p.returncode:raise RuntimeError('libvmaf unavailable or failed: '+p.stderr[-500:])
    return float(json.loads(logpath.read_text())['pooled_metrics']['vmaf']['mean'])
def compress(a):
    src=original(a);d=store.media_dir(a['id']);out=d/'compressed.mp4';tmp=d/'candidate.mp4'
    if a.get('video_deleted'):return
    if out.exists() and not src.exists():a['compression']='complete';store.save_answer(a,'ready');return
    if not src.exists():raise RuntimeError('Original missing')
    duration=duration_of(src);target=float(os.getenv('VMAF_TARGET','80'));base=27; chosen=None; results=[]
    with tempfile.TemporaryDirectory(prefix='vmaf-') as td:
        p=Path(td);ref=p/'reference.mkv';dist=p/'distorted.mp4'
        # 5s from middle, normalized lossless reference (same geometry/fps as candidate).
        run(['ffmpeg','-v','error','-y','-ss',str(max(0,(duration-5)/2)),'-i',str(src),'-t','5','-an','-c:v','ffv1','-pix_fmt','yuv420p',str(ref)],120)
        for crf in [39,36,33,30,27,24,21,18]:
            encode(ref,dist,crf);score=vmaf(dist,ref,p/'score.json');results.append({'crf':crf,'vmaf':round(score,2)})
            if score>=target:chosen=crf;break
        if chosen is None:raise RuntimeError('No tested CRF reached VMAF target; original retained')
    encode(src,tmp,chosen);info=probe(tmp);new_duration=float(info['format']['duration'])
    if abs(new_duration-duration)>1 or not any(s['codec_type']=='video' for s in info['streams']) or not any(s['codec_type']=='audio' for s in info['streams']):raise RuntimeError('Compressed file failed duration/stream validation')
    run(['ffmpeg','-v','error','-xerror','-i',str(tmp),'-f','null','-'])
    if tmp.stat().st_size>=src.stat().st_size:
        tmp.unlink();a.update(compression='original_smaller',compression_trials=results);store.save_answer(a,'ready');return
    before=src.stat().st_size;tmp.replace(out);a.update(compression='complete',compression_trials=results,crf=chosen,original_bytes=before,compressed_bytes=out.stat().st_size);store.save_answer(a,'ready');src.unlink()

def delete_video(a):
    with media_lock:
        d=store.media_dir(a['id']);
        for p in d.iterdir():
            if p.is_file():p.unlink()
        a.update(video_deleted=True,thumbs=[]);store.save_answer(a,a['status'])
def retention():
    days=float(store.get_setting('workspace',{}).get('settings',{}).get('retentionDays',os.getenv('VIDEO_RETENTION_DAYS','4')))
    if days<=0:return
    for s in store.sessions():
        for a in store.answers(s['id']):
            if a['status']=='ready' and not a.get('video_deleted') and store.now()-a.get('recorded_at',store.now())>days*86400:delete_video(a)
def worker(stop):
    store.recover();last=0
    for s in store.sessions():prepare_export(s['id'])
    while not stop.is_set():
        if time.monotonic()-last>60:
            try:retention()
            except Exception:log.exception('Retention failed')
            last=time.monotonic()
        j=store.claim()
        if not j:stop.wait(1);continue
        with media_lock:
            a=store.answer(j['answer']);kind=j['kind']
            try:
                if not a or a.get('video_deleted'):pass
                elif kind=='analyse':store.save_answer(a,'processing');analyse(a)
                else:compress(a)
                store.job_done(j['id'],'done')
            except Exception as e:
                log.exception('Job %s failed',j['id']); a=store.answer(j['answer']) or a
                if a:
                    if kind=='analyse':a['analysis_error']=str(e)[:1200];store.save_answer(a,'failed')
                    else:
                        (store.media_dir(a['id'])/'candidate.mp4').unlink(missing_ok=True);a.update(compression='original_retained',compression_error=str(e)[:1200]);store.save_answer(a,'ready')
                store.job_done(j['id'],'failed')
