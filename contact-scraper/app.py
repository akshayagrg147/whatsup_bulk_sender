"""
Business Contact Scraper — Streamlit UI.
Scrapes directory (Google Maps) for businesses in a category/location
and returns only those WITHOUT a website. Export to Excel.
"""

import io
import threading
import time
import pandas as pd
import streamlit as st

from config import DEFAULT_LOCATIONS, DEFAULT_DEPARTMENTS, MAX_RESULTS_PER_SEARCH
from scraper import run_scrape, BusinessRecord


st.set_page_config(
    page_title="Business Contact Scraper",
    page_icon="📞",
    layout="centered",
)

st.title("📞 Business Contact Scraper")
st.caption("Extract contact numbers for businesses **without** a website (category + location)")

st.sidebar.header("Settings")
headless = st.sidebar.checkbox("Run browser in headless mode", value=False, help="Uncheck to see the browser window (Chromium) while scraping.")
if not headless:
    st.sidebar.caption("🔍 Browser visible: you’ll see Google Maps open, search, and click results.")
only_without_website = st.sidebar.checkbox("Only businesses without website", value=False)
debug_website = st.sidebar.checkbox("Debug website extraction", value=False)
max_results = st.sidebar.number_input(
    "Max results per search",
    min_value=5,
    max_value=100,
    value=min(MAX_RESULTS_PER_SEARCH, 30),
    step=5,
)

# Location: dropdown + optional custom text
location_options = list(DEFAULT_LOCATIONS)
location_choice = st.selectbox(
    "Location",
    options=[""] + location_options,
    format_func=lambda x: "— Select or type below —" if x == "" else x,
    key="location_select",
)
location_custom = st.text_input(
    "Or type a custom location",
    placeholder="e.g. Indore, Jaipur",
    key="location_custom",
)
location = (location_custom.strip() or location_choice).strip()
if not location:
    st.info("Please select or enter a location.")

# Department/Category
department_options = list(DEFAULT_DEPARTMENTS)
department_choice = st.selectbox(
    "Department / Category",
    options=[""] + department_options,
    format_func=lambda x: "— Select or type below —" if x == "" else x,
    key="dept_select",
)
department_custom = st.text_input(
    "Or type a custom category",
    placeholder="e.g. Yoga Studios, Pet Shops",
    key="dept_custom",
)
department = (department_custom.strip() or department_choice).strip()
if not department:
    st.info("Please select or enter a category.")

# Start Scraping
start = st.button("Start Scraping", type="primary")

if start and location and department:
    st.session_state["scrape_done"] = True
    st.session_state["scrape_location"] = location
    st.session_state["scrape_department"] = department
else:
    if "scrape_done" not in st.session_state:
        st.session_state["scrape_done"] = False

if start and location and department:
    st.subheader("Live results")
    table_placeholder = st.empty()
    log_placeholder = st.empty()
    progress_placeholder = st.empty()

    # Shared state: scraper thread appends here; main thread reads and displays
    live_results = []
    log_lines = []
    done_event = threading.Event()
    scrape_error = [None]  # mutable to hold exception

    def on_progress(msg: str) -> None:
        log_lines.append(msg)

    def on_result(record: BusinessRecord) -> None:
        live_results.append(record)

    def run_scrape_thread() -> None:
        try:
            run_scrape(
                location=location,
                category=department,
                max_results=max_results,
                headless=headless,
                on_progress=on_progress,
                on_result=on_result,
                only_without_website=only_without_website,
                debug_website=debug_website,
            )
        except Exception as e:
            scrape_error[0] = e
        finally:
            done_event.set()

    thread = threading.Thread(target=run_scrape_thread, daemon=True)
    thread.start()

    # Poll every second and refresh the table so the UI updates live
    while not done_event.is_set():
        with table_placeholder.container():
            st.caption(f"**{len(live_results)}** businesses — updating every second")
            if live_results:
                df = pd.DataFrame(
                    [
                        {
                            "#": i + 1,
                            "Business Name": r.name,
                            "Contact Number": r.phone,
                            "Rating": r.rating,
                            "Reviews": r.reviews,
                            "Category": r.category,
                            "Address": r.address,
                            "Website": r.website or "",
                        }
                        for i, r in enumerate(live_results)
                    ]
                )
                st.dataframe(df, use_container_width=True, hide_index=True)
        with log_placeholder.container():
            for line in log_lines[-12:]:
                st.text(line)
        with progress_placeholder:
            st.spinner("Scraping… Rows update below every second.")
        time.sleep(1)

    # Use final list from scraper (same as live_results)
    records = list(live_results)
    progress_placeholder.empty()

    if scrape_error[0]:
        st.error(f"Scraping failed: {scrape_error[0]}")
        st.stop()

    if not records:
        st.warning("No businesses were captured. Try another category or location.")
    else:
        st.success(f"Done. **{len(records)}** businesses captured. Export to Excel when ready.")

        df = pd.DataFrame(
            [
                {
                    "#": i + 1,
                    "Business Name": r.name,
                    "Contact Number": r.phone,
                    "Rating": r.rating,
                    "Reviews": r.reviews,
                    "Category": r.category,
                    "Address": r.address,
                    "Website": r.website or "",
                }
                for i, r in enumerate(records)
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Contacts")
        buffer.seek(0)

        st.download_button(
            label="Download Excel",
            data=buffer,
            file_name=f"contacts_{department.replace(' ', '_')}_{location.replace(' ', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        st.session_state["last_df"] = df
        st.session_state["last_records"] = records

# Show last result table and download if we have stored data (e.g. after rerun)
if "last_records" in st.session_state and st.session_state.get("scrape_done"):
    st.divider()
    st.subheader("Last scrape summary")
    df = st.session_state.get("last_df")
    if df is not None and not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Contacts")
        buffer.seek(0)
        loc = st.session_state.get("scrape_location", "Location")
        dept = st.session_state.get("scrape_department", "Category")
        st.download_button(
            label="Download Excel (last results)",
            data=buffer,
            file_name=f"contacts_{dept.replace(' ', '_')}_{loc.replace(' ', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_last",
        )
