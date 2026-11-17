# Upgrading the bridge PC

For the bridge PC that already runs `SapBridge` (installed from commit `0b0411a`). Takes about 10 minutes; the
dashboard falls back to its last import while the service is stopped (about a minute).

Run everything in an **administrator Command Prompt**.

## 1. Note what is running now
```bat
cd C:\sap-bridge
git log -1 --oneline
copy .env .env.before-upgrade
```
Write down the commit shown (expected `0b0411a`). That is what you roll back to.

## 2. Get the new code
```bat
nssm stop SapBridge
git fetch origin
git checkout contract-and-hardening
```
(Once it is merged, use `git checkout main` and `git pull` instead.)

## 3. Install the pinned packages
```bat
.venv\Scripts\pip install --require-hashes -r requirements.txt
```
Every package is checked against a hash. On Python 3.9 this installs hdbcli 2.28 (the last driver with 3.9 wheels);
on 3.10 and later, 2.30. If pip refuses a hash, stop and roll back: do not install without `--require-hashes`.

## 4. Review `.env`
Your `.env` keeps working as it is. Compare it with `.env.example`:

- **`HANA_VALIDATE_CERT`**: your `.env` says `false` (from the original example), so nothing changes today and the
  bridge logs a warning at start-up. Turn it on only after the HANA certificate is installed (checklist below).
  If the line were missing, the new default is `true`, and the bridge would fail to connect until the certificate is
  installed.
- **`API_KEYS`**: the current key still works (it shows as `key1` in the log). When you rotate keys, write it as
  `dashboard:<new key>`.
- New optional settings (`THREADS`, `REQUEST_WAIT_SECONDS`, `ENABLED_QUERIES`, …): leave them out to use the
  defaults. A wrong value stops the bridge at start-up with a message naming the setting.

## 5. Make `nssm restart` reliable (once)
```bat
nssm set SapBridge AppStopMethodSkip 6
nssm set SapBridge AppStopMethodConsole 3000
```

## 6. Start and check
```bat
nssm start SapBridge
type bridge.log
```
The last lines should list any setting warnings and `SAP bridge on http://127.0.0.1:8085 (16 threads)`. Then, with your key:
```bat
curl -s http://127.0.0.1:8085/health
curl -s -H "X-API-Key: <key>" http://127.0.0.1:8085/api/health
curl -s -H "X-API-Key: <key>" http://127.0.0.1:8085/api/catalog
curl -s -o kpi.txt -D - -H "X-API-Key: <key>" http://127.0.0.1:8085/api/reports/kpi
curl -s -o sd.txt -D - -H "X-API-Key: <key>" http://127.0.0.1:8085/api/reports/supply-demand
for %r in (sales-orders sales-deliveries purchase-orders po-receipts production-kitting mrp-forecast) do curl -s -o %r.txt -D - -H "X-API-Key: <key>" http://127.0.0.1:8085/api/reports/%r
curl -s -i http://127.0.0.1:8085/api/catalog
curl -s -i -H "X-API-Key: <key>" "http://127.0.0.1:8085/api/data/so_detail?docnum=abc"
```
Expected: `{"status":"ok"}`; `"status":"ok"` with schema `SBO_SPYDER_PROD`; the report list; `200` with
`X-Row-Count` and `X-Refreshed-At` for every report; `401` JSON without a key; `400` JSON for the bad number. Open
each `.txt` in Excel and compare it with an export of the same query from Query Manager: same columns, same rows.
`type bridge.log` should now show one `ref=… GET /api/… key=key1 status=200 ms=… cache=miss` line per request and
`ran report kpi in …s`.

Finally open the dashboard → Data → **Refresh from SAP** and check the row counts match before.

## Rollback
```bat
nssm stop SapBridge
git checkout 0b0411a
.venv\Scripts\pip install -r requirements.txt
copy /y .env.before-upgrade .env
nssm start SapBridge
curl -s -H "X-API-Key: <key>" http://127.0.0.1:8085/api/health
```
(Use the commit you wrote down in step 1 if it was not `0b0411a`.) The old `requirements.txt` has no hashes, so the
rollback install uses plain `pip install`. Newer package versions left installed by the upgrade work with the old code.

## After the upgrade: your checklist
These need your passwords, HANA or Cloudflare, so they are yours to do. Order matters where noted.

1. **Real HANA checks** (contract section 9): the curl list in step 6 above, plus through the tunnel from any PC:
   `curl -s -H "CF-Access-Client-Id: <id>" -H "CF-Access-Client-Secret: <secret>" -H "X-API-Key: <key>" https://sap-bridge.spyder-api.com/api/health`
   and the same without the Access headers, which must **not** return SAP data.
2. **Per-table grants** (HANA Studio as SYSTEM): edit `sql/create_readonly_user.sql` (schema `SBO_SPYDER_PROD`) and run
   only its GRANT lines, then check `/api/health` and all eight reports still work, **then** remove the schema-wide grants:
   ```sql
   REVOKE SELECT ON SCHEMA "SBO_SPYDER_PROD" FROM DASHBOARD_RO;
   REVOKE SELECT ON SCHEMA "SBO_SPYDER_TEST_20230420" FROM DASHBOARD_RO;
   SELECT SCHEMA_NAME, OBJECT_NAME, PRIVILEGE FROM EFFECTIVE_PRIVILEGES WHERE USER_NAME = 'DASHBOARD_RO';
   ```
3. **Rotate the HANA password** (the current one was shared in a chat):
   `ALTER USER DASHBOARD_RO PASSWORD "<new strong password>";` then update `HANA_PASSWORD` in `.env` and
   `nssm restart SapBridge`. Confirm the sample password from the old example file was never used for any user.
4. **Rotate the API key**: make one (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), set
   `API_KEYS=dashboard:<new key>`, restart, then update the dashboard's Cloudflare Pages secret `SAP_BRIDGE_KEY` for
   **Production and Preview** and redeploy. Consider rotating the tunnel token and the Access service token too.
5. **HANA certificate**: export HANA's CA certificate (from the HANA server or your HANA admin), import it into the
   *Trusted Root Certification Authorities* store of the Windows **user the service runs as** (or save it as PEM and
   set `HANA_TRUSTSTORE`), set `HANA_VALIDATE_CERT=true` (and `HANA_CERT_HOSTNAME=<name in the certificate>` if
   `HANA_HOST` is an IP), restart, check `/api/health`. If it fails, set it back to `false` and restart.
6. **Cloudflare**: on the tunnel's `sap-bridge` route turn on **Protect with Access**; add a rate-limiting rule on the
   dashboard's `/api/sap/*` path (e.g. 30 requests per minute per IP, block 1 minute).
7. **Cold-run time**: after a `refresh=1`, read `ran report kpi in …s` in `bridge.log`. If it is near or over 75 s the
   dashboard gets the last good result (or a 503) while the run finishes in the background; tell the dashboard owner.
8. **Lookups**: decide which `/api/data` queries stay on and set `ENABLED_QUERIES` (they let any dashboard user look up
   orders, customer history and purchase prices).
9. **Tell the dashboard owner** what is listed under "caller-visible changes" in the report: the new 503 replies, the
   `MonthStart` column the contract lists but the supply / demand SQL does not output, the connect-timeout wording,
   `NaN`/`Infinity` → `null` in JSON, and `X-Stale` when a wait times out. Keep `GZIP_RESPONSES` off until they
   confirm their function handles gzip.
