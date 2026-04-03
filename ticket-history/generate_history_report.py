"""Generate a static HTML report for the ticket history dashboard.

Reads from ticket_history_slim.db, outputs history/index.html with embedded
data + Plotly.js charts + JavaScript filtering. Deployed alongside ticket-tracker
on GitHub Pages.
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "ticket_history_slim.db"
OUT_DIR = Path(__file__).parent / "history"
OUT_DIR.mkdir(exist_ok=True)

# ── Load data from DB ─────────────────────────────────────────────────────────
conn = sqlite3.connect(str(DB_PATH))

print("Loading incidents...")
rows = conn.execute("""
    SELECT i.number, i.name, i.state, i.priority, i.category, i.subcategory,
           i.assignee_name, i.requester_name, i.site, i.department,
           i.is_service_request, i.is_escalated,
           substr(i.created_at, 1, 10) as created_date,
           substr(i.created_at, 1, 7) as month,
           substr(i.updated_at, 1, 10) as updated_date,
           substr(i.resolved_at, 1, 10) as resolved_date,
           COALESCE(i.entity, 'N/A') as entity,
           COALESCE(u.entity, 'N/A') as requester_entity,
           COALESCE(u.department, '') as requester_dept
    FROM incidents i
    LEFT JOIN (SELECT name, entity, department, MIN(id) as id FROM users GROUP BY name) u ON i.requester_name = u.name
    WHERE i.created_at >= '2025-03-01'
    ORDER BY i.created_at DESC
""").fetchall()

cols = ["number", "name", "state", "priority", "category", "subcategory",
        "assignee", "requester", "site", "department",
        "is_svc", "is_escalated", "created_date", "month", "updated_date", "resolved_date", "entity", "requester_entity", "requester_dept"]

incidents = [dict(zip(cols, r)) for r in rows]
print(f"Loaded {len(incidents):,} incidents")

# Load time tracks
print("Loading time tracks...")
tt_rows = conn.execute("""
    SELECT tt.incident_id, tt.minutes
    FROM time_tracks tt
    JOIN incidents i ON tt.incident_id = i.id
    WHERE i.created_at >= '2025-03-01'
""").fetchall()
# Build a dict of incident_id -> total minutes
tt_by_incident = {}
for inc_id, mins in tt_rows:
    tt_by_incident[inc_id] = tt_by_incident.get(inc_id, 0) + mins
print(f"Loaded {len(tt_rows):,} time track entries")

# Also get incident IDs for mapping
id_rows = conn.execute("""
    SELECT id, number FROM incidents WHERE created_at >= '2025-03-01'
""").fetchall()
number_to_id = {r[1]: r[0] for r in id_rows}

# Add minutes to each incident
for inc in incidents:
    inc_id = number_to_id.get(inc["number"], 0)
    inc["minutes"] = tt_by_incident.get(inc_id, 0)

# Entity is now loaded directly from DB

# Get unique filter values
departments = sorted(set(r["department"] for r in incidents if r["department"] and r["department"].strip()))
sites = sorted(set(r["site"] for r in incidents if r["site"] and r["site"].strip()))
categories = sorted(set(r["category"] for r in incidents if r["category"] and r["category"].strip()))
states = sorted(set(r["state"] for r in incidents if r["state"] and r["state"].strip()))
priorities = sorted(set(r["priority"] for r in incidents if r["priority"] and r["priority"].strip()))
assignees = sorted(set(r["assignee"] for r in incidents if r["assignee"] and r["assignee"].strip()))

# Last sync info
last_sync = None
try:
    last_sync = conn.execute("SELECT substr(synced_at, 1, 16) FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
except Exception:
    pass
last_sync_str = last_sync[0].replace("T", " ") if last_sync else "N/A"

conn.close()

gh_pat = os.getenv("GH_PAT", "")
generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

# ── Generate HTML ─────────────────────────────────────────────────────────────
print("Generating HTML report...")

# Serialize data as compact JSON
incidents_json = json.dumps(incidents, separators=(",", ":"))
departments_json = json.dumps(departments)
sites_json = json.dumps(sites)
categories_json = json.dumps(categories)
states_json = json.dumps(states)
priorities_json = json.dumps(priorities)
assignees_json = json.dumps(assignees)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gargash IT Service Desk Reports</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/themes/dark.css">
<script src="https://cdn.jsdelivr.net/npm/flatpickr"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1e2e; color: #c9d1d9; }}

.container {{ max-width: 1400px; margin: 0 auto; padding: 16px; }}

.header {{
    background: #222840; border: 1px solid #2d3352; border-radius: 10px;
    padding: 16px 24px; margin-bottom: 16px;
    display: flex; justify-content: space-between; align-items: center;
}}
.header h1 {{ color: #fff; font-size: 1.3rem; font-weight: 600; }}
.header .breadcrumb {{ color: #8b949e; font-size: 0.75rem; margin-bottom: 4px; }}
.header .meta {{ text-align: right; color: #8b949e; font-size: 0.7rem; }}
.header .ticket-count {{ color: #58a6ff; font-size: 0.85rem; }}

.sync-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }}
.sync-btn {{
    background: #58a6ff; color: #fff; border: none; border-radius: 6px;
    padding: 8px 20px; font-size: 0.85rem; cursor: pointer; font-weight: 600;
}}
.sync-btn:hover {{ background: #79c0ff; }}
.sync-caption {{ color: #8b949e; font-size: 0.8rem; }}
#syncStatus {{ color: #3fb950; font-size: 0.8rem; margin-left: 12px; display: none; }}

.filter-bar {{
    background: #222840; border: 1px solid #2d3352; border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px;
}}
.filter-row {{ display: flex; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }}
.filter-group {{ flex: 1; min-width: 200px; }}
.filter-group label {{ display: block; color: #8b949e; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
.filter-group select, .filter-group input {{
    width: 100%; padding: 6px 10px; background: #1a1e2e; color: #c9d1d9;
    border: 1px solid #2d3352; border-radius: 6px; font-size: 0.85rem;
}}
.filter-group select {{ height: 34px; }}
.filter-group select[multiple] {{ height: 80px; }}
.btn-row {{ display: flex; gap: 8px; margin-top: 10px; }}
.btn-apply {{
    background: #58a6ff; color: #fff; border: none; border-radius: 6px;
    padding: 8px 20px; cursor: pointer; font-size: 0.85rem; font-weight: 600;
}}
.btn-apply:hover {{ background: #79c0ff; }}
.btn-reset {{
    background: #2d3352; color: #c9d1d9; border: 1px solid #2d3352; border-radius: 6px;
    padding: 8px 20px; cursor: pointer; font-size: 0.85rem;
}}
.btn-reset:hover {{ background: #3d4562; }}

.kpi-row {{ display: flex; gap: 12px; margin-bottom: 16px; }}
.kpi-card {{
    background: #222840; border: 1px solid #2d3352; border-radius: 10px;
    padding: 14px 18px; flex: 1; min-width: 0;
}}
.kpi-card .label {{ color: #8b949e; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
.kpi-card .value {{ color: #fff; font-size: 1.6rem; font-weight: 700; }}
.kpi-card .bar {{ height: 3px; border-radius: 2px; margin-top: 6px; }}

.section-title {{ color: #fff; font-size: 1rem; font-weight: 600; margin: 20px 0 10px; display: flex; justify-content: space-between; align-items: center; }}
.dl-btn {{ background: none; border: 1px solid #2d3352; color: #8b949e; border-radius: 4px; padding: 4px 10px; font-size: 0.7rem; cursor: pointer; }}
.dl-btn:hover {{ background: #2d3352; color: #fff; }}

.tabs {{ display: flex; gap: 0; background: #222840; border-radius: 8px; padding: 4px; margin-bottom: 12px; width: fit-content; }}
.tab-btn {{
    font-weight: 700;
    background: transparent; color: #8b949e; border: none; border-radius: 6px;
    padding: 8px 16px; font-size: 0.85rem; cursor: pointer;
}}
.tab-btn.active {{ background: #2d3352; color: #fff; }}

.chart-row {{ display: flex; gap: 16px; margin-bottom: 16px; }}
.chart-half {{ flex: 1; min-width: 0; }}
.chart-third {{ flex: 1; min-width: 0; }}
.chart-full {{ width: 100%; }}
.chart-box {{ background: #222840; border: 1px solid #2d3352; border-radius: 10px; padding: 12px; }}

.table-section {{ background: #222840; border: 1px solid #2d3352; border-radius: 10px; padding: 16px; margin-top: 16px; }}
.search-box {{
    width: 100%; padding: 8px 12px; background: #1a1e2e; color: #c9d1d9;
    border: 1px solid #2d3352; border-radius: 6px; font-size: 0.85rem; margin-bottom: 12px;
}}
table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
th {{ text-align: left; padding: 8px 10px; color: #8b949e; border-bottom: 1px solid #2d3352; font-weight: 600; text-transform: uppercase; font-size: 0.7rem; position: sticky; top: 0; background: #222840; z-index: 1; }}
td {{ padding: 6px 10px; border-bottom: 1px solid #2d3352; color: #c9d1d9; }}
tr:hover {{ background: rgba(88,166,255,0.05); }}
.page-nav {{ display: flex; align-items: center; gap: 12px; margin-top: 10px; }}
.page-btn {{ background: #2d3352; color: #c9d1d9; border: none; border-radius: 6px; padding: 6px 16px; cursor: pointer; font-size: 0.8rem; }}
.page-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
.page-info {{ color: #8b949e; font-size: 0.8rem; }}
.export-btn {{
    background: #238636; color: #fff; border: none; border-radius: 6px;
    padding: 8px 20px; cursor: pointer; font-size: 0.85rem; margin-top: 10px;
}}
.export-btn:hover {{ background: #2ea043; }}

@media (max-width: 768px) {{
    .kpi-row, .chart-row, .filter-row {{ flex-direction: column; }}
    .header {{ flex-direction: column; gap: 8px; }}
}}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <div>
        <div class="breadcrumb">Dashboards / IT Service Desk</div>
        <h1>Gargash IT Service Desk Reports - Beta</h1>
    </div>
    <div class="meta">
        <div class="ticket-count" id="ticketCount">{len(incidents):,} tickets loaded</div>
        <div>Generated: {generated_at}</div>
        <div>Last sync: {last_sync_str}</div>
    </div>
</div>


<div class="filter-bar">
    <div class="filter-row">
        <div class="filter-group">
            <label>Date From</label>
            <input type="text" id="dateFrom" value="01/03/2025" placeholder="DD/MM/YYYY">
        </div>
        <div class="filter-group">
            <label>Date To</label>
            <input type="text" id="dateTo" value="{datetime.now().strftime('%d/%m/%Y')}" placeholder="DD/MM/YYYY">
        </div>
        <div class="filter-group">
            <label>Ticket Type</label>
            <select id="filterType" multiple>
                <option value="Incident" selected>Incident</option>
                <option value="Service Request" selected>Service Request</option>
                <option value="Internal">Internal</option>
                <option value="N/A">N/A</option>
            </select>
        </div>
        <div class="filter-group">
            <label>Department</label>
            <select id="filterDept" multiple>
                {"".join(f'<option value="{d}">{d}</option>' for d in departments)}
            </select>
        </div>
    </div>
    <div class="filter-row">
        <div class="filter-group">
            <label>State</label>
            <select id="filterState" multiple>
                {"".join(f'<option value="{s}">{s}</option>' for s in states)}
            </select>
        </div>
        <div class="filter-group">
            <label>Priority</label>
            <select id="filterPriority" multiple>
                {"".join(f'<option value="{p}">{p}</option>' for p in priorities)}
            </select>
        </div>
        <div class="filter-group">
            <label>Category</label>
            <select id="filterCat" multiple>
                {"".join(f'<option value="{c}">{c}</option>' for c in categories)}
            </select>
        </div>
        <div class="filter-group">
            <label>Assignee</label>
            <select id="filterAssignee" multiple>
                {"".join(f'<option value="{a}">{a}</option>' for a in assignees)}
            </select>
        </div>
    </div>
    <div class="btn-row">
        <button class="btn-apply" onclick="applyFilters()">Apply Filters</button>
        <button class="btn-reset" onclick="resetFilters()">Reset All</button>
    </div>
</div>

<div class="kpi-row">
    <div class="kpi-card"><div class="label">Total Tickets</div><div class="value" id="kpiTotal">-</div><div class="bar" style="background:#58a6ff;"></div></div>
    <div class="kpi-card"><div class="label">Resolved</div><div class="value" id="kpiResolved">-</div><div class="bar" id="barResolved"></div></div>
    <div class="kpi-card"><div class="label">Closed</div><div class="value" id="kpiClosed">-</div><div class="bar" id="barClosed"></div></div>
    <div class="kpi-card"><div class="label">Open</div><div class="value" id="kpiOpen">-</div><div class="bar" id="barOpen"></div></div>
    <div class="kpi-card"><div class="label">Total Hours Logged</div><div class="value" id="kpiHours">-</div><div class="bar" style="background:#56d364;"></div></div>
    <div class="kpi-card"><div class="label">Users Supported</div><div class="value" id="kpiUsers">-</div><div class="bar" style="background:#79c0ff;"></div></div>
</div>

<div class="section-title"><span>Ticket Volume Trends</span><button class="dl-btn" onclick="downloadChart('trendChart','ticket_volume_trends')">Download</button></div>
<div class="tabs">
    <button class="tab-btn active" onclick="showTrend('monthly',this)">Monthly</button>
    <button class="tab-btn" onclick="showTrend('weekly',this)">Weekly</button>
    <button class="tab-btn" onclick="showTrend('daily',this)">Daily</button>
</div>
<div class="chart-box"><div id="trendChart" style="height:350px;"></div></div>

<div class="section-title"><span>Open / Pending Backlog</span><button class="dl-btn" onclick="downloadCharts(['backlogChart','typeChart','priorityChart'],'backlog_charts')">Download</button></div>
<div class="chart-row">
    <div class="chart-third"><div class="chart-box"><div id="backlogChart" style="height:400px;"></div></div></div>
    <div class="chart-third"><div class="chart-box"><div id="typeChart" style="height:400px;"></div></div></div>
    <div class="chart-third"><div class="chart-box"><div id="priorityChart" style="height:400px;"></div></div></div>
</div>

<div class="section-title"><span>Tickets by Business Entity</span><button class="dl-btn" onclick="downloadCharts(['entityChart','entityUsersChart','deptUsersChart'],'business_entity')">Download</button></div>
<div class="chart-row">
    <div class="chart-half"><div class="chart-box"><div id="entityChart" style="height:400px;"></div></div></div>
    <div class="chart-half"><div class="chart-box"><div id="entityUsersChart" style="height:400px;"></div></div></div>
</div>
<div class="chart-box" style="margin-top:16px;max-height:450px;overflow-y:auto;"><div id="deptUsersChart"></div></div>

<div class="section-title"><span>Category & Department Breakdown</span><button class="dl-btn" onclick="downloadCharts(['categoryChart','deptChart'],'category_department')">Download</button></div>
<div class="chart-row">
    <div class="chart-half"><div class="chart-box"><div id="categoryChart" style="height:450px;"></div></div></div>
    <div class="chart-half"><div class="chart-box"><div id="deptChart" style="height:450px;"></div></div></div>
</div>

<div class="section-title">Ticket Details</div>
<div class="table-section">
    <input type="text" class="search-box" id="searchBox" placeholder="Search by ticket name, number, or requester..." oninput="renderTable()">
    <div id="ticketTable" style="max-height:400px;overflow-y:auto;"></div>
    <div class="page-nav">
        <button class="page-btn" id="prevBtn" onclick="prevPage()">Previous</button>
        <span class="page-info" id="pageInfo"></span>
        <button class="page-btn" id="nextBtn" onclick="nextPage()">Next</button>
        <button class="export-btn" onclick="exportCsv()">Export CSV</button>
    </div>
</div>

</div><!-- container -->

<script>
const ALL_DATA = {incidents_json};
const TYPE_MAP = {{
    'Incident': ['INC - Application','INC - EndPoints','INC - IT Security','INC - IT Software Request ','INC - Infrastructure','INC - M365 ','INC - Networks '],
    'Service Request': ['SVC - Endpoint','SVC - IT Application ','SVC - IT Procurement ','SVC - Infrastructure Request ','SVC - Networks ','SVC- IT Access Request','SVC- IT Security ','SVC- IT Software Request ','HR Related','Project-Enhancement'],
    'Internal': ['Internal', 'Alerts'],
    'N/A': ['', null, undefined]
}};
const CHART_BG = '#222840';
const fmt = (n) => n.toLocaleString();
const fmtMonth = (m) => {{ const [y, mo] = m.split('-'); const mns = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']; return mns[parseInt(mo)-1] + ' ' + y; }};
const GRID_COLOR = '#2d3352';

let filtered = [];
let currentPage = 1;
const perPage = 30;
let currentTrend = 'monthly';

function getSelected(id) {{
    return Array.from(document.getElementById(id).selectedOptions).map(o => o.value);
}}

function getTicketType(cat) {{
    if (!cat || cat.trim() === '') return 'N/A';
    if (cat.startsWith('INC ')) return 'Incident';
    if (cat.startsWith('SVC') || cat === 'HR Related' || cat === 'Project-Enhancement') return 'Service Request';
    if (cat === 'Internal' || cat === 'Alerts') return 'Internal';
    return 'N/A';
}}

function applyFilters() {{
    const dateFrom = parseDMY(document.getElementById('dateFrom').value);
    const dateTo = parseDMY(document.getElementById('dateTo').value);
    const types = getSelected('filterType');
    const depts = getSelected('filterDept');
    const states = getSelected('filterState');
    const pris = getSelected('filterPriority');
    const cats = getSelected('filterCat');
    const assigns = getSelected('filterAssignee');

    // Resolve types to allowed ticket types
    const allowedTypes = types.length > 0 ? types : [];

    filtered = ALL_DATA.filter(r => {{
        if (r.created_date < dateFrom || r.created_date > dateTo) return false;
        if (allowedTypes.length > 0 && !allowedTypes.includes(getTicketType(r.category))) return false;
        if (cats.length > 0 && !cats.includes(r.category)) return false;
        if (depts.length > 0 && !depts.includes(r.department)) return false;
        if (states.length > 0 && !states.includes(r.state)) return false;
        if (pris.length > 0 && !pris.includes(r.priority)) return false;
        if (assigns.length > 0 && !assigns.includes(r.assignee)) return false;
        return true;
    }});

    currentPage = 1;
    renderAll();
}}

function resetFilters() {{
    document.getElementById('dateFrom').value = '01/03/2025';
    document.getElementById('dateTo').value = '{datetime.now().strftime("%d/%m/%Y")}';
    ['filterType','filterDept','filterState','filterPriority','filterCat','filterAssignee'].forEach(id => {{
        const sel = document.getElementById(id);
        Array.from(sel.options).forEach(o => {{
            o.selected = (id === 'filterType' && o.value !== 'Internal' && o.value !== 'N/A');
        }});
    }});
    applyFilters();
}}

function renderAll() {{
    renderKPIs();
    renderTrend(currentTrend);
    renderBacklog();
    renderTypeChart();
    renderPriorityChart();
    renderEntityChart();
    renderEntityUsersChart();
    renderDeptUsersChart();
    renderCategoryChart();
    renderDeptChart();
    renderTable();
}}

function renderKPIs() {{
    const total = filtered.length;
    const resolved = filtered.filter(r => r.state && r.state.toLowerCase() === 'resolved').length;
    const closed = filtered.filter(r => r.state && r.state.toLowerCase() === 'closed').length;
    const open = total - resolved - closed;
    const totalMins = filtered.reduce((sum, r) => sum + (r.minutes || 0), 0);
    const totalHours = (totalMins / 60).toFixed(1);

    document.getElementById('kpiTotal').textContent = total.toLocaleString();
    document.getElementById('kpiResolved').textContent = resolved.toLocaleString();
    document.getElementById('kpiClosed').textContent = closed.toLocaleString();
    document.getElementById('kpiOpen').textContent = open.toLocaleString();
    document.getElementById('kpiHours').textContent = Number(totalHours).toLocaleString(undefined, {{minimumFractionDigits: 1}});
    const uniqueUsers = new Set(filtered.filter(r => r.requester).map(r => r.requester)).size;
    document.getElementById('kpiUsers').textContent = uniqueUsers.toLocaleString();
    document.getElementById('ticketCount').textContent = total.toLocaleString() + ' tickets';

    const pct = (v) => Math.round(v / Math.max(total, 1) * 100);
    document.getElementById('barResolved').style.background = `linear-gradient(90deg, #3fb950 ${{pct(resolved)}}%, #2d3352 ${{pct(resolved)}}%)`;
    document.getElementById('barClosed').style.background = `linear-gradient(90deg, #a371f7 ${{pct(closed)}}%, #2d3352 ${{pct(closed)}}%)`;
    document.getElementById('barOpen').style.background = `linear-gradient(90deg, #f0883e ${{pct(open)}}%, #2d3352 ${{pct(open)}}%)`;
}}

function showTrend(type, btn) {{
    currentTrend = type;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderTrend(type);
}}

function renderTrend(type) {{
    if (type === 'monthly') {{
        const months = {{}};
        filtered.forEach(r => {{
            const m = r.month;
            if (!months[m]) months[m] = {{inc: 0, svc: 0}};
            const t = getTicketType(r.category);
            if (t === 'Incident') months[m].inc++;
            else if (t === 'Service Request') months[m].svc++;
        }});
        const keys = Object.keys(months).sort();
        const monthLabels = keys.map(k => fmtMonth(k));
        const mmMax = Math.max(...keys.map(k => Math.max(months[k].inc, months[k].svc)));
        Plotly.react('trendChart', [
            {{x: monthLabels, y: keys.map(k => months[k].inc), name: 'Incident', type: 'bar', marker: {{color: '#ff7b72'}}, text: keys.map(k => '<b>' + fmt(months[k].inc) + '</b>'), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 9}}}},
            {{x: monthLabels, y: keys.map(k => months[k].svc), name: 'Service Request', type: 'bar', marker: {{color: '#58a6ff'}}, text: keys.map(k => '<b>' + fmt(months[k].svc) + '</b>'), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 9}}}},
        ], {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Incident vs Service Request per Month', barmode: 'group', margin: {{t: 40, b: 30, l: 40, r: 10}}, xaxis: {{gridcolor: GRID_COLOR}}, yaxis: {{gridcolor: GRID_COLOR, range: [0, mmMax * 1.15], showticklabels: false}}, legend: {{orientation: 'h', y: 1.1, font: {{color: '#c9d1d9'}}}}}}, {{responsive: true, displayModeBar: false}});
    }} else if (type === 'weekly') {{
        const weeks = {{}};
        filtered.forEach(r => {{
            const d = new Date(r.created_date);
            const day = d.getDay();
            const diff = d.getDate() - day + (day === 0 ? -6 : 1);
            const monday = new Date(d.setDate(diff));
            const key = monday.toISOString().slice(0, 10);
            if (!weeks[key]) weeks[key] = {{inc: 0, svc: 0}};
            const t = getTicketType(r.category);
            if (t === 'Incident') weeks[key].inc++;
            else if (t === 'Service Request') weeks[key].svc++;
        }});
        const keys = Object.keys(weeks).sort();
        const mns = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        let prevYear = '';
        const weekLabels = keys.map(k => {{
            const [y, m, d] = k.split('-');
            const yr = y.slice(2);
            const label = mns[parseInt(m)-1] + ' ' + parseInt(d);
            if (yr !== prevYear) {{ prevYear = yr; return label + ' ' + yr; }}
            return label;
        }});
        const wMax = Math.max(...keys.map(k => Math.max(weeks[k].inc, weeks[k].svc)));
        Plotly.react('trendChart', [
            {{x: weekLabels, y: keys.map(k => weeks[k].inc), name: 'Incident', type: 'bar', marker: {{color: '#ff7b72'}}, text: keys.map(k => '<b>' + fmt(weeks[k].inc) + '</b>'), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 8}}}},
            {{x: weekLabels, y: keys.map(k => weeks[k].svc), name: 'Service Request', type: 'bar', marker: {{color: '#58a6ff'}}, text: keys.map(k => '<b>' + fmt(weeks[k].svc) + '</b>'), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 8}}}},
        ], {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Incident vs Service Request per Week', barmode: 'group', margin: {{t: 50, b: 80, l: 40, r: 10}}, xaxis: {{gridcolor: GRID_COLOR, type: 'category', tickangle: 0, dtick: keys.length > 26 ? 4 : 1}}, yaxis: {{gridcolor: GRID_COLOR, range: [0, wMax * 1.1], showticklabels: false}}, legend: {{orientation: 'h', y: 1.12, font: {{color: '#c9d1d9'}}}}}}, {{responsive: true, displayModeBar: false}});
    }} else {{
        const days = {{}};
        filtered.forEach(r => {{
            const d = r.created_date;
            if (!days[d]) days[d] = {{inc: 0, svc: 0}};
            const t = getTicketType(r.category);
            if (t === 'Incident') days[d].inc++;
            else if (t === 'Service Request') days[d].svc++;
        }});
        const keys = Object.keys(days).sort();
        const ddMax = Math.max(...keys.map(k => Math.max(days[k].inc, days[k].svc)));
        Plotly.react('trendChart', [
            {{x: keys, y: keys.map(k => days[k].inc), name: 'Incident', type: 'bar', marker: {{color: '#ff7b72'}}, text: keys.map(k => '<b>' + fmt(days[k].inc) + '</b>'), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 8}}}},
            {{x: keys, y: keys.map(k => days[k].svc), name: 'Service Request', type: 'bar', marker: {{color: '#58a6ff'}}, text: keys.map(k => '<b>' + fmt(days[k].svc) + '</b>'), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 8}}}},
        ], {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Incident vs Service Request per Day', barmode: 'group', margin: {{t: 40, b: 30, l: 40, r: 10}}, xaxis: {{gridcolor: GRID_COLOR}}, yaxis: {{gridcolor: GRID_COLOR, range: [0, ddMax * 1.15], showticklabels: false}}, legend: {{orientation: 'h', y: 1.1, font: {{color: '#c9d1d9'}}}}}}, {{responsive: true, displayModeBar: false}});
    }}
}}

function renderBacklog() {{
    const total = filtered.length;
    const resolved = filtered.filter(r => r.state && r.state.toLowerCase() === 'resolved').length;
    const closed = filtered.filter(r => r.state && r.state.toLowerCase() === 'closed').length;
    const open = total - resolved - closed;
    const labels = [fmt(open) + ' Open', fmt(closed) + ' Closed', fmt(resolved) + ' Resolved'];
    const values = [open, closed, resolved];
    const colors = ['#f0883e', '#a371f7', '#3fb950'];
    Plotly.react('backlogChart', [{{labels, values, type: 'pie', hole: 0.5, marker: {{colors}}, textinfo: 'label', textposition: 'auto', textfont: {{color: '#fff', size: 11}}, outsidetextfont: {{color: '#c9d1d9', size: 10}}}}],
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Open vs Closed / Resolved', margin: {{t: 40, b: 40, l: 10, r: 10}}, showlegend: true, legend: {{font: {{size: 10, color: '#c9d1d9'}}, orientation: 'v', x: 1, y: 0.5, xanchor: 'left'}}, annotations: [{{text: '<b>' + total.toLocaleString() + '</b>', x: 0.5, y: 0.5, showarrow: false, font: {{size: 18, color: '#fff'}}}}]}}, {{responsive: true, displayModeBar: false}});
}}

function renderTypeChart() {{
    const byType = {{}};
    filtered.forEach(r => {{
        const t = getTicketType(r.category);
        byType[t] = (byType[t] || 0) + 1;
    }});
    const rawLabels = Object.keys(byType).sort((a, b) => byType[b] - byType[a]);
    const values = rawLabels.map(l => byType[l]);
    const labels = rawLabels.map((l, i) => l + '<br>' + fmt(values[i]));
    const colors = {{'Incident':'#ff7b72','Service Request':'#58a6ff','Internal':'#8b949e','Other':'#ffa657'}};
    Plotly.react('typeChart', [{{labels, values, type: 'pie', hole: 0.5, marker: {{colors: rawLabels.map(l => colors[l] || '#8b949e')}}, textinfo: 'label', textposition: 'outside', textfont: {{color: '#c9d1d9', size: 10}}}}],
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Incident vs Service Request', margin: {{t: 40, b: 60, l: 10, r: 10}}, showlegend: true, legend: {{font: {{size: 10, color: '#c9d1d9'}}, orientation: 'h', y: -0.1, x: 0.5, xanchor: 'center'}}, annotations: [{{text: '<b>' + filtered.length.toLocaleString() + '</b>', x: 0.5, y: 0.5, showarrow: false, font: {{size: 20, color: '#fff'}}}}]}}, {{responsive: true, displayModeBar: false}});
}}

function renderPriorityChart() {{
    const byPri = {{}};
    filtered.forEach(r => {{ const p = (r.priority && r.priority.trim()) ? r.priority : 'None'; byPri[p] = (byPri[p] || 0) + 1; }});
    const rawLabels = Object.keys(byPri).sort((a, b) => byPri[b] - byPri[a]);
    const values = rawLabels.map(l => byPri[l]);
    const colors = {{'Critical':'#ff7b72','High':'#f0883e','Medium':'#ffa657','Low':'#3fb950'}};
    const priTotal = values.reduce((a, b) => a + b, 0);
    const labels = rawLabels.map((l, i) => fmt(values[i]) + ' ' + l);
    const priPositions = rawLabels.map(l => ['Critical', 'Medium'].includes(l) ? 'outside' : 'inside');
    const priPull = rawLabels.map(l => ['Critical', 'Medium'].includes(l) ? 0.05 : 0);
    Plotly.react('priorityChart', [{{labels, values, type: 'pie', hole: 0.4, marker: {{colors: rawLabels.map(l => colors[l] || '#8b949e')}}, textinfo: 'label', textposition: priPositions, textfont: {{color: '#fff', size: 11}}, outsidetextfont: {{color: '#c9d1d9', size: 10}}, pull: priPull}}],
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Priority Breakdown', margin: {{t: 40, b: 10, l: 50, r: 10}}, showlegend: true, legend: {{font: {{size: 10, color: '#c9d1d9'}}, orientation: 'v', x: 1, y: 0.5, xanchor: 'left'}}, annotations: [{{text: '<b>' + filtered.length.toLocaleString() + '</b>', x: 0.5, y: 0.5, showarrow: false, font: {{size: 18, color: '#fff'}}}}]}}, {{responsive: true, displayModeBar: false}});
}}

function renderEntityChart() {{
    const byEntity = {{}};
    filtered.forEach(r => {{
        const e = r.entity || 'N/A';
        byEntity[e] = (byEntity[e] || 0) + 1;
    }});
    const sorted = Object.entries(byEntity).sort((a, b) => a[1] - b[1]);
    const eColors = {{'Automotive':'#58a6ff','Group Functions':'#a371f7','Financial Services':'#3fb950','Real Estate':'#f0883e','F&B':'#f778ba','N/A':'#8b949e'}};
    const eMax = Math.max(...sorted.map(s => s[1]));
    Plotly.react('entityChart', [{{x: sorted.map(s => s[1]), y: sorted.map(s => s[0]), type: 'bar', orientation: 'h', marker: {{color: sorted.map(s => eColors[s[0]] || '#79c0ff')}}, text: sorted.map(s => fmt(s[1])), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 11}}, cliponaxis: false}}],
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Tickets by Business Entity', margin: {{t: 40, b: 10, l: 10, r: 60}}, xaxis: {{visible: false, gridcolor: GRID_COLOR, range: [0, eMax * 1.15]}}, yaxis: {{gridcolor: GRID_COLOR, automargin: true}}}}, {{responsive: true, displayModeBar: false}});
}}

function renderEntityUsersChart() {{
    const byEntity = {{}};
    filtered.forEach(r => {{
        const e = r.requester_entity || 'N/A';
        if (!byEntity[e]) byEntity[e] = new Set();
        if (r.requester) byEntity[e].add(r.requester);
    }});
    const sorted = Object.entries(byEntity).map(([k, v]) => [k, v.size]).sort((a, b) => a[1] - b[1]);
    const eColors = {{'Automotive':'#58a6ff','Group Functions':'#a371f7','Financial Services':'#3fb950','Real Estate':'#f0883e','F&B':'#f778ba','N/A':'#8b949e'}};
    const euMax = Math.max(...sorted.map(s => s[1]));
    Plotly.react('entityUsersChart', [{{x: sorted.map(s => s[1]), y: sorted.map(s => s[0]), type: 'bar', orientation: 'h', marker: {{color: sorted.map(s => eColors[s[0]] || '#79c0ff')}}, text: sorted.map(s => '<b>' + fmt(s[1]) + '</b>'), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 11}}, cliponaxis: false}}],
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Users Supported by Business Entity', margin: {{t: 40, b: 10, l: 10, r: 60}}, xaxis: {{visible: false, gridcolor: GRID_COLOR, range: [0, euMax * 1.15]}}, yaxis: {{gridcolor: GRID_COLOR, automargin: true}}}}, {{responsive: true, displayModeBar: false}});
}}

function renderDeptUsersChart() {{
    const byDept = {{}};
    filtered.forEach(r => {{
        const dept = r.requester_dept || 'Unknown';
        if (!byDept[dept]) byDept[dept] = new Set();
        if (r.requester) byDept[dept].add(r.requester);
    }});
    const sorted = Object.entries(byDept).map(([k, v]) => [k, v.size]).sort((a, b) => a[1] - b[1]);
    const duMax = Math.max(...sorted.map(s => s[1]));
    Plotly.react('deptUsersChart', [{{x: sorted.map(s => s[1]), y: sorted.map(s => s[0]), type: 'bar', orientation: 'h', marker: {{color: '#79c0ff'}}, text: sorted.map(s => '<b>' + fmt(s[1]) + '</b>'), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 10}}, cliponaxis: false}}],
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Users Supported by Department', margin: {{t: 40, b: 10, l: 10, r: 60}}, height: Math.max(400, sorted.length * 22 + 60), xaxis: {{visible: false, gridcolor: GRID_COLOR, range: [0, duMax * 1.15]}}, yaxis: {{gridcolor: GRID_COLOR, automargin: true}}}}, {{responsive: true, displayModeBar: false}});
}}

function renderCategoryChart() {{
    const byCat = {{}};
    filtered.forEach(r => {{ if (r.category) byCat[r.category] = (byCat[r.category] || 0) + 1; }});
    const sorted = Object.entries(byCat).sort((a, b) => a[1] - b[1]);
    Plotly.react('categoryChart', [{{x: sorted.map(s => s[1]), y: sorted.map(s => s[0]), type: 'bar', orientation: 'h', marker: {{color: '#58a6ff'}}, text: sorted.map(s => fmt(s[1])), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 10}}, cliponaxis: false}}],
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Tickets by Category', margin: {{t: 40, b: 10, l: 10, r: 60}}, xaxis: {{visible: false, gridcolor: GRID_COLOR}}, yaxis: {{gridcolor: GRID_COLOR, automargin: true}}}}, {{responsive: true, displayModeBar: false}});
}}

function renderDeptChart() {{
    const byDept = {{}};
    filtered.forEach(r => {{ if (r.department) byDept[r.department] = (byDept[r.department] || 0) + 1; }});
    const sorted = Object.entries(byDept).sort((a, b) => b[1] - a[1]).slice(0, 20).reverse();
    Plotly.react('deptChart', [{{x: sorted.map(s => s[1]), y: sorted.map(s => s[0]), type: 'bar', orientation: 'h', marker: {{color: '#58a6ff'}}, text: sorted.map(s => fmt(s[1])), textposition: 'outside', textfont: {{color: '#c9d1d9', size: 10}}, cliponaxis: false}}],
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Top 20 Departments', margin: {{t: 40, b: 10, l: 10, r: 60}}, xaxis: {{visible: false, gridcolor: GRID_COLOR}}, yaxis: {{gridcolor: GRID_COLOR, automargin: true}}}}, {{responsive: true, displayModeBar: false}});
}}

function renderPriDeptChart() {{
    const topDepts = {{}};
    filtered.forEach(r => {{ if (r.department) topDepts[r.department] = (topDepts[r.department] || 0) + 1; }});
    const top15 = Object.entries(topDepts).sort((a, b) => b[1] - a[1]).slice(0, 15).map(d => d[0]);
    const priColors = {{'Critical':'#ff7b72','High':'#f0883e','Medium':'#ffa657','Low':'#3fb950'}};
    const traces = [];
    ['Critical','High','Medium','Low'].forEach(pri => {{
        const byDept = {{}};
        filtered.filter(r => r.priority === pri && top15.includes(r.department)).forEach(r => {{
            byDept[r.department] = (byDept[r.department] || 0) + 1;
        }});
        if (Object.keys(byDept).length > 0) {{
            traces.push({{x: top15.map(d => byDept[d] || 0), y: top15, type: 'bar', orientation: 'h', name: pri, marker: {{color: priColors[pri]}}, text: top15.map(d => byDept[d] || ''), textposition: 'inside', textfont: {{color: '#fff', size: 10}}}});
        }}
    }});
    Plotly.react('priDeptChart', traces,
        {{paper_bgcolor: CHART_BG, plot_bgcolor: CHART_BG, font: {{color: '#c9d1d9'}}, title: 'Priority Distribution by Department', barmode: 'stack', margin: {{t: 40, b: 10, l: 10, r: 10}}, xaxis: {{gridcolor: GRID_COLOR}}, yaxis: {{gridcolor: GRID_COLOR, automargin: true}}, legend: {{orientation: 'h', y: 1.08, font: {{color: '#c9d1d9'}}}}}}, {{responsive: true, displayModeBar: false}});
}}

function renderTable() {{
    const search = document.getElementById('searchBox').value.toLowerCase();
    let rows = filtered;
    if (search) {{
        rows = rows.filter(r =>
            (r.name && r.name.toLowerCase().includes(search)) ||
            (r.number && String(r.number).includes(search)) ||
            (r.requester && r.requester.toLowerCase().includes(search))
        );
    }}
    const totalRows = rows.length;
    const totalPages = Math.max(1, Math.ceil(totalRows / perPage));
    if (currentPage > totalPages) currentPage = totalPages;
    const start = (currentPage - 1) * perPage;
    const pageRows = rows.slice(start, start + perPage);

    let html = '<table><thead><tr><th>Ticket #</th><th>Name</th><th>State</th><th>Priority</th><th>Category</th><th>Department</th><th>Assignee</th><th>Requester</th><th style="white-space:nowrap;">Created</th></tr></thead><tbody>';
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const fmtDate = (d) => {{
        if (!d) return '';
        const parts = d.split('-');
        if (parts.length !== 3) return d;
        return parts[2] + '-' + months[parseInt(parts[1])-1] + '-' + parts[0];
    }};
    pageRows.forEach(r => {{
        html += `<tr><td>${{r.number}}</td><td>${{r.name || ''}}</td><td>${{r.state || ''}}</td><td>${{r.priority || ''}}</td><td>${{r.category || ''}}</td><td>${{r.department || ''}}</td><td>${{r.assignee || ''}}</td><td>${{r.requester || ''}}</td><td style="white-space:nowrap;">${{fmtDate(r.created_date)}}</td></tr>`;
    }});
    html += '</tbody></table>';
    document.getElementById('ticketTable').innerHTML = html;
    document.getElementById('pageInfo').textContent = `Showing ${{start + 1}}-${{Math.min(start + perPage, totalRows)}} of ${{totalRows.toLocaleString()}} (Page ${{currentPage}}/${{totalPages}})`;
    document.getElementById('prevBtn').disabled = currentPage <= 1;
    document.getElementById('nextBtn').disabled = currentPage >= totalPages;
}}

function prevPage() {{ currentPage--; renderTable(); }}
function nextPage() {{ currentPage++; renderTable(); }}

function exportCsv() {{
    const search = document.getElementById('searchBox').value.toLowerCase();
    let rows = filtered;
    if (search) rows = rows.filter(r => (r.name && r.name.toLowerCase().includes(search)) || String(r.number).includes(search) || (r.requester && r.requester.toLowerCase().includes(search)));
    let csv = 'Ticket #,Name,State,Priority,Category,Department,Site,Assignee,Requester,Created\\n';
    rows.forEach(r => {{
        csv += `"${{r.number}}","${{(r.name||'').replace(/"/g,'""')}}","${{r.state||''}}","${{r.priority||''}}","${{r.category||''}}","${{r.department||''}}","${{r.site||''}}","${{r.assignee||''}}","${{r.requester||''}}","${{r.created_date||''}}"\\n`;
    }});
    const blob = new Blob([csv], {{type: 'text/csv'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'ticket_history_export.csv';
    a.click();
}}

const GH_PAT = '{gh_pat}';
const REPO = 'automation-g/gg_solarwinds_aiplayground';
const WORKFLOW = 'deploy.yml';

async function triggerRefresh() {{
    const btn = document.querySelector('.sync-btn');
    const status = document.getElementById('syncStatus');
    if (!GH_PAT) {{
        window.open(`https://github.com/${{REPO}}/actions/workflows/${{WORKFLOW}}`, '_blank');
        return;
    }}
    btn.disabled = true;
    btn.textContent = 'Triggering...';
    status.style.display = 'inline';
    status.textContent = 'Sending request...';
    try {{
        const resp = await fetch(`https://api.github.com/repos/${{REPO}}/actions/workflows/${{WORKFLOW}}/dispatches`, {{
            method: 'POST',
            headers: {{ 'Authorization': `Bearer ${{GH_PAT}}`, 'Accept': 'application/vnd.github.v3+json' }},
            body: JSON.stringify({{ ref: 'main' }})
        }});
        if (resp.status === 204) {{
            status.textContent = 'Build triggered! Page will update in ~3 minutes.';
            btn.textContent = 'Build running...';
            setTimeout(() => location.reload(), 180000);
        }} else {{
            status.textContent = `Error: ${{resp.status}}`;
            status.style.color = '#f87171';
            btn.textContent = 'Refresh Data';
            btn.disabled = false;
        }}
    }} catch (e) {{
        status.textContent = 'Network error';
        status.style.color = '#f87171';
        btn.textContent = 'Refresh Data';
        btn.disabled = false;
    }}
}}

function downloadChart(chartId, filename) {{
    Plotly.downloadImage(chartId, {{format: 'png', width: 1200, height: 500, filename: filename, scale: 2}});
}}

function downloadCharts(chartIds, filename) {{
    chartIds.forEach((id, i) => {{
        setTimeout(() => {{
            Plotly.downloadImage(id, {{format: 'png', width: 800, height: 500, filename: filename + '_' + (i+1), scale: 2}});
        }}, i * 500);
    }});
}}

// Date picker setup
// Date picker setup
function parseDMY(s) {{
    const [d, m, y] = s.split('/');
    return y + '-' + m.padStart(2,'0') + '-' + d.padStart(2,'0');
}}

flatpickr('#dateFrom', {{
    dateFormat: 'd/m/Y',
    defaultDate: '01/03/2025',
    theme: 'dark'
}});
flatpickr('#dateTo', {{
    dateFormat: 'd/m/Y',
    defaultDate: '03/04/2026',
    theme: 'dark'
}});

// Initialize
applyFilters();
</script>
</body>
</html>"""

out_path = OUT_DIR / "index.html"
out_path.write_text(html, encoding="utf-8")
print(f"Generated: {out_path} ({len(html) / 1024 / 1024:.1f} MB)")
