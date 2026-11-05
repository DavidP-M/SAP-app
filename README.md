# SAP B1 Bridge

Read-only HTTP access to **SAP Business One 9.3 on HANA** for Spyder Controls apps. First user: the
[Spyder KPI Dashboard](https://github.com/spy-kareena/spyder-kpi-dashboard), which today loads an Excel export of a
Query Manager query. With the bridge it gets the same data live with one click.

```
Dashboard (Cloudflare Pages)          Cloudflare                     Spyder network
 browser ── /api/sap/kpi ──> Pages Function ──> Access + Tunnel ──> bridge (this repo) ──SELECT──> HANA
          (signed-in users)  (holds the keys)   (service token)     127.0.0.1:8085      read-only user
```

**Why it is safe**
- HANA is never exposed to the internet. Cloudflare Tunnel makes an outbound connection, so no firewall ports open.
- Three locks: Cloudflare Access service token → bridge API key → a HANA user that can only `SELECT`.
- Callers pick a report **by name**; they never send SQL. The bridge refuses any query file that is not a single
  `SELECT` / `WITH`. Writing to SAP stays with the DI API (as in the inventory count app), never SQL.
- Keys live in `.env` on the bridge PC and as Cloudflare Pages secrets, never in GitHub or the browser.
- Results are cached 5 minutes and parallel requests share one HANA run, so a busy dashboard costs HANA little.

## What it serves

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status":"ok"}`. No key, no SAP data (for monitoring) |
| `GET /api/health` | HANA connection check: server time and schema |
| `GET /api/catalog` | The reports and queries available |
| `GET /api/reports/kpi` | The KPI dashboard query (`sql/kpi_dashboard.sql`) as a tab-delimited file, the same layout as SAP Export to Excel |
| `GET /api/reports/supply-demand` | The supply / demand snapshot (`sql/supply_demand.sql`) |
| `GET /api/reports/<name>?format=json` | The same rows as JSON |
| `GET /api/data/<name>` | Small JSON queries in `reports.py` (open SOs, sales by month, stock by warehouse, open POs) |

All `/api/*` calls need `X-API-Key`. Add `refresh=1` to skip the cache.

## Set up (once)

### 1. Read-only HANA user
Run `sql/create_readonly_user.sql` in HANA Studio as SYSTEM on the tenant that holds the company. Replace
`SBO_SPYDER` with your company database name and set a strong password.

### 2. Install the bridge on a PC that can reach HANA
Windows or Linux, Python 3.9+. The B1 server or any always-on PC on the network works.
```bat
git clone https://github.com/DavidP-M/SAP-app.git C:\sap-bridge
cd C:\sap-bridge
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
notepad .env
.venv\Scripts\python app.py
```
In `.env`, fill in the HANA values and an `API_KEYS` value. Make the key with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`. Check it:
```bat
curl -H "X-API-Key: <your key>" http://127.0.0.1:8085/api/health
curl -H "X-API-Key: <your key>" http://127.0.0.1:8085/api/reports/kpi -o test.txt
```
`test.txt` should match a Query Manager export of the KPI query, and the dashboard should load it via Upload.

**Run it as a Windows service** with [NSSM](https://nssm.cc):
`nssm install SapBridge C:\sap-bridge\.venv\Scripts\python.exe C:\sap-bridge\app.py`, then set the startup
directory to `C:\sap-bridge`. Then run `nssm start SapBridge`.

### 3. Cloudflare Tunnel and Access
1. Install `cloudflared` on the same PC: `cloudflared tunnel login`, then `cloudflared tunnel create sap-bridge`.
2. Copy `deploy/cloudflared-config.example.yml` to the cloudflared config and set your hostname, e.g.
   `sap-bridge.yourdomain.com`. Run `cloudflared tunnel route dns sap-bridge sap-bridge.yourdomain.com`, then
   `cloudflared service install`.
3. Cloudflare Zero Trust → **Access → Service Auth → Service Tokens** → create `kpi-dashboard`. Keep its Client ID
   and Secret.
4. Zero Trust → **Access → Applications** → Self-hosted, `sap-bridge.yourdomain.com`. Add a policy with
   **Action: Service Auth** that includes that service token. Nothing else gets through.

### 4. Turn it on in the KPI dashboard
The dashboard needs two small additions. They are kept here in `integration/spyder-kpi-dashboard/` so the dashboard
repo is not touched until you decide:
1. Copy `integration/spyder-kpi-dashboard/functions/api/sap/[[path]].js` to the dashboard's `functions/api/sap/`.
2. Paste `integration/spyder-kpi-dashboard/load-from-sap.js` into `index.html` next to `handleFiles`. Add the
   **Load from SAP** button (the snippet's first comment has the markup) to the upload screen and the export-date
   popover.
3. Set the Pages secrets (each one asks for its value):
   ```
   npx wrangler pages secret put SAP_BRIDGE_URL --project-name spyder-kpi-dashboard           # https://sap-bridge.yourdomain.com
   npx wrangler pages secret put SAP_BRIDGE_KEY --project-name spyder-kpi-dashboard           # the API key from .env
   npx wrangler pages secret put CF_ACCESS_CLIENT_ID --project-name spyder-kpi-dashboard
   npx wrangler pages secret put CF_ACCESS_CLIENT_SECRET --project-name spyder-kpi-dashboard
   ```
**Load from SAP** feeds the live file through the same code as an upload, so the KPIs, notes, Data Check, at-risk
history and the shared report work as before. A Clear To Build workbook or Logican sheet already loaded stays loaded.

## Keeping the SQL in step
`sql/kpi_dashboard.sql` and `sql/supply_demand.sql` are copies of the dashboard repo's `queries/` files. They should
also match the queries saved in Query Manager. After changing a query, update it in all three places. Then restart the
bridge (it reads the files on every run, so a restart is only needed to clear the cache).

## Tests
No SAP connection needed: HANA is mocked and the data is synthetic.
```bash
python -m unittest discover tests                                        # bridge: security, reports, cache, values
node tests/pages_function.test.mjs                                       # the dashboard's /api/sap function
python tests/test_bridge.py --write-fixture tests/fixtures/kpi_from_bridge.txt
DASHBOARD_REPO=../spyder-kpi-dashboard node tests/dashboard_roundtrip.test.js   # bridge output loaded by the dashboard's own code
```
The round-trip test checks that the bridge's file gives the dashboard the same lines, dates and amounts as uploading
the original export.

## Adding more
- **A whole saved query** (file for the dashboard): put the `.sql` in `sql/` and add it to `REPORTS` in
  `reports.py`. Query Manager prompts like `[%0]` are not supported in report files.
- **A small JSON query** (tiles, other apps): add it to `QUERIES` in `reports.py`, with `?` placeholders and
  `params`.
- **Another app**: give it its own key in `API_KEYS`, so it can be revoked on its own.
