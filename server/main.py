"""Comm Desk server: serves the single-page app and keeps its data in SQLite.

The browser owns all the case logic. The server stores the app's state as one JSON document with a
version number and refuses writes based on an older version, so two tabs can't silently overwrite
each other. Sign-in uses one static password (stored as a scrypt hash) and a signed 24-hour cookie.

    python server/main.py set-password      # once, before first run
    python server/main.py                   # serve on 127.0.0.1:8000
    python server/main.py snapshots         # list snapshots
    python server/main.py restore <id>      # roll the data back to a snapshot
    python server/main.py erase             # delete all data (a snapshot is kept first)
"""

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_DIR = Path(os.environ.get("COMMDESK_DATA", ROOT.parent / "data")).resolve()
DB_PATH = DATA_DIR / "commdesk.db"
PASSWORD_FILE = DATA_DIR / "password.hash"
SECRET_FILE = DATA_DIR / "secret.key"
HOST = os.environ.get("COMMDESK_HOST", "127.0.0.1")
PORT = int(os.environ.get("COMMDESK_PORT", "8000"))

COOKIE = "commdesk_session"
TOKEN_TTL = 24 * 3600
SNAPSHOT_EVERY = 3600          # keep at most one snapshot per hour of saving
SNAPSHOT_KEEP = 30 * 24 * 3600  # and drop snapshots older than 30 days
MAX_FAILS, FAIL_WINDOW = 5, 60  # sign-in attempts allowed per minute
NO_STORE = {"Cache-Control": "no-store"}


# ---------- files in the data directory ----------

def write_private(path: Path, text: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def load_secret() -> bytes:
    """Key that signs session cookies. Delete the file to sign out every session."""
    if not SECRET_FILE.exists():
        write_private(SECRET_FILE, secrets.token_hex(32))
    return bytes.fromhex(SECRET_FILE.read_text().strip())


# ---------- password ----------

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode()
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{b64(salt)}:{b64(key)}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, key = stored.split(":")
        expected = base64.urlsafe_b64decode(key)
        got = hashlib.scrypt(password.encode(), salt=base64.urlsafe_b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except ValueError:
        return False
    return hmac.compare_digest(got, expected)


def stored_password() -> str:
    value = os.environ.get("COMMDESK_PASSWORD_HASH") or (
        PASSWORD_FILE.read_text().strip() if PASSWORD_FILE.exists() else "")
    if not value:
        sys.exit("No password set. Run: python server/main.py set-password")
    return value


# ---------- session tokens: "<expiry>.<hmac>" ----------

def make_token(secret: bytes) -> str:
    exp = str(int(time.time()) + TOKEN_TTL)
    return f"{exp}.{hmac.new(secret, exp.encode(), hashlib.sha256).hexdigest()}"


def token_ok(token: str | None, secret: bytes) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    good = hmac.new(secret, exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and exp.isdigit() and int(exp) > time.time()


# ---------- database ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS state(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  version INTEGER NOT NULL,
  data TEXT NOT NULL,
  saved_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY,
  version INTEGER NOT NULL,
  data TEXT NOT NULL,
  saved_at REAL NOT NULL,
  note TEXT NOT NULL DEFAULT 'hourly');
"""
EMPTY_STATE = json.dumps({"cases": [], "commands": [], "archive": []})


@contextmanager
def db(write: bool = False):
    con = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        if write:
            con.execute("BEGIN IMMEDIATE")  # takes the write lock before reading the version
        try:
            yield con
            if write:
                con.execute("COMMIT")
        except BaseException:
            if write:
                con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(SCHEMA)
        if "note" not in {row[1] for row in con.execute("PRAGMA table_info(snapshots)")}:
            con.execute("ALTER TABLE snapshots ADD COLUMN note TEXT NOT NULL DEFAULT 'hourly'")
    os.chmod(DB_PATH, 0o600)


def current_state(con: sqlite3.Connection) -> tuple[int, str | None, float]:
    row = con.execute("SELECT version, data, saved_at FROM state WHERE id = 1").fetchone()
    return row if row else (0, None, 0.0)


def snapshot_current(con: sqlite3.Connection, note: str) -> int | None:
    """Keep a copy of the saved data before something replaces all of it."""
    version, data, _ = current_state(con)
    if data is None:
        return None
    cur = con.execute("INSERT INTO snapshots(version, data, saved_at, note) VALUES (?, ?, ?, ?)",
                      (version, data, time.time(), note))
    return cur.lastrowid


def write_state(con: sqlite3.Connection, version: int, data: str) -> float:
    now = time.time()
    con.execute(
        "INSERT INTO state(id, version, data, saved_at) VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET version = excluded.version, data = excluded.data, saved_at = excluded.saved_at",
        (version, data, now))
    last = con.execute("SELECT MAX(saved_at) FROM snapshots WHERE note = 'hourly'").fetchone()[0]
    if last is None or now - last >= SNAPSHOT_EVERY:
        con.execute("INSERT INTO snapshots(version, data, saved_at) VALUES (?, ?, ?)", (version, data, now))
        con.execute("DELETE FROM snapshots WHERE saved_at < ?", (now - SNAPSHOT_KEEP,))
    return now


# ---------- app ----------

class StateIn(BaseModel):
    version: int | None  # the version this state was based on; null overwrites whatever is saved
    data: dict[str, Any]
    snapshot: bool = False  # keep a copy of the current data first (sent by restore and overwrite)


class LoginIn(BaseModel):
    password: str


def create_app() -> FastAPI:
    secret = load_secret()
    password = stored_password()
    init_db()
    fails: list[float] = []
    fails_lock = threading.Lock()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def signed_in(request: Request) -> bool:
        return token_ok(request.cookies.get(COOKIE), secret)

    def require_auth(request: Request) -> None:
        if not signed_in(request):
            raise HTTPException(401, "Sign in first")

    @app.get("/")
    def index(request: Request):
        if not signed_in(request):
            return RedirectResponse("/login", 303)
        return FileResponse(STATIC / "index.html", headers=NO_STORE)

    @app.get("/login")
    def login_page(request: Request):
        if signed_in(request):
            return RedirectResponse("/", 303)
        return FileResponse(STATIC / "login.html", headers=NO_STORE)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        # browsers ask for /favicon.ico on their own (history, bookmarks); answer with the SVG logo
        return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")

    @app.post("/api/login")
    def login(body: LoginIn, request: Request):
        now = time.time()
        with fails_lock:
            fails[:] = [t for t in fails if now - t < FAIL_WINDOW]
            if len(fails) >= MAX_FAILS:
                raise HTTPException(429, "Too many attempts")
        if not check_password(body.password, password):
            with fails_lock:
                fails.append(now)
            time.sleep(1)
            raise HTTPException(401, "Wrong password")
        res = JSONResponse({"ok": True})
        res.set_cookie(COOKIE, make_token(secret), max_age=TOKEN_TTL, httponly=True, samesite="strict",
                       secure=request.url.scheme == "https", path="/")
        return res

    @app.post("/api/logout")
    def logout():
        res = JSONResponse({"ok": True})
        res.delete_cookie(COOKIE, path="/")
        return res

    @app.get("/api/state", dependencies=[Depends(require_auth)])
    def get_state():
        with db() as con:
            version, data, saved_at = current_state(con)
        return JSONResponse({"version": version, "savedAt": saved_at,
                             "data": json.loads(data) if data else None}, headers=NO_STORE)

    @app.get("/api/state/version", dependencies=[Depends(require_auth)])
    def get_version():
        with db() as con:
            version, _, saved_at = current_state(con)
        return JSONResponse({"version": version, "savedAt": saved_at}, headers=NO_STORE)

    @app.put("/api/state", dependencies=[Depends(require_auth)])
    def put_state(body: StateIn):
        if not isinstance(body.data.get("cases"), list) or not isinstance(body.data.get("commands"), list):
            raise HTTPException(422, "Not a Comm Desk state")
        text = json.dumps(body.data, ensure_ascii=False, separators=(",", ":"))
        with db(write=True) as con:
            version, _, _ = current_state(con)
            if body.version is not None and body.version != version:
                return JSONResponse({"version": version}, status_code=409, headers=NO_STORE)
            if body.snapshot:
                snapshot_current(con, "before replace")
            saved_at = write_state(con, version + 1, text)
        return JSONResponse({"version": version + 1, "savedAt": saved_at}, headers=NO_STORE)

    return app


# ---------- command line ----------

def cmd_set_password() -> None:
    first = getpass.getpass("New password: ")
    if len(first) < 8:
        sys.exit("Use at least 8 characters.")
    if getpass.getpass("Repeat it: ") != first:
        sys.exit("The passwords don't match.")
    write_private(PASSWORD_FILE, hash_password(first) + "\n")
    print(f"Saved to {PASSWORD_FILE}. Restart the server to use it.")


def cmd_snapshots() -> None:
    init_db()
    with db() as con:
        rows = con.execute("SELECT id, version, saved_at, data, note FROM snapshots ORDER BY id DESC").fetchall()
    if not rows:
        print("No snapshots yet.")
    for sid, version, saved_at, data, note in rows:
        cases = len(json.loads(data).get("cases", []))
        print(f"{sid:>5}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(saved_at))}  v{version:<5} {cases:>3} cases  {note}")


def cmd_restore(sid: str) -> None:
    init_db()
    with db(write=True) as con:
        row = con.execute("SELECT data FROM snapshots WHERE id = ?", (int(sid),)).fetchone()
        if not row:
            sys.exit(f"No snapshot {sid}.")
        kept = snapshot_current(con, "before restore")
        version, _, _ = current_state(con)
        write_state(con, version + 1, row[0])
    print(f"Restored snapshot {sid} as version {version + 1}. Open tabs will load it when you return to them.")
    if kept:
        print(f"The data it replaced is snapshot {kept}.")


def cmd_erase() -> None:
    init_db()
    if input('This deletes all cases, commands, links and variables. Type "erase" to continue: ').strip() != "erase":
        sys.exit("Nothing changed.")
    with db(write=True) as con:
        kept = snapshot_current(con, "before erase")
        version, _, _ = current_state(con)
        write_state(con, version + 1, EMPTY_STATE)
    print("All data erased." + (f" To undo: python server/main.py restore {kept}" if kept else ""))


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["set-password"]:
        cmd_set_password()
    elif args[:1] == ["snapshots"]:
        cmd_snapshots()
    elif args[:1] == ["restore"] and len(args) == 2:
        cmd_restore(args[1])
    elif args[:1] == ["erase"]:
        cmd_erase()
    elif not args:
        import uvicorn
        uvicorn.run(create_app(), host=HOST, port=PORT, proxy_headers=False)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
