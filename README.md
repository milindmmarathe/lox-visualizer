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

GitHub Pages cannot run the server. To have accounts on the public site, host `server/server.py` somewhere,
then:

```
python3 tools/site.py server https://<the server's address>
```

and on the server set `AYPR_ORIGIN=https://milindmmarathe.github.io`.

## Change the name of the site

```
python3 tools/site.py title "New Name"
git commit -am "Rename the site" && git push
```

The address of the site does not change when the name does.
