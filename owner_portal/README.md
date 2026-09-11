# Local owner editor

Run the editor from the repository root after setting a long `OWNER_PASSWORD` and an independent `SESSION_SECRET` of at least 32 characters:

```powershell
.\.venv\Scripts\python.exe owner_portal\local_server.py
```

Open `http://127.0.0.1:8765`. The service refuses non-loopback binding. It uses signed, HTTP-only, same-site session cookies and same-origin checks. A save validates the complete edition, atomically updates `data/latest_briefing.json` and its dated archive, runs `owner_edit_validation.py`, stages only those two files, commits with `Owner edit news edition `, and pushes to `main` through local Git credentials.

`.github/workflows/publish-owner-edits.yml` recognizes that commit prefix, validates the edition again, and republishes GitHub Pages.

The serverless API remains for compatibility, but local operation is preferred because it needs no public write service or GitHub token. Never put passwords, session secrets, or tokens in frontend code or Git.
