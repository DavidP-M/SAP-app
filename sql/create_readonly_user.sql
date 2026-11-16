-- The bridge's HANA user: SELECT on exactly the tables the bridge reads, nothing else.
--
-- Run in HANA Studio / HANA Cockpit SQL console as SYSTEM (or a user administrator), connected to the tenant
-- database that holds your SAP B1 company. Before running:
--   1. Replace SBO_SPYDER with your company schema name (all lines).
--   2. Replace the password placeholder with a strong password of your own. The placeholder does not meet HANA's
--      default password rules (it needs upper case and digits), so HANA refuses it if it is left in by mistake.
-- The user already exists? Skip CREATE USER and ALTER USER; run the GRANT lines only.
--
-- The table list is derived from sql/kpi_dashboard.sql, sql/supply_demand.sql and every query in reports.py
-- (tests/test_hardening.py checks it stays complete). Add a GRANT here when a new query reads another table.

CREATE USER DASHBOARD_RO PASSWORD "replace-with-a-strong-password" NO FORCE_FIRST_PASSWORD_CHANGE;

-- Service account: don't let the password expire and break the dashboards
ALTER USER DASHBOARD_RO DISABLE PASSWORD LIFETIME;

-- Read-only, table by table. No INSERT / UPDATE / DELETE - ever.
GRANT SELECT ON "SBO_SPYDER"."CRD1" TO DASHBOARD_RO;   -- business partner addresses (ship-to country)
GRANT SELECT ON "SBO_SPYDER"."DLN1" TO DASHBOARD_RO;   -- delivery lines
GRANT SELECT ON "SBO_SPYDER"."INV1" TO DASHBOARD_RO;   -- invoice lines
GRANT SELECT ON "SBO_SPYDER"."ITM1" TO DASHBOARD_RO;   -- item prices
GRANT SELECT ON "SBO_SPYDER"."OCRD" TO DASHBOARD_RO;   -- business partners
GRANT SELECT ON "SBO_SPYDER"."OCRG" TO DASHBOARD_RO;   -- business partner groups
GRANT SELECT ON "SBO_SPYDER"."ODLN" TO DASHBOARD_RO;   -- deliveries
GRANT SELECT ON "SBO_SPYDER"."OHEM" TO DASHBOARD_RO;   -- employees (document owner)
GRANT SELECT ON "SBO_SPYDER"."OINV" TO DASHBOARD_RO;   -- invoices
GRANT SELECT ON "SBO_SPYDER"."OITB" TO DASHBOARD_RO;   -- item groups
GRANT SELECT ON "SBO_SPYDER"."OITM" TO DASHBOARD_RO;   -- items
GRANT SELECT ON "SBO_SPYDER"."OITW" TO DASHBOARD_RO;   -- item stock per warehouse
GRANT SELECT ON "SBO_SPYDER"."OPDN" TO DASHBOARD_RO;   -- goods receipt POs
GRANT SELECT ON "SBO_SPYDER"."OPOR" TO DASHBOARD_RO;   -- purchase orders
GRANT SELECT ON "SBO_SPYDER"."ORDR" TO DASHBOARD_RO;   -- sales orders
GRANT SELECT ON "SBO_SPYDER"."ORIN" TO DASHBOARD_RO;   -- credit notes
GRANT SELECT ON "SBO_SPYDER"."OSHP" TO DASHBOARD_RO;   -- shipping types (ship method)
GRANT SELECT ON "SBO_SPYDER"."OSLP" TO DASHBOARD_RO;   -- sales employees
GRANT SELECT ON "SBO_SPYDER"."OUDP" TO DASHBOARD_RO;   -- departments
GRANT SELECT ON "SBO_SPYDER"."OWHS" TO DASHBOARD_RO;   -- warehouses
GRANT SELECT ON "SBO_SPYDER"."OWOR" TO DASHBOARD_RO;   -- production orders
GRANT SELECT ON "SBO_SPYDER"."PDN1" TO DASHBOARD_RO;   -- goods receipt PO lines
GRANT SELECT ON "SBO_SPYDER"."POR1" TO DASHBOARD_RO;   -- purchase order lines
GRANT SELECT ON "SBO_SPYDER"."RDR1" TO DASHBOARD_RO;   -- sales order lines
GRANT SELECT ON "SBO_SPYDER"."RIN1" TO DASHBOARD_RO;   -- credit note lines

-- Alternative (wider): SELECT on the whole company schema. Simpler to maintain, but the bridge's user can then read
-- every table, including ones no report needs (payroll, bank details, ...). If you used this before, see the README's
-- upgrade notes for moving to the per-table grants above.
-- GRANT SELECT ON SCHEMA "SBO_SPYDER" TO DASHBOARD_RO;

-- Check it worked (log in as DASHBOARD_RO):
-- SELECT COUNT(*) FROM "SBO_SPYDER"."OITM";
-- See what the user can read:
-- SELECT OBJECT_NAME, PRIVILEGE FROM SYS.EFFECTIVE_PRIVILEGES WHERE USER_NAME = 'DASHBOARD_RO' AND SCHEMA_NAME = 'SBO_SPYDER';
