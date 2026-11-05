-- Run in HANA Studio / HANA Cockpit SQL console as SYSTEM (or a user admin),
-- connected to the tenant database that holds your SAP B1 company.
-- Replace SBO_SPYDER with your company schema name and set a strong password.

CREATE USER DASHBOARD_RO PASSWORD "Change_Me_Strong_Pwd_2026" NO FORCE_FIRST_PASSWORD_CHANGE;

-- Service account: don't let the password expire and break the dashboards
ALTER USER DASHBOARD_RO DISABLE PASSWORD LIFETIME;

-- Read-only on the company schema. No INSERT/UPDATE/DELETE - ever.
GRANT SELECT ON SCHEMA "SBO_SPYDER" TO DASHBOARD_RO;

-- Optional, tighter: grant only the tables the dashboards use instead of the schema
-- GRANT SELECT ON "SBO_SPYDER"."OINV" TO DASHBOARD_RO;
-- GRANT SELECT ON "SBO_SPYDER"."INV1" TO DASHBOARD_RO;
-- ... etc.

-- Check it worked (log in as DASHBOARD_RO):
-- SELECT COUNT(*) FROM "SBO_SPYDER"."OITM";
