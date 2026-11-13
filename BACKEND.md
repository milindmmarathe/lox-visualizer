# The server: accounts, saved progress and the leaderboard

Built in v2.1. One file, `server/server.py`, Python 3.8 or newer, standard library only. Data is one SQLite file.

## Run it

```
python3 tools/build.py            # makes dist/index.html and dist/sw.js
python3 server/server.py          # the site and the API on http://localhost:8000
```

Everyone on the same network opens `http://<this computer's address>:8000`. Options: `--port`, `--db FILE`,
`--site FILE`, `--host`. On a hosting service, the `PORT` environment variable is used.

| Command | What it does |
| --- | --- |
| `python3 server/server.py users` | List the accounts with XP and topics |
| `python3 server/server.py reset-password NAME` | Set a new password for a learner who forgot it |
| `python3 server/server.py remove NAME` | Delete an account and its progress |
| `python3 server/server.py backup FILE` | Copy the database safely, even while the server runs |

The database is `server/data/aypr.db`. Back it up before the expo and after.

## Why it does not break

- **The page never depends on the server.** Progress is written in the browser first, then sent. If the server
  is down, the learner sees "Offline: saved here" and carries on. The change is sent later, even after a reload.
- **The page opens without the server** after one visit (`sw.js`, a small offline copy of the page).
- **No server at all** (the file opened from disk, or a static host): nothing about accounts is shown.
- **Every request gets a plain JSON answer**, whatever was sent. No request can stop the server. A sudden power
  cut loses nothing that was saved (SQLite in WAL mode, every change in one transaction).
- **Two devices, one account:** each save carries a version number. An out-of-date save cannot overwrite a newer
  one; the copy with more XP is kept.
- **Shared computers:** logging out clears the browser, so the next learner starts clean.
- **Guest progress is never thrown away silently.** If both the device and the account have XP, the learner chooses.

## What is stored

| Table | Columns |
| --- | --- |
| `users` | name, the name in lower case (unique), salt, password hash (PBKDF2-SHA256, 200,000 rounds), created |
| `sessions` | a hash of the login token, the user, created. A login lasts 60 days. |
| `progress` | per user: version number, the saved keys as JSON, and the leaderboard numbers worked out from them |
| `fails` | wrong-password counts. Eight in a row lock that name for five minutes. |

Passwords and tokens are never stored as they are. There is no email, so there is no "forgot password" link:
use `reset-password`.

## The API

All answers are JSON with `ok`. Errors have `error`, a sentence that can be shown to the learner.
Send the token as `Authorization: Bearer <token>`.

| Request | Body | Answer |
| --- | --- | --- |
| `GET /api/health` | | `{ok, service}` |
| `POST /api/register` | `{name, password}` | `{ok, name, token}`; 409 if the name is taken |
| `POST /api/login` | `{name, password}` | `{ok, name, token}`; 401 if wrong; 429 if locked |
| `POST /api/logout` | | `{ok}` |
| `GET /api/me` | | `{ok, name}` |
| `GET /api/progress` | | `{ok, rev, data, xp}`; `data` is null for a new account |
| `PUT /api/progress` | `{rev, data, force?}` | `{ok, rev, xp}`, or `{ok:false, conflict:true, rev, data, xp}` when `rev` is out of date |
| `GET /api/leaderboard?limit=50` | | `{ok, at, players, top:[{rank,name,xp,topics,tests,levels,me}], me?}` |

`data` is a set of Store keys and their texts. Only these keys are kept: lpr2, xp, streak, btm, tests, name,
codes2, lex, pre, badges, done2, lastL2 and prob:<id>. At most 300 KB per account.

## The page side

`src/ui/account.js` does all of it: the account card on Profile, the "Saved" mark in the header, the Leaderboard
tab, sending changes (it listens with `Store.on`), and choosing between two copies. `src/content/config.js` says
where the server is (`API_BASE`): the same address, another address, or `null` for none.

## Limits to know

- **Scores come from the browser.** The server works the leaderboard numbers out itself and caps them (XP at
  20,000, topics at 15), but a learner who edits their saved progress can still raise their XP. For a graded
  competition, move the grading of tests to the server; the engine in `src/engine` runs in Node as it is.
- **Use HTTPS on the open internet.** The server speaks plain HTTP. On a hosting service (Render, Railway,
  PythonAnywhere) HTTPS is added in front of it. On a college network, plain HTTP inside the lab is the usual case;
  tell learners not to reuse a password from another site.
- **One server process.** It handles a class easily (40 learners saving at once is tested). It is not built for
  thousands at the same moment.

## Check it

```
python3 tests/server_check.py     # the API: accounts, progress, leaderboard, junk requests, a sudden stop
python3 tests/account_check.py    # in the browser: two devices, server down and back, a shared computer
```
