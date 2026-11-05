-- Copied from spy-kareena/spyder-kpi-dashboard queries/sap_b1_hana_supply_demand_snapshot_v2.sql (commit 26609e5).
-- Keep this file identical to the query saved in SAP Query Manager and to the dashboard repo copy.
-- SAP B1 HANA: monthly demand vs usable PO supply snapshot for the dashboard.
-- Export to Excel or CSV and upload alongside the SO/PO KPI export.
-- Quantities are in inventory UoM (OpenQty x NumPerMsr) so SO demand and PO supply net correctly.
-- Stock on hand is not included: a "shortage" here is a gap in open PO coverage.
-- RiskSupplyQty counts each PO line's open qty once if it is unconfirmed, has no commit date, is rescheduled,
-- or is past its ship date (the separate risk columns can overlap and should not be added together).
WITH "SO_LINES" AS (
    SELECT
        T1."ItemCode" AS "ItemCode",
        T1."Dscription" AS "ItemDescription",
        T3."ItmsGrpNam" AS "ItemGroupName",
        T1."WhsCode" AS "WarehouseCode",
        CASE T1."WhsCode"
            WHEN '11' THEN 'Spyder - Lacombe'
            WHEN '12' THEN 'Lacombe - Engineering'
            WHEN '13' THEN 'Lacombe - Critical Components'
            WHEN '14' THEN 'Lacombe'
            WHEN '15' THEN 'Lacombe - QC'
            WHEN '17' THEN 'Lacombe - Service'
            WHEN '19' THEN 'Lacombe - Quarantine'
            WHEN '21' THEN 'Spyder - McBride'
            WHEN '22' THEN 'Spyder - Rally'
            WHEN '29' THEN 'McBride - Quarantine'
            WHEN '31' THEN 'Intransit - Lacombe/McBride'
            WHEN '32' THEN 'Spyder - Elkhart'
            WHEN '61' THEN 'Spyder - Tiffin'
            WHEN '62' THEN 'Spyder - USA'
            WHEN '16' THEN 'CM - Logican'
            WHEN '40' THEN 'CM - DSM'
            WHEN '41' THEN 'CM - IMS'
            WHEN '42' THEN 'CM - Price'
            WHEN '43' THEN 'CM - Logican'
            WHEN '44' THEN 'CM - Mountain Tech'
            WHEN '45' THEN 'CM - August Electronics'
            WHEN '495' THEN 'CM - Dorigo'
            WHEN '70' THEN 'CM SFG - DSM'
            WHEN '71' THEN 'CM SFG - IMS'
            WHEN '72' THEN 'CM SFG - Price'
            WHEN '73' THEN 'CM SFG - Logican'
            WHEN '75' THEN 'CM SFG - August Electronics'
            WHEN '78' THEN 'CM SFG - Alta'
            WHEN '79' THEN 'CM SFG - Dorigo'
            WHEN '46' THEN 'Supplier - Active Electronic'
            WHEN '47' THEN 'Supplier - TTI'
            WHEN '48' THEN 'Supplier - Alta Injection'
            WHEN '49' THEN 'Supplier - Firefly'
            WHEN '50' THEN 'Supplier - Other'
            ELSE 'Non-inventory / Unmapped Warehouse'
        END AS "WarehouseName",
        CASE
            WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") IS NOT NULL
                 AND COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") <> '1111-11-11'
            THEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date")
            ELSE T1."ShipDate"
        END AS "PlanningDate",
        CASE
            WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") = '1111-11-11'
            THEN 'SO No Commit Date'
            WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") IS NOT NULL
                 AND COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") <> '1111-11-11'
            THEN 'SO Rescheduled'
            ELSE 'SO Original Date'
        END AS "TimingSource",
        T0."DocNum" AS "DemandDocumentNumber",
        T1."OpenQty" * COALESCE(NULLIF(T1."NumPerMsr", 0), 1) AS "DemandOpenQty",
        CASE
            WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") = '1111-11-11'
            THEN T1."OpenQty" * COALESCE(NULLIF(T1."NumPerMsr", 0), 1)
            ELSE 0
        END AS "DemandNoCommitQty",
        CASE
            WHEN T1."Quantity" > 0 THEN T1."LineTotal" * (T1."OpenQty" / T1."Quantity")
            ELSE T1."LineTotal"
        END AS "DemandOpenValue",
        0 AS "UsableSupplyQty",
        0 AS "RescheduledSupplyQty",
        0 AS "UnconfirmedSupplyQty",
        0 AS "NoCommitSupplyQty",
        0 AS "LateSupplyQty",
        0 AS "RiskSupplyQty"
    FROM "ORDR" T0
    INNER JOIN "RDR1" T1 ON T0."DocEntry" = T1."DocEntry"
    LEFT JOIN "OITM" T2 ON T1."ItemCode" = T2."ItemCode"
    LEFT JOIN "OITB" T3 ON T2."ItmsGrpCod" = T3."ItmsGrpCod"
    WHERE
        T0."CANCELED" = 'N'
        AND T0."DocStatus" = 'O'
        AND T1."LineStatus" = 'O'
        AND T1."OpenQty" > 0
),
"PO_LINES" AS (
    SELECT
        T1."ItemCode" AS "ItemCode",
        T1."Dscription" AS "ItemDescription",
        T3."ItmsGrpNam" AS "ItemGroupName",
        T1."WhsCode" AS "WarehouseCode",
        CASE T1."WhsCode"
            WHEN '11' THEN 'Spyder - Lacombe'
            WHEN '12' THEN 'Lacombe - Engineering'
            WHEN '13' THEN 'Lacombe - Critical Components'
            WHEN '14' THEN 'Lacombe'
            WHEN '15' THEN 'Lacombe - QC'
            WHEN '17' THEN 'Lacombe - Service'
            WHEN '19' THEN 'Lacombe - Quarantine'
            WHEN '21' THEN 'Spyder - McBride'
            WHEN '22' THEN 'Spyder - Rally'
            WHEN '29' THEN 'McBride - Quarantine'
            WHEN '31' THEN 'Intransit - Lacombe/McBride'
            WHEN '32' THEN 'Spyder - Elkhart'
            WHEN '61' THEN 'Spyder - Tiffin'
            WHEN '62' THEN 'Spyder - USA'
            WHEN '16' THEN 'CM - Logican'
            WHEN '40' THEN 'CM - DSM'
            WHEN '41' THEN 'CM - IMS'
            WHEN '42' THEN 'CM - Price'
            WHEN '43' THEN 'CM - Logican'
            WHEN '44' THEN 'CM - Mountain Tech'
            WHEN '45' THEN 'CM - August Electronics'
            WHEN '495' THEN 'CM - Dorigo'
            WHEN '70' THEN 'CM SFG - DSM'
            WHEN '71' THEN 'CM SFG - IMS'
            WHEN '72' THEN 'CM SFG - Price'
            WHEN '73' THEN 'CM SFG - Logican'
            WHEN '75' THEN 'CM SFG - August Electronics'
            WHEN '78' THEN 'CM SFG - Alta'
            WHEN '79' THEN 'CM SFG - Dorigo'
            WHEN '46' THEN 'Supplier - Active Electronic'
            WHEN '47' THEN 'Supplier - TTI'
            WHEN '48' THEN 'Supplier - Alta Injection'
            WHEN '49' THEN 'Supplier - Firefly'
            WHEN '50' THEN 'Supplier - Other'
            ELSE 'Non-inventory / Unmapped Warehouse'
        END AS "WarehouseName",
        CASE
            WHEN T1."U_PLS_Reschedule_Date_Line" IS NOT NULL
                 AND T1."U_PLS_Reschedule_Date_Line" <> '1111-11-11'
            THEN T1."U_PLS_Reschedule_Date_Line"
            ELSE T1."ShipDate"
        END AS "PlanningDate",
        CASE
            WHEN T1."U_PLS_Reschedule_Date_Line" = '1111-11-11' THEN 'PO No Commit Date'
            WHEN T0."Confirmed" = 'N' THEN 'PO Not Confirmed'
            WHEN T1."U_PLS_Reschedule_Date_Line" IS NOT NULL
                 AND T1."U_PLS_Reschedule_Date_Line" <> '1111-11-11'
            THEN 'PO Rescheduled'
            ELSE 'PO Original Date'
        END AS "TimingSource",
        NULL AS "DemandDocumentNumber",
        0 AS "DemandOpenQty",
        0 AS "DemandNoCommitQty",
        0 AS "DemandOpenValue",
        CASE
            WHEN T0."Confirmed" = 'Y'
                 AND (T1."U_PLS_Reschedule_Date_Line" IS NULL OR T1."U_PLS_Reschedule_Date_Line" <> '1111-11-11')
            THEN T1."OpenQty" * COALESCE(NULLIF(T1."NumPerMsr", 0), 1)
            ELSE 0
        END AS "UsableSupplyQty",
        CASE
            WHEN T1."U_PLS_Reschedule_Date_Line" IS NOT NULL
                 AND T1."U_PLS_Reschedule_Date_Line" <> '1111-11-11'
            THEN T1."OpenQty" * COALESCE(NULLIF(T1."NumPerMsr", 0), 1)
            ELSE 0
        END AS "RescheduledSupplyQty",
        CASE WHEN T0."Confirmed" = 'N' THEN T1."OpenQty" * COALESCE(NULLIF(T1."NumPerMsr", 0), 1) ELSE 0 END AS "UnconfirmedSupplyQty",
        CASE WHEN T1."U_PLS_Reschedule_Date_Line" = '1111-11-11' THEN T1."OpenQty" * COALESCE(NULLIF(T1."NumPerMsr", 0), 1) ELSE 0 END AS "NoCommitSupplyQty",
        CASE
            WHEN T1."OpenQty" > 0
                 AND (T1."U_PLS_Reschedule_Date_Line" IS NULL OR T1."U_PLS_Reschedule_Date_Line" <> '1111-11-11')
                 AND CURRENT_DATE > CASE
                     WHEN T1."U_PLS_Reschedule_Date_Line" IS NOT NULL
                          AND T1."U_PLS_Reschedule_Date_Line" <> '1111-11-11'
                     THEN T1."U_PLS_Reschedule_Date_Line"
                     ELSE T1."ShipDate"
                 END
            THEN T1."OpenQty" * COALESCE(NULLIF(T1."NumPerMsr", 0), 1)
            ELSE 0
        END AS "LateSupplyQty",
        CASE
            WHEN T0."Confirmed" = 'N'
                 OR T1."U_PLS_Reschedule_Date_Line" IS NOT NULL
                 OR CURRENT_DATE > T1."ShipDate"
            THEN T1."OpenQty" * COALESCE(NULLIF(T1."NumPerMsr", 0), 1)
            ELSE 0
        END AS "RiskSupplyQty"
    FROM "OPOR" T0
    INNER JOIN "POR1" T1 ON T0."DocEntry" = T1."DocEntry"
    LEFT JOIN "OITM" T2 ON T1."ItemCode" = T2."ItemCode"
    LEFT JOIN "OITB" T3 ON T2."ItmsGrpCod" = T3."ItmsGrpCod"
    WHERE
        T0."CANCELED" = 'N'
        AND T0."DocStatus" = 'O'
        AND T1."LineStatus" = 'O'
        AND T1."OpenQty" > 0
),
"MONTHLY" AS (
    SELECT * FROM "SO_LINES"
    UNION ALL
    SELECT * FROM "PO_LINES"
),
"SUMMARY" AS (
    SELECT
        CURRENT_DATE AS "ExportDate",
        "ItemCode",
        MAX("ItemDescription") AS "ItemDescription",
        MAX("ItemGroupName") AS "ItemGroupName",
        "WarehouseCode",
        MAX("WarehouseName") AS "WarehouseName",
        ADD_DAYS("PlanningDate", 1 - DAYOFMONTH("PlanningDate")) AS "MonthStart",
        TO_NVARCHAR("PlanningDate", 'YYYY-MM') AS "YearMonth",
        SUM("DemandOpenQty") AS "DemandOpenQty",
        SUM("DemandNoCommitQty") AS "DemandNoCommitQty",
        SUM("DemandOpenValue") AS "DemandOpenValue",
        SUM("UsableSupplyQty") AS "UsableSupplyQty",
        SUM("RescheduledSupplyQty") AS "RescheduledSupplyQty",
        SUM("UnconfirmedSupplyQty") AS "UnconfirmedSupplyQty",
        SUM("NoCommitSupplyQty") AS "NoCommitSupplyQty",
        SUM("LateSupplyQty") AS "LateSupplyQty",
        SUM("RiskSupplyQty") AS "RiskSupplyQty",
        COUNT(DISTINCT "DemandDocumentNumber") AS "DemandOrderCount"
    FROM "MONTHLY"
    GROUP BY
        "ItemCode",
        "WarehouseCode",
        ADD_DAYS("PlanningDate", 1 - DAYOFMONTH("PlanningDate")),
        TO_NVARCHAR("PlanningDate", 'YYYY-MM')
)
SELECT
    "ExportDate",
    "ItemCode",
    "ItemDescription",
    "ItemGroupName",
    "WarehouseCode",
    "WarehouseName",
    "YearMonth",
    "DemandOpenQty",
    "DemandNoCommitQty",
    "DemandOpenValue",
    "UsableSupplyQty",
    "RescheduledSupplyQty",
    "UnconfirmedSupplyQty",
    "NoCommitSupplyQty",
    "LateSupplyQty",
    "RiskSupplyQty",
    "DemandOrderCount",
    "UsableSupplyQty" - "DemandOpenQty" AS "MonthlyNetQty",
    SUM("UsableSupplyQty" - "DemandOpenQty") OVER (
        PARTITION BY "ItemCode", "WarehouseCode"
        ORDER BY "MonthStart"
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS "CumulativeNetQty",
    CASE
        WHEN "DemandOpenQty" = 0 THEN 'Supply Only'
        WHEN "UsableSupplyQty" >= "DemandOpenQty" THEN 'Covered This Month'
        WHEN "UsableSupplyQty" > 0 THEN 'Partial Shortage'
        ELSE 'No Supply'
    END AS "MonthlyCoverageStatus",
    CASE
        WHEN SUM("UsableSupplyQty" - "DemandOpenQty") OVER (
            PARTITION BY "ItemCode", "WarehouseCode"
            ORDER BY "MonthStart"
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) < 0 THEN 'Cumulative Shortage'
        ELSE 'Cumulative Covered'
    END AS "CumulativeCoverageStatus",
    CASE
        WHEN "DemandNoCommitQty" > 0 THEN 'SO Has No Commit Date'
        WHEN "NoCommitSupplyQty" > 0 THEN 'PO Has No Commit Date'
        WHEN "UnconfirmedSupplyQty" > 0 THEN 'PO Not Confirmed'
        WHEN "LateSupplyQty" > 0 THEN 'PO Supply Late'
        WHEN "RescheduledSupplyQty" > 0 THEN 'PO Rescheduled'
        ELSE 'OK'
    END AS "SupplyRiskFlag"
FROM "SUMMARY"
WHERE
    "DemandOpenQty" <> 0
    OR "UsableSupplyQty" <> 0
    OR "UnconfirmedSupplyQty" <> 0
    OR "NoCommitSupplyQty" <> 0
    OR "LateSupplyQty" <> 0
ORDER BY
    "WarehouseName",
    "ItemCode",
    "MonthStart"
