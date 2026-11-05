// The dashboard's /api/sap/* Pages Function, with the bridge mocked.  Run: node tests/pages_function.test.mjs
import assert from 'node:assert';
import { onRequest } from '../integration/spyder-kpi-dashboard/functions/api/sap/[[path]].js';

const env = { SAP_BRIDGE_URL: 'https://sap-bridge.example.com', SAP_BRIDGE_KEY: 'k1', CF_ACCESS_CLIENT_ID: 'id', CF_ACCESS_CLIENT_SECRET: 'sec' };
const call = (path, e = env, method = 'GET') => onRequest({ request: new Request(`https://dash.example.com/api/sap/${path}`, { method }), env: e, params: { path: path.split('?')[0].split('/') } });
let seen;
const bridge = (status, body, headers = {}) => { globalThis.fetch = async (url, opts) => { seen = { url: String(url), headers: opts.headers }; return new Response(body, { status, headers }); }; };
const tests = {
  async 'passes the file through with keys sent only to the bridge'() {
    bridge(200, 'ExportDate\tSourceType\r\n', { 'content-type': 'text/tab-separated-values', 'content-disposition': 'attachment; filename="SAP_live_KPI_2026-11-05.txt"', 'x-row-count': '1' });
    const r = await call('kpi?refresh=1');
    assert.strictEqual(r.status, 200);
    assert.strictEqual(await r.text(), 'ExportDate\tSourceType\r\n');
    assert.match(r.headers.get('content-disposition'), /SAP_live_KPI/);
    assert.strictEqual(seen.url, 'https://sap-bridge.example.com/api/reports/kpi?refresh=1');
    assert.deepStrictEqual(seen.headers, { 'X-API-Key': 'k1', 'CF-Access-Client-Id': 'id', 'CF-Access-Client-Secret': 'sec' });
  },
  async 'unknown report and non-GET refused'() {
    assert.strictEqual((await call('../admin')).status, 404);
    assert.strictEqual((await call('kpi', env, 'POST')).status, 405);
  },
  async 'not configured is a clear 503'() {
    const r = await call('kpi', {});
    assert.strictEqual(r.status, 503);
    assert.match((await r.json()).error, /not set up/);
  },
  async 'bridge errors and Access refusals become readable 502s'() {
    bridge(502, JSON.stringify({ error: "SAP report 'kpi' failed: timeout" }), { 'content-type': 'application/json' });
    assert.match((await (await call('kpi')).json()).error, /timeout/);
    bridge(302, '', { location: 'https://x.cloudflareaccess.com/login' });
    assert.match((await (await call('kpi')).json()).error, /Cloudflare Access/);
    globalThis.fetch = async () => { throw new TypeError('network'); };
    assert.match((await (await call('kpi')).json()).error, /could not be reached/);
  },
};
let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { await fn(); console.log(`ok   ${name}`); } catch (err) { failed++; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${Object.keys(tests).length - failed}/${Object.keys(tests).length} passed`);
process.exitCode = failed ? 1 : 0;
