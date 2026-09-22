# VS Code runbook — crypto-clob (NOBI pipeline)

Run, debug and version the whole NOBI pipeline from VS Code.

## What lives where

| Path (under `Terminal\Common`) | Component |
|---|---|
| `crypto-clob\` | **This repo** — bridge, engine, AI brain, launchers, MT5 files |
| `crypto-clob\nobi_bridge.py` | Bridge: reads engine metrics → writes `nobi_signal.sig` + `nobi_anchor.sig` for the MT5 EA |
| `crypto-clob\run.py` | Engine: CLOB feed → metrics (`--out crypto-clob-ui\data-ui`) |
| `crypto-clob\ai\brain_server.py` | AI loop, port **8090**, hot-reloads `nobi_config.json` |
| `crypto-clob-ui\ui_server.py` | Dashboard UI server (sibling folder) |

## First time

1. Install the **Python** extension when VS Code prompts (`.vscode\extensions.json`).
2. Open this folder in VS Code: `File > Open Folder` → `...\Terminal\Common\crypto-clob`.
3. Interpreter is auto-set to the bundled `_runtime\python\python.exe` (`.vscode\settings.json`).

## Start / stop

| Action | How |
|---|---|
| Start **all four** components | `Ctrl+Shift+B` (Run Build Task → `NOBI: Start All`) — one terminal each |
| Start one component | `Terminal > Run Task…` → pick it |
| Debug one component | `Ctrl+Shift+D` → pick `NOBI: Bridge (debug)` / `Engine` / `UI server` / `Brain` → `F5` |
| Stop | Close its terminal, or kill via Task Manager (`pythonw.exe` / `python.exe`) |

> ⚠️ **Stop old instances first.** If the pipeline is already running from a console/batch,
> close those before starting from VS Code — the engine is guarded against duplicates
> (`start_engine.py`), the bridge/UI/brain are **not**.

## Config files

| File | Role |
|---|---|
| `config.json` | Feed/tick tuning (exchanges, venues, sweep, iceberg, absorb) |
| `nobi_config.json` | Bridge signal params: `epoch_sec`, `min_abs`, signal/anchor paths — the AI brain **hot-reloads** this, so expect live edits |
| `MT5_files\` | Deployed MQL5 build: `NobiScalpTrader.mq5/.ex5`, indicators, presets — synced from the terminal workspace |

## Git / GitHub

1. **Install Git for Windows** (not installed on this machine yet):
   <https://git-scm.com/download/win> — install, restart VS Code, reopen this folder.
2. One-time identity:
   ```bat
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```
3. One-time GitHub remote (create an **empty** repo on GitHub first, no README):
   ```bat
   git remote add origin https://github.com/<you>/crypto-clob.git
   git branch -M main
   git push -u origin main
   ```
4. Daily loop:
   ```bat
   git status
   git add -A
   git commit -m "what changed"
   git push
   ```
5. Experiment on branches, merge to `main` when solid:
   ```bat
   git checkout -b feature/xyz
   git checkout main
   git merge feature/xyz
   ```
6. **Never commit:** `_runtime\`, `dist\`, `ai\dataset.csv`, `*.log` — already covered by `.gitignore`.

## Releases & versions

| File | Role |
|---|---|
| `VERSION` | Current version + build date/time + source commit |
| `RELEASES.md` | Changelog of every tagged version |
| `bump_release.py` | One-command bumper (see below) |

Stamp the next version (adds date+time, commits, tags `vX.Y`, pushes to GitHub):

```bat
_runtime\python\python.exe bump_release.py --note "short summary"
```

- `--inc major|minor|patch` chooses the level (default `minor`: v1.0 → v1.1).
- Every tag is a **fallback point**: `git switch -c hotfix/v1.0 v1.0`.
- Tags are pushed automatically; use `--no-push` to keep it local.