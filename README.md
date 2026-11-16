# SAP B1 Bridge

Read-only HTTP access to **SAP Business One 9.3 on HANA** for Spyder Controls apps. First user: the
[Spyder KPI Dashboard](https://github.com/spy-kareena/spyder-kpi-dashboard), which reads its two reports and the Ask AI
lookups from here instead of an Excel export.

```
Dashboard (Cloudflare Pages)          Cloudflare                       Spyder network
 browser ── /api/sap/* ──> Pages Function ──> Access + Tunnel ──> bridge (this repo) ──SELECT──> HANA
          (signed-in users)  (holds the keys)   (service token)       127.0.0.1:8085      read-only user
```

Upgrading a running bridge PC: **[UPGRADE.md](UPGRADE.md)** (steps, rollback, checklist).

## Why it is safe

- HANA is never exposed to the internet. Cloudflare Tunnel makes an outbound connection, so no firewall ports open.
- Four locks: Cloudflare Access (enforced at the tunnel too, `deploy/cloudflared-config.example.yml`) → a service token
  → the bridge's API key → a HANA user that can `SELECT` only the 25 tables the bridge reads
  (`sql/create_readonly_user.sql`).
- Callers pick a report or query **by name**; they never send SQL. Lookup values are bound to `?` placeholders,
  never written into the SQL.
- The report SQL is read and checked **once, at start-up**: a file changed on disk does nothing until the service
  restarts. The check refuses anything that is not a single `SELECT` / `WITH` or that contains a write or DDL keyword
  (`DELETE`, `INSERT … INTO`, `UPDATE`, `FOR UPDATE`, `CALL`, …) outside strings. That check is a **guard against
  mistakes**; the real control is the HANA grant. Writing to SAP stays with the DI API, never SQL.
- Keys live in `.env` on the bridge PC and as Cloudflare Pages secrets, never in GitHub or the browser. Each caller
  gets its own labelled key (`API_KEYS=dashboard:<key>,gateway:<key>`); the access log shows the label, never the key.
- The HANA TLS certificate is checked by default (`HANA_VALIDATE_CERT=true`); the bridge warns loudly at start-up when
  it is not.
- Error messages say what failed in plain words with a log reference; host names, users and SQL stay in the bridge log.

## How it protects HANA (and stays up when HANA does not)

HANA is the working ERP: every run the bridge makes slows down people using SAP. So:

- **Cache:** a report is read at most once per 5 minutes (`ttl`) however many dashboards are open, a lookup once per
  60–120 s. `refresh=1` reads again at most once per `REFRESH_MIN_SECONDS` (30 s) per report.
- **One run per key:** callers asking for the same thing share one HANA run. The run happens in its own thread, so it
  finishes and fills the cache even if every caller gave up waiting.
- **Bounded waiting:** a request waits for HANA at most `REQUEST_WAIT_SECONDS` (75 s, under Cloudflare's ~100 s), then
  gets the last good result or a 503 in plain words. At most `HANA_WAITERS_MAX` requests wait at once (default: web
  server threads − 2), so `/health` and cached answers always have a thread.
- **Last good result:** when HANA fails, the last good result is served with `X-Stale: 1` (reports) or
  `"stale": true` (JSON), with the time it was read. The dashboard shows "SAP not reachable · showing data from 10:42".
- **Back-off:** after a failed run, that report or lookup is not tried again for `FAILURE_BACKOFF_SECONDS` (15 s):
  callers get the last good result, or the same error and log reference.
- **Lookups:** kept in a bounded cache (`LOOKUP_CACHE_MAX` 500, oldest unused dropped first); new lookup runs are
  limited to `LOOKUP_MAX_CONCURRENT` (2) at once and `LOOKUP_MAX_PER_MINUTE` (60). Over the limit: last good result
  or a 503. The owner can switch lookups off with `ENABLED_QUERIES`.
- **Size and speed:** reports are read in batches (`FETCH_BATCH_ROWS`) and encoded as they arrive; a report over
  `REPORT_MAX_ROWS` (500,000) counts as a failed run. The encoded file is kept and sent as is, without copying, to
  every caller until the next run; JSON is built once, on the first request for it. `/api/health` is cached 15 s.
- **Timeouts:** 15 s to connect, 4 minutes per query (`HANA_CONNECT_TIMEOUT_MS`, `HANA_QUERY_TIMEOUT_MS`).

Every setting, with its default: `.env.example`. The bridge checks them at start-up and refuses to start, naming the
setting, if one is wrong.

## What it serves

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status":"ok"}`. No key, no SAP data (for monitoring) |
| `GET /api/health` | HANA connection check: server time and schema (cached 15 s) |
| `GET /api/catalog` | The reports and enabled queries |
| `GET /api/reports/kpi` | The KPI query (`sql/kpi_dashboard.sql`, 49 columns) as a tab-delimited file, the layout of SAP Export to Excel |
| `GET /api/reports/supply-demand` | The supply / demand snapshot (`sql/supply_demand.sql`) |
| `GET /api/reports/<name>?format=json` | The same rows as JSON |
| `GET /api/data/<name>?<param>=…` | A named query from `reports.py` as JSON (list below) |

All `/api/*` calls need `X-API-Key`. Add `refresh=1` to skip the cache. Report headers: `X-Row-Count`,
`X-Refreshed-At` (when HANA was read, with the PC's UTC offset), `X-Stale: 1` only when HANA failed. The bridge never
redirects and never answers `/api/*` with HTML (the dashboard reads either as an Access refusal). Errors are JSON
`{"error": "..."}`: 400 / 404 for bad input, 502 when HANA failed, 503 when the bridge will not wait for HANA right
now (busy, still running, or over a lookup limit).

The dashboard's side of all this is specified in its contract; the 49 KPI columns and the supply / demand columns are
checked from the SQL text by `tests/test_contract.py`.

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
| `item_stock` | `item` (required, ≤ 50) | warehouse holding the item: on hand, committed, on order, available |
| `customer_orders` | `cardcode` (required, ≤ 15), `months` (12, 1–60) | sales order of that customer: total, open value, last delivery, days late |
| `order_deliveries` | `docnum` (required, a sales order) | delivery or invoice line made from that order |
| `item_info` | `item` (required, ≤ 50) | the item: group, unit, lead time, min stock, last purchase price, list price 1, default vendor |
| `production_orders` | `item` ('' = all, ≤ 50), `status` (`open` or `all`) | production order: planned and completed quantity, due date, status |

The last seven return at most 200 rows. **They let any signed-in dashboard user look up any order, a customer's
history, purchase prices and default vendors by number.** Whether that is acceptable is the owner's decision:
`ENABLED_QUERIES` lists the ones to offer (empty = all); a query left out disappears from the catalog and answers 404.

## Set up (once)

### 1. Read-only HANA user
Run `sql/create_readonly_user.sql` in HANA Studio as SYSTEM on the tenant that holds the company. Replace
`SBO_SPYDER` with your company schema and the password placeholder with a strong password. It grants SELECT on the
25 tables the bridge reads (the list is derived from the SQL and checked by the tests); a schema-wide grant is there as
a commented-out alternative.

### 2. Install the bridge on a PC that can reach HANA
Windows, Python 3.9 or later (3.14 is fine). An always-on PC on the network.
```bat
git clone https://github.com/DavidP-M/SAP-app.git C:\sap-bridge
cd C:\sap-bridge
python -m venv .venv
.venv\Scripts\pip install --require-hashes -r requirements.txt
copy .env.example .env
notepad .env
.venv\Scripts\python app.py
```
`requirements.txt` pins every package to an exact version and hash (generated from `requirements.in`; on Python 3.9 it
installs hdbcli 2.28, the last driver with 3.9 wheels). In `.env`, fill in the HANA values and `API_KEYS`
(`dashboard:<key>`; make the key with `python -c "import secrets; print(secrets.token_urlsafe(32))"`). Check it:
```bat
curl -H "X-API-Key: <your key>" http://127.0.0.1:8085/api/health
curl -s -D - -o test.txt -H "X-API-Key: <your key>" http://127.0.0.1:8085/api/reports/kpi
```

**Run it as a Windows service** with [NSSM](https://nssm.cc), in an administrator Command Prompt:
```bat
nssm install SapBridge "C:\sap-bridge\.venv\Scripts\python.exe" "C:\sap-bridge\app.py"
nssm set SapBridge AppDirectory C:\sap-bridge
nssm set SapBridge DisplayName "SAP Bridge (KPI dashboard)"
nssm set SapBridge Start SERVICE_AUTO_START
nssm set SapBridge AppStdout C:\sap-bridge\bridge.log
nssm set SapBridge AppStderr C:\sap-bridge\bridge.log
nssm set SapBridge AppRotateFiles 1
nssm set SapBridge AppRotateOnline 1
nssm set SapBridge AppRotateBytes 10485760
nssm set SapBridge AppStopMethodSkip 6
nssm set SapBridge AppStopMethodConsole 3000
nssm start SapBridge
```
**Run the service as a Windows user, not LocalSystem** (services.msc → SAP Bridge → Log On → This account). With
`HANA_ENCRYPT=true`, the HANA driver's Windows crypto provider cannot set up TLS under LocalSystem, which has no user
profile; it fails with `-10709 … RTE:[1000013] Invalid flags specified`. The stop settings make `nssm restart` end
the process within 3 s instead of hanging (Windows error 1053).

### 3. Cloudflare Tunnel and Access
1. Cloudflare Zero Trust → **Networks → Tunnels** → create a tunnel, then on the bridge PC, as administrator:
   `cloudflared.exe service install <token>`.
2. Add a **published application** route: `sap-bridge.<your domain>` → HTTP `localhost:8085`.
3. **Access → Service credentials → Service Tokens** → create `kpi-dashboard`; keep the Client ID and Secret.
4. **Access → Applications** → Self-hosted, `sap-bridge.<your domain>`, policy **Action: Service Auth** including that
   token. Nothing else gets through.
5. Enforce Access at the tunnel too: edit the route → **Additional application settings → Access → Protect with
   Access** (or `originRequest.access` in a config file: `deploy/cloudflared-config.example.yml`).

### 4. The KPI dashboard
The dashboard has the Live SAP function since 2v6.7. Set its Pages secrets `SAP_BRIDGE_URL`, `SAP_BRIDGE_KEY`,
`CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` and redeploy (see the dashboard's `docs/LIVE_SAP.md`).
`integration/spyder-kpi-dashboard/` here is **reference only**: the original hand-off, now outdated. The dashboard
repository owns that code; do not develop it here.

## Logs

One line per `/api` request (logger `sap-bridge.access`), and one per HANA run or failure:
```
INFO ref=3f9c1a2b GET /api/reports/kpi key=dashboard status=200 ms=0.4 bytes=13431002 cache=hit
INFO ran report kpi in 3.1s
ERROR report kpi failed (ref ab12cd34): (-10709, 'Connect failed (connect timeout expired) ...')
```
`cache` is `hit`, `miss` (HANA was read), `stale` (last good result), `busy` (503) or `-`. Query strings are never
logged (lookup values can be personal data); neither are keys. `ran report kpi in …` is the real cold-run time to
compare with Cloudflare's ~100 s request limit. The `ref` in an error message is the `ref` of its log line.

## Keeping the SQL in step
`sql/kpi_dashboard.sql` and `sql/supply_demand.sql` are byte-for-byte copies of the dashboard repo's `queries/` files
(after a two-line header), and should match the queries saved in Query Manager. After changing a query, update it in
all three places, then **restart the service** (`nssm restart SapBridge`): the SQL is read once at start-up. Git keeps
these files byte for byte on every checkout (`.gitattributes`).

## What was checked against the real driver
Without a HANA server, with hdbcli 2.30.27 against a local socket that accepts and never answers: `connectTimeout`
and `communicationTimeout` are **milliseconds** (2000 → failed after 2.00 s, 3000 → 3.00 s; with both set the smaller
wins during the connection), as SAP's SQLDBC property reference says. Driver errors are `hdbcli.dbapi.Error` with
`.errorcode` and `.errortext`: a refused or timed-out connection is `-10709`. Error messages are chosen from that code
(`hana.error_class`), the text only deciding within a code family. The driver knows `sslTrustStore` and
`sslHostNameInCertificate` (used by `HANA_TRUSTSTORE`, `HANA_CERT_HOSTNAME`).

## Tests and measurements
No SAP connection needed: HANA is faked and the data is synthetic.
```bash
python -m unittest discover tests        # contract, hardening, driver, golden responses, the original tests
node tests/pages_function.test.mjs       # the reference Pages Function
python tests/test_bridge.py --write-fixture tests/fixtures/kpi_from_bridge.txt
DASHBOARD_REPO=../spyder-kpi-dashboard node tests/dashboard_roundtrip.test.js   # bridge output, dashboard's own code
python tools/loadtest.py --code . --out results.json   # the hardening targets, with tools/harness.py (Linux)
```
`tests/test_golden.py` proves every response a caller can see is byte for byte what it was before the hardening.
GitHub Actions runs the tests on Windows and Linux (Python 3.9, 3.12, 3.14) plus `ruff`, `bandit` and `pip-audit`.

## Adding more
- **A whole saved query** (file for the dashboard): put the `.sql` in `sql/`, add it to `REPORTS` in `reports.py`,
  add GRANTs for any new table to `sql/create_readonly_user.sql` (the tests say which), restart. Query Manager prompts
  like `[%0]` are not supported in report files.
- **A small JSON query**: add it to `QUERIES` in `reports.py`, with `?` placeholders, `params` and a `max_rows` cap.
- **Another app**: give it its own labelled key in `API_KEYS`, so it can be revoked on its own.
