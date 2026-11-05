/**
 * Live SAP for the Spyder KPI Dashboard: GET /api/sap/kpi and GET /api/sap/supply-demand.
 * Copy to functions/api/sap/[[path]].js in spy-kareena/spyder-kpi-dashboard. The sign-in gate in
 * functions/_middleware.js runs first, so only signed-in users reach it.
 *
 * It fetches the report from the SAP bridge (DavidP-M/SAP-app) through Cloudflare Tunnel and returns it as the
 * tab-delimited file the page already knows how to read. The bridge address and keys are Pages secrets and never
 * reach the browser:
 *   SAP_BRIDGE_URL                 e.g. https://sap-bridge.example.com   (the tunnel hostname)
 *   SAP_BRIDGE_KEY                 one of the bridge's API_KEYS
 *   CF_ACCESS_CLIENT_ID / _SECRET  the Cloudflare Access service token in front of the tunnel (recommended)
 * Set each with: npx wrangler pages secret put <NAME> --project-name spyder-kpi-dashboard
 */
const REPORTS = new Set(['kpi', 'supply-demand']);

export async function onRequest({ request, env, params }) {
  if (request.method !== 'GET') return json(405, { error: 'Use GET.' }, { Allow: 'GET' });
  const name = [].concat(params.path || []).join('/');
  if (!REPORTS.has(name)) return json(404, { error: `Unknown SAP report '${name}'.` });
  if (!env.SAP_BRIDGE_URL || !env.SAP_BRIDGE_KEY) {
    return json(503, { configured: false, error: 'Live SAP is not set up on this site (SAP_BRIDGE_URL, SAP_BRIDGE_KEY).' });
  }

  const url = new URL(`/api/reports/${name}`, env.SAP_BRIDGE_URL);
  if (new URL(request.url).searchParams.get('refresh') === '1') url.searchParams.set('refresh', '1');
  const headers = { 'X-API-Key': env.SAP_BRIDGE_KEY };
  if (env.CF_ACCESS_CLIENT_ID && env.CF_ACCESS_CLIENT_SECRET) {
    headers['CF-Access-Client-Id'] = env.CF_ACCESS_CLIENT_ID;
    headers['CF-Access-Client-Secret'] = env.CF_ACCESS_CLIENT_SECRET;
  }

  let res;
  try {
    res = await fetch(url, { headers, redirect: 'manual' });
  } catch (err) {
    return json(502, { error: 'The SAP bridge could not be reached. Is the bridge PC on and the tunnel running?' });
  }
  const type = res.headers.get('content-type') || '';
  // Cloudflare Access answers a refused service token with a redirect or its sign-in page, not the bridge's JSON.
  if (res.status >= 300 && res.status < 400 || (res.ok && type.includes('text/html'))) {
    return json(502, { error: 'Cloudflare Access refused the request: check CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET.' });
  }
  if (!res.ok) {
    let message = `The SAP bridge answered ${res.status}.`;
    try { message = (await res.json()).error || message; } catch (err) { /* not JSON */ }
    return json(502, { error: message });
  }
  return new Response(res.body, {
    headers: {
      'content-type': 'text/tab-separated-values; charset=utf-8',
      'content-disposition': res.headers.get('content-disposition') || `attachment; filename="SAP_live_${name}.txt"`,
      'x-row-count': res.headers.get('x-row-count') || '',
      'x-refreshed-at': res.headers.get('x-refreshed-at') || '',
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
    },
  });
}

function json(status, body, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...extra },
  });
}
