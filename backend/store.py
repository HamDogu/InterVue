import json, os, sqlite3, time, uuid
from pathlib import Path
ROOT=Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent.parent/'data')).resolve()
ROOT.mkdir(parents=True,exist_ok=True)
DB=ROOT/'intervue.sqlite3'
def connect():
    c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; return c

def init():
    with connect() as c:
        c.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY, session TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, answer TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, UNIQUE(answer,kind));''')
def uid(): return str(uuid.uuid4())
def now(): return time.time()
def dump(x): return json.dumps(x,ensure_ascii=False)
def get_setting(key,default=None):
    with connect() as c: r=c.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
    return json.loads(r['value']) if r else default
def set_setting(key,value):
    with connect() as c:c.execute('INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,dump(value)))
def session(sid):
    with connect() as c:r=c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
    return json.loads(r['data']) if r else None
def sessions():
    with connect() as c: rows=c.execute('SELECT data FROM sessions ORDER BY created DESC').fetchall()
    return [json.loads(r['data']) for r in rows]
def save_session(s):
    with connect() as c:c.execute('INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(s['id'],dump(s),s['created']))
def answer(aid):
    with connect() as c:r=c.execute('SELECT * FROM answers WHERE id=?',(aid,)).fetchone()
    return dict(json.loads(r['data']),status=r['status']) if r else None
def answers(sid):
    with connect() as c:rows=c.execute('SELECT * FROM answers WHERE session=? ORDER BY updated',(sid,)).fetchall()
    return [dict(json.loads(r['data']),status=r['status']) for r in rows]
def save_answer(a,status=None):
    a=dict(a); st=status or a.pop('status','uploading'); a.pop('status',None)
    with connect() as c:c.execute('INSERT INTO answers VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,status=excluded.status,updated=excluded.updated',(a['id'],a['session'],dump(a),st,now()))
def queue(aid,kind):
    with connect() as c:c.execute("INSERT INTO jobs(answer,kind,status) VALUES(?,?,'queued') ON CONFLICT(answer,kind) DO UPDATE SET status='queued' WHERE jobs.status IN ('failed','done')",(aid,kind))
def claim():
    with connect() as c:
        c.execute('BEGIN IMMEDIATE'); r=c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY CASE kind WHEN 'analyse' THEN 0 ELSE 1 END,id LIMIT 1").fetchone()
        if r:c.execute("UPDATE jobs SET status='running' WHERE id=?",(r['id'],))
        return dict(r) if r else None
def job_done(jid,status):
    with connect() as c:c.execute('UPDATE jobs SET status=? WHERE id=?',(status,jid))
def recover():
    with connect() as c:
        c.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        rows=c.execute("SELECT id,status FROM answers WHERE status IN ('queued','processing')").fetchall()
        for r in rows:c.execute("INSERT INTO jobs(answer,kind,status) VALUES(?,'analyse','queued') ON CONFLICT(answer,kind) DO UPDATE SET status='queued'",(r['id'],))
def media_dir(aid):
    # IDs only originate from the database; prevent traversal even if called elsewhere.
    uuid.UUID(aid); p=ROOT/'media'/aid;p.mkdir(parents=True,exist_ok=True);return p
