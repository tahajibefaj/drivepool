"""
DrivePool — Unified Google Drive Manager
Hosted on Render.com (free tier), persistent disk at /data
"""
import os, sqlite3, pickle, io, json, time, threading
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, UploadFile, File as FFile, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# ── Paths — persistent on Render (/data), fallback local ──────────────────────
DATA_DIR  = Path(os.getenv("DATA_DIR", "./data"))
TOKENS    = DATA_DIR / "tokens"
DB_PATH   = DATA_DIR / "drivepool.db"
SECRETS   = Path(os.getenv("SECRETS_PATH", "client_secrets.json"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKENS.mkdir(parents=True, exist_ok=True)

PORT     = int(os.getenv("PORT", 8000))
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# ── Active transfer tracking (prevents sleep during transfers) ─────────────────
_active_transfers: set[str] = set()
_last_activity    = time.time()

def touch():
    global _last_activity
    _last_activity = time.time()

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="DrivePool")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="frontend/static", html=False), name="static") if Path("frontend/static").exists() else None

# ── DB ─────────────────────────────────────────────────────────────────────────
def db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    c = db()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT UNIQUE NOT NULL,
            name        TEXT,
            avatar      TEXT,
            token_file  TEXT,
            total_bytes INTEGER DEFAULT 0,
            used_bytes  INTEGER DEFAULT 0,
            synced_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS files (
            gid         TEXT NOT NULL,
            account_id  INTEGER NOT NULL,
            name        TEXT,
            mime        TEXT,
            size        INTEGER DEFAULT 0,
            parent_gid  TEXT,
            created_at  TEXT,
            modified_at TEXT,
            trashed     INTEGER DEFAULT 0,
            starred     INTEGER DEFAULT 0,
            view_link   TEXT,
            PRIMARY KEY(gid, account_id)
        );
        CREATE INDEX IF NOT EXISTS idx_files_acc     ON files(account_id);
        CREATE INDEX IF NOT EXISTS idx_files_parent  ON files(parent_gid);
        CREATE INDEX IF NOT EXISTS idx_files_trashed ON files(trashed);
        CREATE INDEX IF NOT EXISTS idx_files_name    ON files(name);
    """)
    c.commit(); c.close()

init_db()

# ── Helpers ────────────────────────────────────────────────────────────────────
def human(b: int) -> str:
    if not b: return "0 B"
    for u in ["B","KB","MB","GB","TB"]:
        if abs(b) < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

def pct(used, total): return round(used/total*100, 1) if total else 0

def get_creds(account_id: int) -> Credentials:
    c = db()
    row = c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    c.close()
    if not row: raise HTTPException(404, "Account not found")
    tf = Path(row["token_file"]) if row["token_file"] else None
    if not tf or not tf.exists():
        raise HTTPException(401, f"Token missing for {row['email']} — re-authenticate")
    with open(tf, "rb") as f:
        creds: Credentials = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(GRequest())
        with open(tf, "wb") as f: pickle.dump(creds, f)
    return creds

def svc(account_id: int, api="drive", version="v3"):
    return build(api, version, credentials=get_creds(account_id), cache_discovery=False)

def make_flow(state=None):
    flow = Flow.from_client_secrets_file(
        str(SECRETS), scopes=SCOPES,
        redirect_uri=f"{BASE_URL}/auth/callback"
    )
    if state: flow.state = state
    return flow

# ── Pages ──────────────────────────────────────────────────────────────────────
def html(name): return open(f"frontend/{name}").read()

@app.get("/", response_class=HTMLResponse)
async def index(): touch(); return html("index.html")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(): touch(); return html("dashboard.html")

# ── Wake / ping ────────────────────────────────────────────────────────────────
@app.get("/api/ping")
async def ping():
    touch()
    return {
        "ok": True,
        "ts": int(time.time()),
        "active_transfers": len(_active_transfers),
        "uptime_s": int(time.time() - _start_time),
    }

@app.post("/api/keepalive")
async def keepalive(transfer_id: str = Form(...)):
    """Client calls this every 30s during active upload/download."""
    touch()
    _active_transfers.add(transfer_id)
    return {"ok": True}

@app.delete("/api/keepalive/{transfer_id}")
async def end_transfer(transfer_id: str):
    _active_transfers.discard(transfer_id)
    return {"ok": True}

# ── Auth ───────────────────────────────────────────────────────────────────────
@app.get("/auth/start")
async def auth_start():
    touch()
    if not SECRETS.exists():
        raise HTTPException(503, "client_secrets.json not found. Check DATA_DIR or SECRETS_PATH.")
    flow = make_flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    (TOKENS / f"state_{state}.tmp").write_text(state)
    return {"auth_url": url}

@app.get("/auth/callback")
async def auth_callback(code: str, state: str):
    touch()
    flow = make_flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials

    info = build("oauth2","v2",credentials=creds,cache_discovery=False).userinfo().get().execute()
    email  = info["email"]
    name   = info.get("name", email)
    avatar = info.get("picture", "")

    about = build("drive","v3",credentials=creds,cache_discovery=False).about().get(
        fields="storageQuota").execute().get("storageQuota", {})
    total = int(about.get("limit", 0))
    used  = int(about.get("usage",  0))

    safe = email.replace("@","_").replace(".","_")
    tf   = str(TOKENS / f"{safe}.pkl")
    with open(tf, "wb") as f: pickle.dump(creds, f)

    c = db()
    c.execute("""
        INSERT INTO accounts(email,name,avatar,token_file,total_bytes,used_bytes)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(email) DO UPDATE SET
          name=excluded.name, avatar=excluded.avatar, token_file=excluded.token_file,
          total_bytes=excluded.total_bytes, used_bytes=excluded.used_bytes
    """, (email,name,avatar,tf,total,used))
    c.commit(); c.close()

    tmp = TOKENS / f"state_{state}.tmp"
    if tmp.exists(): tmp.unlink()

    return RedirectResponse("/dashboard")

# ── Accounts API ───────────────────────────────────────────────────────────────
@app.get("/api/accounts")
async def list_accounts():
    touch()
    c = db()
    rows = [dict(r) for r in c.execute("SELECT * FROM accounts ORDER BY id").fetchall()]
    c.close()
    return rows

@app.delete("/api/accounts/{aid}")
async def remove_account(aid: int):
    touch()
    c = db()
    row = c.execute("SELECT token_file FROM accounts WHERE id=?", (aid,)).fetchone()
    if row and row["token_file"]:
        try: Path(row["token_file"]).unlink()
        except: pass
    c.execute("DELETE FROM files   WHERE account_id=?", (aid,))
    c.execute("DELETE FROM accounts WHERE id=?",        (aid,))
    c.commit(); c.close()
    return {"ok": True}

# ── Stats ──────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def stats():
    touch()
    c = db()
    accs    = [dict(r) for r in c.execute("SELECT * FROM accounts").fetchall()]
    files   = c.execute("SELECT COUNT(*) FROM files WHERE trashed=0 AND mime!='application/vnd.google-apps.folder'").fetchone()[0]
    folders = c.execute("SELECT COUNT(*) FROM files WHERE trashed=0 AND mime='application/vnd.google-apps.folder'").fetchone()[0]
    trashed = c.execute("SELECT COUNT(*) FROM files WHERE trashed=1").fetchone()[0]
    c.close()
    total = sum(a["total_bytes"] for a in accs)
    used  = sum(a["used_bytes"]  for a in accs)
    return {
        "accounts": len(accs),
        "total_bytes": total,  "total_human": human(total),
        "used_bytes":  used,   "used_human":  human(used),
        "free_bytes":  total-used, "free_human": human(total-used),
        "files": files, "folders": folders, "trashed": trashed,
        "pct_used": pct(used, total),
        "account_list": accs,
    }

# ── Files ──────────────────────────────────────────────────────────────────────
@app.get("/api/files")
async def list_files(
    account_id: Optional[int] = None,
    parent_gid: Optional[str] = None,
    trashed: bool = False,
    q: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
):
    touch()
    c = db()
    sql = """SELECT f.*, a.email, a.name as acct_name, a.avatar
             FROM files f JOIN accounts a ON f.account_id=a.id
             WHERE f.trashed=?"""
    params: list = [1 if trashed else 0]
    if account_id: sql += " AND f.account_id=?"; params.append(account_id)
    if parent_gid: sql += " AND f.parent_gid=?"; params.append(parent_gid)
    if q:          sql += " AND f.name LIKE ?";  params.append(f"%{q}%")
    sql += " ORDER BY f.mime DESC, LOWER(f.name) LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = [dict(r) for r in c.execute(sql, params).fetchall()]
    c.close()
    return rows

@app.post("/api/sync")
async def sync_all():
    """Pull fresh file listing from every connected Google Drive account."""
    touch()
    c = db()
    accs = c.execute("SELECT * FROM accounts").fetchall()
    summary = []
    for acc in accs:
        try:
            drv = svc(acc["id"])
            about = drv.about().get(fields="storageQuota").execute().get("storageQuota",{})
            total = int(about.get("limit", 0)); used = int(about.get("usage", 0))
            c.execute("UPDATE accounts SET total_bytes=?,used_bytes=?,synced_at=? WHERE id=?",
                      (total,used,datetime.utcnow().isoformat(),acc["id"]))

            c.execute("DELETE FROM files WHERE account_id=?", (acc["id"],))
            token, n = None, 0
            while True:
                res = drv.files().list(
                    pageSize=1000, pageToken=token,
                    fields="nextPageToken,files(id,name,mimeType,size,parents,"
                           "createdTime,modifiedTime,trashed,starred,webViewLink)",
                ).execute()
                for f in res.get("files",[]):
                    parent = (f.get("parents") or [None])[0]
                    c.execute("""INSERT OR REPLACE INTO files
                        (gid,account_id,name,mime,size,parent_gid,created_at,modified_at,trashed,starred,view_link)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (f["id"],acc["id"],f.get("name",""),f.get("mimeType",""),
                         int(f.get("size",0)),parent,f.get("createdTime"),f.get("modifiedTime"),
                         1 if f.get("trashed") else 0,
                         1 if f.get("starred")  else 0,
                         f.get("webViewLink","")))
                    n += 1
                token = res.get("nextPageToken")
                if not token: break
            c.commit()
            summary.append({"email":acc["email"],"files":n,"ok":True})
        except Exception as e:
            summary.append({"email":acc["email"],"error":str(e),"ok":False})
    c.close()
    return {"synced": summary}

@app.post("/api/upload")
async def upload(file: UploadFile = FFile(...), parent_gid: Optional[str] = Form(None),
                 transfer_id: Optional[str] = Form(None)):
    touch()
    if transfer_id: _active_transfers.add(transfer_id)
    try:
        # Pick account with most free space
        c = db()
        best = c.execute(
            "SELECT * FROM accounts WHERE total_bytes>used_bytes ORDER BY (total_bytes-used_bytes) DESC LIMIT 1"
        ).fetchone()
        c.close()
        if not best: raise HTTPException(400,"No accounts with free space. Connect more accounts.")

        content = await file.read()
        meta    = {"name": file.filename}
        if parent_gid: meta["parents"] = [parent_gid]

        drv    = svc(best["id"])
        media  = MediaIoBaseUpload(io.BytesIO(content),
                                   mimetype=file.content_type or "application/octet-stream",
                                   resumable=len(content) > 5_000_000)
        result = drv.files().create(body=meta, media_body=media,
                                    fields="id,name,mimeType,size,parents,createdTime,modifiedTime,webViewLink"
                                    ).execute()

        parent_r = (result.get("parents") or [None])[0]
        c = db()
        c.execute("""INSERT OR REPLACE INTO files
            (gid,account_id,name,mime,size,parent_gid,created_at,modified_at,trashed,starred,view_link)
            VALUES(?,?,?,?,?,?,?,?,0,0,?)""",
            (result["id"],best["id"],result.get("name",""),result.get("mimeType",""),
             int(result.get("size",0)),parent_r,
             result.get("createdTime"),result.get("modifiedTime"),result.get("webViewLink","")))
        c.execute("UPDATE accounts SET used_bytes=used_bytes+? WHERE id=?",
                  (int(result.get("size",0)),best["id"]))
        c.commit(); c.close()
        return {**result, "routed_to": best["email"]}
    finally:
        if transfer_id: _active_transfers.discard(transfer_id)

@app.get("/api/files/{gid}/download")
async def download_file(gid: str, transfer_id: Optional[str] = None):
    touch()
    if transfer_id: _active_transfers.add(transfer_id)
    c = db()
    row = c.execute("SELECT * FROM files WHERE gid=? LIMIT 1",(gid,)).fetchone()
    c.close()
    if not row: raise HTTPException(404,"File not found")
    try:
        buf = io.BytesIO()
        req = svc(row["account_id"]).files().get_media(fileId=gid)
        dl  = MediaIoBaseDownload(buf, req, chunksize=10*1024*1024)
        done = False
        while not done: _, done = dl.next_chunk()
        buf.seek(0)
        return StreamingResponse(buf,
            media_type=row["mime"] or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{row["name"]}"'})
    finally:
        if transfer_id: _active_transfers.discard(transfer_id)

@app.delete("/api/files/{gid}")
async def delete_file(gid: str, permanent: bool = False):
    touch()
    c = db()
    row = c.execute("SELECT * FROM files WHERE gid=? LIMIT 1",(gid,)).fetchone()
    if not row: raise HTTPException(404,"File not found")
    drv = svc(row["account_id"])
    if permanent:
        drv.files().delete(fileId=gid).execute()
        c.execute("DELETE FROM files WHERE gid=?",(gid,))
    else:
        drv.files().update(fileId=gid, body={"trashed": True}).execute()
        c.execute("UPDATE files SET trashed=1 WHERE gid=?",(gid,))
    c.commit(); c.close()
    return {"ok": True}

@app.post("/api/files/{gid}/restore")
async def restore_file(gid: str):
    touch()
    c = db()
    row = c.execute("SELECT * FROM files WHERE gid=? LIMIT 1",(gid,)).fetchone()
    if not row: raise HTTPException(404,"File not found")
    svc(row["account_id"]).files().update(fileId=gid, body={"trashed": False}).execute()
    c.execute("UPDATE files SET trashed=0 WHERE gid=?",(gid,))
    c.commit(); c.close()
    return {"ok": True}

@app.post("/api/mkdir")
async def mkdir(name: str = Form(...), account_id: int = Form(...),
                parent_gid: Optional[str] = Form(None)):
    touch()
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_gid: meta["parents"] = [parent_gid]
    result = svc(account_id).files().create(
        body=meta, fields="id,name,mimeType,createdTime,modifiedTime").execute()
    c = db()
    c.execute("""INSERT OR REPLACE INTO files
        (gid,account_id,name,mime,size,parent_gid,created_at,modified_at,trashed,starred,view_link)
        VALUES(?,?,?,?,0,?,?,?,0,0,'')""",
        (result["id"],account_id,name,"application/vnd.google-apps.folder",
         parent_gid,result.get("createdTime"),result.get("modifiedTime")))
    c.commit(); c.close()
    return dict(result)

# ── Boot ───────────────────────────────────────────────────────────────────────
_start_time = time.time()

if __name__ == "__main__":
    import uvicorn
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT","1")
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
