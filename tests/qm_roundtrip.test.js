// The six Query Manager reports as the bridge serves them (tests/fixtures/qm, synthetic rows), loaded by the KPI
// dashboard's own code the way six uploaded exports are: handleFiles with all six files at once.
//   cd tests && python test_qm_reports.py --write-fixtures fixtures/qm
//   DASHBOARD_REPO=../spyder-kpi-dashboard node tests/qm_roundtrip.test.js
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert');

const repo = path.resolve(process.env.DASHBOARD_REPO || path.join(__dirname, '..', '..', 'spyder-kpi-dashboard'));
const { loadPage, createSuite } = require(path.join(repo, 'tests', 'harness.js'));
const { test, run } = createSuite();

const dir = path.join(__dirname, 'fixtures', 'qm');
const KPI = ['sales-orders', 'sales-deliveries', 'purchase-orders', 'po-receipts'];
const OTHER = ['production-kitting', 'mrp-forecast'];
const file = (name) => ({ name: `SAP_live_${name}_2026-09-10.txt`, bytes: new Uint8Array(fs.readFileSync(path.join(dir, `${name}.txt`))) });
const settle = () => new Promise((r) => setTimeout(r, 20));
const ROWS = 'JSON.stringify(allLoadedRows.map(r=>[r.documentNumber,r.sourceType,r.orderType,r.lineNumber,r.itemCode,+r.dueDate,r.quantity,r.deliveredQuantity,r.openQuantity,r.amountLocal,r.status]).sort())';

test('the four KPI reports load together as one report, every line once', async () => {
  const page = loadPage({ today: '2026-09-24' });
  await page.ctx.handleFiles([...KPI, ...OTHER].map(file));
  await settle();
  assert.deepStrictEqual(page.alerts, []);
  assert.strictEqual(page.run('allLoadedRows.length'), 12, '3 synthetic lines from each of the 4 KPI reports');
  assert.deepStrictEqual(page.run('loadedFiles.map(f=>f.name)'), KPI.map((n) => file(n).name));
  assert.strictEqual(page.run('[reportAsOfDate.getFullYear(),reportAsOfDate.getMonth()+1,reportAsOfDate.getDate()].join("-")'), '2026-9-10');
  const kinds = JSON.parse(page.run('JSON.stringify([...new Set(allLoadedRows.map(r=>r.sourceType+" / "+r.orderType))].sort())'));
  assert.deepStrictEqual(kinds, ['Purchase Order / PO Receipt', 'Purchase Order / Purchase Order', 'Sales Order / Sales Delivery', 'Sales Order / Sales Order']);
  const rows = JSON.parse(page.run(ROWS));
  assert.ok(rows.every((r) => typeof r[9] === 'number' && r[9] > 0), 'amounts read as numbers');
  assert.ok(rows.every((r) => typeof r[7] === 'number'), 'DeliveredQuantity read');
});

test('production orders and the MRP forecast are not read by this dashboard version: listed as skipped', async () => {
  const page = loadPage({ today: '2026-09-24' });
  await page.ctx.handleFiles([...KPI, ...OTHER].map(file));
  await settle();
  const issues = page.run('JSON.stringify(dataQualityIssues.filter(i=>i.kind==="file").map(i=>i.message))');
  for (const n of OTHER) assert.match(issues, new RegExp(`${file(n).name}.*not a recognized`));
});

test('same lines as loading each report on its own and adding the next', async () => {
  const together = loadPage({ today: '2026-09-24' });
  await together.ctx.handleFiles(KPI.map(file));
  await settle();
  const oneByOne = loadPage({ today: '2026-09-24' });
  await oneByOne.ctx.handleFiles([file(KPI[0])]);
  for (const n of KPI.slice(1)) {
    oneByOne.ctx.nextFile = file(n);                                   // Add file: the loaded files plus one more
    await oneByOne.run('handleFiles([...loadedFiles, nextFile], {adding: true})');
    await settle();
  }
  assert.strictEqual(oneByOne.run('loadedFiles.length'), 4);
  assert.strictEqual(oneByOne.run(ROWS), together.run(ROWS));
});

run();
