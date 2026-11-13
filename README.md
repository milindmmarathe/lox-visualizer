# lox-visualizer

A free, step-by-step course for beginners: press Run and watch what a computer does with your code.
15 short topics, quizzes, and a real working engine in the browser.

**Live site:** https://milindmmarathe.github.io/lox-visualizer/

## What is here

| File | What it is |
| --- | --- |
| `index.html` | The whole site in one file. GitHub Pages serves it. |
| `sw.js` | Lets the page open offline after one visit. |
| `server/server.py` | Accounts, saved progress and the leaderboard. Python 3.8+, nothing to install. See `BACKEND.md`. |
| `tools/site.py` | Rename the site, or point the page at a server. |
| `TITLE.txt` | The current name of the site. |

## Run it with accounts and the leaderboard

```
python3 server/server.py
```

Open http://localhost:8000. Others on the same network open `http://<this computer's address>:8000`.

GitHub Pages cannot run the server. For accounts on the public site, the server runs on a separate host
(PythonAnywhere, free plan, which keeps the database file between restarts):

1. On the host, clone this repository.
2. Add a web app (manual configuration) and point its WSGI file at `server/wsgi.py`:
   ```
   import sys
   sys.path.insert(0, '/home/<username>/lox-visualizer/server')
   from wsgi import application
   ```
3. Here, tell the page where the server is, then commit and push:
   ```
   python3 tools/site.py server https://<username>.pythonanywhere.com
   ```

`server/wsgi.py` allows requests from `https://milindmmarathe.github.io`. If the page moves to another address,
set `AYPR_ORIGIN` on the host to the new one.

## Change the name of the site

```
python3 tools/site.py title "New Name"
git commit -am "Rename the site" && git push
```

The address of the site does not change when the name does.
