<!-- headroom:rtk-instructions -->
# RTK (Rust Token Killer) - Token-Optimized Commands

When running shell commands, **always prefix with `rtk`**. This reduces context
usage by 60-90% with zero behavior change. If rtk has no filter for a command,
it passes through unchanged — so it is always safe to use.

## Key Commands
```bash
# Git (59-80% savings)
rtk git status          rtk git diff            rtk git log

# Files & Search (60-75% savings)
rtk ls <path>           rtk read <file>         rtk grep <pattern>
rtk find <pattern>      rtk diff <file>

# Test (90-99% savings) — shows failures only
rtk pytest tests/       rtk cargo test          rtk test <cmd>

# Build & Lint (80-90% savings) — shows errors only
rtk tsc                 rtk lint                rtk cargo build
rtk prettier --check    rtk mypy                rtk ruff check

# Analysis (70-90% savings)
rtk err <cmd>           rtk log <file>          rtk json <file>
rtk summary <cmd>       rtk deps                rtk env

# GitHub (26-87% savings)
rtk gh pr view <n>      rtk gh run list         rtk gh issue list

# Infrastructure (85% savings)
rtk docker ps           rtk kubectl get         rtk docker logs <c>

# Package managers (70-90% savings)
rtk pip list            rtk pnpm install        rtk npm run <script>
```

## Rules
- In command chains, prefix each segment: `rtk git add . && rtk git commit -m "msg"`
- For debugging, use raw command without rtk prefix
- `rtk proxy <cmd>` runs command without filtering but tracks usage
<!-- /headroom:rtk-instructions -->

# Project: BlackBoardScrapper

Python/Flask web app to backup Blackboard Learn courses. Universidad Mayor target.

## Dev server
```bash
python run.py          # servidor local Waitress, abre el navegador
```

## Testing
```bash
python -m pytest tests/ -v
```

## Architecture
- `app.py` — Flask server, SSE progress, download/audit/scan endpoints
- `auth.py` — cookie parsing, session persistence, and validation
- `bb_client.py` — Blackboard Learn REST API wrapper
- `collab_client.py` — Collaborate Ultra recording API
- `content_sync.py` — Content tree traversal, asset download dispatch
- `downloader.py` — HTTP download with retry, SHA-256 verification, .part handling
- `manifest.py` — `manifest.json` with per-file hash/size/mtime tracking
- `storage.py` — OneDrive/local storage detection and migration
- `organizer.py` — File system layout (semester/course/content/)
- `maintenance.py` — Audit and cleanup tasks
- `config.py` — URLs, timeouts, constants
- `packaging/` — PyInstaller, Inno Setup, and AppImage build files
- `static/app.js` — Frontend SPA, SSE client, operation phases
- `templates/index.html` — Single-page app shell
- `tests/test_core.py` — Unit tests

## Key patterns
- `manifest.save(persist=False)` — defer disk writes during loops
- `download_file(..., verify_hash=False)` — skip CPU-heavy verification during bulk ops
- `.part` files — in-progress downloads; removed on 416, accepted on MIME-matched size mismatch
- SSE `event: progress` — frontend renders per-file progress bar
- Storage migration (`storage.migrate_to`) — atomic copy-then-delete, conflict detection

## Storage
- Session and app state: `%LOCALAPPDATA%/Campus Archive` on Windows or `~/.local/share/Campus Archive` on Linux
- OneDrive auto-detected on Windows/WSL via environment variables and `/mnt/c/Users/*/OneDrive`
- Default backup: `Documents/Campus Archive`
- `BB_OUTPUT_DIR` overrides the default backup location

## Packaging
- Windows: build `Campus-Archive-Setup.exe` with `packaging/windows.iss` after PyInstaller.
- Linux: build `Campus-Archive-x86_64.AppImage` with `packaging/build-appimage.sh`.
- GitHub Actions intentionally does not run for pull requests. CI runs on `main`; release builds run only for `v*.*.*` tags or manual dispatch.
