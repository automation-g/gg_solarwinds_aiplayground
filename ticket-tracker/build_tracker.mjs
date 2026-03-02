import ExcelJS from 'exceljs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '.env') });

const API_TOKEN = process.env.SOLARWINDS_API_TOKEN || '';
const REGION = (process.env.SOLARWINDS_REGION || 'us').toLowerCase();
const PER_PAGE = parseInt(process.env.SOLARWINDS_PER_PAGE || '100');
const MAX_PAGES = parseInt(process.env.SOLARWINDS_MAX_PAGES || '50');
const BASE_URL = REGION === 'eu' ? 'https://apieu.samanage.com' : 'https://api.samanage.com';
const HEADERS = {
  'X-Samanage-Authorization': `Bearer ${API_TOKEN}`,
  'Accept': 'application/vnd.samanage.v2.1+json',
  'Content-Type': 'application/json',
};

const outDir = path.join(__dirname, '..');

async function fetchPaginated(urlPath, params = {}) {
  params.per_page = PER_PAGE;
  const all = [];
  let page = 1;
  while (page <= MAX_PAGES) {
    params.page = page;
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (Array.isArray(v)) v.forEach(item => qs.append(k, item));
      else qs.append(k, String(v));
    }
    const url = `${BASE_URL}${urlPath}?${qs.toString()}`;
    const resp = await fetch(url, { headers: HEADERS });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    const totalPages = parseInt(resp.headers.get('X-Total-Pages') || '1');
    const data = await resp.json();
    if (!Array.isArray(data) || data.length === 0) break;
    all.push(...data);
    if (page >= totalPages) break;
    page++;
  }
  return all;
}

async function fetchIncidents(startDate, endDate) {
  const raw = await fetchPaginated('/incidents.json', {
    sort_by: 'created_at',
    sort_order: 'DESC',
    'created[]': 'Select Date Range',
    created_custom_gte: startDate,
    created_custom_lte: endDate,
  });
  return raw.filter(r => {
    const cat = (r.category?.name || '').trim().toLowerCase();
    return cat !== 'internal';
  });
}

function getDayName(dateStr) {
  const d = new Date(dateStr);
  return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()];
}

function safeGet(obj, ...keys) {
  let cur = obj;
  for (const k of keys) {
    if (cur && typeof cur === 'object') cur = cur[k];
    else return '';
  }
  return cur || '';
}

async function build() {
  const today = new Date();
  const startDate = new Date(today);
  startDate.setDate(today.getDate() - 7);
  const startStr = startDate.toISOString().split('T')[0] + 'T00:00:00Z';
  const endStr = today.toISOString().split('T')[0] + 'T23:59:59Z';
  const todayStr = today.toISOString().split('T')[0];

  console.log(`Fetching incidents from ${startStr} to ${endStr}...`);
  const incidents = await fetchIncidents(startStr, endStr);
  console.log(`Got ${incidents.length} incidents (excl. Internal)`);

  // Group by date
  const byDate = {};
  for (const inc of incidents) {
    const dateStr = (inc.created_at || '').substring(0, 10);
    if (!byDate[dateStr]) byDate[dateStr] = [];
    byDate[dateStr].push(inc);
  }
  const sortedDates = Object.keys(byDate).sort();

  const wb = new ExcelJS.Workbook();
  wb.creator = 'IT Service Desk Tracker';
  wb.created = new Date();

  // ========== SHEET 1: Daily Summary ==========
  const ws1 = wb.addWorksheet('Daily Summary', {
    properties: { tabColor: { argb: '4472C4' } }
  });

  const startLabel = startDate.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
  const endLabel = today.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });

  // Title
  ws1.mergeCells('A1:Q1');
  const titleCell = ws1.getCell('A1');
  titleCell.value = `DAILY IT TICKET TRACKER (Excl. Internal) - ${startLabel} to ${endLabel}`;
  titleCell.font = { bold: true, size: 14, color: { argb: 'FFFFFF' } };
  titleCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: '2F5496' } };
  titleCell.alignment = { horizontal: 'center' };

  ws1.mergeCells('A2:Q2');
  ws1.getCell('A2').value = `Last Updated: ${today.toLocaleDateString('en-GB')}`;
  ws1.getCell('A2').font = { italic: true, size: 10 };

  // Headers row 4
  const headers = [
    'Date', 'Day', 'Tickets Raised', 'High', 'Medium', 'Closed', 'Resolved',
    'Still Open', 'Overdue',
    'Top Problem Area #1', 'Top Problem Area #2', 'Top Problem Area #3',
    'Top Subcategory #1', 'Top Subcategory #2', 'Top Subcategory #3',
    'Escalation Notes', 'Open Breakdown'
  ];
  ws1.addRow([]); // row 3 spacer
  ws1.addRow(headers);
  const hRow = ws1.getRow(4);
  hRow.font = { bold: true, color: { argb: 'FFFFFF' }, size: 10 };
  hRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: '4472C4' } };
  hRow.alignment = { horizontal: 'center', wrapText: true };

  const nowUtc = new Date();
  let totalRaised = 0, totalHigh = 0, totalMedium = 0, totalOpen = 0;

  for (const dateStr of sortedDates) {
    const grp = byDate[dateStr];
    const raised = grp.length;
    const high = grp.filter(i => (i.priority || '').toLowerCase() === 'high').length;
    const medium = grp.filter(i => (i.priority || '').toLowerCase() === 'medium').length;
    const closed = grp.filter(i => (i.state || '').toLowerCase() === 'closed').length;
    const resolved = grp.filter(i => (i.state || '').toLowerCase() === 'resolved').length;
    const stillOpen = grp.filter(i => !['closed', 'resolved'].includes((i.state || '').toLowerCase())).length;
    const overdue = grp.filter(i => {
      if (!i.due_at) return false;
      return new Date(i.due_at) < nowUtc && !['closed', 'resolved'].includes((i.state || '').toLowerCase());
    }).length;

    // Top 3 categories
    const catCounts = {};
    for (const i of grp) {
      const cat = safeGet(i, 'category', 'name').trim();
      if (cat) catCounts[cat] = (catCounts[cat] || 0) + 1;
    }
    const topCats = Object.entries(catCounts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([c, n]) => `${c} (${n})`);

    // Top 3 subcategories
    const subcatCounts = {};
    for (const i of grp) {
      const sub = safeGet(i, 'subcategory', 'name').trim();
      if (sub) subcatCounts[sub] = (subcatCounts[sub] || 0) + 1;
    }
    const topSubcats = Object.entries(subcatCounts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([s, n]) => `${s} (${n})`);

    // Escalation notes
    const escCount = grp.filter(i => i.is_escalated).length;
    const highMed = high + medium;
    let escNotes = '';
    if (escCount) escNotes += `${escCount} escalated`;
    if (highMed) escNotes += (escNotes ? ', ' : '') + `${highMed} High/Medium priority raised`;

    // Open breakdown
    const openGrp = grp.filter(i => !['closed', 'resolved'].includes((i.state || '').toLowerCase()));
    const stateCounts = {};
    for (const i of openGrp) stateCounts[i.state] = (stateCounts[i.state] || 0) + 1;
    const breakdown = Object.entries(stateCounts).map(([s, n]) => {
      const short = s.replace('Work In Progress', 'WIP').replace('Pending with Customer', 'PendCust')
        .replace('Pending from Vendor', 'PendVendor').replace('Awaiting Input', 'AwaitInput')
        .replace('Pending L1 Assignment', 'PendL1');
      return `${short}=${n}`;
    }).join(' ');

    const dayName = getDayName(dateStr);
    const row = [
      dateStr, dayName, raised, high, medium, closed, resolved, stillOpen, overdue,
      topCats[0] || '', topCats[1] || '', topCats[2] || '',
      topSubcats[0] || '', topSubcats[1] || '', topSubcats[2] || '',
      escNotes, breakdown
    ];
    const r = ws1.addRow(row);
    r.alignment = { horizontal: 'center' };

    // Conditional formatting
    if (high > 0) r.getCell(4).font = { bold: true, color: { argb: 'FF0000' } };
    if (medium > 0) r.getCell(5).font = { bold: true, color: { argb: 'FF8C00' } };
    if (stillOpen > 20) {
      r.getCell(8).font = { bold: true, color: { argb: 'FF0000' } };
      r.getCell(8).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFCCCC' } };
    }

    totalRaised += raised;
    totalHigh += high;
    totalMedium += medium;
    totalOpen += stillOpen;
  }

  // Summary section
  const weekdays = sortedDates.filter(d => { const day = new Date(d).getDay(); return day >= 1 && day <= 5; });
  const weekends = sortedDates.filter(d => { const day = new Date(d).getDay(); return day === 0 || day === 6; });
  const avgWeekday = weekdays.length ? (weekdays.reduce((s, d) => s + byDate[d].length, 0) / weekdays.length).toFixed(1) : 0;
  const avgWeekend = weekends.length ? (weekends.reduce((s, d) => s + byDate[d].length, 0) / weekends.length).toFixed(1) : 0;

  ws1.addRow([]);
  const sumTitle = ws1.addRow(['SUMMARY']);
  sumTitle.font = { bold: true, size: 12 };
  sumTitle.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'D9E2F3' } };
  ws1.addRow(['Total Raised (excl Internal)', totalRaised]);
  ws1.addRow(['Total High Priority', totalHigh]);
  ws1.addRow(['Total Medium Priority', totalMedium]);
  ws1.addRow(['Avg Daily (Weekdays)', parseFloat(avgWeekday)]);
  ws1.addRow(['Avg Daily (Weekend)', parseFloat(avgWeekend)]);
  ws1.addRow(['Currently Open', totalOpen]);

  // Column widths
  ws1.getColumn(1).width = 12;
  ws1.getColumn(2).width = 6;
  ws1.getColumn(3).width = 14;
  ws1.getColumn(4).width = 6;
  ws1.getColumn(5).width = 8;
  ws1.getColumn(6).width = 8;
  ws1.getColumn(7).width = 10;
  ws1.getColumn(8).width = 10;
  ws1.getColumn(9).width = 9;
  ws1.getColumn(10).width = 28;
  ws1.getColumn(11).width = 28;
  ws1.getColumn(12).width = 28;
  ws1.getColumn(13).width = 28;
  ws1.getColumn(14).width = 28;
  ws1.getColumn(15).width = 28;
  ws1.getColumn(16).width = 40;
  ws1.getColumn(17).width = 40;

  // ========== SHEET 2: Raw Ticket Data ==========
  const ws2 = wb.addWorksheet('Raw Tickets', {
    properties: { tabColor: { argb: '70AD47' } }
  });

  const rawHeaders = ['Date', 'Day', 'Ticket #', 'Ticket ID', 'Name', 'State', 'Priority', 'Category', 'Subcategory', 'Assignee', 'Requester', 'Created At', 'Updated At', 'Due At'];
  const rawHRow = ws2.addRow(rawHeaders);
  rawHRow.font = { bold: true, color: { argb: 'FFFFFF' }, size: 10 };
  rawHRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: '70AD47' } };

  let totalIncluded = 0;
  for (const dateStr of sortedDates) {
    for (const inc of byDate[dateStr]) {
      ws2.addRow([
        dateStr,
        getDayName(dateStr),
        inc.number,
        inc.id,
        inc.name,
        (inc.state || '').trim(),
        inc.priority,
        safeGet(inc, 'category', 'name').trim(),
        safeGet(inc, 'subcategory', 'name').trim(),
        safeGet(inc, 'assignee', 'name'),
        safeGet(inc, 'requester', 'name'),
        inc.created_at,
        inc.updated_at,
        inc.due_at || ''
      ]);
      totalIncluded++;
    }
  }

  // Auto-filter on raw data
  ws2.autoFilter = { from: 'A1', to: 'N1' };

  // Column widths for raw data
  ws2.getColumn(1).width = 12;
  ws2.getColumn(2).width = 6;
  ws2.getColumn(3).width = 10;
  ws2.getColumn(4).width = 12;
  ws2.getColumn(5).width = 55;
  ws2.getColumn(6).width = 20;
  ws2.getColumn(7).width = 8;
  ws2.getColumn(8).width = 28;
  ws2.getColumn(9).width = 28;
  ws2.getColumn(10).width = 22;
  ws2.getColumn(11).width = 30;
  ws2.getColumn(12).width = 28;
  ws2.getColumn(13).width = 28;
  ws2.getColumn(14).width = 28;

  // Freeze header row
  ws2.views = [{ state: 'frozen', ySplit: 1 }];

  // Save
  const outPath = path.join(outDir, 'Daily_Ticket_Tracker.xlsx');
  await wb.xlsx.writeFile(outPath);
  console.log(`Excel file saved: ${outPath}`);
  console.log(`Raw tickets: ${totalIncluded}`);
}

build().catch(console.error);
