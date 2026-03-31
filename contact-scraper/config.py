"""Default options for locations and business categories."""

DEFAULT_LOCATIONS = [
    "Gurugram",
    "Delhi",
    "Mumbai",
    "Bangalore",
    "Hyderabad",
    "Chennai",
    "Kolkata",
    "Pune",
    "Noida",
    "Faridabad",
    "Ghaziabad",
]

DEFAULT_DEPARTMENTS = [
    "Clinics",
    "Dentists",
    "Hospitals",
    "Real Estate",
    "Schools",
    "Restaurants",
    "Gyms",
    "Salons",
    "Pharmacies",
    "Lawyers",
    "Accountants",
    "Plumbers",
    "Electricians",
    "Carpenters",
    "Photographers",
    "Travel Agents",
    "Insurance Agents",
]

# Anti-blocking: user agents (desktop, common browsers)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# Min/max sleep between actions (seconds)
SLEEP_MIN = 1.5
SLEEP_MAX = 4.0

# Max results to scrape per search (to avoid long runs and blocks)
MAX_RESULTS_PER_SEARCH = 50
