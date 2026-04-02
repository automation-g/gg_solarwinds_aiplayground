"""Fetch 1 year of data (March 2025 - March 2026)."""
from fetch import fetch_month, init_db

init_db()

months = [
    "2025-03", "2025-04", "2025-05", "2025-06",
    "2025-07", "2025-08", "2025-09", "2025-10",
    "2025-11", "2025-12", "2026-01", "2026-02", "2026-03",
]

for m in months:
    try:
        fetch_month(m)
    except Exception as e:
        print(f"[{m}] ERROR: {e}")
        from db import get_conn, set_progress
        conn = get_conn()
        set_progress(conn, m, status="error", error=str(e))
        conn.close()
        continue

print("\nAll done!")
