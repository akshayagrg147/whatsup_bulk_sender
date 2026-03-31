# How to Create 5–6 Instances (Numbers) in Evolution API and Scan QR

Evolution API runs at **http://localhost:8080**. Each **instance** = one WhatsApp number. Create one instance per number, then scan its QR with that phone.

---

## Prerequisites

- Evolution API is running: `docker-compose up -d`
- Your `.env` has `EVOLUTION_API_KEY=your_secret_key_here` (same as in docker-compose)

---

## Method 1: Using the API (browser or Postman)

### Step 1: Create the first instance

**Request:**
- **URL:** `http://localhost:8080/instance/create`
- **Method:** POST
- **Headers:**
  - `Content-Type: application/json`
  - `apikey: your_secret_key_here`  ← use the value from your `.env`
- **Body (JSON):**
```json
{
  "instanceName": "number1",
  "integration": "WHATSAPP-BAILEYS",
  "qrcode": true
}
```

Replace `number1` with any name you want (e.g. `sales`, `support`). This name must match what you put in `EVOLUTION_INSTANCES` in `.env`.

### Step 2: Get the QR code for that instance

**Request:**
- **URL:** `http://localhost:8080/instance/connect/number1`
- **Method:** GET
- **Header:** `apikey: your_secret_key_here`

**Response:** Evolution API may return:
- A **base64 image** (e.g. `data:image/png;base64,...`) — you can paste that in a browser or use an online base64-to-image tool to see the QR.
- Or a **pairing code** / **link** — use that in WhatsApp (Linked devices → Link with phone number instead of QR).

### Step 3: Scan the QR with WhatsApp

1. On your **first** phone, open **WhatsApp** → **Settings (or ⋮)** → **Linked devices** → **Link a device**.
2. Choose **Link with QR code** and scan the QR you got (or use “Link with phone number” if API gave a code).
3. Once linked, that instance is connected. You can close the QR.

### Step 4: Repeat for more numbers (number2, number3, …)

Do the same for each extra number:

1. **Create instance:**  
   POST `http://localhost:8080/instance/create`  
   Body: `{ "instanceName": "number2", "integration": "WHATSAPP-BAILEYS", "qrcode": true }`  
   (Header: `apikey: your_secret_key_here`)

2. **Get QR:**  
   GET `http://localhost:8080/instance/connect/number2`  
   (Header: `apikey: your_secret_key_here`)

3. On your **second** phone, open WhatsApp → Linked devices → Link a device → scan the QR (or use code).

Repeat for `number3`, `number4`, `number5`, etc.

### Step 5: Add instance names to `.env`

In your project `.env`:

```env
EVOLUTION_INSTANCES=number1,number2,number3,number4,number5
```

Use the **exact** names you used in `instanceName` when creating. Restart your Flask app so it picks up the list and uses Auto-rotate.

---

## Method 2: Using cURL in terminal

Replace `YOUR_API_KEY` with your real `EVOLUTION_API_KEY` from `.env`.

**Create instance (e.g. number1):**
```bash
curl -X POST http://localhost:8080/instance/create \
  -H "Content-Type: application/json" \
  -H "apikey: YOUR_API_KEY" \
  -d '{"instanceName":"number1","integration":"WHATSAPP-BAILEYS","qrcode":true}'
```

**Get QR (then open the URL or decode base64 from response):**
```bash
curl -X GET "http://localhost:8080/instance/connect/number1" \
  -H "apikey: YOUR_API_KEY"
```

Run the same for `number2`, `number3`, etc. (change `number1` in both the JSON and the GET URL).

---

## Method 3: Web UI (if your Evolution API has one)

Some Evolution API setups expose a simple UI:

1. Open **http://localhost:8080** in the browser.
2. If you see a dashboard or “Instances” / “Create instance”, use it to create an instance and view the QR there.
3. Create 5–6 instances, scan each QR with the right phone.

If you only see a blank page or API docs, use **Method 1** or **Method 2** above.

---

## Quick checklist

| # | Instance name | Create (POST /instance/create) | Get QR (GET /instance/connect/...) | Scan with phone |
|---|----------------|---------------------------------|-------------------------------------|------------------|
| 1 | number1       | ✅                              | ✅                                  | Phone 1          |
| 2 | number2       | ✅                              | ✅                                  | Phone 2          |
| 3 | number3       | ✅                              | ✅                                  | Phone 3          |
| 4 | number4       | ✅                              | ✅                                  | Phone 4          |
| 5 | number5       | ✅                              | ✅                                  | Phone 5          |

Then set in `.env`:  
`EVOLUTION_INSTANCES=number1,number2,number3,number4,number5`  
and restart the WhatsApp Marketing Suite (Flask).
