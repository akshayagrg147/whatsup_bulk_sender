# Steps to Run WhatsApp Marketing Suite

## Prerequisites

- **Docker** and **Docker Compose** (for Evolution API)
- **Python 3.10+**
- Terminal / command line

---

## Step 1: Configure environment

- Open `.env` in the project root.
- Optionally set a strong `EVOLUTION_API_KEY` (e.g. a long random string).  
  If you change it, use the **same** value in Step 2 for Docker.
- Your project uses `FLASK_PORT=5001`, so the dashboard will be at **http://localhost:5001/dashboard**.
- Optionally set `YOUR_STORE_URL` and `YOUR_PHONE` for auto-reply messages.
- **Multiple WhatsApp numbers:** Set `EVOLUTION_INSTANCES=number1,number2,support` (comma-separated). Connect each in Evolution API (create instance → scan QR). In the dashboard you can either:
  - **Auto (rotate when 200/day reached):** Messages 1–200 go from first number, 201–400 from second, etc. No need to pick a number.
  - Or pick a specific instance to send only from that number.

---

## Step 2: Start Evolution API (Docker)

From the project root:

```bash
cd /Users/akshay/.gemini/antigravity/scratch/whatsapp-marketing
docker-compose up -d
```

- Evolution API will be at **http://localhost:8080**.
- Ensure the same `EVOLUTION_API_KEY` is in `.env` (Docker Compose reads it for the container).

Check that the container is running:

```bash
docker ps
```

---

## Step 3: Install Python dependencies

```bash
cd /Users/akshay/.gemini/antigravity/scratch/whatsapp-marketing
pip install -r requirements.txt
```

Use a virtual environment if you prefer:

```bash
python3 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

## Step 4: Run the Flask app (Marketing Suite)

```bash
cd /Users/akshay/.gemini/antigravity/scratch/whatsapp-marketing
python main.py
```

This will:

- Create the SQLite DB and tables under `./data/` (and `./uploads/` if needed).
- Start the scheduler (weekly report, midnight reset).
- Register the webhook with Evolution API (and create the instance if it doesn’t exist).
- Start the Flask server.

You should see something like:

```
✅ Evolution API: http://localhost:8080
✅ Dashboard: http://localhost:5001/dashboard
✅ Webhook registered internally on /webhook
✅ Scheduler running
🚀 WhatsApp Marketing Suite is LIVE!
```

- **Dashboard:** open **http://localhost:5001/dashboard** in your browser.

---

## Step 5: Connect WhatsApp (QR code)

1. Get the QR code from Evolution API. If you use **multiple instances** (`EVOLUTION_INSTANCES`), create and connect each one in Evolution API (e.g. create `number1`, scan QR; create `number2`, scan QR). Each instance = one WhatsApp number. For example:
   - **REST:**  
     `GET http://localhost:8080/instance/connect/{EVOLUTION_INSTANCE}`  
     with header `apikey: YOUR_EVOLUTION_API_KEY`  
     (Evolution API may return a QR in the response or a URL to a page that shows it.)
   - Or use Evolution API’s own UI/docs if it provides a web page for your version.

2. Open **WhatsApp** on your phone → **Settings → Linked devices → Link a device**.

3. Scan the QR code shown by Evolution API.

4. Once linked, the Marketing Suite can send and receive messages; the webhook will get events.

---

## Step 6: (Optional) Sample Excel for bulk send

To generate a sample contacts file:

```bash
python generate_sample.py
```

This creates `sample_contacts.xlsx`. Use it in the dashboard under **Start Bulk Campaign** (upload Excel, set message template with `{Name}`, then start bulk send).

---

## Quick reference

| What              | URL or command |
|-------------------|----------------|
| Evolution API     | http://localhost:8080 |
| Dashboard         | http://localhost:5001/dashboard |
| Webhook (internal)| http://localhost:5001/webhook |
| Start Evolution   | `docker-compose up -d` |
| Start Suite       | `python main.py` |

---

## Troubleshooting

- **Webhook not receiving events:**  
  Evolution API runs inside Docker and must reach Flask on your host. The code uses `host.docker.internal` as the webhook host. On Linux, ensure Docker is set up so that `host.docker.internal` resolves to your host (recent Docker versions support this).

- **"Instance not found" / connection state:**  
  Ensure Evolution API is up (`docker-compose up -d`) and that you’ve connected WhatsApp (Step 5). Then restart `python main.py` so it can register the webhook again.

- **Port already in use:**  
  Change `FLASK_PORT` in `.env` (e.g. to 5002) and restart `main.py`. Dashboard will be on the new port.

- **Database / uploads:**  
  DB path is from `DB_PATH` in `.env` (default `./data/marketing.db`). Uploads go to `./uploads/`. Ensure the app has write permission to the project directory.
