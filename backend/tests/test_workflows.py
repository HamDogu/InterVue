import os, tempfile, uuid, json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import store,media,contracts
from backend.main import app
@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'ROOT',tmp_path);monkeypatch.setattr(store,'DB',tmp_path/'test.sqlite3');store.init();return TestClient(app)
def session(client):
    r=client.post('/api/sessions',json={'profile':{'title':'Example','company':'Example Company'},'settings':{'prep':120,'answer':120},'questions':[{'id':'q1','text':'Tell us about yourself.','tags':['Personal']}]});assert r.status_code==200;return r.json()
def ready(client):
    s=session(client);p=f"/api/sessions/{s['id']}";a=client.post(p+'/answers',json={'index':0,'extension':'webm','client_id':str(uuid.uuid4()),'duration':12}).json();path=f"/api/answers/{a['id']}";raw=b'example video bytes'
    assert client.put(path+'/chunk?offset=0',content=raw).json()['offset']==len(raw)
    assert client.put(path+'/chunk?offset=0',content=raw).json()['offset']==len(raw)
    assert client.put(path+'/chunk?offset=100',content=b'x').status_code==409
    assert client.post(path+'/complete',json={'size':len(raw)}).status_code==200
    assert client.get(p+'/assessment-request').status_code==409
    a=store.answer(a['id']);a.update(transcript='I organised a team.',segments=[{'start':0,'end':3,'text':'I organised a team.'}],metrics=media.metrics('I organised a team.',[],12,120),compression='queued');store.save_answer(a,'ready')
    assert client.post(p+'/finish',json={}).status_code==200
    return s,a,p,path

def test_roundtrip(client):
    s,a,p,path=ready(client);r=client.get(p+'/assessment-request');assert r.status_code==200;req=r.json();assert req['answers'][0]['id']==a['id'];assert store.get_setting('request:'+s['id'])['session_id']==s['id'];res=req['result_schema_example'];assert client.post(p+'/assessment',json=res).status_code==200
    res['session_id']='wrong';assert client.post(p+'/assessment',json=res).status_code==400
    res['session_id']=s['id'];res['questions'][0]['scores']['relevance']=999;assert client.post(p+'/assessment',json=res).status_code==400
    assert client.delete(path+'/video').status_code==200;out=client.get(p).json();assert out['answers'][0]['transcript']=='I organised a team.';assert out['assessment'];assert client.get(path+'/video').status_code==404

def test_recover_and_retention(client):
    s,a,p,path=ready(client);j=store.claim();assert j['kind']=='analyse';store.recover();assert store.claim()['id']==j['id'];a=store.answer(a['id']);a['recorded_at']=store.now()-5*86400;store.save_answer(a,'ready');media.retention();assert store.answer(a['id'])['video_deleted']

def test_edit_and_bulk(client):
    s,a,p,path=ready(client);r=client.patch(path+'/transcript',json={'transcript':'Corrected answer.'});assert r.status_code==200;assert r.json()['segments']==[];assert r.json()['segments_original'];assert client.get(p+'/assessment-request').json()['answers'][0]['transcript']=='Corrected answer.';assert client.delete('/api/videos').json()['deleted']==1;assert client.delete(p).status_code==200;assert store.get_setting('request:'+s['id']) is None

def test_validation(client):
    assert client.post('/api/sessions',json={'questions':[],'settings':{}}).status_code==400
    assert client.put('/api/workspace',json={'settings':{'retentionDays':-1}}).status_code==400
    assert client.get('/api/voices').json()==[]
    assert client.post('/api/voice',json={'voice':'not-approved','text':'Hello'}).status_code==400

def test_metrics():
    m=media.metrics('Um I worked with a team', [{'start':0,'end':1},{'start':4,'end':5}], 60,120);assert m['words_per_minute']==6;assert m['filler_words']==1;assert m['time_utilised']==50;assert len(m['pauses'])==1

def test_duration_and_ffmpeg(tmp_path):
    import shutil
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg not installed')
    p=tmp_path/'test.webm';media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=160x120:rate=12','-f','lavfi','-i','sine=frequency=440','-t','1','-c:v','libvpx','-c:a','libopus','-f','webm','-live','1',str(p)]);assert .8<=media.duration_of(p)<=1.2
    out=tmp_path/'out.mp4';media.encode(p,out,27);assert out.exists();assert .8<=media.duration_of(out)<=1.2
