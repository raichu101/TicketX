# TicketX — Deployable Web App (with PWA)

This bundle lets you run TicketX online and "install" it as an app (PWA).

## Run locally

```bash
python ticketx.py --web
# open http://localhost:8000
```

## Deploy options

### Render (Docker)
1. Push this folder to a Git repo.
2. Connect the repo on https://render.com and "Create new Web Service".
3. Choose **Docker**; Render will use the Dockerfile here.
4. Once live, open the URL; health check is `/health`.

### Railway / Fly / Heroku-alikes
- Railway: deploy from repo; start command `python ticketx.py --web`.
- Fly.io: `fly launch` and expose internal 8000 -> public 80.
- Heroku (or Dokku): this Procfile works: `web: python ticketx.py --web`.

### Docker (any VPS)
```bash
docker build -t ticketx .
docker run -p 8000:8000 -v $(pwd)/uploads:/app/uploads ticketx
```

## PWA (Installable App)
- The server exposes `/manifest.json` and `/sw.js`.
- Add icons (PNG) at:
  - `uploads/icon-192.png`
  - `uploads/icon-512.png`
- Then visit on mobile Chrome/Safari and **Add to Home Screen**.

## Notes
- All data is in-memory; avatars & icons live under `uploads/`.
- If you redeploy, mount `uploads/` as a volume to persist files.
