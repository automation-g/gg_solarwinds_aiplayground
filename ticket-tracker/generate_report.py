"""Generate a static HTML report for GitHub Pages deployment.

Outputs index.html with embedded Plotly charts, sidebar controls, and live refresh."""

from __future__ import annotations

import datetime
import html
import os

import pandas as pd
import plotly.express as px
import plotly.io as pio

from api_client import fetch_incidents, fetch_incidents_updated, fetch_incidents_with_details, fetch_time_tracks, fetch_agent_groups, fetch_resolved_dates, safe_get

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

chart_opts = dict(full_html=False, include_plotlyjs=False)

# ── Agent Time Tracking (today only, including Internal) ─────────────────────
print("Fetching today's tickets (incl. Internal) for agent utilization...")
raw_with_internal = fetch_incidents(start_str, end_str, exclude_internal=False)
today_raw = [r for r in raw_with_internal if pd.to_datetime(r.get("created_at", ""), utc=True).date() == today]
print(f"Got {len(today_raw)} tickets today (incl. Internal)")
today_detailed = fetch_incidents_with_details(today_raw)
time_tracks = fetch_time_tracks(today_detailed)
print(f"Got {len(time_tracks)} time track entries")

from collections import defaultdict
agent_util: dict[str, dict] = defaultdict(lambda: {"minutes": 0, "entries": 0, "tickets_assigned": 0, "tasks": [], "group": ""})

# Map agent to their team group (from groups API, not ticket assignment)
print("Fetching agent group memberships...")
agent_group_map = fetch_agent_groups()
print(f"Mapped {len(agent_group_map)} agents to groups")

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
    })
agent_util_df = pd.DataFrame(agent_rows) if agent_rows else pd.DataFrame()
agent_util_html = agent_util_df.to_html(index=False, classes="data-table", table_id="agent-util") if not agent_util_df.empty else ""

# Agent utilization CSV for download
agent_csv_b64 = ""
if not agent_util_df.empty:
    agent_csv_data = agent_util_df.to_csv(index=False)
    agent_csv_b64 = __import__("base64").b64encode(agent_csv_data.encode()).decode()

# Group filter options
agent_groups = sorted(set(r["Group"] for r in agent_rows if r["Group"])) if agent_rows else []
agent_group_options = "\n".join(f'<option value="{g}">{g}</option>' for g in agent_groups)

# Chart: Time logged per group
chart_agent_group = ""
if not agent_util_df.empty:
    # Aggregate minutes per group (convert "Xh Ym" back to minutes for charting)
    group_time = defaultdict(int)
    group_tickets = defaultdict(int)
    for _, row in agent_util_df.iterrows():
        g = row["Group"] if row["Group"] else "Unassigned"
        tl = row["Time Logged"]
        if tl != "-":
            parts = tl.replace("h", "").replace("m", "").split()
            mins_total = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0
            group_time[g] += mins_total
        group_tickets[g] += row["Tickets Assigned"]
    group_chart_data = pd.DataFrame([
        {"Group": g, "Hours": round(m / 60, 1), "Tickets": group_tickets[g]}
        for g, m in sorted(group_time.items(), key=lambda x: -x[1])
        if m > 0
    ])
    if not group_chart_data.empty:
        fig_group = px.bar(
            group_chart_data, x="Hours", y="Group", orientation="h",
            title="Time Logged by Group (Today)",
            text="Hours", color="Group",
            hover_data=["Tickets"],
        )
        fig_group.update_traces(textposition="outside")
        fig_group.update_layout(
            xaxis_title="Hours", yaxis_title="",
            showlegend=False,
            margin=dict(t=40, b=20),
            height=max(250, len(group_chart_data) * 50 + 80),
            xaxis=dict(fixedrange=True),
            yaxis=dict(fixedrange=True),
        )
        chart_agent_group = pio.to_html(fig_group, **chart_opts)

# ── Agent Time Log (Today's logs across ALL tickets, incl. Internal) ─────────
print("Fetching all incidents updated today (incl. Internal) for time logs...")
updated_today_raw = fetch_incidents_updated(
    today.strftime("%Y-%m-%dT00:00:00Z"),
    today.strftime("%Y-%m-%dT23:59:59Z"),
    exclude_internal=False,
)
print(f"Got {len(updated_today_raw)} incidents updated today (incl. Internal)")
updated_detailed = fetch_incidents_with_details(updated_today_raw)
all_time_tracks = fetch_time_tracks(updated_detailed)
# Filter to only time entries logged today
today_str = today.strftime("%Y-%m-%d")
todays_logs = [t for t in all_time_tracks if t.get("created_at", "")[:10] == today_str]
print(f"Time entries logged today (all tickets): {len(todays_logs)}")

all_agent_util: dict[str, dict] = defaultdict(lambda: {"minutes": 0, "entries": 0, "group": ""})
for tt in todays_logs:
    creator = tt.get("creator", {}).get("name", "Unknown")
    mins = tt.get("minutes", 0)
    all_agent_util[creator]["minutes"] += mins
    all_agent_util[creator]["entries"] += 1
    all_agent_util[creator]["group"] = agent_group_map.get(creator, "")

all_agent_rows = []
for agent, data in sorted(all_agent_util.items(), key=lambda x: -x[1]["minutes"]):
    total_mins = data["minutes"]
    hrs = total_mins // 60
    mins_r = total_mins % 60
    all_agent_rows.append({
        "Group": data["group"],
        "Agent": agent,
        "Time Logged": f"{hrs}h {mins_r}m",
        "Entries": data["entries"],
    })
all_agent_util_df = pd.DataFrame(all_agent_rows) if all_agent_rows else pd.DataFrame()
all_agent_util_html = all_agent_util_df.to_html(index=False, classes="data-table", table_id="all-agent-util") if not all_agent_util_df.empty else ""

# CSV for all agent time log
all_agent_csv_b64 = ""
if not all_agent_util_df.empty:
    all_agent_csv_data = all_agent_util_df.to_csv(index=False)
    all_agent_csv_b64 = __import__("base64").b64encode(all_agent_csv_data.encode()).decode()

# Group filter options for all-tickets section
all_agent_groups = sorted(set(r["Group"] for r in all_agent_rows if r["Group"])) if all_agent_rows else []
all_agent_group_options = "\n".join(f'<option value="{g}">{g}</option>' for g in all_agent_groups)

# Chart: Time logged per group (all tickets)
chart_all_agent_group = ""
if not all_agent_util_df.empty:
    all_group_time = defaultdict(int)
    for _, row in all_agent_util_df.iterrows():
        g = row["Group"] if row["Group"] else "Unassigned"
        tl = row["Time Logged"]
        parts = tl.replace("h", "").replace("m", "").split()
        mins_total = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0
        all_group_time[g] += mins_total
    all_group_chart_data = pd.DataFrame([
        {"Group": g, "Hours": round(m / 60, 1)}
        for g, m in sorted(all_group_time.items(), key=lambda x: -x[1])
        if m > 0
    ])
    if not all_group_chart_data.empty:
        fig_all_group = px.bar(
            all_group_chart_data, x="Hours", y="Group", orientation="h",
            title="Time Logged by Group (All Tickets)",
            text="Hours", color="Group",
        )
        fig_all_group.update_traces(textposition="outside")
        fig_all_group.update_layout(
            xaxis_title="Hours", yaxis_title="",
            showlegend=False,
            margin=dict(t=40, b=20),
            height=max(250, len(all_group_chart_data) * 50 + 80),
            xaxis=dict(fixedrange=True),
            yaxis=dict(fixedrange=True),
        )
        chart_all_agent_group = pio.to_html(fig_all_group, **chart_opts)

# ── Resolutions by Agent (today) ──────────────────────────────────────────────
# SolarWinds API has no resolved_at field. Fetch the actual resolution date
# from the audit trail (state change to Resolved/Closed).
resolved_candidates = []
for r in updated_detailed:
    # Exclude Internal from resolution chart
    cat = safe_get(r, "category", "name") if isinstance(r.get("category"), dict) else str(r.get("category", ""))
    if cat.strip().lower() == "internal":
        continue
    state_val = r.get("state", "")
    if isinstance(state_val, dict):
        state_val = state_val.get("name", "")
    if str(state_val).strip().lower() in ("resolved", "closed"):
        resolved_candidates.append(r)

print(f"Fetching resolution dates from audit trail for {len(resolved_candidates)} tickets...")
resolved_dates = fetch_resolved_dates(resolved_candidates)
print(f"Got {len(resolved_dates)} resolution dates")

resolved_today_list = []
for r in resolved_candidates:
    inc_id = r.get("id", 0)
    resolved_at = resolved_dates.get(inc_id, "")
    if today_str in str(resolved_at):
        resolved_today_list.append(r)
res_by_agent = defaultdict(int)
for r in resolved_today_list:
    assignee = safe_get(r, "assignee", "name") or "Unassigned"
    res_by_agent[assignee] += 1

chart_resolution_agent = ""
total_resolved_by_agent = sum(res_by_agent.values())
if res_by_agent:
    res_agent_df = pd.DataFrame(
        sorted(res_by_agent.items(), key=lambda x: x[1]),
        columns=["Agent", "Resolved"],
    )
    fig_res_agent = px.bar(
        res_agent_df, x="Resolved", y="Agent", orientation="h",
        title=f"All Resolved/Closed Today — {total_resolved_by_agent}",
        text="Resolved", color="Resolved",
        color_continuous_scale=["#a0a7e8", "#636EFA", "#2d2d5e", "#1a1a2e"],
    )
    fig_res_agent.update_traces(textposition="outside")
    fig_res_agent.update_layout(
        xaxis_title="Resolved Tickets", yaxis_title="",
        showlegend=False, coloraxis_showscale=False,
        margin=dict(l=150, t=40, r=30, b=30),
        xaxis=dict(fixedrange=True),
        yaxis=dict(fixedrange=True, automargin=True),
    )
    chart_resolution_agent = pio.to_html(fig_res_agent, **chart_opts, default_height="100%")

# Chart: Resolutions by agent for today's tickets only (created AND resolved today)
chart_resolution_today_only = ""
res_today_only = defaultdict(int)
for r in resolved_today_list:
    created = r.get("created_at", "")
    if today_str in str(created):
        assignee = safe_get(r, "assignee", "name") or "Unassigned"
        res_today_only[assignee] += 1

total_resolved_today_only = sum(res_today_only.values())
if res_today_only:
    res_today_df = pd.DataFrame(
        sorted(res_today_only.items(), key=lambda x: x[1]),
        columns=["Agent", "Resolved"],
    )
    fig_res_today = px.bar(
        res_today_df, x="Resolved", y="Agent", orientation="h",
        title=f"Today's Tickets Resolved/Closed — {total_resolved_today_only}",
        text="Resolved", color="Resolved",
        color_continuous_scale=["#b8d4a8", "#00cc96", "#1a7a5c", "#0d4030"],
    )
    fig_res_today.update_traces(textposition="outside")
    fig_res_today.update_layout(
        xaxis_title="Resolved Tickets", yaxis_title="",
        showlegend=False, coloraxis_showscale=False,
        margin=dict(l=150, t=40, r=30, b=30),
        xaxis=dict(fixedrange=True),
        yaxis=dict(fixedrange=True, automargin=True),
    )
    chart_resolution_today_only = pio.to_html(fig_res_today, **chart_opts, default_height="100%")

# ── Service Request vs Incident breakdown by agent (resolved/closed) ──────────
# Overall: all resolved today (regardless of creation date)
svc_inc_all_rows = []
for r in resolved_today_list:
    assignee = safe_get(r, "assignee", "name") or "Unassigned"
    req_type = "Service Request" if r.get("is_service_request") else "Incident"
    svc_inc_all_rows.append({"Agent": assignee, "Type": req_type})

chart_svc_inc_all = ""
if svc_inc_all_rows:
    svc_inc_all_df = pd.DataFrame(svc_inc_all_rows)
    svc_inc_all_agg = svc_inc_all_df.groupby(["Agent", "Type"]).size().reset_index(name="Count")
    agent_order_all = svc_inc_all_agg.groupby("Agent")["Count"].sum().sort_values(ascending=True).index.tolist()
    agent_order_all.reverse()
    fig_svc_all = px.bar(
        svc_inc_all_agg, x="Count", y="Agent", color="Type", orientation="h",
        title=f"All Resolved/Closed Today — SVC vs INC ({len(svc_inc_all_rows)} total)",
        text="Count", barmode="stack",
        color_discrete_map={"Service Request": "#7B68EE", "Incident": "#DAA520"},
        category_orders={"Agent": agent_order_all},
    )
    fig_svc_all.update_traces(textposition="inside", textfont_color="white")
    fig_svc_all.update_layout(
        xaxis_title="Tickets", yaxis_title="",
        margin=dict(l=150, t=40, r=30, b=30),
        height=max(350, len(agent_order_all) * 30 + 80),
        xaxis=dict(fixedrange=True),
        yaxis=dict(fixedrange=True, automargin=True),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    chart_svc_inc_all = pio.to_html(fig_svc_all, **chart_opts, default_height="100%")

# Today's tickets only: created today AND resolved today
svc_inc_created_today_rows = []
for r in resolved_today_list:
    created = r.get("created_at", "")
    if today_str in str(created):
        assignee = safe_get(r, "assignee", "name") or "Unassigned"
        req_type = "Service Request" if r.get("is_service_request") else "Incident"
        svc_inc_created_today_rows.append({"Agent": assignee, "Type": req_type})

chart_svc_inc_created_today = ""
if svc_inc_created_today_rows:
    svc_inc_ct_df = pd.DataFrame(svc_inc_created_today_rows)
    svc_inc_ct_agg = svc_inc_ct_df.groupby(["Agent", "Type"]).size().reset_index(name="Count")
    agent_order_ct = svc_inc_ct_agg.groupby("Agent")["Count"].sum().sort_values(ascending=True).index.tolist()
    agent_order_ct.reverse()
    fig_svc_ct = px.bar(
        svc_inc_ct_agg, x="Count", y="Agent", color="Type", orientation="h",
        title=f"Today's Tickets Resolved/Closed — SVC vs INC ({len(svc_inc_created_today_rows)} total)",
        text="Count", barmode="stack",
        color_discrete_map={"Service Request": "#7B68EE", "Incident": "#DAA520"},
        category_orders={"Agent": agent_order_ct},
    )
    fig_svc_ct.update_traces(textposition="inside", textfont_color="white")
    fig_svc_ct.update_layout(
        xaxis_title="Tickets", yaxis_title="",
        margin=dict(l=150, t=40, r=30, b=30),
        height=max(350, len(agent_order_ct) * 30 + 80),
        xaxis=dict(fixedrange=True),
        yaxis=dict(fixedrange=True, automargin=True),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    chart_svc_inc_created_today = pio.to_html(fig_svc_ct, **chart_opts, default_height="100%")

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

# Daily volume trend — fixed to last 5 days
daily_counts = df.groupby("Date").size().reset_index(name="Tickets").sort_values("Date")
# Ensure last 5 calendar days are present (fill missing days with 0)
last_5_dates = [today - datetime.timedelta(days=i) for i in range(4, -1, -1)]
last_5_df = pd.DataFrame({"Date": last_5_dates})
last_5_df = last_5_df.merge(daily_counts, on="Date", how="left").fillna(0)
last_5_df["Tickets"] = last_5_df["Tickets"].astype(int)
last_5_df["Day"] = last_5_df["Date"].apply(lambda d: pd.Timestamp(d).strftime("%m/%d"))
fig_vol = px.bar(last_5_df, x="Day", y="Tickets", title="Daily Volume Trend (Last 5 Days)", text="Tickets", color_discrete_sequence=["#636EFA"])
max_tickets = last_5_df["Tickets"].max()
fig_vol.update_traces(textposition="outside", width=0.6)
fig_vol.update_layout(
    xaxis_title="", yaxis_title="Tickets",
    xaxis=dict(type="category", tickangle=0, fixedrange=True),
    yaxis=dict(fixedrange=True, range=[0, max_tickets * 1.25]),
    margin=dict(t=40, b=40),
    height=350,
    dragmode=False,
)
chart_volume = pio.to_html(fig_vol, **chart_opts)

# State distribution (today only)
state_counts = df[df["Date"] == today]["State"].value_counts().reset_index()
state_counts.columns = ["State", "Count"]
fig_state = px.bar(state_counts, x="Count", y="State", orientation="h", title="State Distribution (Today)", text="Count", color_discrete_sequence=["#EF553B"])
fig_state.update_traces(textposition="outside")
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
    .main {{ margin-left: 0; padding: 12px; }}
    body {{ flex-direction: column; }}
    .charts-row {{ flex-direction: column; }}
    .chart-box {{ min-width: 100% !important; width: 100% !important; flex: none !important; height: auto !important; }}
    .chart-full {{ width: 100%; }}
    .kpi-row {{ flex-wrap: wrap; }}
    .kpi-card {{ min-width: 45%; }}
    .filter-row {{ flex-direction: column; }}
    .filter-row select {{ width: 100%; min-width: auto; }}
    .search-box {{ width: 100%; }}
    .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
    .data-table {{ min-width: 600px; }}
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

  <div style="margin-top:12px;">
    <a class="btn" href="shift.html" style="background:#7B68EE;">Shift Report</a>
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

<h2>Resolutions by Agent (Today)</h2>
<div class="charts-row" style="align-items: stretch;">
  <div class="chart-box" style="min-width: 48%; flex: 1; height: 550px; overflow: auto;">{chart_resolution_agent if chart_resolution_agent else '<p style="padding:20px;color:#888;">No resolved tickets today.</p>'}</div>
  <div class="chart-box" style="min-width: 48%; flex: 1; height: 550px; overflow: auto;">{chart_resolution_today_only if chart_resolution_today_only else '<p style="padding:20px;color:#888;">No today-created tickets resolved yet.</p>'}</div>
</div>

<h2>Service Request vs Incident (Resolved/Closed Today)</h2>
<div class="charts-row" style="align-items: stretch;">
  <div class="chart-box" style="min-width: 48%; flex: 1; height: 550px; overflow: auto;">{chart_svc_inc_all if chart_svc_inc_all else '<p style="padding:20px;color:#888;">No data.</p>'}</div>
  <div class="chart-box" style="min-width: 48%; flex: 1; height: 550px; overflow: auto;">{chart_svc_inc_created_today if chart_svc_inc_created_today else '<p style="padding:20px;color:#888;">No data.</p>'}</div>
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

<h2>Agent Utilization (Today)</h2>
{f'<div class="chart-full">{chart_agent_group}</div>' if chart_agent_group else ''}
<div style="margin-bottom:12px;margin-top:12px;">
  <a class="content-btn" href="data:text/csv;base64,{agent_csv_b64}" download="agent_utilization_{today}.csv">Export CSV</a>
</div>
<div class="filter-row">
  <select id="filterGroup" multiple onchange="filterAgentTable()" style="min-width:300px;min-height:36px;padding:6px;">
    {agent_group_options}
  </select>
  <span style="font-size:0.8rem;color:#888;align-self:center;">Hold Ctrl/Cmd to select multiple groups. No selection = All.</span>
</div>
<div class="table-wrap">{agent_util_html if agent_util_html else '<p style="padding:20px;color:#888;">No time tracking data for today.</p>'}</div>

<h2>Agent Time Log (All Tickets)</h2>
<p style="font-size:0.85rem;color:#666;margin-bottom:12px;">Time entries logged today across all tickets (including older ones).</p>
{f'<div class="chart-full">{chart_all_agent_group}</div>' if chart_all_agent_group else ''}
<div style="margin-bottom:12px;margin-top:12px;">
  <a class="content-btn" href="data:text/csv;base64,{all_agent_csv_b64}" download="agent_time_log_{today}.csv">Export CSV</a>
</div>
<div class="filter-row">
  <select id="filterAllGroup" multiple onchange="filterAllAgentTable()" style="min-width:300px;min-height:36px;padding:6px;">
    {all_agent_group_options}
  </select>
  <span style="font-size:0.8rem;color:#888;align-self:center;">Hold Ctrl/Cmd to select multiple groups. No selection = All.</span>
</div>
<div class="table-wrap">{all_agent_util_html if all_agent_util_html else '<p style="padding:20px;color:#888;">No time log data for today.</p>'}</div>

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
  const sel = document.getElementById('filterGroup');
  const selected = Array.from(sel.selectedOptions).map(o => o.value);
  const rows = document.querySelectorAll('#agent-util tbody tr');
  rows.forEach(row => {{
    const cells = row.querySelectorAll('td');
    const groupVal = cells[0] ? cells[0].textContent.trim() : '';
    const matchGroup = selected.length === 0 || selected.includes(groupVal);
    row.style.display = matchGroup ? '' : 'none';
  }});
}}

function filterAllAgentTable() {{
  const sel = document.getElementById('filterAllGroup');
  const selected = Array.from(sel.selectedOptions).map(o => o.value);
  const rows = document.querySelectorAll('#all-agent-util tbody tr');
  rows.forEach(row => {{
    const cells = row.querySelectorAll('td');
    const groupVal = cells[0] ? cells[0].textContent.trim() : '';
    const matchGroup = selected.length === 0 || selected.includes(groupVal);
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

# ── Shift Report (client-side time-filtered page) ────────────────────────────
import json

# Prepare today's ticket data for embedding
shift_tickets = []
for r in raw:
    created = r.get("created_at", "")
    if today_str not in str(created):
        continue
    shift_tickets.append({
        "number": r.get("number", ""),
        "name": r.get("name", ""),
        "state": r.get("state", ""),
        "priority": r.get("priority", ""),
        "category": safe_get(r, "category", "name"),
        "subcategory": safe_get(r, "subcategory", "name"),
        "assignee": safe_get(r, "assignee", "name") or "Unassigned",
        "requester": safe_get(r, "requester", "name"),
        "created_at": created,
        "is_service_request": r.get("is_service_request", False),
    })

# Prepare resolution data with audit-trail timestamps
shift_resolutions = []
for r in resolved_today_list:
    inc_id = r.get("id", 0)
    resolved_at = resolved_dates.get(inc_id, "")
    state_val = r.get("state", "")
    if isinstance(state_val, dict):
        state_val = state_val.get("name", "")
    shift_resolutions.append({
        "assignee": safe_get(r, "assignee", "name") or "Unassigned",
        "is_service_request": r.get("is_service_request", False),
        "created_at": r.get("created_at", ""),
        "resolved_at": resolved_at,
        "state": str(state_val).strip(),
    })

# Prepare time log data
shift_time_logs = []
for tt in todays_logs:
    shift_time_logs.append({
        "creator": tt.get("creator", {}).get("name", "Unknown"),
        "minutes": tt.get("minutes", 0),
        "created_at": tt.get("created_at", ""),
    })

shift_json = json.dumps({
    "tickets": shift_tickets,
    "resolutions": shift_resolutions,
    "time_logs": shift_time_logs,
    "agent_groups": dict(agent_group_map),
    "today": today_str,
}, default=str)

shift_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shift Report | IT Ticket Tracker</title>
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
  .sidebar input[type="time"] {{ width: 100%; padding: 8px; border-radius: 6px; border: none; background: rgba(255,255,255,0.1); color: #fff; font-size: 0.9rem; margin-top: 4px; }}

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
    .main {{ margin-left: 0; padding: 12px; }}
    body {{ flex-direction: column; }}
    .charts-row {{ flex-direction: column; }}
    .chart-box {{ min-width: 100% !important; width: 100% !important; flex: none !important; height: auto !important; }}
    .chart-full {{ width: 100%; }}
    .kpi-row {{ flex-wrap: wrap; }}
    .kpi-card {{ min-width: 45%; }}
    .filter-row {{ flex-direction: column; }}
    .filter-row select {{ width: 100%; min-width: auto; }}
    .search-box {{ width: 100%; }}
    .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
    .data-table {{ min-width: 600px; }}
  }}
</style>
</head>
<body>

<!-- Sidebar -->
<div class="sidebar">
  <h1>IT Ticket Tracker<br><small style="font-size:0.75rem;color:#8888aa;">Shift Report</small></h1>

  <div class="section-label">Time Window (UAE)</div>
  <div class="info-box">
    <div class="label">Start Time</div>
    <input type="time" id="shiftStart" value="14:15">
  </div>
  <div class="info-box">
    <div class="label">End Time</div>
    <input type="time" id="shiftEnd" value="20:30">
  </div>

  <button class="btn" onclick="renderAll()" style="background:#7B68EE;">Apply Time Filter</button>

  <div class="section-label">Data Status</div>
  <div class="info-box">
    <div class="label"><span class="status-dot"></span>Report Date</div>
    <div class="value">{today}</div>
  </div>
  <div class="info-box">
    <div class="label">Last Updated</div>
    <div class="value">{generated_at}</div>
  </div>

  <div class="section-label">Quick Stats</div>
  <div class="info-box">
    <div class="label">Tickets in Window</div>
    <div class="value" id="sidebarTickets">-</div>
  </div>
  <div class="info-box">
    <div class="label">Resolved in Window</div>
    <div class="value" id="sidebarResolved">-</div>
  </div>

  <div style="margin-top:20px;">
    <a class="btn btn-outline" href="index.html">Back to Full Report</a>
  </div>
</div>

<!-- Main Content -->
<div class="main">

<h2>Today's Summary <span id="timeBadge" style="display:inline-block;background:#7B68EE;color:#fff;padding:4px 12px;border-radius:12px;font-size:0.85rem;font-weight:600;"></span></h2>
<div class="kpi-row">
  <div class="kpi-card"><div class="label">Raised</div><div class="value" id="kpiRaised">-</div></div>
  <div class="kpi-card"><div class="label">Closed</div><div class="value" id="kpiClosed">-</div></div>
  <div class="kpi-card"><div class="label">Resolved</div><div class="value" id="kpiResolved">-</div></div>
  <div class="kpi-card"><div class="label">Still Open</div><div class="value" id="kpiOpen">-</div></div>
</div>

<h2>Charts</h2>
<div class="charts-row">
  <div class="chart-box" id="chartState" style="min-height:350px;"></div>
</div>

<h2>Resolutions by Agent (Today)</h2>
<div class="charts-row" style="align-items: stretch;">
  <div class="chart-box" id="chartResAll" style="min-width: 48%; flex: 1;"></div>
  <div class="chart-box" id="chartResToday" style="min-width: 48%; flex: 1;"></div>
</div>

<h2>Service Request vs Incident (Resolved/Closed Today)</h2>
<div class="charts-row" style="align-items: stretch;">
  <div class="chart-box" id="chartSvcAll" style="min-width: 48%; flex: 1;"></div>
  <div class="chart-box" id="chartSvcToday" style="min-width: 48%; flex: 1;"></div>
</div>

<h2>Subcategory Breakdown (Today)</h2>
<div class="charts-row">
  <div class="chart-box subcat-table-scroll" id="subcatTable"></div>
  <div class="chart-box subcat-chart" id="chartSunburst"></div>
</div>

<h2>Raw Tickets</h2>
<div class="filter-row">
  <select id="filterState" onchange="filterRawTable()">
    <option value="">All States</option>
  </select>
  <select id="filterPriority" onchange="filterRawTable()">
    <option value="">All Priorities</option>
  </select>
  <select id="filterCategory" onchange="filterRawTable()">
    <option value="">All Categories</option>
  </select>
  <input class="search-box" type="text" id="search" placeholder="Search..." onkeyup="filterRawTable()" style="margin:0;">
</div>
<div class="table-wrap" id="rawTable"></div>

<h2>Agent Utilization (Today)</h2>
<div class="chart-full" id="chartAgentGroup"></div>
<div class="table-wrap" id="agentTable"></div>

</div><!-- end .main -->

<script>
const DATA = {shift_json};
const UAE_OFFSET_MS = 4 * 3600000; // UTC+4 in milliseconds

function toUaeMinutes(utcStr) {{
  if (!utcStr) return -1;
  const d = new Date(utcStr);
  if (isNaN(d)) return -1;
  // Add UAE offset and use UTC methods to get UAE hours/minutes
  const uae = new Date(d.getTime() + UAE_OFFSET_MS);
  return uae.getUTCHours() * 60 + uae.getUTCMinutes();
}}

function fmtUaeTime(utcStr) {{
  if (!utcStr) return '';
  const d = new Date(utcStr);
  if (isNaN(d)) return '';
  const uae = new Date(d.getTime() + UAE_OFFSET_MS);
  const y = uae.getUTCFullYear();
  const mo = String(uae.getUTCMonth() + 1).padStart(2, '0');
  const da = String(uae.getUTCDate()).padStart(2, '0');
  const h = String(uae.getUTCHours()).padStart(2, '0');
  const mi = String(uae.getUTCMinutes()).padStart(2, '0');
  return y + '-' + mo + '-' + da + ' ' + h + ':' + mi;
}}

function inWindow(utcStr, startMins, endMins) {{
  const m = toUaeMinutes(utcStr);
  return m >= 0 && m >= startMins && m <= endMins;
}}

function getWindow() {{
  const s = document.getElementById('shiftStart').value.split(':');
  const e = document.getElementById('shiftEnd').value.split(':');
  const startMins = parseInt(s[0]) * 60 + parseInt(s[1]);
  const endMins = parseInt(e[0]) * 60 + parseInt(e[1]);
  return {{ startMins, endMins, startH: parseInt(s[0]), startM: parseInt(s[1]), endH: parseInt(e[0]), endM: parseInt(e[1]) }};
}}

function fmtAmPm(h, m) {{
  const ampm = h >= 12 ? 'PM' : 'AM';
  return (h % 12 || 12) + ':' + String(m).padStart(2, '0') + ' ' + ampm;
}}

function countBy(arr, key) {{
  const m = {{}};
  arr.forEach(r => {{ const k = r[key] || 'Unassigned'; m[k] = (m[k] || 0) + 1; }});
  return m;
}}

// Interpolate a gradient color scale (array of [position, hex])
function gradientColor(val, maxVal, scale) {{
  if (maxVal === 0) return scale[0][1];
  const t = val / maxVal;
  for (let i = 1; i < scale.length; i++) {{
    if (t <= scale[i][0]) {{
      const t0 = scale[i-1][0], t1 = scale[i][0];
      const f = (t - t0) / (t1 - t0);
      const c0 = scale[i-1][1], c1 = scale[i][1];
      const r = Math.round(parseInt(c0.slice(1,3),16) * (1-f) + parseInt(c1.slice(1,3),16) * f);
      const g = Math.round(parseInt(c0.slice(3,5),16) * (1-f) + parseInt(c1.slice(3,5),16) * f);
      const b = Math.round(parseInt(c0.slice(5,7),16) * (1-f) + parseInt(c1.slice(5,7),16) * f);
      return '#' + [r,g,b].map(x => x.toString(16).padStart(2,'0')).join('');
    }}
  }}
  return scale[scale.length-1][1];
}}

const BLUE_SCALE = [[0,'#a0a7e8'],[0.33,'#636EFA'],[0.66,'#2d2d5e'],[1,'#1a1a2e']];
const GREEN_SCALE = [[0,'#b8d4a8'],[0.33,'#00cc96'],[0.66,'#1a7a5c'],[1,'#0d4030']];

let currentTickets = [];

function renderAll() {{
  const w = getWindow();
  document.getElementById('timeBadge').textContent = fmtAmPm(w.startH, w.startM) + ' - ' + fmtAmPm(w.endH, w.endM) + ' UAE';

  // Filter tickets created in window
  const tickets = DATA.tickets.filter(t => inWindow(t.created_at, w.startMins, w.endMins));
  currentTickets = tickets;

  // KPIs — all based on tickets created in the window only
  const raised = tickets.length;
  const closed = tickets.filter(t => (t.state||'').toLowerCase() === 'closed').length;
  const resolved = tickets.filter(t => (t.state||'').toLowerCase() === 'resolved').length;
  const stillOpen = tickets.filter(t => !['closed','resolved'].includes((t.state||'').toLowerCase())).length;
  document.getElementById('kpiRaised').textContent = raised;
  document.getElementById('kpiClosed').textContent = closed;
  document.getElementById('kpiResolved').textContent = resolved;
  document.getElementById('kpiOpen').textContent = stillOpen;

  // Resolutions: only tickets created in the window that are resolved/closed
  const ticketNumbers = new Set(tickets.map(t => t.number));
  const resAll = DATA.resolutions.filter(r => inWindow(r.resolved_at, w.startMins, w.endMins));
  // Filter to only tickets created in the window
  const resInWindow = resAll.filter(r => inWindow(r.created_at, w.startMins, w.endMins));
  // "Today's tickets" = created in window AND resolved in window (same set for shift)
  const resToday = resInWindow;

  document.getElementById('sidebarTickets').textContent = raised;
  document.getElementById('sidebarResolved').textContent = resInWindow.length;

  // ── State Distribution ──
  const stateCounts = countBy(tickets, 'state');
  const stateEntries = Object.entries(stateCounts).sort((a,b) => b[1] - a[1]);
  const stateEl = document.getElementById('chartState');
  if (stateEntries.length) {{
    Plotly.newPlot('chartState', [{{
      type: 'bar', orientation: 'h',
      y: stateEntries.map(e => e[0]), x: stateEntries.map(e => e[1]),
      text: stateEntries.map(e => String(e[1])), textposition: 'outside',
      marker: {{ color: '#EF553B' }},
    }}], {{
      title: 'State Distribution (Today)', margin: {{ l: 150, t: 40, r: 40, b: 20 }},
      xaxis: {{ title: 'Tickets', fixedrange: true }}, yaxis: {{ fixedrange: true, automargin: true }},
      height: 350, bargap: 0.15,
    }}, {{ displayModeBar: false, responsive: true }});
  }} else {{
    stateEl.innerHTML = '<p style="padding:20px;color:#888;">No data for this time window.</p>';
  }}

  // ── Resolutions by Agent — All (created in window) ──
  const resAllByAgent = countBy(resInWindow, 'assignee');
  const resAllSorted = Object.entries(resAllByAgent).sort((a,b) => a[1] - b[1]);
  const resAllMax = Math.max(...resAllSorted.map(e => e[1]), 1);
  const resAllEl = document.getElementById('chartResAll');
  if (resAllSorted.length) {{
    Plotly.newPlot('chartResAll', [{{
      type: 'bar', orientation: 'h',
      y: resAllSorted.map(e => e[0]), x: resAllSorted.map(e => e[1]),
      text: resAllSorted.map(e => String(e[1])), textposition: 'outside',
      marker: {{ color: resAllSorted.map(e => gradientColor(e[1], resAllMax, BLUE_SCALE)) }},
      showlegend: false,
    }}], {{
      title: 'All Resolved/Closed Today \\u2014 ' + resInWindow.length,
      margin: {{ l: 150, t: 40, r: 30, b: 30 }},
      xaxis: {{ title: 'Resolved Tickets', fixedrange: true }},
      yaxis: {{ fixedrange: true, automargin: true }},
      showlegend: false, bargap: 0.15,
    }}, {{ displayModeBar: false, responsive: true }});
  }} else {{
    resAllEl.innerHTML = '<p style="padding:20px;color:#888;">No resolved tickets in this time window.</p>';
  }}

  // ── Resolutions by Agent — Today's tickets only ──
  const resTodayByAgent = countBy(resToday, 'assignee');
  const resTodaySorted = Object.entries(resTodayByAgent).sort((a,b) => a[1] - b[1]);
  const resTodayMax = Math.max(...resTodaySorted.map(e => e[1]), 1);
  const resTodayEl = document.getElementById('chartResToday');
  if (resTodaySorted.length) {{
    Plotly.newPlot('chartResToday', [{{
      type: 'bar', orientation: 'h',
      y: resTodaySorted.map(e => e[0]), x: resTodaySorted.map(e => e[1]),
      text: resTodaySorted.map(e => String(e[1])), textposition: 'outside',
      marker: {{ color: resTodaySorted.map(e => gradientColor(e[1], resTodayMax, GREEN_SCALE)) }},
      showlegend: false,
    }}], {{
      title: "Today's Tickets Resolved/Closed \\u2014 " + resToday.length,
      margin: {{ l: 150, t: 40, r: 30, b: 30 }},
      xaxis: {{ title: 'Resolved Tickets', fixedrange: true }},
      yaxis: {{ fixedrange: true, automargin: true }},
      showlegend: false, bargap: 0.15,
    }}, {{ displayModeBar: false, responsive: true }});
  }} else {{
    resTodayEl.innerHTML = '<p style="padding:20px;color:#888;">No today-created tickets resolved yet.</p>';
  }}

  // ── SVC vs INC — All resolved (created in window) ──
  const svcIncAll = {{}};
  resInWindow.forEach(r => {{
    const a = r.assignee || 'Unassigned';
    if (!svcIncAll[a]) svcIncAll[a] = {{ svc: 0, inc: 0 }};
    r.is_service_request ? svcIncAll[a].svc++ : svcIncAll[a].inc++;
  }});
  const svcAllAgents = Object.entries(svcIncAll).sort((a,b) => (b[1].svc+b[1].inc) - (a[1].svc+a[1].inc));
  const svcAllEl = document.getElementById('chartSvcAll');
  if (svcAllAgents.length) {{
    Plotly.newPlot('chartSvcAll', [
      {{ type:'bar', orientation:'h', y: svcAllAgents.map(e=>e[0]), x: svcAllAgents.map(e=>e[1].svc),
         name:'Service Request', marker:{{color:'#7B68EE'}}, text: svcAllAgents.map(e=>e[1].svc||''), textposition:'inside', textfont:{{color:'white'}} }},
      {{ type:'bar', orientation:'h', y: svcAllAgents.map(e=>e[0]), x: svcAllAgents.map(e=>e[1].inc),
         name:'Incident', marker:{{color:'#DAA520'}}, text: svcAllAgents.map(e=>e[1].inc||''), textposition:'inside', textfont:{{color:'white'}} }},
    ], {{
      barmode:'stack', title:'All Resolved/Closed Today \\u2014 SVC vs INC ('+resInWindow.length+' total)',
      margin:{{l:150,t:40,r:30,b:30}}, height: Math.max(350, svcAllAgents.length*40+80),
      xaxis:{{fixedrange:true,title:'Tickets'}}, yaxis:{{fixedrange:true,automargin:true}},
      legend:{{orientation:'h',yanchor:'bottom',y:1.02,xanchor:'right',x:1}}, bargap:0.15,
    }}, {{displayModeBar:false,responsive:true}});
  }} else {{
    svcAllEl.innerHTML = '<p style="padding:20px;color:#888;">No data.</p>';
  }}

  // ── SVC vs INC — Today's tickets only ──
  const svcIncToday = {{}};
  resToday.forEach(r => {{
    const a = r.assignee || 'Unassigned';
    if (!svcIncToday[a]) svcIncToday[a] = {{ svc: 0, inc: 0 }};
    r.is_service_request ? svcIncToday[a].svc++ : svcIncToday[a].inc++;
  }});
  const svcTodayAgents = Object.entries(svcIncToday).sort((a,b) => (b[1].svc+b[1].inc) - (a[1].svc+a[1].inc));
  const svcTodayEl = document.getElementById('chartSvcToday');
  if (svcTodayAgents.length) {{
    Plotly.newPlot('chartSvcToday', [
      {{ type:'bar', orientation:'h', y: svcTodayAgents.map(e=>e[0]), x: svcTodayAgents.map(e=>e[1].svc),
         name:'Service Request', marker:{{color:'#7B68EE'}}, text: svcTodayAgents.map(e=>e[1].svc||''), textposition:'inside', textfont:{{color:'white'}} }},
      {{ type:'bar', orientation:'h', y: svcTodayAgents.map(e=>e[0]), x: svcTodayAgents.map(e=>e[1].inc),
         name:'Incident', marker:{{color:'#DAA520'}}, text: svcTodayAgents.map(e=>e[1].inc||''), textposition:'inside', textfont:{{color:'white'}} }},
    ], {{
      barmode:'stack', title:"Today's Tickets Resolved/Closed \\u2014 SVC vs INC ("+resToday.length+' total)',
      margin:{{l:150,t:40,r:30,b:30}}, height: Math.max(350, svcTodayAgents.length*40+80),
      xaxis:{{fixedrange:true,title:'Tickets'}}, yaxis:{{fixedrange:true,automargin:true}},
      legend:{{orientation:'h',yanchor:'bottom',y:1.02,xanchor:'right',x:1}}, bargap:0.15,
    }}, {{displayModeBar:false,responsive:true}});
  }} else {{
    svcTodayEl.innerHTML = '<p style="padding:20px;color:#888;">No data.</p>';
  }}

  // ── Subcategory Breakdown ──
  const subcatCounts = {{}};
  tickets.forEach(t => {{
    if (!t.subcategory) return;
    const key = t.category + '|||' + t.subcategory;
    subcatCounts[key] = (subcatCounts[key] || 0) + 1;
  }});
  const subcatEntries = Object.entries(subcatCounts).sort((a,b) => b[1] - a[1]);
  const subcatEl = document.getElementById('subcatTable');
  if (subcatEntries.length) {{
    const total = subcatEntries.reduce((s,e) => s+e[1], 0);
    let html = '<table class="data-table"><thead><tr><th>Category</th><th>Subcategory</th><th>Tickets</th><th>% of Total</th></tr></thead><tbody>';
    subcatEntries.forEach(([k,v]) => {{
      const [cat, sub] = k.split('|||');
      html += '<tr><td>'+cat+'</td><td>'+sub+'</td><td>'+v+'</td><td>'+(v/total*100).toFixed(1)+'%</td></tr>';
    }});
    html += '</tbody></table>';
    subcatEl.innerHTML = html;
  }} else {{
    subcatEl.innerHTML = '<p style="padding:20px;color:#888;">No subcategory data for this window.</p>';
  }}

  // Sunburst — use unique ids to avoid duplicate subcategory names across categories
  const sunEl = document.getElementById('chartSunburst');
  if (subcatEntries.length) {{
    const sunData = {{}};
    tickets.forEach(t => {{
      if (!t.subcategory) return;
      if (!sunData[t.category]) sunData[t.category] = {{}};
      sunData[t.category][t.subcategory] = (sunData[t.category][t.subcategory] || 0) + 1;
    }});
    const ids = [], labels = [], parents = [], values = [];
    Object.entries(sunData).forEach(([cat, subs]) => {{
      ids.push(cat); labels.push(cat); parents.push(''); values.push(Object.values(subs).reduce((a,b)=>a+b,0));
      Object.entries(subs).forEach(([sub, cnt]) => {{
        ids.push(cat + ' / ' + sub); labels.push(sub); parents.push(cat); values.push(cnt);
      }});
    }});
    Plotly.newPlot('chartSunburst', [{{
      type:'sunburst', ids:ids, labels:labels, parents:parents, values:values,
      textinfo:'label+percent entry', branchvalues:'total',
    }}], {{
      title:"Today's Category \\u2192 Subcategory", margin:{{t:40,b:20}}, height:450,
    }}, {{displayModeBar:false,responsive:true}});
  }} else {{
    sunEl.innerHTML = '<p style="padding:20px;color:#888;">No data.</p>';
  }}

  // ── Agent Utilization ──
  const timeLogs = DATA.time_logs.filter(t => inWindow(t.created_at, w.startMins, w.endMins));
  const agentTime = {{}};
  timeLogs.forEach(t => {{
    const c = t.creator;
    if (!agentTime[c]) agentTime[c] = {{ minutes:0, entries:0, group: DATA.agent_groups[c]||'' }};
    agentTime[c].minutes += t.minutes;
    agentTime[c].entries++;
  }});
  const agentEntries = Object.entries(agentTime).sort((a,b) => b[1].minutes - a[1].minutes);

  // Group chart
  const groupTime = {{}};
  const groupTickets = {{}};
  agentEntries.forEach(([a,d]) => {{
    const g = d.group || 'Unassigned';
    groupTime[g] = (groupTime[g]||0) + d.minutes;
  }});
  const groupEntries = Object.entries(groupTime).sort((a,b) => b[1]-a[1]).filter(e => e[1]>0);
  const agGroupEl = document.getElementById('chartAgentGroup');
  if (groupEntries.length) {{
    Plotly.newPlot('chartAgentGroup', [{{
      type:'bar', orientation:'h',
      y: groupEntries.map(e=>e[0]), x: groupEntries.map(e=>+(e[1]/60).toFixed(1)),
      text: groupEntries.map(e=>(e[1]/60).toFixed(1)), textposition:'outside',
      marker:{{ color: groupEntries.map((_,i) => ['#636EFA','#EF553B','#00cc96','#ab63fa','#FFA15A','#19d3f3'][i%6]) }},
    }}], {{
      title:'Time Logged by Group (Today)', margin:{{l:150,t:40,r:40,b:20}},
      height: Math.max(350, groupEntries.length*60+100),
      xaxis:{{fixedrange:true,title:'Hours'}}, yaxis:{{fixedrange:true,automargin:true}}, showlegend:false, bargap:0.15,
    }}, {{displayModeBar:false,responsive:true}});
  }} else {{
    agGroupEl.innerHTML = '<p style="padding:20px;color:#888;">No time log data for this window.</p>';
  }}

  // Agent table
  const agentTableEl = document.getElementById('agentTable');
  if (agentEntries.length) {{
    let html = '<table class="data-table"><thead><tr><th>Group</th><th>Agent</th><th>Time Logged</th><th>Entries</th></tr></thead><tbody>';
    agentEntries.forEach(([a,d]) => {{
      const h = Math.floor(d.minutes/60), m = d.minutes%60;
      html += '<tr><td>'+(d.group||'')+'</td><td>'+a+'</td><td>'+h+'h '+m+'m</td><td>'+d.entries+'</td></tr>';
    }});
    html += '</tbody></table>';
    agentTableEl.innerHTML = html;
  }} else {{
    agentTableEl.innerHTML = '<p style="padding:20px;color:#888;">No time log data for this window.</p>';
  }}

  // ── Raw Tickets Table ──
  renderRawTable(tickets);
  populateFilters(tickets);
}}

function renderRawTable(tickets) {{
  const rawEl = document.getElementById('rawTable');
  if (!tickets.length) {{
    rawEl.innerHTML = '<p style="padding:20px;color:#888;">No tickets in this time window.</p>';
    return;
  }}
  const sorted = [...tickets].sort((a,b) => b.created_at.localeCompare(a.created_at));
  let html = '<table class="data-table" id="raw-tickets"><thead><tr><th>Ticket #</th><th>Name</th><th>State</th><th>Priority</th><th>Category</th><th>Subcategory</th><th>Assignee</th><th>Requester</th><th>Created</th></tr></thead><tbody>';
  sorted.forEach(t => {{
    html += '<tr><td>'+t.number+'</td><td>'+t.name+'</td><td>'+t.state+'</td><td>'+t.priority+'</td><td>'+t.category+'</td><td>'+t.subcategory+'</td><td>'+t.assignee+'</td><td>'+t.requester+'</td><td>'+fmtUaeTime(t.created_at)+'</td></tr>';
  }});
  html += '</tbody></table>';
  rawEl.innerHTML = html;
}}

function populateFilters(tickets) {{
  const states = [...new Set(tickets.map(t=>t.state).filter(Boolean))].sort();
  const priorities = [...new Set(tickets.map(t=>t.priority).filter(Boolean))].sort();
  const categories = [...new Set(tickets.map(t=>t.category).filter(Boolean))].sort();
  const stateEl = document.getElementById('filterState');
  const prioEl = document.getElementById('filterPriority');
  const catEl = document.getElementById('filterCategory');
  stateEl.innerHTML = '<option value="">All States</option>' + states.map(s=>'<option value="'+s+'">'+s+'</option>').join('');
  prioEl.innerHTML = '<option value="">All Priorities</option>' + priorities.map(p=>'<option value="'+p+'">'+p+'</option>').join('');
  catEl.innerHTML = '<option value="">All Categories</option>' + categories.map(c=>'<option value="'+c+'">'+c+'</option>').join('');
}}

function filterRawTable() {{
  const q = document.getElementById('search').value.toLowerCase();
  const state = document.getElementById('filterState').value;
  const priority = document.getElementById('filterPriority').value;
  const category = document.getElementById('filterCategory').value;
  const rows = document.querySelectorAll('#raw-tickets tbody tr');
  rows.forEach(row => {{
    const cells = row.querySelectorAll('td');
    const text = row.textContent.toLowerCase();
    const ok = (!q || text.includes(q))
      && (!state || (cells[2] && cells[2].textContent.trim() === state))
      && (!priority || (cells[3] && cells[3].textContent.trim() === priority))
      && (!category || (cells[4] && cells[4].textContent.trim() === category));
    row.style.display = ok ? '' : 'none';
  }});
}}

document.addEventListener('DOMContentLoaded', renderAll);
</script>

</body>
</html>"""

shift_out = "shift.html"
with open(shift_out, "w", encoding="utf-8") as f:
    f.write(shift_html)
print(f"Shift report generated: {shift_out} ({len(shift_html):,} bytes)")
