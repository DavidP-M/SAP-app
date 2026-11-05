// Round trip: the bytes the bridge serves for /api/reports/kpi, loaded by the KPI dashboard's own code through
// integration/spyder-kpi-dashboard/load-from-sap.js. Needs a checkout of spy-kareena/spyder-kpi-dashboard:
//   python tests/test_bridge.py --write-fixture tests/fixtures/kpi_from_bridge.txt
//   DASHBOARD_REPO=../spyder-kpi-dashboard node tests/dashboard_roundtrip.test.js
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert');

const repo = path.resolve(process.env.DASHBOARD_REPO || path.join(__dirname, '..', '..', 'spyder-kpi-dashboard'));
const { loadPage, createSuite } = require(path.join(repo, 'tests', 'harness.js'));
const snippet = fs.readFileSync(path.join(__dirname, '..', 'integration', 'spyder-kpi-dashboard', 'load-from-sap.js'), 'utf8');
const kpiBytes = fs.readFileSync(path.join(__dirname, 'fixtures', 'kpi_from_bridge.txt'));
const { test, run } = createSuite();

function pageWithSap(routes) {
  const page = loadPage({ today: '2026-09-24' });
  const requests = [];
  page.ctx.File = class { constructor(parts, name) { this.name = name; this.bytes = new Uint8Array(Buffer.from(parts.join(''), 'utf8')); } };
  page.ctx.fetch = async (url, opts = {}) => {
    requests.push(String(url));
    const hit = Object.entries(routes).find(([p]) => String(url).startsWith(p));
    if (!hit) return { ok: true, status: 204, headers: { get: () => null }, json: async () => ({}), text: async () => '', arrayBuffer: async () => new ArrayBuffer(0) };
    const r = hit[1];
    return { ok: r.status === 200, status: r.status, headers: { get: (h) => (r.headers || {})[h.toLowerCase()] || null },
      text: async () => r.body.toString('utf8'), json: async () => JSON.parse(r.body.toString('utf8')) };
  };
  vm.runInContext(snippet, page.ctx, { filename: 'load-from-sap.js' });
  return { page, requests };
}

const kpiRoute = { status: 200, body: kpiBytes, headers: { 'content-disposition': 'attachment; filename="SAP_live_KPI_2026-09-10.txt"' } };

test('bridge output loads as a KPI export with the right lines, dates and amounts', async () => {
  const { page, requests } = pageWithSap({ '/api/sap/kpi': kpiRoute, '/api/sap/supply-demand': { status: 503, body: Buffer.from('{"error":"not set up"}') } });
  await page.ctx.loadFromSap();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepStrictEqual(page.alerts, []);
  assert.ok(requests.includes('/api/sap/kpi'));
  const lines = page.run('allLoadedRows.length');
  assert.strictEqual(lines, 5, 'all 5 synthetic lines are loaded');
  assert.strictEqual(page.run('[reportAsOfDate.getFullYear(),reportAsOfDate.getMonth()+1,reportAsOfDate.getDate()].join("-")'), '2026-9-10');
  assert.deepStrictEqual(page.run('loadedFiles.map(f=>f.name)'), ['SAP_live_KPI_2026-09-10.txt']);
  const total = page.run('allLoadedRows.reduce((t,r)=>t+r.amountLocal,0)');
  assert.strictEqual(typeof total, 'number');
  assert.ok(total > 0, 'amounts were read as numbers');
});

test('same result as uploading the original sample export', async () => {
  const { page: live } = pageWithSap({ '/api/sap/kpi': kpiRoute });
  await live.ctx.loadFromSap();
  const sample = loadPage({ today: '2026-09-24' });
  await sample.ctx.handleFiles([{ name: 'sample.csv', bytes: new Uint8Array(fs.readFileSync(path.join(__dirname, 'fixtures', 'synthetic_kpi_rows.csv'))) }]);
  await new Promise((r) => setTimeout(r, 20));
  const pick = 'JSON.stringify(allLoadedRows.map(r=>[r.documentNumber,r.sourceType,r.code,r.itemCode,+r.dueDate,+r.lineRescheduleDate||null,r.quantity,r.openQuantity,r.amountLocal,r.status]).sort())';
  const a = live.run(pick), b = sample.run(pick);
  assert.ok(JSON.parse(a).length === 5 && JSON.parse(a).every((row) => row.every((v) => v !== undefined)), a);
  assert.strictEqual(a, b);
});

test('a bridge error shows a message and loads nothing', async () => {
  const { page } = pageWithSap({ '/api/sap/kpi': { status: 502, body: Buffer.from('{"error":"The SAP bridge could not be reached."}') } });
  await page.ctx.loadFromSap();
  assert.strictEqual(page.run('allLoadedRows.length'), 0);
  assert.match(page.els['upload-error'].textContent + page.els['upload-error'].innerHTML, /could not be reached/);
});

run();
