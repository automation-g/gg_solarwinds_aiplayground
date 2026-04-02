"""Streamlit UI for browsing historical ticket data — optimized with SQL queries."""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path(__file__).parent / "ticket_history_slim.db"

st.set_page_config(page_title="IT Ticket History", page_icon="🛡️", layout="wide", initial_sidebar_state="collapsed")

# ── Dark theme CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .stApp { background-color: #1a1e2e; }
    [data-testid="stSidebar"] { display: none; }
    .dashboard-header {
        background: #222840; border: 1px solid #2d3352; border-radius: 10px;
        padding: 16px 24px; margin-bottom: 16px;
        display: flex; justify-content: space-between; align-items: center;
    }
    .dashboard-header h1 { color: #fff; font-size: 1.3rem; margin: 0; font-weight: 600; }
    .dashboard-header .breadcrumb { color: #8b949e; font-size: 0.75rem; margin-bottom: 2px; }
    .dashboard-header .ticket-count { color: #58a6ff; font-size: 0.85rem; }
    .kpi-row { display: flex; gap: 12px; margin: 0 0 16px; }
    .kpi-card {
        background: #222840; border: 1px solid #2d3352; border-radius: 10px;
        padding: 14px 18px; flex: 1; min-width: 0;
    }
    .kpi-card .label { color: #8b949e; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
    .kpi-card .value { color: #fff; font-size: 1.6rem; font-weight: 700; font-variant-numeric: tabular-nums; }
    .kpi-card .bar { height: 3px; border-radius: 2px; margin-top: 6px; }
    .section-title { color: #fff; font-size: 1rem; font-weight: 600; margin: 20px 0 10px; }
    #MainMenu { visibility: hidden; }
    header { visibility: hidden; }
    .stDeployButton { display: none; }
    .stTabs [data-baseweb="tab-list"] { gap: 0; background: #222840; border-radius: 8px; padding: 4px; }
    .stTabs [data-baseweb="tab"] { background: transparent; color: #8b949e; border-radius: 6px; padding: 8px 16px; font-size: 0.85rem; }
    .stTabs [aria-selected="true"] { background: #2d3352; color: #fff; }
    .stTabs [data-baseweb="tab-panel"] { padding: 0; }
    [data-testid="stDataFrame"] { border: 1px solid #2d3352; border-radius: 8px; }
    .stDownloadButton button { background: #238636; color: #fff; border: none; border-radius: 6px; }
    .stDownloadButton button:hover { background: #2ea043; }
    .stExpander { border: 1px solid #2d3352 !important; border-radius: 10px !important; background: #161b22 !important; }
    .stExpander summary { color: #c9d1d9 !important; }
    .stMultiSelect label, .stDateInput label, .stSelectbox label {
        color: #8b949e !important; font-size: 0.75rem !important;
        text-transform: uppercase; letter-spacing: 0.5px;
    }
    .page-nav { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
    .page-info { color: #8b949e; font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)

# ── DB helpers ────────────────────────────────────────────────────────────────
def get_conn():
    return sqlite3.connect(str(DB_PATH), check_same_thread=False)


def build_where(date_from, date_to, departments, sites, categories, states, priorities, assignees):
    """Build SQL WHERE clause from filters."""
    conditions = []
    params = []
    if date_from:
        conditions.append("created_at >= ?")
        params.append(f"{date_from}T00:00:00")
    if date_to:
        conditions.append("created_at <= ?")
        params.append(f"{date_to}T23:59:59")
    if departments:
        conditions.append(f"department IN ({','.join('?' for _ in departments)})")
        params.extend(departments)
    if sites:
        conditions.append(f"site IN ({','.join('?' for _ in sites)})")
        params.extend(sites)
    if categories:
        conditions.append(f"category IN ({','.join('?' for _ in categories)})")
        params.extend(categories)
    if states:
        conditions.append(f"state IN ({','.join('?' for _ in states)})")
        params.extend(states)
    if priorities:
        conditions.append(f"priority IN ({','.join('?' for _ in priorities)})")
        params.extend(priorities)
    if assignees:
        conditions.append(f"assignee_name IN ({','.join('?' for _ in assignees)})")
        params.extend(assignees)
    where = " AND ".join(conditions) if conditions else "1=1"
    return where, params


@st.cache_data(ttl=600)
def get_filter_options():
    """Load filter options once — lightweight query."""
    conn = get_conn()
    opts = {}
    for col in ["department", "site", "category", "state", "priority", "assignee_name"]:
        rows = conn.execute(f"SELECT DISTINCT {col} FROM incidents WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}").fetchall()
        opts[col] = [r[0] for r in rows]
    dates = conn.execute("SELECT MIN(substr(created_at,1,10)), MAX(substr(created_at,1,10)) FROM incidents").fetchone()
    opts["min_date"] = dates[0]
    opts["max_date"] = dates[1]
    total = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    opts["total"] = total
    conn.close()
    return opts


def query_kpis(where, params):
    conn = get_conn()
    row = conn.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN LOWER(state) = 'resolved' THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN LOWER(state) = 'closed' THEN 1 ELSE 0 END) as closed,
            SUM(CASE WHEN LOWER(state) NOT IN ('resolved', 'closed') THEN 1 ELSE 0 END) as open_tickets,
            SUM(CASE WHEN is_escalated = 1 THEN 1 ELSE 0 END) as escalated
        FROM incidents WHERE {where}
    """, params).fetchone()
    conn.close()
    return {
        "total": row[0] or 0, "resolved": row[1] or 0, "closed": row[2] or 0,
        "open": row[3] or 0, "escalated": row[4] or 0,
    }


def query_hours(where, params):
    conn = get_conn()
    hours_row = conn.execute(f"""
        SELECT COALESCE(SUM(tt.minutes), 0) / 60.0
        FROM time_tracks tt
        WHERE tt.incident_id IN (SELECT id FROM incidents WHERE {where})
    """, params).fetchone()
    conn.close()
    return round(hours_row[0] or 0, 1)


def query_monthly(where, params):
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT substr(created_at, 1, 7) as month,
            COUNT(*) as total,
            SUM(CASE WHEN LOWER(state) = 'resolved' THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN LOWER(state) = 'closed' THEN 1 ELSE 0 END) as closed
        FROM incidents WHERE {where}
        GROUP BY month ORDER BY month
    """, params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["month", "Total", "Resolved", "Closed"])


def query_categories(where, params):
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT category, COUNT(*) as cnt
        FROM incidents WHERE {where} AND category IS NOT NULL AND category != ''
        GROUP BY category ORDER BY cnt DESC
    """, params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["Category", "Count"])


def query_monthly_type(where, params):
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT substr(created_at, 1, 7) as month,
            SUM(CASE WHEN category LIKE 'INC -%' THEN 1 ELSE 0 END) as Incident,
            SUM(CASE WHEN category LIKE 'SVC%' OR category = 'HR Related' OR category = 'Project-Enhancement' THEN 1 ELSE 0 END) as ServiceRequest
        FROM incidents WHERE {where}
        GROUP BY month ORDER BY month
    """, params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["month", "Incident", "Service Request"])


def query_departments(where, params, limit=15):
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT department, COUNT(*) as cnt
        FROM incidents WHERE {where} AND department IS NOT NULL AND department != ''
        GROUP BY department ORDER BY cnt DESC LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["Department", "Count"])


def query_priority_by_dept(where, params, limit=15):
    conn = get_conn()
    # Get top departments first
    top_depts = conn.execute(f"""
        SELECT department FROM incidents WHERE {where} AND department IS NOT NULL AND department != ''
        GROUP BY department ORDER BY COUNT(*) DESC LIMIT ?
    """, params + [limit]).fetchall()
    dept_list = [r[0] for r in top_depts]
    if not dept_list:
        conn.close()
        return pd.DataFrame()
    dept_placeholders = ",".join("?" for _ in dept_list)
    rows = conn.execute(f"""
        SELECT department, priority, COUNT(*) as cnt
        FROM incidents WHERE {where} AND department IN ({dept_placeholders})
        GROUP BY department, priority
    """, params + dept_list).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["Department", "Priority", "Count"])


def query_tickets(where, params, search=None, page=1, per_page=100):
    conn = get_conn()
    extra_where = ""
    extra_params = []
    if search:
        extra_where = " AND (LOWER(name) LIKE ? OR CAST(number AS TEXT) LIKE ? OR LOWER(requester_name) LIKE ?)"
        s = f"%{search.lower()}%"
        extra_params = [s, f"%{search}%", s]

    total = conn.execute(f"SELECT COUNT(*) FROM incidents WHERE {where}{extra_where}", params + extra_params).fetchone()[0]
    offset = (page - 1) * per_page
    rows = conn.execute(f"""
        SELECT number, name, state, priority, category, department, site,
               assignee_name, requester_name, substr(created_at, 1, 19) as created
        FROM incidents WHERE {where}{extra_where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """, params + extra_params + [per_page, offset]).fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=["Ticket #", "Name", "State", "Priority", "Category", "Department", "Site", "Assignee", "Requester", "Created"])
    return df, total


def query_csv(where, params, search=None):
    conn = get_conn()
    extra_where = ""
    extra_params = []
    if search:
        extra_where = " AND (LOWER(name) LIKE ? OR CAST(number AS TEXT) LIKE ? OR LOWER(requester_name) LIKE ?)"
        s = f"%{search.lower()}%"
        extra_params = [s, f"%{search}%", s]
    rows = conn.execute(f"""
        SELECT number, name, state, priority, category, department, site,
               assignee_name, requester_name, substr(created_at, 1, 19) as created
        FROM incidents WHERE {where}{extra_where}
        ORDER BY created_at DESC
    """, params + extra_params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["Ticket #", "Name", "State", "Priority", "Category", "Department", "Site", "Assignee", "Requester", "Created"])


def query_daily(where, params):
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT substr(created_at, 1, 10) as day, COUNT(*) as cnt
        FROM incidents WHERE {where}
        GROUP BY day ORDER BY day
    """, params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["Date", "Count"])


def query_weekly(where, params):
    conn = get_conn()
    # Group by ISO week (Monday-based)
    rows = conn.execute(f"""
        SELECT strftime('%Y-W%W', substr(created_at, 1, 10)) as week,
               MIN(substr(created_at, 1, 10)) as week_start,
               COUNT(*) as cnt
        FROM incidents WHERE {where}
        GROUP BY week ORDER BY week
    """, params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["Week", "WeekStart", "Count"])


def query_backlog_by_state(where, params):
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT state, COUNT(*) as cnt
        FROM incidents WHERE {where} AND LOWER(state) NOT IN ('resolved', 'closed')
        GROUP BY state ORDER BY cnt DESC
    """, params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["State", "Count"])


def query_ticket_type_breakdown(where, params):
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT
            CASE
                WHEN category LIKE 'INC -%' THEN 'Incident'
                WHEN category LIKE 'SVC%' OR category = 'HR Related' OR category = 'Project-Enhancement' THEN 'Service Request'
                WHEN category = 'Internal' THEN 'Internal'
                ELSE 'Other'
            END as ticket_type,
            COUNT(*) as cnt
        FROM incidents WHERE {where}
        GROUP BY ticket_type ORDER BY cnt DESC
    """, params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["Type", "Count"])


def query_priority_breakdown(where, params):
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT priority, COUNT(*) as cnt
        FROM incidents WHERE {where} AND priority IS NOT NULL AND priority != ''
        GROUP BY priority ORDER BY cnt DESC
    """, params).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["Priority", "Count"])


# ── Plotly dark template ──────────────────────────────────────────────────────
dark_layout = dict(
    paper_bgcolor="#222840",
    plot_bgcolor="#222840",
    font=dict(color="#c9d1d9", size=12),
    title_font=dict(color="#fff", size=14),
    margin=dict(l=10, r=10, t=40, b=10),
    xaxis=dict(gridcolor="#2d3352", fixedrange=True),
    yaxis=dict(gridcolor="#2d3352", fixedrange=True, automargin=True),
)

# ── Load filter options (fast) ───────────────────────────────────────────────
opts = get_filter_options()

# ── Header ────────────────────────────────────────────────────────────────────
# Get last sync info
_last_ticket = None
_last_sync = None
try:
    _conn = get_conn()
    _last_ticket = _conn.execute("SELECT substr(created_at, 1, 16) FROM incidents ORDER BY created_at DESC LIMIT 1").fetchone()
    _last_sync = _conn.execute("SELECT substr(synced_at, 1, 16) FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    _conn.close()
except Exception:
    pass
_last_date = _last_ticket[0].replace("T", " ") if _last_ticket else "Unknown"
_last_sync_date = _last_sync[0].replace("T", " ") if _last_sync else "Never"

st.markdown(f"""
<div class="dashboard-header">
    <div>
        <div class="breadcrumb">Dashboards / IT Ticket History</div>
        <h1>Gargash IT Service Desk Reports - Beta</h1>
    </div>
    <div style="text-align:right;">
        <div class="ticket-count">{opts['total']:,} tickets loaded</div>
        <div style="color:#8b949e;font-size:0.7rem;">Latest ticket: {_last_date}</div>
        <div style="color:#8b949e;font-size:0.7rem;">Last sync: {_last_sync_date}</div>
    </div>
</div>
""", unsafe_allow_html=True)

sync_col1, sync_col2 = st.columns([1, 5])
with sync_col1:
    if st.button("Sync Data", type="primary"):
        from sync import sync_recent
        progress_text = st.empty()
        def update_progress(msg):
            progress_text.info(msg)
        result = sync_recent(on_progress=update_progress)
        get_filter_options.clear()
        progress_text.empty()
        st.success(f"Synced! {result['new']} new, {result['updated']} updated. Total: {result['total']:,} (since {result['since']})")
        st.rerun()
with sync_col2:
    st.caption("Syncs new tickets and updates (state changes, reassignments) since the last ticket in the database.")

# ── Filter bar ───────────────────────────────────────────────────────────────
from datetime import datetime as dt, date
min_d = date(2025, 3, 1)  # Default start: March 2025
max_d = dt.strptime(opts["max_date"], "%Y-%m-%d").date()
db_min_d = dt.strptime(opts["min_date"], "%Y-%m-%d").date()

# Ticket type mapping
TICKET_TYPE_MAP = {
    "Incident": [
        "INC - Application", "INC - EndPoints", "INC - IT Security",
        "INC - IT Software Request ", "INC - Infrastructure", "INC - M365 ", "INC - Networks ",
    ],
    "Service Request": [
        "SVC - Endpoint", "SVC - IT Application ", "SVC - IT Procurement ",
        "SVC - Infrastructure Request ", "SVC - Networks ", "SVC- IT Access Request",
        "SVC- IT Security ", "SVC- IT Software Request ", "HR Related",
        "Project-Enhancement",
    ],
    "Internal": ["Internal"],
}

DEFAULT_TYPES = ["Incident", "Service Request"]
DEFAULT_TYPE_CATS = []
for _t in DEFAULT_TYPES:
    DEFAULT_TYPE_CATS.extend(TICKET_TYPE_MAP.get(_t, []))

FILTER_KEYS = ["f_date", "f_dept", "f_site", "f_cat", "f_type", "f_state", "f_pri", "f_assign"]

def reset_filters():
    st.session_state["reset_flag"] = True
    for k in FILTER_KEYS:
        if k == "f_type":
            st.session_state[k] = DEFAULT_TYPES
        elif k != "f_date":
            st.session_state[k] = []
    st.session_state["applied_filters"] = {
        "date_from": str(min_d), "date_to": str(max_d),
        "departments": [], "sites": [], "categories": DEFAULT_TYPE_CATS,
        "states": [], "priorities": [], "assignees": [],
    }

def apply_filters():
    st.session_state["show_loading"] = True
    # Resolve ticket type to categories
    sel_types = list(st.session_state.get("f_type", []))
    type_categories = []
    for t in sel_types:
        type_categories.extend(TICKET_TYPE_MAP.get(t, []))

    # Merge with any directly selected categories
    direct_cats = list(st.session_state.get("f_cat", []))
    all_cats = list(set(type_categories + direct_cats)) if (type_categories or direct_cats) else []

    st.session_state["applied_filters"] = {
        "date_from": str(st.session_state.get("f_date", (min_d, max_d))[0]) if isinstance(st.session_state.get("f_date"), tuple) and len(st.session_state.get("f_date", ())) == 2 else str(min_d),
        "date_to": str(st.session_state.get("f_date", (min_d, max_d))[1]) if isinstance(st.session_state.get("f_date"), tuple) and len(st.session_state.get("f_date", ())) == 2 else str(max_d),
        "departments": list(st.session_state.get("f_dept", [])),
        "sites": list(st.session_state.get("f_site", [])),
        "categories": all_cats,
        "states": list(st.session_state.get("f_state", [])),
        "priorities": list(st.session_state.get("f_pri", [])),
        "assignees": list(st.session_state.get("f_assign", [])),
    }

with st.expander("Filters", expanded=True):
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        if st.session_state.pop("reset_flag", False) or "f_date" not in st.session_state:
            st.session_state["f_date"] = (min_d, max_d)
        date_range = st.date_input("Date Range", min_value=db_min_d, max_value=max_d, key="f_date")
    with fc2:
        if "f_type" not in st.session_state:
            st.session_state["f_type"] = DEFAULT_TYPES
        st.multiselect("Ticket Type", ["Incident", "Service Request", "Internal"], key="f_type")
    with fc3:
        st.multiselect("Department", opts["department"], key="f_dept")
    with fc4:
        st.multiselect("Site", opts["site"], key="f_site")

    fc5, fc6, fc7, fc8 = st.columns(4)
    with fc5:
        st.multiselect("Category", opts["category"], key="f_cat")
    with fc6:
        st.multiselect("State", opts["state"], key="f_state")
    with fc7:
        st.multiselect("Priority", opts["priority"], key="f_pri")
    with fc8:
        st.multiselect("Assignee", opts["assignee_name"], key="f_assign")

    # Buttons row
    btn1, btn2, btn3 = st.columns([1, 1, 6])
    with btn1:
        st.button("Apply Filters", on_click=apply_filters, type="primary")
    with btn2:
        st.button("Reset All", on_click=reset_filters)

# Use applied filters (or defaults on first load)
af = st.session_state.get("applied_filters", {
    "date_from": str(min_d), "date_to": str(max_d),
    "departments": [], "sites": [], "categories": DEFAULT_TYPE_CATS,
    "states": [], "priorities": [], "assignees": [],
})

where, params = build_where(
    af["date_from"], af["date_to"],
    af["departments"], af["sites"], af["categories"],
    af["states"], af["priorities"], af["assignees"],
)

# ── KPIs (instant SQL query) ─────────────────────────────────────────────────
if st.session_state.pop("show_loading", False):
    st.toast("Applying filters...", icon="⏳")
kpis = query_kpis(where, params)
total_hours = query_hours(where, params)

st.markdown(f"""
<div class="kpi-row">
    <div class="kpi-card">
        <div class="label">Total Tickets</div>
        <div class="value">{kpis['total']:,}</div>
        <div class="bar" style="background:linear-gradient(90deg, #58a6ff 100%, #2d3352 0%);"></div>
    </div>
    <div class="kpi-card">
        <div class="label">Resolved</div>
        <div class="value">{kpis['resolved']:,}</div>
        <div class="bar" style="background:linear-gradient(90deg, #3fb950 {kpis['resolved']/max(kpis['total'],1)*100:.0f}%, #2d3352 {kpis['resolved']/max(kpis['total'],1)*100:.0f}%);"></div>
    </div>
    <div class="kpi-card">
        <div class="label">Closed</div>
        <div class="value">{kpis['closed']:,}</div>
        <div class="bar" style="background:linear-gradient(90deg, #a371f7 {kpis['closed']/max(kpis['total'],1)*100:.0f}%, #2d3352 {kpis['closed']/max(kpis['total'],1)*100:.0f}%);"></div>
    </div>
    <div class="kpi-card">
        <div class="label">Open</div>
        <div class="value">{kpis['open']:,}</div>
        <div class="bar" style="background:linear-gradient(90deg, #f0883e {kpis['open']/max(kpis['total'],1)*100:.0f}%, #2d3352 {kpis['open']/max(kpis['total'],1)*100:.0f}%);"></div>
    </div>
    <div class="kpi-card">
        <div class="label">Total Hours Logged</div>
        <div class="value">{total_hours:,.1f}</div>
        <div class="bar" style="background:linear-gradient(90deg, #f778ba 100%, #2d3352 0%);"></div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Ticket Volume Trends ──────────────────────────────────────────────────────
st.markdown('<div class="section-title">Ticket Volume Trends</div>', unsafe_allow_html=True)

trend_tab1, trend_tab2, trend_tab3 = st.tabs(["Monthly", "Weekly", "Daily"])

with trend_tab1:
    monthly_type = query_monthly_type(where, params)
    if not monthly_type.empty:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=monthly_type["month"], y=monthly_type["Incident"], name="Incident", marker_color="#ff7b72",
                             text=monthly_type["Incident"], textposition="outside", textfont=dict(color="#c9d1d9", size=9)))
        fig.add_trace(go.Bar(x=monthly_type["month"], y=monthly_type["Service Request"], name="Service Request", marker_color="#58a6ff",
                             text=monthly_type["Service Request"], textposition="outside", textfont=dict(color="#c9d1d9", size=9)))
        fig.update_layout(**dark_layout, title="Incident vs Service Request per Month", barmode="group", height=380,
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#c9d1d9")))
        st.plotly_chart(fig, width="stretch")

with trend_tab2:
    weekly = query_weekly(where, params)
    if not weekly.empty:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=weekly["WeekStart"], y=weekly["Count"], name="Tickets", marker_color="#58a6ff",
                             text=weekly["Count"], textposition="outside", textfont=dict(color="#c9d1d9", size=9)))
        fig.update_layout(**dark_layout, title="Tickets Received per Week", height=350)
        st.plotly_chart(fig, width="stretch")

with trend_tab3:
    daily = query_daily(where, params)
    if not daily.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=daily["Date"], y=daily["Count"], mode="lines",
                                 line=dict(color="#58a6ff", width=1.5), fill="tozeroy",
                                 fillcolor="rgba(88,166,255,0.1)"))
        fig.update_layout(**dark_layout, title="Tickets Received per Day", height=350)
        st.plotly_chart(fig, width="stretch")

# ── Backlog & Categorization ─────────────────────────────────────────────────
st.markdown('<div class="section-title">Open / Pending Backlog</div>', unsafe_allow_html=True)

col_backlog, col_type, col_pri = st.columns(3)

with col_backlog:
    backlog_df = query_backlog_by_state(where, params)
    if not backlog_df.empty:
        state_colors = {
            "Internal-Task": "#8b949e", "Work In Progress": "#58a6ff",
            "Assigned": "#a371f7", "Pending with Customer ": "#ffa657",
            "Pending from Vendor": "#f0883e", "Pending Delivery ": "#f778ba",
            "New": "#3fb950", "Awaiting Input": "#79c0ff",
            "Pending L1 Assignment": "#d2a8ff",
        }
        fig = go.Figure(data=[go.Pie(
            labels=backlog_df["State"], values=backlog_df["Count"],
            hole=0.5, marker=dict(colors=[state_colors.get(s, "#8b949e") for s in backlog_df["State"]]),
            textinfo="value", textfont=dict(color="#fff", size=10),
            textposition="inside",
            outsidetextfont=dict(color="#c9d1d9", size=10),
        )])
        total_open = backlog_df["Count"].sum()
        fig.update_layout(**dark_layout, title="Open Tickets by State", height=400, showlegend=True,
                          legend=dict(font=dict(size=10, color="#c9d1d9"), bgcolor="rgba(0,0,0,0)",
                                      orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5))
        fig.add_annotation(text=f"<b>{total_open:,}</b>", x=0.5, y=0.5, showarrow=False, font=dict(size=20, color="#fff"))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No open tickets.")

with col_type:
    type_df = query_ticket_type_breakdown(where, params)
    if not type_df.empty:
        type_colors = {"Incident": "#ff7b72", "Service Request": "#58a6ff",
                       "Internal": "#8b949e", "Project-Enhancement": "#a371f7", "Other": "#ffa657"}
        fig = go.Figure(data=[go.Pie(
            labels=type_df["Type"], values=type_df["Count"],
            hole=0.5, marker=dict(colors=[type_colors.get(t, "#8b949e") for t in type_df["Type"]]),
            textinfo="label+value", textfont=dict(color="#fff", size=11),
            textposition="outside",
            outsidetextfont=dict(color="#c9d1d9", size=10),
        )])
        fig.update_layout(**dark_layout, title="Incident vs Service Request", height=400, showlegend=True,
                          legend=dict(font=dict(size=10, color="#c9d1d9"), bgcolor="rgba(0,0,0,0)",
                                      orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5))
        fig.add_annotation(text=f"<b>{kpis['total']:,}</b>", x=0.5, y=0.5, showarrow=False, font=dict(size=20, color="#fff"))
        st.plotly_chart(fig, width="stretch")

with col_pri:
    pri_break = query_priority_breakdown(where, params)
    if not pri_break.empty:
        pri_colors_map = {"Critical": "#ff7b72", "High": "#f0883e", "Medium": "#ffa657", "Low": "#3fb950"}
        fig = go.Figure(data=[go.Pie(
            labels=pri_break["Priority"], values=pri_break["Count"],
            hole=0.5, marker=dict(colors=[pri_colors_map.get(p, "#8b949e") for p in pri_break["Priority"]]),
            textinfo="label+value", textfont=dict(color="#fff", size=11),
            textposition="outside",
            outsidetextfont=dict(color="#c9d1d9", size=10),
        )])
        fig.update_layout(**dark_layout, title="Priority Breakdown", height=400, showlegend=True,
                          legend=dict(font=dict(size=10, color="#c9d1d9"), bgcolor="rgba(0,0,0,0)",
                                      orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5))
        fig.add_annotation(text=f"<b>{kpis['total']:,}</b>", x=0.5, y=0.5, showarrow=False, font=dict(size=20, color="#fff"))
        st.plotly_chart(fig, width="stretch")

# ── Category & Department ─────────────────────────────────────────────────────
st.markdown('<div class="section-title">Category & Department Breakdown</div>', unsafe_allow_html=True)

col_cat, col_dept = st.columns(2)

with col_cat:
    cat_df = query_categories(where, params)
    if not cat_df.empty:
        colors = ["#58a6ff", "#3fb950", "#f0883e", "#a371f7", "#f778ba", "#ffa657",
                  "#79c0ff", "#56d364", "#d2a8ff", "#ff7b72"]
        cat_sorted = cat_df.sort_values("Count")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=cat_sorted["Count"].values, y=cat_sorted["Category"].values, orientation="h",
            marker_color=colors[:len(cat_sorted)], text=cat_sorted["Count"].values,
            textposition="outside", textfont=dict(color="#c9d1d9", size=10),
        ))
        fig.update_layout(**dark_layout, title="Tickets by Category", height=max(350, len(cat_sorted) * 28 + 80))
        st.plotly_chart(fig, width="stretch")

with col_dept:
    dept_df = query_departments(where, params)
    if not dept_df.empty:
        dept_sorted = dept_df.sort_values("Count")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=dept_sorted["Count"].values, y=dept_sorted["Department"].values, orientation="h",
            marker_color="#58a6ff", text=dept_sorted["Count"].values, textposition="outside",
            textfont=dict(color="#c9d1d9", size=10),
        ))
        fig.update_layout(**dark_layout, title="Top 15 Departments", height=max(350, len(dept_sorted) * 28 + 80))
        st.plotly_chart(fig, width="stretch")

# ── Priority by Department ────────────────────────────────────────────────────
st.markdown('<div class="section-title">Priority by Department</div>', unsafe_allow_html=True)

pri_df = query_priority_by_dept(where, params)
if not pri_df.empty:
    pri_colors = {"Critical": "#ff7b72", "High": "#f0883e", "Medium": "#ffa657", "Low": "#3fb950"}
    fig = go.Figure()
    for pri in ["Critical", "High", "Medium", "Low"]:
        subset = pri_df[pri_df["Priority"] == pri]
        if not subset.empty:
            fig.add_trace(go.Bar(
                x=subset["Count"].values, y=subset["Department"].values, orientation="h",
                name=pri, marker_color=pri_colors.get(pri, "#8b949e"),
                text=subset["Count"].values, textposition="inside",
                textfont=dict(color="#fff", size=10),
            ))
    fig.update_layout(**dark_layout, title="Priority Distribution by Department", barmode="stack",
                      height=max(350, len(pri_df["Department"].unique()) * 28 + 80))
    st.plotly_chart(fig, width="stretch")

# ── Ticket Table (paginated) ─────────────────────────────────────────────────
st.markdown('<div class="section-title">Ticket Details</div>', unsafe_allow_html=True)

search = st.text_input("Search tickets", placeholder="Search by ticket name, number, or requester...", label_visibility="collapsed")

# Pagination
per_page = 100
if "page" not in st.session_state:
    st.session_state.page = 1

ticket_df, total_rows = query_tickets(where, params, search=search or None, page=st.session_state.page, per_page=per_page)
total_pages = max(1, (total_rows + per_page - 1) // per_page)

# Clamp page
if st.session_state.page > total_pages:
    st.session_state.page = total_pages

col_prev, col_info, col_next = st.columns([1, 3, 1])
with col_prev:
    if st.button("Previous", disabled=st.session_state.page <= 1):
        st.session_state.page -= 1
        st.rerun()
with col_info:
    start_row = (st.session_state.page - 1) * per_page + 1
    end_row = min(st.session_state.page * per_page, total_rows)
    st.markdown(f'<div class="page-info">Showing {start_row:,}-{end_row:,} of {total_rows:,} tickets (Page {st.session_state.page}/{total_pages})</div>', unsafe_allow_html=True)
with col_next:
    if st.button("Next", disabled=st.session_state.page >= total_pages):
        st.session_state.page += 1
        st.rerun()

st.dataframe(ticket_df, width="stretch", height=500)

# CSV export (full filtered dataset)
if st.button("Export CSV"):
    csv_df = query_csv(where, params, search=search or None)
    csv = csv_df.to_csv(index=False)
    st.download_button("Download CSV", csv, file_name="ticket_history_export.csv", mime="text/csv")
