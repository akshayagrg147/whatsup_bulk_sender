# Business Contact Scraper

A free web scraping app that extracts **contact numbers** for businesses in a given **category** and **location**, keeping only businesses that **do not have a website** listed. Output is shown in the UI and can be downloaded as Excel (.xlsx).

## Tech Stack (all free)

- **UI:** Streamlit  
- **Scraping:** Python + Playwright (Chromium)  
- **Data / Export:** Pandas, Openpyxl  

## Features

- **Location:** Dropdown with common Indian cities (Gurugram, Delhi, Mumbai, etc.) or type your own.
- **Category:** Dropdown (Clinics, Dentists, Hospitals, Real Estate, etc.) or custom text.
- **Filter:** Only businesses **without** a website are included.
- **Anti-blocking:** Random delays and rotating user agents to reduce block risk.
- **Export:** Preview table + **Download Excel** with columns: Business Name, Contact Number.

## Prerequisites

- **Python 3.10+**
- **Playwright browsers** (installed via Playwright’s CLI after `pip install`)

## Step-by-step: run locally

### 1. Create and activate a virtual environment (recommended)

```bash
cd contact-scraper
python3 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright browsers (one-time)

```bash
playwright install chromium
```

### 4. Launch the Streamlit app

```bash
streamlit run app.py
```

Your browser will open at `http://localhost:8501`. Use the dropdowns (or custom text) to set **Location** and **Department/Category**, then click **Start Scraping**. When finished, preview the table and use **Download Excel** to get the .xlsx file.

## Project layout

```
contact-scraper/
├── app.py           # Streamlit UI and Excel export
├── scraper.py       # Google Maps scraping (Playwright), no-website filter
├── config.py        # Default locations, categories, delays, user agents
├── requirements.txt
└── README.md
```

## Configuration

- **Default locations and categories** are in `config.py` (`DEFAULT_LOCATIONS`, `DEFAULT_DEPARTMENTS`). Edit there to add more options.
- **Anti-blocking:** `SLEEP_MIN`, `SLEEP_MAX` (seconds) and `USER_AGENTS` in `config.py`.
- **Max results per search:** Set in the sidebar in the app, or change `MAX_RESULTS_PER_SEARCH` in `config.py`.

## Notes on “free” scraping

- Google Maps uses strong anti-bot measures. If you get blocked or captchas:
  - Use the **headless** option unchecked in the sidebar so the browser window is visible (sometimes helps).
  - Lower the number of results per search and add longer delays in `config.py`.
  - As a next step, you can add rotating proxies (not included here).

## License

Use at your own risk. Respect the target site’s terms of service and robots.txt.
