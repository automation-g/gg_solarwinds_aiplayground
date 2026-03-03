"""IT Daily Ticket Tracker — Streamlit Dashboard."""

from __future__ import annotations

import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from collections import defaultdict

from api_client import fetch_incidents, fetch_incidents_with_details, fetch_time_tracks, safe_get

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="IT Ticket Tracker", layout="wide")

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.title("IT Ticket Tracker")
today = datetime.date.today()
default_start = today - datetime.timedelta(days=7)

date_range = st.sidebar.date_input(
    "Date range",
    value=(default_start, today),
    max_value=today,
)

if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = default_start, today

auto_refresh = st.sidebar.toggle("Auto-refresh (5 min)", value=True)
manual_refresh = st.sidebar.button("Refresh Now")

if auto_refresh:
    st.sidebar.caption("Data refreshes every 5 minutes")


# ── Data loading ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner="Fetching tickets from SolarWinds...")
def load_data(start: str, end: str) -> pd.DataFrame:
    raw = fetch_incidents(start, end)
    if not raw:
        return pd.DataFrame()

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
                "Resolved": r.get("resolved_at", ""),
                "Is Escalated": r.get("is_escalated", False),
            }
        )
    df = pd.DataFrame(rows)
    df["Created"] = pd.to_datetime(df["Created"], errors="coerce", utc=True)
    df["Updated"] = pd.to_datetime(df["Updated"], errors="coerce", utc=True)
    df["Due"] = pd.to_datetime(df["Due"], errors="coerce", utc=True)
    df["Resolved"] = pd.to_datetime(df["Resolved"], errors="coerce", utc=True)
    df["Date"] = df["Created"].dt.date
    return df


if manual_refresh:
    st.cache_data.clear()

start_str = start_date.strftime("%Y-%m-%dT00:00:00Z")
end_str = end_date.strftime("%Y-%m-%dT23:59:59Z")

df = load_data(start_str, end_str)

if df.empty:
    st.warning("No tickets found for the selected date range.")
    st.stop()

# ── Derived metrics ──────────────────────────────────────────────────────────
now_utc = pd.Timestamp.now(tz="UTC")
today_mask = df["Date"] == today

open_states = ["new", "assigned", "awaiting input", "in progress"]

# Today's numbers
raised_today = int(today_mask.sum())
closed_today = int(((df["State"].str.lower() == "closed") & today_mask).sum())
resolved_today = int(((df["State"].str.lower() == "resolved") & today_mask).sum())
still_open_today = int(((~df["State"].str.lower().isin(["closed", "resolved"])) & today_mask).sum())
overdue_today = int(((df["Due"].notna()) & (df["Due"] < now_utc) & (~df["State"].str.lower().isin(["closed", "resolved"])) & today_mask).sum())

# Date range numbers
open_backlog = int((~df["State"].str.lower().isin(["closed", "resolved"])).sum())
high_crit = int(df["Priority"].str.lower().isin(["high", "medium", "critical"]).sum())
overdue_all = int(((df["Due"].notna()) & (df["Due"] < now_utc) & (~df["State"].str.lower().isin(["closed", "resolved"]))).sum())

# ── KPI Cards ────────────────────────────────────────────────────────────────
st.markdown("### Today's Summary")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Raised", raised_today)
k2.metric("Closed", closed_today)
k3.metric("Resolved", resolved_today)
k4.metric("Still Open", f"{still_open_today}  ({overdue_today} overdue)" if overdue_today else still_open_today)

# ── Agent Utilization (Today) ───────────────────────────────────────────────
st.markdown("### Agent Utilization (Today)")
with st.spinner("Fetching time tracks..."):
    today_raw_list = [r for r in load_data.__wrapped__(start_str, end_str) if False] if False else []
    # Re-fetch today's incidents from the raw API for time tracks
    today_incidents_raw = fetch_incidents(
        today.strftime("%Y-%m-%dT00:00:00Z"),
        today.strftime("%Y-%m-%dT23:59:59Z"),
    )
    today_detailed = fetch_incidents_with_details(today_incidents_raw)
    time_tracks = fetch_time_tracks(today_detailed)

agent_util: dict[str, dict] = defaultdict(lambda: {"minutes": 0, "entries": 0, "tickets_assigned": 0, "tasks": []})

for r in today_incidents_raw:
    assignee = safe_get(r, "assignee", "name")
    if assignee:
        agent_util[assignee]["tickets_assigned"] += 1

for tt in time_tracks:
    creator = tt.get("creator", {}).get("name", "Unknown")
    mins = tt.get("minutes", 0)
    task_name = tt.get("name", "")
    agent_util[creator]["minutes"] += mins
    agent_util[creator]["entries"] += 1
    agent_util[creator]["tasks"].append(f"{task_name} ({mins}m)")

agent_rows = []
for agent, data in sorted(agent_util.items(), key=lambda x: -x[1]["minutes"]):
    total_mins = data["minutes"]
    hrs = total_mins // 60
    mins_rem = total_mins % 60
    agent_rows.append({
        "Agent": agent,
        "Tickets Assigned": data["tickets_assigned"],
        "Time Logged": f"{hrs}h {mins_rem}m" if total_mins > 0 else "-",
        "Entries": data["entries"],
        "Tasks": ", ".join(data["tasks"][:5]) if data["tasks"] else "-",
    })

if agent_rows:
    st.dataframe(pd.DataFrame(agent_rows), use_container_width=True, hide_index=True)
else:
    st.info("No time tracking data for today.")

st.markdown("### Overall Status")
r1, r2, r3 = st.columns(3)
r1.metric("Open Backlog", open_backlog)
r2.metric("High / Critical", high_crit)
r3.metric("Overdue", overdue_all)

st.divider()

# ── Charts ───────────────────────────────────────────────────────────────────
c1, c2 = st.columns(2)

# Daily volume trend — fixed to last 5 days
daily_counts = df.groupby("Date").size().reset_index(name="Tickets").sort_values("Date")
last_5_dates = [today - datetime.timedelta(days=i) for i in range(4, -1, -1)]
last_5_df = pd.DataFrame({"Date": last_5_dates})
last_5_df = last_5_df.merge(daily_counts, on="Date", how="left").fillna(0)
last_5_df["Tickets"] = last_5_df["Tickets"].astype(int)
last_5_df["Day"] = last_5_df["Date"].apply(lambda d: pd.Timestamp(d).strftime("%b %d (%a)"))
fig_vol = px.bar(
    last_5_df,
    x="Day",
    y="Tickets",
    title="Daily Volume Trend (Last 5 Days)",
    text="Tickets",
    color_discrete_sequence=["#636EFA"],
)
fig_vol.update_traces(textposition="outside", width=0.6)
fig_vol.update_layout(
    xaxis_title="", yaxis_title="Tickets",
    xaxis=dict(type="category", tickangle=0, fixedrange=True),
    yaxis=dict(fixedrange=True),
    margin=dict(t=40, b=40), height=350,
    dragmode=False,
)
c1.plotly_chart(fig_vol, use_container_width=True)

# Subcategory breakdown — today's tickets only
st.divider()
st.markdown("### Subcategory Breakdown (Today)")
df_today = df[df["Date"] == today]

if df_today.empty:
    st.info("No tickets raised today yet.")
else:
    sc1, sc2 = st.columns(2)

    # Summary table: Category → Subcategory with counts
    subcat_table = (
        df_today[df_today["Subcategory"] != ""]
        .groupby(["Category", "Subcategory"])
        .size()
        .reset_index(name="Tickets")
        .sort_values("Tickets", ascending=False)
    )
    if not subcat_table.empty:
        # Add percentage column
        total = subcat_table["Tickets"].sum()
        subcat_table["% of Total"] = (subcat_table["Tickets"] / total * 100).round(1)
        sc1.dataframe(subcat_table, use_container_width=True, hide_index=True, height=350)
    else:
        sc1.info("No subcategory data for today.")

    # Sunburst: Category → Subcategory hierarchy — today only
    sunburst_data = df_today[df_today["Subcategory"] != ""].groupby(["Category", "Subcategory"]).size().reset_index(name="Count")
    if not sunburst_data.empty:
        fig_sun = px.sunburst(
            sunburst_data,
            path=["Category", "Subcategory"],
            values="Count",
            title="Today's Category → Subcategory",
        )
        fig_sun.update_traces(textinfo="label+percent entry")
        fig_sun.update_layout(margin=dict(t=40, b=20), height=450)
        sc2.plotly_chart(fig_sun, use_container_width=True)

# Daily subcategory stacked bar chart
daily_subcat = df[df["Subcategory"] != ""].groupby(["Date", "Subcategory"]).size().reset_index(name="Tickets")
daily_subcat = daily_subcat.sort_values("Date")
fig_daily_subcat = px.bar(
    daily_subcat,
    x="Date",
    y="Tickets",
    color="Subcategory",
    title="Daily Tickets by Subcategory",
    barmode="stack",
)
fig_daily_subcat.update_layout(xaxis_title="", yaxis_title="Tickets", margin=dict(t=40, b=20), legend=dict(font=dict(size=10)))
st.plotly_chart(fig_daily_subcat, use_container_width=True)

# State distribution
state_counts = df["State"].value_counts().reset_index()
state_counts.columns = ["State", "Count"]
fig_state = px.bar(
    state_counts,
    x="Count",
    y="State",
    orientation="h",
    title="State Distribution",
    color_discrete_sequence=["#EF553B"],
)
fig_state.update_layout(xaxis_title="Tickets", yaxis_title="", margin=dict(t=40, b=20))
c2.plotly_chart(fig_state, use_container_width=True)

st.divider()

# ── Daily Breakdown Table ────────────────────────────────────────────────────
st.markdown("### Daily Breakdown")


def build_daily_summary(data: pd.DataFrame) -> pd.DataFrame:
    summary_rows = []
    for date, grp in data.groupby("Date"):
        raised = len(grp)
        closed = int((grp["State"].str.lower() == "closed").sum())
        resolved = int((grp["State"].str.lower() == "resolved").sum())
        still_open = int(
            grp["State"].str.lower().isin(["new", "assigned", "awaiting input", "in progress"]).sum()
        )
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
                "Date": date,
                "Day": day_name,
                "Raised": raised,
                "Closed": closed,
                "Resolved": resolved,
                "Still Open": still_open,
                "Top Category Area": top_cat,
                "Top Subcategory": top_subcat,
                "Escalations": escalations,
                "Overdue": overdue_count,
            }
        )
    return pd.DataFrame(summary_rows).sort_values("Date", ascending=False)


daily_summary = build_daily_summary(df)
st.dataframe(daily_summary, use_container_width=True, hide_index=True)

st.divider()

# ── Raw Tickets Tab ──────────────────────────────────────────────────────────
st.markdown("### Raw Tickets")

# Filters
f1, f2, f3, f4 = st.columns(4)
with f1:
    states = ["All"] + sorted(df["State"].dropna().unique().tolist())
    sel_state = st.selectbox("State", states)
with f2:
    priorities = ["All"] + sorted(df["Priority"].dropna().unique().tolist())
    sel_priority = st.selectbox("Priority", priorities)
with f3:
    categories = ["All"] + sorted(df["Category"].dropna().unique().tolist())
    sel_category = st.selectbox("Category", categories)
with f4:
    subcat_options = df["Subcategory"].dropna().unique().tolist()
    subcategories = ["All"] + sorted([s for s in subcat_options if s != ""])
    sel_subcategory = st.selectbox("Subcategory", subcategories)

filtered = df.copy()
if sel_state != "All":
    filtered = filtered[filtered["State"] == sel_state]
if sel_priority != "All":
    filtered = filtered[filtered["Priority"] == sel_priority]
if sel_category != "All":
    filtered = filtered[filtered["Category"] == sel_category]
if sel_subcategory != "All":
    filtered = filtered[filtered["Subcategory"] == sel_subcategory]

display_cols = [
    "Ticket #", "Name", "State", "Priority", "Category", "Subcategory",
    "Assignee", "Requester", "Created", "Updated",
]
st.dataframe(
    filtered[display_cols].sort_values("Created", ascending=False),
    use_container_width=True,
    hide_index=True,
)

# CSV download
csv = filtered[display_cols].to_csv(index=False)
st.download_button(
    label="Download CSV",
    data=csv,
    file_name=f"tickets_{start_date}_{end_date}.csv",
    mime="text/csv",
)
