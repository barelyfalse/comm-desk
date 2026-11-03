# Comm Desk

A case and communication tracker (see [FEATURES.md](FEATURES.md)). It runs on your work VM and you open it on the Chromebox through an SSH tunnel.

```
Chromebox                              Work VM
Chrome ── http://localhost:8000 ──SSH tunnel──► 127.0.0.1:8000  FastAPI (server/main.py)
                                                  ├─ /            app (server/static/index.html)
                                                  ├─ /login       sign-in page
                                                  ├─ /api/state   load and save data
                                                  └─ data/commdesk.db  SQLite
```

The server listens only on `127.0.0.1`, so nothing on the work network can reach it except through your SSH session. Everything is local: fonts and the icon are served by the app, and the page loads nothing from the internet.

## Layout

| Path | What |
|---|---|
| `server/main.py` | FastAPI app, SQLite storage, sign-in, command-line tools |
| `server/static/index.html` | The app (ported from `Comm Desk.html`) |
| `server/static/login.html` | Sign-in page |
| `server/static/fonts/` | Public Sans and JetBrains Mono (OFL), self-hosted |
| `pyproject.toml`, `uv.lock` | Dependencies (managed by uv) |
| `deploy/commdesk.service` | systemd user service |
| `data/` | Created on first run: `commdesk.db`, `password.hash`, `secret.key` (all mode 600) |

## Setup on the VM

The project is managed with [uv](https://docs.astral.sh/uv/). Dependencies are declared in `pyproject.toml` and pinned exactly in `uv.lock`.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # once, if uv isn't installed (or: pipx install uv)
cd ~/comm-desk
uv sync                                    # creates .venv with the locked versions (and Python, if needed)
uv run server/main.py set-password         # prompts twice; stores a scrypt hash
uv run server/main.py                      # serves on 127.0.0.1:8000
```

`uv run` keeps `.venv` in sync with the lock file before running, so `uv sync` is optional. To upgrade the dependencies, run `uv lock --upgrade`, test, and commit the new `uv.lock`.

To keep it running after you disconnect, use the systemd user service:

```bash
mkdir -p ~/.config/systemd/user && cp deploy/commdesk.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now commdesk
loginctl enable-linger "$USER"   # keeps user services running while you're logged out
```

If `enable-linger` isn't allowed on your VM, run the server inside `tmux` instead.

Optional environment variables: `COMMDESK_PORT` (default `8000`), `COMMDESK_HOST` (default `127.0.0.1`), and `COMMDESK_DATA` (default `./data`).

## Updating

Run `git pull`, then reload the page. The server reads `server/static/` on every request, so a change to the app alone needs no restart. Restart the service only when `server/main.py` or the dependencies changed (`systemctl --user restart commdesk`).

Saved data from older versions is upgraded in the browser when the page loads, and written back with the next save. If something looks wrong, the hourly snapshots (`main.py snapshots` / `restore`) still have the data as it was.

## Opening it from the Chromebox

Forward local port 8000 to the VM's port 8000 when you connect, then open `http://localhost:8000` in Chrome.

- **Terminal app, SSH connection:** add `-L 8000:localhost:8000` to the connection's SSH arguments. This setup has been confirmed to work.
- **Linux container (`ssh` in a shell):** run `ssh -L 8000:localhost:8000 you@vm`. ChromeOS forwards the container's localhost ports to Chrome.
- **`gcloud compute ssh`:** run `gcloud compute ssh VM -- -L 8000:localhost:8000`.

Use `localhost`, not the VM's IP address. The copy buttons need a secure context, and `http://localhost` counts as one.

## Moving your existing data

1. In the old `Comm Desk.html` (the Artifact), open **Data → Copy data as JSON**.
2. In the server version, open **Settings**, paste the JSON into **Restore** and confirm.

## Sign-in

- There is one password and no sign-up. The password is stored only as a scrypt hash in `data/password.hash`. To change it, run `set-password` again and restart the server.
- A successful sign-in sets an HttpOnly, SameSite=Strict cookie signed with `data/secret.key`. It is valid for 24 hours.
- After 5 failed attempts within a minute, sign-in is blocked for the rest of that minute.
- **Sign out** is on the Settings page. To end every session at once, delete `data/secret.key` and restart the server.

## How saving works

- **Debounced saves.** Every change calls `save()`, which restarts a 600 ms timer. When you stop typing for 600 ms, the whole state is sent to the server as JSON, together with the version number it was based on. The indicator in the top bar shows **Saving…** until the server confirms, then **Saved** (hover it to see the time).
- **Version check.** The server bumps the version on every save. It refuses a save based on an older version (HTTP 409), so a stale tab can't silently overwrite newer data. When that happens, a banner offers **Load saved data** or **Overwrite with mine**.
- **Coming back to a tab.** When a tab becomes visible again, it checks the server's version. If the data is newer and the tab has no unsaved edits, it reloads. Switching between two tabs therefore stays in sync.
- **Leaving a tab.** Pending changes are sent right away when you switch away or close the tab. If something is still unsaved, the browser asks before closing.
- **Server unreachable** (tunnel down or server stopped). The indicator turns red (**Not saved · retrying**) and retries after 2, 4, 8 and so on, up to 30 seconds. Click it to retry immediately. Until a save is confirmed, the latest state is also kept in the browser's `localStorage`. On the next load it is sent again automatically, or the conflict banner appears if the server changed in the meantime.
- **Saves whose reply was lost.** If a save reaches the server but the tab closes or the network drops before the reply arrives, the page keeps a fingerprint of what it sent. On the next load or retry, if the server's data matches that fingerprint, the save is recognized as its own. Any later edits are applied on top without a conflict banner. The banner only appears when another tab or device really saved in between.
- **Expired sign-in.** A save that comes back 401 sends you to the sign-in page. Your unsaved changes are restored after you sign in.

## Snapshots, restore and erase

On top of the current state, the server keeps snapshots for 30 days:

- **hourly**: one per hour of activity.
- **before replace**: taken before a web **Restore** from a pasted backup, or an **Overwrite with mine**.
- **before restore** and **before erase**: taken by the commands below.

Every operation that replaces all the data can therefore be undone.

```bash
uv run server/main.py snapshots        # list them, with case counts and why each was taken
uv run server/main.py restore 42       # make snapshot 42 the current data
uv run server/main.py erase            # delete all data; asks you to type "erase", prints the restore id
```

Erasing all data is only available on the server, not in the web page.

For a full backup, copy `data/commdesk.db`, or run `sqlite3 data/commdesk.db ".backup backup.db"` while the server is running.
