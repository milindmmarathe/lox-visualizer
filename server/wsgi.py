"""The same server, in the shape that hosts like PythonAnywhere need (WSGI).

It uses everything from server.py: the same accounts, the same database file, the same answers.
Only the part that listens on a port is replaced, because the host does that part itself.

Settings (environment variables, all optional):
    AYPR_DB      the database file. Default: server/data/aypr.db
    AYPR_SITE    the page. Default: index.html at the top of the project
    AYPR_ORIGIN  the address the public page is on. Default: https://milindmmarathe.github.io
"""
import json, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)
import server as S

app = S.App(os.environ.get('AYPR_DB', os.path.join(S.ROOT, 'server', 'data', 'aypr.db')),
            os.environ.get('AYPR_SITE', os.path.join(S.ROOT, 'index.html')),
            os.environ.get('AYPR_ORIGIN', 'https://milindmmarathe.github.io'))
STATUS = {200: 'OK', 204: 'No Content', 400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found',
          405: 'Method Not Allowed', 409: 'Conflict', 413: 'Payload Too Large', 414: 'URI Too Long',
          429: 'Too Many Requests', 500: 'Internal Server Error', 503: 'Service Unavailable'}


def application(environ, start_response):
    method = environ.get('REQUEST_METHOD', 'GET')

    def send(status, payload, ctype='application/json; charset=utf-8'):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf8')
        h = [('Content-Type', ctype), ('Content-Length', str(len(data))), ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff')]
        if app.origin:
            h += [('Access-Control-Allow-Origin', app.origin), ('Vary', 'Origin'),
                  ('Access-Control-Allow-Headers', 'Content-Type, Authorization'), ('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')]
        start_response(f'{status} {STATUS.get(status, "OK")}', h)
        return [b'' if method == 'HEAD' else data]

    try:
        path = environ.get('PATH_INFO') or '/'; qs = environ.get('QUERY_STRING') or ''
        if len(path) + len(qs) > 2000: return send(414, {'ok': False, 'error': 'That address is too long.'})
        if method == 'OPTIONS': return send(204, b'')
        if not path.startswith('/api/'):
            if method not in ('GET', 'HEAD'): return send(405, {'ok': False, 'error': 'Not allowed here.'})
            if path == '/sw.js':
                try:
                    with open(os.path.join(os.path.dirname(os.path.abspath(app.site_path)), 'sw.js'), 'rb') as f: return send(200, f.read(), 'text/javascript; charset=utf-8')
                except OSError: return send(404, {'ok': False, 'error': 'There is nothing at this address.'})
            if path not in ('/', '/index.html'): return send(404, {'ok': False, 'error': 'There is nothing at this address.'})
            try:
                with open(app.site_path, 'rb') as f: return send(200, f.read(), 'text/html; charset=utf-8')
            except OSError: return send(503, {'ok': False, 'error': 'The site file is missing.'})
        query = {}
        for part in qs.split('&')[:10]:
            k, _, v = part.partition('='); query[k[:20]] = v[:40]
        body = {}
        if method in ('POST', 'PUT'):
            try: n = int(environ.get('CONTENT_LENGTH') or 0)
            except ValueError: n = -1
            if n < 0 or n > S.MAX_BODY: return send(413, {'ok': False, 'error': 'That is too much to send.'})
            raw = environ['wsgi.input'].read(n) if n else b''
            try: body = json.loads(raw.decode('utf8')) if raw else {}
            except (ValueError, UnicodeDecodeError, RecursionError): return send(400, {'ok': False, 'error': 'That was not valid JSON.'})
            if not isinstance(body, dict): return send(400, {'ok': False, 'error': 'Send a JSON object.'})
        auth = environ.get('HTTP_AUTHORIZATION') or ''; token = auth[7:].strip() if auth[:7].lower() == 'bearer ' else ''
        # behind the host's proxy the visitor's address is in a header
        ip = (environ.get('HTTP_X_REAL_IP') or (environ.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip() or environ.get('REMOTE_ADDR') or '?')[:60]
        return send(200, app.handle(method, path, query, token, body, ip))
    except S.Bad as e: return send(e.status, {'ok': False, 'error': e.msg})
    except sqlite3.Error as e:
        sys.stderr.write(f'database error: {e!r}\n'); return send(503, {'ok': False, 'error': 'The server is busy. Your work is safe on your device. Try again soon.'})
    except Exception as e:
        sys.stderr.write(f'error on {method}: {e!r}\n'); return send(500, {'ok': False, 'error': 'Something went wrong on the server.'})
