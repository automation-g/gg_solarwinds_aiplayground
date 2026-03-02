"""Generate a static HTML report for GitHub Pages deployment."""

from __future__ import annotations

import datetime
import html

import pandas as pd
import plotly.express as px
import plotly.io as pio

from api_client import fetch_incidents, safe_get

# ── Fetch data ───────────────────────────────────────────────────────────────
today = datetime.date.today()
start_date = today - datetime.timedelta(days=7)
start_str = start_date.strftime("%Y-%m-%dT00:00:00Z")
end_str = today.strftime("%Y-%m-%dT23:59:59Z")

print(f"Fetching incidents from {start_date} to {today}...")
raw = fetch_incidents(start_str, end_str)
print(f"Got {len(raw)} incidents")

rows = []
for r in raw:
    rows.append(
        {
            "Ticket #": r.get("number", ""),
            "Name": r.get("name", ""),
            "State": r.get("state", ""),
            "Priority": r.get("priority", ""),
            "Category": safe_get(r, "category", "name"),
            "Subcategory": safe_get(r, "subcategory", "name"),
            "Assignee": safe_get(r, "assignee", "name"),
            "Requester": safe_get(r, "requester", "name"),
            "Created": r.get("created_at", ""),
            "Updated": r.get("updated_at", ""),
            "Due": r.get("due_at", ""),
            "Resolved At": r.get("resolved_at", ""),
            "Is Escalated": r.get("is_escalated", False),
        }
    )

df = pd.DataFrame(rows)
df["Created"] = pd.to_datetime(df["Created"], errors="coerce", utc=True)
df["Updated"] = pd.to_datetime(df["Updated"], errors="coerce", utc=True)
df["Due"] = pd.to_datetime(df["Due"], errors="coerce", utc=True)
df["Resolved At"] = pd.to_datetime(df["Resolved At"], errors="coerce", utc=True)
df["Date"] = df["Created"].dt.date

# ── Metrics ──────────────────────────────────────────────────────────────────
now_utc = pd.Timestamp.now(tz="UTC")
today_mask = df["Date"] == today

raised_today = int(today_mask.sum())
closed_today = int(((df["State"].str.lower() == "closed") & today_mask).sum())
resolved_today = int(((df["State"].str.lower() == "resolved") & today_mask).sum())
still_open_today = int(((~df["State"].str.lower().isin(["closed", "resolved"])) & today_mask).sum())
overdue_today = int(
    ((df["Due"].notna()) & (df["Due"] < now_utc) & (~df["State"].str.lower().isin(["closed", "resolved"])) & today_mask).sum()
)

open_backlog = int((~df["State"].str.lower().isin(["closed", "resolved"])).sum())
high_crit = int(df["Priority"].str.lower().isin(["high", "medium", "critical"]).sum())
overdue_all = int(((df["Due"].notna()) & (df["Due"] < now_utc) & (~df["State"].str.lower().isin(["closed", "resolved"]))).sum())

still_open_label = f"{still_open_today} ({overdue_today} overdue)" if overdue_today else str(still_open_today)

# ── Charts ───────────────────────────────────────────────────────────────────
chart_opts = dict(full_html=False, include_plotlyjs=False)

# Daily volume trend
daily_counts = df.groupby("Date").size().reset_index(name="Tickets").sort_values("Date")
fig_vol = px.bar(daily_counts, x="Date", y="Tickets", title="Daily Volume Trend", text="Tickets", color_discrete_sequence=["#636EFA"])
fig_vol.update_traces(textposition="outside")
fig_vol.update_layout(xaxis_title="", yaxis_title="Tickets", margin=dict(t=40, b=20))
chart_volume = pio.to_html(fig_vol, **chart_opts)

# State distribution
state_counts = df["State"].value_counts().reset_index()
state_counts.columns = ["State", "Count"]
fig_state = px.bar(state_counts, x="Count", y="State", orientation="h", title="State Distribution", color_discrete_sequence=["#EF553B"])
fig_state.update_layout(xaxis_title="Tickets", yaxis_title="", margin=dict(t=40, b=20))
chart_state = pio.to_html(fig_state, **chart_opts)

# Subcategory breakdown (today)
df_today = df[df["Date"] == today]
subcat_table_html = ""
chart_sunburst = ""
if not df_today.empty:
    subcat_table_data = (
        df_today[df_today["Subcategory"] != ""]
        .groupby(["Category", "Subcategory"])
        .size()
        .reset_index(name="Tickets")
        .sort_values("Tickets", ascending=False)
    )
    if not subcat_table_data.empty:
        total = subcat_table_data["Tickets"].sum()
        subcat_table_data["% of Total"] = (subcat_table_data["Tickets"] / total * 100).round(1)
        subcat_table_html = subcat_table_data.to_html(index=False, classes="data-table")

    sunburst_data = df_today[df_today["Subcategory"] != ""].groupby(["Category", "Subcategory"]).size().reset_index(name="Count")
    if not sunburst_data.empty:
        fig_sun = px.sunburst(sunburst_data, path=["Category", "Subcategory"], values="Count", title="Today's Category → Subcategory")
        fig_sun.update_traces(textinfo="label+percent entry")
        fig_sun.update_layout(margin=dict(t=40, b=20))
        chart_sunburst = pio.to_html(fig_sun, **chart_opts)

# Daily subcategory stacked bar
daily_subcat = df[df["Subcategory"] != ""].groupby(["Date", "Subcategory"]).size().reset_index(name="Tickets").sort_values("Date")
fig_daily_subcat = px.bar(daily_subcat, x="Date", y="Tickets", color="Subcategory", title="Daily Tickets by Subcategory", barmode="stack")
fig_daily_subcat.update_layout(xaxis_title="", yaxis_title="Tickets", margin=dict(t=40, b=20), legend=dict(font=dict(size=10)))
chart_daily_subcat = pio.to_html(fig_daily_subcat, **chart_opts)

# ── Daily breakdown table ────────────────────────────────────────────────────
summary_rows = []
for date, grp in df.groupby("Date"):
    raised = len(grp)
    closed = int((grp["State"].str.lower() == "closed").sum())
    resolved = int((grp["State"].str.lower() == "resolved").sum())
    still_open = int((~grp["State"].str.lower().isin(["closed", "resolved"])).sum())
    top_cat = grp["Category"].value_counts().idxmax() if not grp["Category"].empty else ""
    subcat_series = grp["Subcategory"][grp["Subcategory"] != ""]
    top_subcat = subcat_series.value_counts().idxmax() if not subcat_series.empty else ""
    escalations = int(grp["Is Escalated"].sum())
    overdue_count = int(
        ((grp["Due"].notna()) & (grp["Due"] < now_utc) & (~grp["State"].str.lower().isin(["closed", "resolved"]))).sum()
    )
    day_name = pd.Timestamp(date).strftime("%A")
    summary_rows.append(
        {
            "Date": date, "Day": day_name, "Raised": raised, "Closed": closed,
            "Resolved": resolved, "Still Open": still_open, "Top Problem Area": top_cat,
            "Top Subcategory": top_subcat, "Escalations": escalations, "Overdue": overdue_count,
        }
    )
daily_summary = pd.DataFrame(summary_rows).sort_values("Date", ascending=False)
daily_summary_html = daily_summary.to_html(index=False, classes="data-table")

# ── Raw tickets table ────────────────────────────────────────────────────────
display_cols = ["Ticket #", "Name", "State", "Priority", "Category", "Subcategory", "Assignee", "Requester", "Created", "Updated"]
raw_df = df[display_cols].copy()
raw_df["Created"] = raw_df["Created"].dt.strftime("%Y-%m-%d %H:%M")
raw_df["Updated"] = raw_df["Updated"].dt.strftime("%Y-%m-%d %H:%M")
raw_df = raw_df.sort_values("Created", ascending=False)
raw_tickets_html = raw_df.to_html(index=False, classes="data-table", table_id="raw-tickets")

# CSV for download
csv_data = raw_df.to_csv(index=False)
csv_b64 = __import__("base64").b64encode(csv_data.encode()).decode()

# ── Build HTML ───────────────────────────────────────────────────────────────
generated_at = now_utc.strftime("%Y-%m-%d %H:%M UTC")

page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="1800">
<title>IT Ticket Tracker</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8f9fa; color: #1a1a2e; padding: 20px; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.2rem; margin: 24px 0 12px; color: #333; border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; }}
  .timestamp {{ color: #888; font-size: 0.85rem; margin-bottom: 20px; }}
  .kpi-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
  .kpi-card {{ background: #fff; border-radius: 10px; padding: 16px 24px; flex: 1; min-width: 140px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); text-align: center; }}
  .kpi-card .label {{ font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
  .kpi-card .value {{ font-size: 2rem; font-weight: 700; color: #1a1a2e; margin-top: 4px; }}
  .charts-row {{ display: flex; gap: 16px; flex-wrap: wrap; }}
  .chart-box {{ background: #fff; border-radius: 10px; padding: 12px; flex: 1; min-width: 400px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .chart-full {{ background: #fff; border-radius: 10px; padding: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-top: 16px; }}
  .data-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  .data-table th {{ background: #1a1a2e; color: #fff; padding: 10px 12px; text-align: left; position: sticky; top: 0; }}
  .data-table td {{ padding: 8px 12px; border-bottom: 1px solid #e8e8e8; }}
  .data-table tr:hover td {{ background: #f0f4ff; }}
  .table-wrap {{ background: #fff; border-radius: 10px; padding: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-top: 16px; max-height: 500px; overflow: auto; }}
  .btn {{ display: inline-block; padding: 10px 20px; background: #1a1a2e; color: #fff; border-radius: 6px; text-decoration: none; font-size: 0.85rem; margin-top: 10px; cursor: pointer; border: none; }}
  .btn:hover {{ background: #2d2d5e; }}
  .search-box {{ padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; width: 300px; margin-bottom: 10px; font-size: 0.85rem; }}
</style>
</head>
<body>

<h1>IT Daily Ticket Tracker</h1>
<p class="timestamp">Last updated: {generated_at} &middot; Auto-refreshes every 30 minutes &middot; Data range: {start_date} to {today}</p>

<h2>Today's Summary</h2>
<div class="kpi-row">
  <div class="kpi-card"><div class="label">Raised</div><div class="value">{raised_today}</div></div>
  <div class="kpi-card"><div class="label">Closed</div><div class="value">{closed_today}</div></div>
  <div class="kpi-card"><div class="label">Resolved</div><div class="value">{resolved_today}</div></div>
  <div class="kpi-card"><div class="label">Still Open</div><div class="value">{html.escape(still_open_label)}</div></div>
</div>

<h2>Overall Status</h2>
<div class="kpi-row">
  <div class="kpi-card"><div class="label">Open Backlog</div><div class="value">{open_backlog}</div></div>
  <div class="kpi-card"><div class="label">High / Critical</div><div class="value">{high_crit}</div></div>
  <div class="kpi-card"><div class="label">Overdue</div><div class="value">{overdue_all}</div></div>
</div>

<h2>Charts</h2>
<div class="charts-row">
  <div class="chart-box">{chart_volume}</div>
  <div class="chart-box">{chart_state}</div>
</div>

<h2>Subcategory Breakdown (Today)</h2>
<div class="charts-row">
  <div class="chart-box">{subcat_table_html if subcat_table_html else '<p style="padding:20px;color:#888;">No subcategory data for today.</p>'}</div>
  <div class="chart-box">{chart_sunburst if chart_sunburst else '<p style="padding:20px;color:#888;">No data.</p>'}</div>
</div>

<div class="chart-full">{chart_daily_subcat}</div>

<h2>Daily Breakdown</h2>
<div class="table-wrap">{daily_summary_html}</div>

<h2>Raw Tickets</h2>
<input class="search-box" type="text" id="search" placeholder="Search tickets..." onkeyup="filterTable()">
<a class="btn" href="data:text/csv;base64,{csv_b64}" download="tickets_{start_date}_{today}.csv">Download CSV</a>
<div class="table-wrap">{raw_tickets_html}</div>

<script>
function filterTable() {{
  const q = document.getElementById('search').value.toLowerCase();
  const rows = document.querySelectorAll('#raw-tickets tbody tr');
  rows.forEach(row => {{
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}}
</script>

</body>
</html>"""

out_path = "index.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(page_html)

print(f"Report generated: {out_path} ({len(page_html):,} bytes)")
