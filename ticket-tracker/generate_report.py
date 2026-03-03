"""Generate a static HTML report for GitHub Pages deployment.

Outputs index.html with embedded Plotly charts, sidebar controls, and live refresh."""

from __future__ import annotations

import datetime
import html
import os

import pandas as pd
import plotly.express as px
import plotly.io as pio

from api_client import fetch_incidents, fetch_incidents_with_details, fetch_time_tracks, safe_get

# ── Fetch data ───────────────────────────────────────────────────────────────
today = datetime.date.today()
days_back = int(os.getenv("DAYS_BACK", "7"))
start_date = today - datetime.timedelta(days=days_back)
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

# ── Agent Time Tracking (today only) ────────────────────────────────────────
print("Fetching time tracks for today's tickets...")
today_raw = [r for r in raw if pd.to_datetime(r.get("created_at", ""), utc=True).date() == today]
today_detailed = fetch_incidents_with_details(today_raw)
time_tracks = fetch_time_tracks(today_detailed)
print(f"Got {len(time_tracks)} time track entries")

from collections import defaultdict
agent_util: dict[str, dict] = defaultdict(lambda: {"minutes": 0, "entries": 0, "tickets_assigned": 0, "tasks": [], "group": ""})

# Map agent to group from detailed incidents
agent_group_map: dict[str, str] = {}
for d in today_detailed:
    assignee = safe_get(d, "assignee", "name")
    group = safe_get(d, "group_assignee", "name")
    if assignee and group:
        agent_group_map[assignee] = group

# Count tickets assigned per agent (today)
for r in today_detailed:
    assignee = safe_get(r, "assignee", "name")
    if assignee:
        agent_util[assignee]["tickets_assigned"] += 1
        agent_util[assignee]["group"] = agent_group_map.get(assignee, "")

# Aggregate time tracks
for tt in time_tracks:
    creator = tt.get("creator", {}).get("name", "Unknown")
    mins = tt.get("minutes", 0)
    task_name = tt.get("name", "")
    agent_util[creator]["minutes"] += mins
    agent_util[creator]["entries"] += 1
    agent_util[creator]["tasks"].append(f"{task_name} ({mins}m)")
    if not agent_util[creator]["group"]:
        agent_util[creator]["group"] = agent_group_map.get(creator, "")

# Build agent utilization table
agent_rows = []
for agent, data in sorted(agent_util.items(), key=lambda x: -x[1]["minutes"]):
    total_mins = data["minutes"]
    hrs = total_mins // 60
    mins = total_mins % 60
    agent_rows.append({
        "Group": data["group"],
        "Agent": agent,
        "Tickets Assigned": data["tickets_assigned"],
        "Time Logged": f"{hrs}h {mins}m" if total_mins > 0 else "-",
        "Entries": data["entries"],
        "Tasks": ", ".join(data["tasks"][:5]) if data["tasks"] else "-",
    })
agent_util_df = pd.DataFrame(agent_rows) if agent_rows else pd.DataFrame()
agent_util_html = agent_util_df.to_html(index=False, classes="data-table", table_id="agent-util") if not agent_util_df.empty else ""

# Group filter options
agent_groups = sorted(set(r["Group"] for r in agent_rows if r["Group"])) if agent_rows else []
agent_group_options = "\n".join(f'<option value="{g}">{g}</option>' for g in agent_groups)

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

# Daily volume trend — fixed to last 5 days
daily_counts = df.groupby("Date").size().reset_index(name="Tickets").sort_values("Date")
# Ensure last 5 calendar days are present (fill missing days with 0)
last_5_dates = [today - datetime.timedelta(days=i) for i in range(4, -1, -1)]
last_5_df = pd.DataFrame({"Date": last_5_dates})
last_5_df = last_5_df.merge(daily_counts, on="Date", how="left").fillna(0)
last_5_df["Tickets"] = last_5_df["Tickets"].astype(int)
last_5_df["Day"] = last_5_df["Date"].apply(lambda d: pd.Timestamp(d).strftime("%b %d (%a)"))
fig_vol = px.bar(last_5_df, x="Day", y="Tickets", title="Daily Volume Trend (Last 5 Days)", text="Tickets", color_discrete_sequence=["#636EFA"])
fig_vol.update_traces(textposition="outside", width=0.6)
fig_vol.update_layout(
    xaxis_title="", yaxis_title="Tickets",
    xaxis=dict(type="category", tickangle=0, fixedrange=True),
    yaxis=dict(fixedrange=True),
    margin=dict(t=40, b=40),
    height=350,
    dragmode=False,
)
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
        fig_sun.update_layout(margin=dict(t=40, b=20), height=450)
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
    cat_series = grp["Category"][grp["Category"].fillna("").str.strip() != ""]
    top_cat = cat_series.value_counts().idxmax() if not cat_series.empty else ""
    subcat_series = grp["Subcategory"][grp["Subcategory"].fillna("").str.strip() != ""]
    top_subcat = subcat_series.value_counts().idxmax() if not subcat_series.empty else ""
    escalations = int(grp["Is Escalated"].sum())
    overdue_count = int(
        ((grp["Due"].notna()) & (grp["Due"] < now_utc) & (~grp["State"].str.lower().isin(["closed", "resolved"]))).sum()
    )
    day_name = pd.Timestamp(date).strftime("%A")
    summary_rows.append(
        {
            "Date": date, "Day": day_name, "Raised": raised, "Closed": closed,
            "Resolved": resolved, "Still Open": still_open, "Top Category Area": top_cat,
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

# Filter options for raw tickets
state_options = "\n".join(f'<option value="{s}">{s}</option>' for s in sorted(df["State"].dropna().unique()))
priority_options = "\n".join(f'<option value="{p}">{p}</option>' for p in sorted(df["Priority"].dropna().unique()))
category_options = "\n".join(f'<option value="{c}">{c}</option>' for c in sorted(df["Category"].dropna().unique()))
subcat_list = sorted([s for s in df["Subcategory"].dropna().unique() if s != ""])
subcategory_options = "\n".join(f'<option value="{s}">{s}</option>' for s in subcat_list)

# GitHub PAT for dispatch (embedded securely — only has actions scope)
gh_pat = os.getenv("GH_PAT", "")

# ── Build HTML ───────────────────────────────────────────────────────────────
generated_at = now_utc.strftime("%Y-%m-%d %H:%M UTC")

page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="1800">
<title>IT Ticket Tracker (Beta)</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8f9fa; color: #1a1a2e; display: flex; min-height: 100vh; }}

  /* Sidebar */
  .sidebar {{ width: 260px; background: #1a1a2e; color: #fff; padding: 24px 16px; flex-shrink: 0; position: fixed; top: 0; left: 0; bottom: 0; overflow-y: auto; }}
  .sidebar h1 {{ font-size: 1.2rem; margin-bottom: 24px; color: #fff; }}
  .sidebar .section-label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 1px; color: #8888aa; margin: 20px 0 8px; }}
  .sidebar .info-box {{ background: rgba(255,255,255,0.08); border-radius: 8px; padding: 12px; margin-bottom: 12px; }}
  .sidebar .info-box .label {{ font-size: 0.75rem; color: #aaa; }}
  .sidebar .info-box .value {{ font-size: 0.95rem; font-weight: 600; margin-top: 2px; }}
  .sidebar .btn {{ display: block; width: 100%; padding: 10px; background: #4472C4; color: #fff; border: none; border-radius: 6px; font-size: 0.85rem; cursor: pointer; text-align: center; margin-top: 8px; text-decoration: none; }}
  .sidebar .btn:hover {{ background: #3561b0; }}
  .sidebar .btn-outline {{ background: transparent; border: 1px solid rgba(255,255,255,0.3); }}
  .sidebar .btn-outline:hover {{ background: rgba(255,255,255,0.1); }}
  .sidebar .status-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #4ade80; margin-right: 6px; }}
  .sidebar select {{ width: 100%; padding: 8px; border-radius: 6px; border: none; font-size: 0.85rem; margin-top: 4px; background: rgba(255,255,255,0.1); color: #fff; }}
  .sidebar select option {{ background: #1a1a2e; color: #fff; }}

  /* Main content */
  .main {{ margin-left: 260px; padding: 24px; flex: 1; }}
  h2 {{ font-size: 1.2rem; margin: 24px 0 12px; color: #333; border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; }}
  .kpi-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
  .kpi-card {{ background: #fff; border-radius: 10px; padding: 16px 24px; flex: 1; min-width: 140px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); text-align: center; }}
  .kpi-card .label {{ font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
  .kpi-card .value {{ font-size: 2rem; font-weight: 700; color: #1a1a2e; margin-top: 4px; }}
  .charts-row {{ display: flex; gap: 16px; flex-wrap: wrap; }}
  .chart-box {{ background: #fff; border-radius: 10px; padding: 12px; flex: 1; min-width: 400px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .chart-full {{ background: #fff; border-radius: 10px; padding: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-top: 16px; }}
  .subcat-table-scroll {{ height: 450px; overflow-y: auto; }}
  .subcat-chart {{ height: 450px; }}
  .data-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  .data-table th {{ background: #1a1a2e; color: #fff; padding: 10px 12px; text-align: left; position: sticky; top: 0; }}
  .data-table td {{ padding: 8px 12px; border-bottom: 1px solid #e8e8e8; }}
  .data-table tr:hover td {{ background: #f0f4ff; }}
  .table-wrap {{ background: #fff; border-radius: 10px; padding: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-top: 16px; max-height: 500px; overflow: auto; }}
  .content-btn {{ display: inline-block; padding: 10px 20px; background: #1a1a2e; color: #fff; border-radius: 6px; text-decoration: none; font-size: 0.85rem; margin-top: 10px; cursor: pointer; border: none; }}
  .content-btn:hover {{ background: #2d2d5e; }}
  .search-box {{ padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; width: 300px; margin-bottom: 10px; font-size: 0.85rem; }}
  .filter-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }}
  .filter-row select {{ padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 0.85rem; min-width: 160px; }}

  @media (max-width: 900px) {{
    .sidebar {{ position: relative; width: 100%; }}
    .main {{ margin-left: 0; }}
    body {{ flex-direction: column; }}
  }}
</style>
</head>
<body>

<!-- Sidebar -->
<div class="sidebar">
  <h1>IT Ticket Tracker (Beta)</h1>

  <div class="section-label">Date Range</div>
  <div class="info-box">
    <div class="label">From</div>
    <input type="date" id="dateFrom" value="{start_date}" style="width:100%;padding:6px;border-radius:6px;border:none;background:rgba(255,255,255,0.1);color:#fff;margin-top:4px;font-size:0.85rem;">
  </div>
  <div class="info-box">
    <div class="label">To</div>
    <input type="date" id="dateTo" value="{today}" max="{today}" style="width:100%;padding:6px;border-radius:6px;border:none;background:rgba(255,255,255,0.1);color:#fff;margin-top:4px;font-size:0.85rem;">
  </div>

  <button class="btn" id="refreshBtn" onclick="triggerRefresh()">Refresh with New Data</button>
  <div id="refreshStatus" style="font-size:0.75rem;color:#4ade80;margin-top:6px;display:none;"></div>

  <div class="section-label">Data Status</div>
  <div class="info-box">
    <div class="label"><span class="status-dot"></span>Auto-refresh</div>
    <div class="value">Every 30 minutes</div>
  </div>
  <div class="info-box">
    <div class="label">Last Updated</div>
    <div class="value">{generated_at}</div>
  </div>

  <div class="section-label">Quick Stats</div>
  <div class="info-box">
    <div class="label">Total Tickets ({days_back} days)</div>
    <div class="value">{len(df)}</div>
  </div>
  <div class="info-box">
    <div class="label">Today's Tickets</div>
    <div class="value">{raised_today}</div>
  </div>

  <div style="margin-top:20px;">
    <a class="btn btn-outline" href="data:text/csv;base64,{csv_b64}" download="tickets_{start_date}_{today}.csv">Download CSV</a>
  </div>
</div>

<!-- Main Content -->
<div class="main">

<h2>Today's Summary</h2>
<div class="kpi-row">
  <div class="kpi-card"><div class="label">Raised</div><div class="value">{raised_today}</div></div>
  <div class="kpi-card"><div class="label">Closed</div><div class="value">{closed_today}</div></div>
  <div class="kpi-card"><div class="label">Resolved</div><div class="value">{resolved_today}</div></div>
  <div class="kpi-card"><div class="label">Still Open</div><div class="value">{html.escape(still_open_label)}</div></div>
</div>

<h2>Agent Utilization (Today)</h2>
<div class="filter-row">
  <select id="filterGroup" onchange="filterAgentTable()">
    <option value="">All Groups</option>
    {agent_group_options}
  </select>
</div>
<div class="table-wrap">{agent_util_html if agent_util_html else '<p style="padding:20px;color:#888;">No time tracking data for today.</p>'}</div>

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
  <div class="chart-box subcat-table-scroll">{subcat_table_html if subcat_table_html else '<p style="padding:20px;color:#888;">No subcategory data for today.</p>'}</div>
  <div class="chart-box subcat-chart">{chart_sunburst if chart_sunburst else '<p style="padding:20px;color:#888;">No data.</p>'}</div>
</div>

<div class="chart-full">{chart_daily_subcat}</div>

<h2>Daily Breakdown</h2>
<div class="table-wrap">{daily_summary_html}</div>

<h2>Raw Tickets</h2>
<div style="margin-bottom:12px;">
  <a class="content-btn" href="data:text/csv;base64,{csv_b64}" download="tickets_{start_date}_{today}.csv">Export CSV</a>
</div>
<div class="filter-row">
  <select id="filterState" onchange="filterTable()">
    <option value="">All States</option>
    {state_options}
  </select>
  <select id="filterPriority" onchange="filterTable()">
    <option value="">All Priorities</option>
    {priority_options}
  </select>
  <select id="filterCategory" onchange="filterTable()">
    <option value="">All Categories</option>
    {category_options}
  </select>
  <select id="filterSubcategory" onchange="filterTable()">
    <option value="">All Subcategories</option>
    {subcategory_options}
  </select>
  <input class="search-box" type="text" id="search" placeholder="Search..." onkeyup="filterTable()" style="margin:0;">
</div>
<div class="table-wrap">{raw_tickets_html}</div>

</div><!-- end .main -->

<script>
const GH_PAT = '{gh_pat}';
const REPO = 'automation-g/gg_solarwinds_aiplayground';
const WORKFLOW = 'deploy.yml';

async function triggerRefresh() {{
  const btn = document.getElementById('refreshBtn');
  const status = document.getElementById('refreshStatus');
  const dateFrom = document.getElementById('dateFrom').value;
  const dateTo = document.getElementById('dateTo').value;
  const diffMs = new Date(dateTo) - new Date(dateFrom);
  const days = Math.max(1, Math.ceil(diffMs / (1000 * 60 * 60 * 24))).toString();

  if (!GH_PAT) {{
    // Fallback: open GitHub Actions page
    window.open(`https://github.com/${{REPO}}/actions/workflows/${{WORKFLOW}}`, '_blank');
    return;
  }}

  btn.disabled = true;
  btn.textContent = 'Triggering build...';
  status.style.display = 'block';
  status.textContent = 'Sending dispatch request...';

  try {{
    const resp = await fetch(`https://api.github.com/repos/${{REPO}}/actions/workflows/${{WORKFLOW}}/dispatches`, {{
      method: 'POST',
      headers: {{
        'Authorization': `Bearer ${{GH_PAT}}`,
        'Accept': 'application/vnd.github.v3+json',
      }},
      body: JSON.stringify({{ ref: 'main', inputs: {{ days_back: days }} }})
    }});

    if (resp.status === 204) {{
      status.textContent = 'Build triggered! Page will update in ~2 minutes.';
      status.style.color = '#4ade80';
      btn.textContent = 'Build running...';
      // Auto-reload after 2.5 minutes
      setTimeout(() => location.reload(), 150000);
    }} else {{
      const err = await resp.text();
      status.textContent = `Error: ${{resp.status}}`;
      status.style.color = '#f87171';
      btn.textContent = 'Refresh with New Data';
      btn.disabled = false;
    }}
  }} catch (e) {{
    status.textContent = 'Network error';
    status.style.color = '#f87171';
    btn.textContent = 'Refresh with New Data';
    btn.disabled = false;
  }}
}}

function filterAgentTable() {{
  const group = document.getElementById('filterGroup').value;
  const rows = document.querySelectorAll('#agent-util tbody tr');
  rows.forEach(row => {{
    const cells = row.querySelectorAll('td');
    const matchGroup = !group || (cells[0] && cells[0].textContent.trim() === group);
    row.style.display = matchGroup ? '' : 'none';
  }});
}}

function filterTable() {{
  const q = document.getElementById('search').value.toLowerCase();
  const state = document.getElementById('filterState').value;
  const priority = document.getElementById('filterPriority').value;
  const category = document.getElementById('filterCategory').value;
  const subcategory = document.getElementById('filterSubcategory').value;
  const rows = document.querySelectorAll('#raw-tickets tbody tr');
  rows.forEach(row => {{
    const cells = row.querySelectorAll('td');
    const text = row.textContent.toLowerCase();
    const matchSearch = !q || text.includes(q);
    const matchState = !state || (cells[2] && cells[2].textContent.trim() === state);
    const matchPriority = !priority || (cells[3] && cells[3].textContent.trim() === priority);
    const matchCategory = !category || (cells[4] && cells[4].textContent.trim() === category);
    const matchSubcategory = !subcategory || (cells[5] && cells[5].textContent.trim() === subcategory);
    row.style.display = (matchSearch && matchState && matchPriority && matchCategory && matchSubcategory) ? '' : 'none';
  }});
}}
</script>

</body>
</html>"""

out_path = "index.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(page_html)

print(f"Report generated: {out_path} ({len(page_html):,} bytes)")
