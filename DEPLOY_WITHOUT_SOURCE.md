# Running on Another System Without Sharing Full Code

## What the other system needs

| Item | Purpose |
|------|--------|
| **Docker** (+ Docker Compose) | Run Evolution API + your app image |
| **One Docker image** | Your app (built once, no source) |
| **.env file** | Config (API key, ports, limits) — you create it, they don’t need your repo |

They do **not** need: Python, your GitHub repo, or any of your source code.

---

## Option 1: Share a Docker image (no source)

### On your machine (once)

1. Build the image:
   ```bash
   cd /path/to/whatsapp-marketing
   docker build -t whatsapp-marketing-suite:latest .
   ```

2. Export the image to a file:
   ```bash
   docker save whatsapp-marketing-suite:latest -o whatsapp-marketing-suite.tar
   ```

3. Give the other system:
   - `whatsapp-marketing-suite.tar` (the image)
   - `docker-compose.standalone.yml` (below) — or a single `docker-compose.yml` that runs Evolution API + this image
   - `.env.example` (they copy to `.env` and fill in)

### On the other system

1. Install Docker + Docker Compose.
2. Load the image:
   ```bash
   docker load -i whatsapp-marketing-suite.tar
   ```
3. Copy `.env.example` to `.env` and set `EVOLUTION_API_KEY`, etc.
4. Run with the standalone compose (see below).
5. Open dashboard at `http://<their-ip>:5001/dashboard`.

No Python, no git clone, no source code.

---

## Option 2: Push image to a registry (private Docker Hub / GitHub Container Registry)

- Build and tag:
  ```bash
  docker build -t your-registry/whatsapp-marketing-suite:latest .
  docker push your-registry/whatsapp-marketing-suite:latest
  ```
- On the other system: they pull the image and run it with a `docker-compose` that uses `image: your-registry/whatsapp-marketing-suite:latest`. They only need the compose file and `.env`.

---

## Option 3: You host it; they only use the dashboard

- Run the app + Evolution API on your server (or a VPS).
- Give them the dashboard URL and (if you want) login.
- They don’t install or run anything; no code and no image shared.

---

## Files to give when sharing the image (Option 1)

1. **whatsapp-marketing-suite.tar** — the built image.
2. **.env** (or .env.example they fill) — so they can set their own `EVOLUTION_API_KEY`, ports, etc.
3. **docker-compose.standalone.yml** — so they can start Evolution API + your app in one command.

No need to give: `main.py`, `bulk_sender.py`, `auto_reply.py`, or any other source files.
