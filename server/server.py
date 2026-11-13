#!/usr/bin/env python3
"""After you press Run: the server. Accounts, progress saved per account, and a leaderboard.

One file, Python 3.8+, standard library only. Data is one SQLite file. It also serves the site itself, so one
command gives the whole thing:

    python3 server/server.py                      # site + API on http://localhost:8000
    python3 server/server.py --port 9000 --db data/run.db --site index.html

Other commands (the server may keep running while you use them):

    python3 server/server.py users                # list accounts
    python3 server/server.py reset-password NAME  # set a new password for NAME (asks for it)
    python3 server/server.py remove NAME          # delete an account and its progress
    python3 server/server.py backup FILE          # copy the database safely to FILE

Environment: PORT (default 8000), AYPR_DB, AYPR_SITE, AYPR_ORIGIN (only when the page is hosted on another address:
that address, for example https://name.github.io).

Design rules, so that it stays up:
  - Every request is answered with JSON, whatever was sent. No request can stop the server.
  - Passwords are never stored: only a salted PBKDF2 hash. Session tokens are stored only as a hash.
  - The leaderboard numbers are worked out here from the saved progress and are capped, never taken on trust.
  - Request size, text lengths and request rates are limited.
"""
import argparse, getpass, hashlib, hmac, json, os, re, secrets, sqlite3, sys, threading, time, unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_BODY = 400_000            # bytes in one request
MAX_SAVE = 300_000            # bytes of saved progress per account
SESSION_DAYS = 60
PBKDF2_ROUNDS = 200_000
XP_CAP, TOPICS, TESTS, LEVELS = 20_000, 15, 4, 7
SAVE_KEY = re.compile(r'^(lpr2|xp|streak|btm|tests|name|codes2|lex|pre|badges|done2|lastL2|prob:[a-z0-9]{1,24})$')
now = lambda: int(time.time())

# ---------------------------------------------------------------- database
class DB:
    def __init__(self, path):
        self.path = path; self.lock = threading.Lock()
        d = os.path.dirname(os.path.abspath(path)); os.makedirs(d, exist_ok=True)
        with self.conn() as c:
            c.executescript("""
              CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, lname TEXT NOT NULL UNIQUE,
                salt BLOB NOT NULL, hash BLOB NOT NULL, created INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS sessions(th TEXT PRIMARY KEY, user INTEGER NOT NULL, created INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS progress(user INTEGER PRIMARY KEY, rev INTEGER NOT NULL, data TEXT NOT NULL,
                xp INTEGER NOT NULL, topics INTEGER NOT NULL, tests INTEGER NOT NULL, levels INTEGER NOT NULL, updated INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS board ON progress(xp DESC, topics DESC, updated ASC);
              CREATE TABLE IF NOT EXISTS fails(lname TEXT PRIMARY KEY, n INTEGER NOT NULL, until INTEGER NOT NULL);""")
    def conn(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL'); c.execute('PRAGMA busy_timeout=15000'); c.execute('PRAGMA foreign_keys=ON')
        return c
    def read(self, sql, args=()):
        c = self.conn()
        try: return c.execute(sql, args).fetchall()
        finally: c.close()
    def write(self, fn):
        """Run fn(connection) inside one transaction. One writer at a time."""
        with self.lock:
            c = self.conn()
            try:
                c.execute('BEGIN IMMEDIATE'); r = fn(c); c.execute('COMMIT'); return r
            except BaseException:
                try: c.execute('ROLLBACK')
                except sqlite3.Error: pass
                raise
            finally: c.close()

def pw_hash(password, salt): return hashlib.pbkdf2_hmac('sha256', password.encode('utf8'), salt, PBKDF2_ROUNDS)
def tok_hash(token): return hashlib.sha256(token.encode('utf8')).hexdigest()

# ---------------------------------------------------------------- rules
class Bad(Exception):
    def __init__(self, status, msg): self.status, self.msg = status, msg

def clean_name(v):
    if not isinstance(v, str): raise Bad(400, 'Give a name.')
    v = ' '.join(v.split())
    cat = [unicodedata.category(ch)[0] for ch in v]      # letters of any language (with their marks), digits, and space . - _ inside
    good = 3 <= len(v) <= 20 and cat[0] in 'LN' and cat[-1] in 'LMN' and all(c in 'LMN' or ch in ' .-_' for c, ch in zip(cat, v))
    if not good: raise Bad(400, 'A name has 3 to 20 letters or digits. Spaces, dots, - and _ are allowed inside it.')
    return v
def clean_pw(v):
    if not isinstance(v, str) or not 6 <= len(v) <= 100: raise Bad(400, 'A password has 6 to 100 characters.')
    return v
def as_int(v, cap):
    try: n = int(v)
    except (TypeError, ValueError, OverflowError): return 0
    return max(0, min(cap, n))

def summary(data):
    """The leaderboard numbers, worked out here from the saved progress, and capped."""
    def js(k, d):
        try: v = json.loads(data.get(k, ''))
        except (TypeError, ValueError): return d
        return v if isinstance(v, type(d)) else d
    xp = as_int(js('xp', 0), XP_CAP)
    lpr = js('lpr2', {}); topics = min(TOPICS, sum(1 for v in lpr.values() if isinstance(v, dict) and v.get('fin') is True))
    tests = js('tests', {}); passed = min(TESTS, sum(1 for v in tests.values() if isinstance(v, dict) and isinstance(v.get('quiz'), dict) and as_int(v['quiz'].get('best'), 10) >= 7))
    levels = min(LEVELS, len(js('btm', {})))
    return xp, topics, passed, levels

def clean_save(v):
    if not isinstance(v, dict): raise Bad(400, 'Progress must be a set of keys and texts.')
    out = {}
    for k, s in v.items():
        if isinstance(k, str) and SAVE_KEY.match(k) and isinstance(s, str): out[k] = s
    text = json.dumps(out, separators=(',', ':'))
    if len(text.encode('utf8')) > MAX_SAVE: raise Bad(413, 'That is too much to save.')
    return out, text

class Limits:
    """Simple request limits per address, kept in memory. Generous: a whole class may share one address."""
    def __init__(self): self.hits = {}; self.lock = threading.Lock()
    def ok(self, key, n, secs):
        t = time.time()
        with self.lock:
            if len(self.hits) > 50_000: self.hits.clear()
            q = [x for x in self.hits.get(key, ()) if x > t - secs]
            if len(q) >= n: self.hits[key] = q; return False
            q.append(t); self.hits[key] = q; return True

# ---------------------------------------------------------------- the API
class App:
    def __init__(self, db_path, site_path, origin=''):
        self.db = DB(db_path); self.site_path = site_path; self.origin = origin.rstrip('/'); self.limits = Limits(); self.started = now()

    def user_of(self, token):
        if not token or len(token) > 200: return None
        r = self.db.read('SELECT u.id,u.name,s.created FROM sessions s JOIN users u ON u.id=s.user WHERE s.th=?', (tok_hash(token),))
        if not r or r[0][2] < now() - SESSION_DAYS * 86400: return None
        return {'id': r[0][0], 'name': r[0][1]}
    def need_user(self, token):
        u = self.user_of(token)
        if not u: raise Bad(401, 'Please log in again.')
        return u
    def new_session(self, c, uid):
        token = secrets.token_urlsafe(32)
        c.execute('DELETE FROM sessions WHERE created<?', (now() - SESSION_DAYS * 86400,))
        c.execute('INSERT INTO sessions(th,user,created) VALUES(?,?,?)', (tok_hash(token), uid, now()))
        return token

    def register(self, body, ip):
        if not self.limits.ok(('reg', ip), 120, 3600): raise Bad(429, 'Too many new accounts from here. Try again later.')
        name, pw = clean_name(body.get('name')), clean_pw(body.get('password')); salt = secrets.token_bytes(16); h = pw_hash(pw, salt)
        def go(c):
            if c.execute('SELECT 1 FROM users WHERE lname=?', (name.casefold(),)).fetchone(): raise Bad(409, 'That name is taken. Choose another, or log in.')
            uid = c.execute('INSERT INTO users(name,lname,salt,hash,created) VALUES(?,?,?,?,?)', (name, name.casefold(), salt, h, now())).lastrowid
            return self.new_session(c, uid)
        return {'ok': True, 'name': name, 'token': self.db.write(go)}

    def login(self, body, ip):
        if not self.limits.ok(('login', ip), 300, 600): raise Bad(429, 'Too many tries from here. Wait a few minutes.')
        raw = body.get('name'); name = ' '.join(raw.split()) if isinstance(raw, str) else ''; pw = body.get('password')
        if not name or not isinstance(pw, str) or len(name) > 40 or len(pw) > 100: raise Bad(400, 'Give your name and your password.')
        ln = name.casefold(); f = self.db.read('SELECT n,until FROM fails WHERE lname=?', (ln,))
        if f and f[0][1] > now(): raise Bad(429, f'Too many wrong passwords. Try again in {max(1, (f[0][1] - now() + 59) // 60)} minutes.')
        r = self.db.read('SELECT id,name,salt,hash FROM users WHERE lname=?', (ln,))
        good = bool(r) and hmac.compare_digest(pw_hash(pw, r[0][2]), r[0][3])
        if not r: pw_hash(pw, b'0' * 16)                     # same time whether the name exists or not
        if not good:
            def miss(c):
                n = (c.execute('SELECT n FROM fails WHERE lname=?', (ln,)).fetchone() or (0,))[0] + 1
                c.execute('INSERT OR REPLACE INTO fails(lname,n,until) VALUES(?,?,?)', (ln, 0 if n >= 8 else n, now() + 300 if n >= 8 else 0))
            self.db.write(miss); raise Bad(401, 'The name or the password is wrong.')
        def go(c):
            c.execute('DELETE FROM fails WHERE lname=?', (ln,)); return self.new_session(c, r[0][0])
        return {'ok': True, 'name': r[0][1], 'token': self.db.write(go)}

    def logout(self, token):
        if token: self.db.write(lambda c: c.execute('DELETE FROM sessions WHERE th=?', (tok_hash(token),)))
        return {'ok': True}

    def get_progress(self, token):
        u = self.need_user(token); r = self.db.read('SELECT rev,data,xp FROM progress WHERE user=?', (u['id'],))
        if not r: return {'ok': True, 'name': u['name'], 'rev': 0, 'data': None, 'xp': 0}
        return {'ok': True, 'name': u['name'], 'rev': r[0][0], 'data': json.loads(r[0][1]), 'xp': r[0][2]}

    def put_progress(self, token, body):
        u = self.need_user(token); data, text = clean_save(body.get('data')); xp, topics, tests, levels = summary(data)
        base = as_int(body.get('rev'), 10 ** 12); force = body.get('force') is True
        def go(c):
            r = c.execute('SELECT rev,data,xp FROM progress WHERE user=?', (u['id'],)).fetchone()
            if r and r[0] != base and not force: return {'ok': False, 'conflict': True, 'rev': r[0], 'data': json.loads(r[1]), 'xp': r[2]}
            rev = (r[0] if r else 0) + 1
            c.execute('INSERT OR REPLACE INTO progress(user,rev,data,xp,topics,tests,levels,updated) VALUES(?,?,?,?,?,?,?,?)', (u['id'], rev, text, xp, topics, tests, levels, now()))
            return {'ok': True, 'rev': rev, 'xp': xp}
        return self.db.write(go)

    def board(self, token, limit):
        limit = max(1, min(100, as_int(limit, 100) or 50)); u = self.user_of(token)
        rows = self.db.read('SELECT u.name,p.xp,p.topics,p.tests,p.levels,u.id FROM progress p JOIN users u ON u.id=p.user WHERE p.xp>0 ORDER BY p.xp DESC,p.topics DESC,p.updated ASC,u.id ASC LIMIT ?', (limit,))
        out = {'ok': True, 'at': now(), 'players': self.db.read('SELECT COUNT(*) FROM progress WHERE xp>0')[0][0],
               'top': [{'rank': i + 1, 'name': r[0], 'xp': r[1], 'topics': r[2], 'tests': r[3], 'levels': r[4], 'me': bool(u and u['id'] == r[5])} for i, r in enumerate(rows)]}
        if u:
            me = self.db.read('SELECT xp,topics,updated,tests,levels FROM progress WHERE user=?', (u['id'],))
            if me and me[0][0] > 0:
                ahead = self.db.read('SELECT COUNT(*) FROM progress p WHERE p.xp>? OR (p.xp=? AND p.topics>?) OR (p.xp=? AND p.topics=? AND p.updated<?) OR (p.xp=? AND p.topics=? AND p.updated=? AND p.user<?)',
                                     (me[0][0], me[0][0], me[0][1], me[0][0], me[0][1], me[0][2], me[0][0], me[0][1], me[0][2], u['id']))[0][0]
                out['me'] = {'rank': ahead + 1, 'name': u['name'], 'xp': me[0][0], 'topics': me[0][1], 'tests': me[0][3], 'levels': me[0][4]}
        return out

    def handle(self, method, path, query, token, body, ip):
        if not self.limits.ok(('all', ip), 3000, 60): raise Bad(429, 'Too many requests. Slow down.')
        if path == '/api/health' and method == 'GET': return {'ok': True, 'service': 'after-you-press-run', 'v': 1}
        if path == '/api/register' and method == 'POST': return self.register(body, ip)
        if path == '/api/login' and method == 'POST': return self.login(body, ip)
        if path == '/api/logout' and method == 'POST': return self.logout(token)
        if path == '/api/me' and method == 'GET': return {'ok': True, 'name': self.need_user(token)['name']}
        if path == '/api/progress' and method == 'GET': return self.get_progress(token)
        if path == '/api/progress' and method == 'PUT': return self.put_progress(token, body)
        if path == '/api/leaderboard' and method == 'GET': return self.board(token, query.get('limit'))
        raise Bad(404, 'There is nothing at this address.')

def make_handler(app):
    class H(BaseHTTPRequestHandler):
        server_version = 'AYPR/1'; protocol_version = 'HTTP/1.1'; timeout = 20
        def log_message(self, *a): pass
        def send(self, status, payload, ctype='application/json; charset=utf-8', extra=()):
            try:
                data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf8')
                self.send_response(status); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
                if app.origin:
                    self.send_header('Access-Control-Allow-Origin', app.origin); self.send_header('Vary', 'Origin')
                    self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization'); self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
                for k, v in extra: self.send_header(k, v)
                self.end_headers()
                if self.command != 'HEAD': self.wfile.write(data)
            except (OSError, ValueError): self.close_connection = True
        def serve(self):
            try:
                url = self.path.split('#')[0]; path, _, qs = url.partition('?')
                if len(self.path) > 2000: return self.send(414, {'ok': False, 'error': 'That address is too long.'})
                if self.command == 'OPTIONS': return self.send(204, b'')
                if not path.startswith('/api/'):
                    if self.command not in ('GET', 'HEAD'): return self.send(405, {'ok': False, 'error': 'Not allowed here.'})
                    if path == '/sw.js':                    # the offline copy; it lives next to the site file
                        try:
                            with open(os.path.join(os.path.dirname(os.path.abspath(app.site_path)), 'sw.js'), 'rb') as f: return self.send(200, f.read(), 'text/javascript; charset=utf-8')
                        except OSError: return self.send(404, {'ok': False, 'error': 'There is nothing at this address.'})
                    if path not in ('/', '/index.html'): return self.send(404, {'ok': False, 'error': 'There is nothing at this address.'})
                    try:
                        with open(app.site_path, 'rb') as f: page = f.read()
                    except OSError: return self.send(503, {'ok': False, 'error': 'The site file is missing. Run python3 tools/build.py.'})
                    return self.send(200, page, 'text/html; charset=utf-8')
                query = {}
                for part in qs.split('&')[:10]:
                    k, _, v = part.partition('='); query[k[:20]] = v[:40]
                body = {}
                if self.command in ('POST', 'PUT'):
                    try: n = int(self.headers.get('Content-Length') or 0)
                    except ValueError: n = -1
                    if n < 0 or n > MAX_BODY:
                        self.close_connection = True; return self.send(413, {'ok': False, 'error': 'That is too much to send.'})
                    raw = self.rfile.read(n) if n else b''
                    try: body = json.loads(raw.decode('utf8')) if raw else {}
                    except (ValueError, UnicodeDecodeError, RecursionError): return self.send(400, {'ok': False, 'error': 'That was not valid JSON.'})
                    if not isinstance(body, dict): return self.send(400, {'ok': False, 'error': 'Send a JSON object.'})
                auth = self.headers.get('Authorization') or ''; token = auth[7:].strip() if auth[:7].lower() == 'bearer ' else ''
                ip = self.client_address[0] if self.client_address else '?'
                self.send(200, app.handle(self.command, path, query, token, body, ip))
            except Bad as e: self.send(e.status, {'ok': False, 'error': e.msg})
            except sqlite3.Error as e:
                sys.stderr.write(f'database error: {e!r}\n'); self.send(503, {'ok': False, 'error': 'The server is busy. Your work is safe on your device. Try again soon.'})
            except Exception as e:                           # nothing a request does may stop the server
                sys.stderr.write(f'error on {self.command} {self.path[:80]}: {e!r}\n'); self.send(500, {'ok': False, 'error': 'Something went wrong on the server.'})
        do_GET = do_POST = do_PUT = do_HEAD = do_OPTIONS = do_DELETE = do_PATCH = serve
    return H

class Server(ThreadingHTTPServer):
    daemon_threads = True; request_queue_size = 128
    def handle_error(self, request, client_address): pass     # a broken connection is not an error worth a stack trace

def main():
    ap = argparse.ArgumentParser(description='After you press Run: accounts, saved progress and a leaderboard.')
    ap.add_argument('command', nargs='?', default='run', choices=['run', 'users', 'reset-password', 'remove', 'backup'])
    ap.add_argument('arg', nargs='?')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8000)))
    ap.add_argument('--host', default=os.environ.get('HOST', '0.0.0.0'))
    ap.add_argument('--db', default=os.environ.get('AYPR_DB', os.path.join(ROOT, 'server', 'data', 'aypr.db')))
    ap.add_argument('--site', default=os.environ.get('AYPR_SITE', os.path.join(ROOT, 'index.html')))
    ap.add_argument('--origin', default=os.environ.get('AYPR_ORIGIN', ''))
    a = ap.parse_args(); app = App(a.db, a.site, a.origin); db = app.db
    if a.command == 'users':
        rows = db.read('SELECT u.name,COALESCE(p.xp,0),COALESCE(p.topics,0),u.created FROM users u LEFT JOIN progress p ON p.user=u.id ORDER BY 2 DESC,u.name')
        print(f'{len(rows)} accounts')
        for n, xp, t, c in rows: print(f'  {n:22} {xp:6} XP  {t:2} topics  joined {time.strftime("%d %b %Y", time.localtime(c))}')
        return
    if a.command in ('reset-password', 'remove'):
        if not a.arg: sys.exit('Give the name of the account.')
        ln = ' '.join(a.arg.split()).casefold()
        if not db.read('SELECT 1 FROM users WHERE lname=?', (ln,)): sys.exit('There is no account with that name.')
        if a.command == 'remove':
            def rm(c):
                uid = c.execute('SELECT id FROM users WHERE lname=?', (ln,)).fetchone()[0]
                for t, col in (('sessions', 'user'), ('progress', 'user'), ('users', 'id')): c.execute(f'DELETE FROM {t} WHERE {col}=?', (uid,))
            db.write(rm); print('Removed.'); return
        pw = getpass.getpass('New password (6 or more characters): ')
        if not 6 <= len(pw) <= 100: sys.exit('A password has 6 to 100 characters.')
        salt = secrets.token_bytes(16)
        def rs(c):
            c.execute('UPDATE users SET salt=?,hash=? WHERE lname=?', (salt, pw_hash(pw, salt), ln))
            c.execute('DELETE FROM sessions WHERE user=(SELECT id FROM users WHERE lname=?)', (ln,)); c.execute('DELETE FROM fails WHERE lname=?', (ln,))
        db.write(rs); print('Password changed. The account is logged out everywhere.'); return
    if a.command == 'backup':
        if not a.arg: sys.exit('Give the file to write the backup to.')
        src = db.conn(); dst = sqlite3.connect(a.arg)
        try: src.backup(dst)
        finally: dst.close(); src.close()
        print('Backup written to', a.arg); return
    srv = Server((a.host, a.port), make_handler(app))
    print(f'After you press Run is on http://localhost:{a.port}  (database: {a.db})', flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: print('\nStopped. All data is saved.')
    finally: srv.server_close()

if __name__ == '__main__': main()
