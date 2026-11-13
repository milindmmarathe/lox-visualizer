#!/usr/bin/env python3
"""Two small changes to the published page, without a rebuild.

    python3 tools/site.py title "New Name"            # rename the site everywhere on the page
    python3 tools/site.py server https://my-api.com   # accounts and leaderboard live on this server
    python3 tools/site.py server same                 # the server that serves the page (python3 server/server.py)
    python3 tools/site.py server none                 # no accounts, no leaderboard

Then commit and push. GitHub Pages updates in about a minute.
The current name is kept in TITLE.txt, so the next rename knows what to replace.
"""
import html, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, 'index.html')
TITLE = os.path.join(ROOT, 'TITLE.txt')
SERVER = os.path.join(ROOT, 'server', 'server.py')


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def write(p, s):
    with open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(s)


def set_title(new):
    new = ' '.join(new.split())
    if not re.fullmatch(r"[A-Za-z0-9 .,:!?()+\-]{3,40}", new):
        sys.exit('Title: 3 to 40 letters, digits, spaces and . , : ! ? ( ) + -')
    old = read(TITLE).strip()
    if new == old:
        sys.exit('That is already the title.')
    page = read(PAGE)
    n = page.count(old)
    if n == 0:
        sys.exit(f'"{old}" (from TITLE.txt) is not on the page. Fix TITLE.txt first.')
    write(PAGE, page.replace(old, html.escape(new, quote=False)))
    if os.path.exists(SERVER):
        write(SERVER, read(SERVER).replace(old, new))
    write(TITLE, new + '\n')
    print(f'Renamed in {n} places: "{old}" -> "{new}"')


def set_server(where):
    if where == 'none':
        value = 'null'
    elif where == 'same':
        value = "''"
    elif re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d+)?", where.rstrip('/')):
        value = "'" + where.rstrip('/') + "'"
    else:
        sys.exit('Give an address like https://my-api.com, or "same", or "none".')
    page = read(PAGE)
    new, n = re.subn(r"const API_BASE=(?:null|'[^']*');", f"const API_BASE={value};", page)
    if n != 1:
        sys.exit('Could not find the API_BASE line on the page.')
    write(PAGE, new)
    print(f'API_BASE is now {value}')
    if value.startswith("'h"):
        print('On the server, set AYPR_ORIGIN to the address of the page, for example https://name.github.io')


if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == 'title':
        set_title(a[1])
    elif len(a) == 2 and a[0] == 'server':
        set_server(a[1])
    else:
        sys.exit(__doc__)
