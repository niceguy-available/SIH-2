# Self-hosting Moon Match Points

The whole stack (MongoDB, FastAPI backend, React frontend behind nginx) runs
with Docker Compose. No Emergent account or key is needed: without
`EMERGENT_LLM_KEY` the backend stores objects on local disk (`STORAGE_BACKEND=local`).

## Run

```bash
git clone <this repo> && cd SIH-2
docker compose up -d --build
```

Open http://localhost:8080 (or `http://<server-ip>:8080` on a server).
Set `PORT=80 docker compose up -d` to serve on port 80.

Data (MongoDB, cached references, run results) lives in the `mongo-data`
and `app-data` Docker volumes and survives restarts and rebuilds.

## Hosting it on the internet

Any Linux VM with Docker works (AWS/GCP/Azure/Oracle free tier, a college
server, etc.):

1. Install Docker and the Compose plugin.
2. Clone the repo and run `docker compose up -d --build`.
3. Open port 8080 (or 80) in the VM's firewall/security group.
4. Optional HTTPS: put Caddy or nginx with Let's Encrypt in front of port 8080.

The app has no login ("single operator" mode), so anyone with the URL can
use it. Restrict access (firewall allow-list, VPN or a reverse proxy with
basic auth) before sharing the URL publicly.

## Reference dataset (LROC)

On the first source upload the backend downloads the curated LROC products
listed in `backend/data/footprints.json` (from
https://lroc.im-ldi.com/images/downloads/) into the reference cache. To grow
the dataset, add entries to that file (the image URL must be under
`https://lroc.im-ldi.com/data/support/popular_downloads/`) or upload
reference images, such as QuickMap exports, from the "Upload a reference
product" panel. Every uploaded source is then matched by image content
against all cached references, and the best match is selected automatically.

After editing `footprints.json` on an existing deployment, copy it into the
data volume, because the file is seeded only once:
`docker compose cp backend/data/footprints.json backend:/data/footprints.json`.
