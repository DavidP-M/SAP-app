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
  `refresh=1` reads HANA again at most once every 30 s per report (`REFRESH_MIN_SECONDS`).
- If HANA is down, the last good result is still served, marked with the header `X-Stale: 1`, so the dashboard keeps
  its numbers. HANA calls time out (15 s to connect, 4 min per query) instead of hanging.
- Error messages say what failed in plain words with a log reference; host names, users and SQL stay in the bridge log.

## What it serves

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status":"ok"}`. No key, no SAP data (for monitoring) |
| `GET /api/health` | HANA connection check: server time and schema |
| `GET /api/catalog` | The reports and queries available |
| `GET /api/reports/kpi` | The KPI dashboard query (`sql/kpi_dashboard.sql`) as a tab-delimited file, the same layout as SAP Export to Excel |
| `GET /api/reports/supply-demand` | The supply / demand snapshot (`sql/supply_demand.sql`) |
| `GET /api/reports/<name>?format=json` | The same rows as JSON |
| `GET /api/data/<name>?<param>=…` | A small named query from `reports.py` as JSON (list below) |

All `/api/*` calls need `X-API-Key`. Add `refresh=1` to skip the cache. `X-Refreshed-At` is ISO time with the PC's UTC offset.

### Named queries (`/api/data/<name>`)
The JSON is `{query, params, refreshed_at, row_count, stale, truncated, rows}`. `truncated` is `true` when the query hit
its `max_rows` cap. `/api/catalog` lists each query's params with `type`, `default`, `min`, `max` (for text: the longest
value), `required` and, where set, `choices`. Text values may hold only letters, digits, spaces and `. _ / -`.

| Name | Params | One row per |
|---|---|---|
| `open_sales_orders` | none | open sales order, with its open value |
| `sales_by_month` | `months` (12, 1–60) | month: invoices minus credit notes |
| `stock_by_warehouse` | none | warehouse: on-hand quantity and value |
| `open_purchase_orders` | none | open purchase order, with its open value |
| `so_detail` | `docnum` (required) | line of that sales order (ORDR/RDR1) |
| `po_detail` | `docnum` (required) | line of that purchase order (OPOR/POR1) |
| `item_stock` | `item` (required, ≤ 50) | warehouse holding the item: on hand, committed, on order, available (OITW/OITM/OWHS) |
| `customer_orders` | `cardcode` (required, ≤ 15), `months` (12, 1–60) | sales order of that customer: total, open value, last delivery, days late (ORDR/RDR1/ODLN/DLN1) |
| `order_deliveries` | `docnum` (required, a sales order) | delivery or invoice line made from that order (ODLN/DLN1, OINV/INV1) |
| `item_info` | `item` (required, ≤ 50) | the item: group, unit, lead time, min stock, last purchase price, list price 1, default vendor (OITM/OITB/ITM1/OCRD) |
| `production_orders` | `item` ('' = all, ≤ 50), `status` (`open` or `all`) | production order: planned and completed quantity, due date, status (OWOR) |

The last seven return at most 200 rows. They read ORDR, RDR1, OPOR, POR1, OITM, OITW, OWHS, OITB, ITM1, OCRD, ODLN,
DLN1, OINV, INV1 and OWOR; ITM1 and OWOR are new to the bridge. A read-only user granted SELECT on the whole schema
already has them; one granted single tables needs those added.

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
The dashboard already has the Live SAP code (`functions/api/sap/[[path]].js`, since 2v6.7); the copy in
`integration/spyder-kpi-dashboard/` is the original hand-off, kept for reference. Set the Pages secrets (each one asks
for its value) and redeploy:
```
npx wrangler pages secret put SAP_BRIDGE_URL --project-name spyder-kpi-dashboard           # https://sap-bridge.yourdomain.com
npx wrangler pages secret put SAP_BRIDGE_KEY --project-name spyder-kpi-dashboard           # the API key from .env
npx wrangler pages secret put CF_ACCESS_CLIENT_ID --project-name spyder-kpi-dashboard
npx wrangler pages secret put CF_ACCESS_CLIENT_SECRET --project-name spyder-kpi-dashboard
```
The dashboard then shows live SAP data by default and reads the bridge every 5 minutes while it is open. Live data goes
through the same code as an upload, so the KPIs, notes, Data Check and at-risk history work as before.

## Keeping the SQL in step
`sql/kpi_dashboard.sql` and `sql/supply_demand.sql` are copies of the dashboard repo's `queries/` files. They should
also match the queries saved in Query Manager. After changing a query, update it in all three places. Then restart the
bridge (it reads the files on every run, so a restart is only needed to clear the cache).

## Tests
No SAP connection needed: HANA is mocked and the data is synthetic.
```bash
python -m unittest discover tests                                        # bridge: security, reports, cache, values, queries
node tests/pages_function.test.mjs                                       # the dashboard's /api/sap function
python tests/test_bridge.py --write-fixture tests/fixtures/kpi_from_bridge.txt
DASHBOARD_REPO=../spyder-kpi-dashboard node tests/dashboard_roundtrip.test.js   # bridge output loaded by the dashboard's own code
```
The round-trip test checks that the bridge's file gives the dashboard the same lines, dates and amounts as uploading
the original export.

## Adding more
- **A whole saved query** (file for the dashboard): put the `.sql` in `sql/` and add it to `REPORTS` in
  `reports.py`. Query Manager prompts like `[%0]` are not supported in report files.
- **A small JSON query** (tiles, other apps): add it to `QUERIES` in `reports.py`, with `?` placeholders,
  `params` and a `max_rows` cap.
- **Another app**: give it its own key in `API_KEYS`, so it can be revoked on its own.
