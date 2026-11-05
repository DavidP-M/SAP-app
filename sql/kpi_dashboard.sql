-- Copied from spy-kareena/spyder-kpi-dashboard queries/sap_b1_hana_kpi_current_dashboard_so_po_with_lagging.sql (commit 26609e5).
-- Keep this file identical to the query saved in SAP Query Manager and to the dashboard repo copy.
-- SAP B1 HANA: current SO / delivery / PO lines for the KPI dashboard.
-- Export the result to Excel or CSV, then upload that file in index.html.
-- 1111-11-11 on the reschedule UDF means no commit date.
-- Row kinds: one row per SO/PO line (open qty and open amount) plus one row per delivery / goods receipt
-- (shipped qty and amount). AmountLocal is always local currency (CAD): the line's open amount (OpenSum)
-- on order lines and the delivered line total on delivery/receipt rows.
-- Order lines: ordered in the last 365 days, plus any line that is still open regardless of age.
-- Deliveries/receipts: from December 1 of the year before last, so all of last year is covered for YoY/YTD.
-- InventoryItem (OITM.InvntItem) is N for charge, service, and text lines; the dashboard keeps them out of shipping KPIs.
-- TrackingNumber, ShipMethod, ShipToCountry and ShipDate (added in 2v1) are filled on delivery rows only: the delivery's
-- ODLN.TrackNo, its shipping type (OSHP.TrnspName), the ship-to country (CRD1, AdresType S) and the delivery date.
-- They feed the Shipping page (live FedEx tracking, see docs/SHIPPING.md); older exports without them still load.
SELECT
    CURRENT_DATE AS "ExportDate",
    'Sales Order' AS "SourceType",
    T0."DocEntry" AS "DocEntry",
    T0."DocNum" AS "DocumentNumber",
    T1."LineNum" AS "LineNumber",
    T0."NumAtCard" AS "CustomerPONumber",
    T0."DocDate" AS "DocumentDate",
    CAST(NULL AS DATE) AS "ClosedDate",
    '' AS "CloseDateSource",
    '' AS "DeliveryDocNumber",
    COALESCE(T1."ShipDate", T0."DocDueDate") AS "DueDate",
    T0."DocDueDate" AS "HeaderDueDate",
    COALESCE(T1."ShipDate", T0."DocDueDate") AS "LineDueDate",
    T0."CardCode" AS "CustomerVendorCode",
    T0."CardName" AS "CustomerVendorName",
    C."GroupCode" AS "GroupCode",
    G."GroupName" AS "CustomerVendorGroup",
    COALESCE(E."firstName" || ' ' || E."lastName", S."SlpName", 'Unassigned') AS "DocumentOwner",
    'Sales Order' AS "OrderType",
    COALESCE(D."Name", 'Unassigned') AS "Department",
    COALESCE(T1."Project", T0."Project", '') AS "Project",
    T1."ItemCode" AS "ItemCode",
    T1."Dscription" AS "ItemDescription",
    IG."ItmsGrpNam" AS "ItemGroupName",
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
        ELSE 'Unclassified'
    END AS "Warehouse",
    T1."Quantity" AS "Quantity",
    T1."OpenQty" AS "OpenQuantity",
    T1."OpenSum" AS "AmountLocal",
    CASE WHEN T0."DocCur" <> 'CAD' THEN T1."OpenSumFC" ELSE T1."OpenSum" END AS "AmountForeign",
    T0."DocCur" AS "Currency",
    CASE
        WHEN T0."DocStatus" = 'C' OR T1."LineStatus" = 'C' THEN 'Closed'
        ELSE 'Open'
    END AS "Status",
    DAYS_BETWEEN(COALESCE(T1."ShipDate", T0."DocDueDate"), CURRENT_DATE) AS "DaysPastDue",
    CASE
        WHEN T0."DocStatus" = 'C' OR T1."LineStatus" = 'C' THEN 'Closed'
        WHEN T1."LineStatus" = 'O'
             AND COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") = '1111-11-11' THEN 'No Commit'
        WHEN T1."LineStatus" = 'O'
             AND COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") IS NOT NULL
             AND COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") <> '1111-11-11' THEN 'Rescheduled'
        WHEN T1."LineStatus" = 'O' AND T1."ShipDate" < CURRENT_DATE THEN 'Late'
        ELSE 'Open'
    END AS "KPIStatus",
    'Sales Orders' AS "KPIArea",
    CASE
        WHEN T1."U_PLS_Reschedule_Date_Line" IS NULL THEN NULL
        ELSE T1."U_PLS_Reschedule_Date_Line"
    END AS "LineRescheduleDate",
    CASE
        WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") IS NULL THEN NULL
        ELSE COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date")
    END AS "RescheduleDate",
    CASE
        WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") IS NULL THEN 'No Reschedule'
        WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") = '1111-11-11' THEN 'No Commit Date'
        WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") > T1."ShipDate" THEN 'Rescheduled Later Than Original Delivery'
        WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") < T1."ShipDate" THEN 'Rescheduled Earlier Than Original Delivery'
        ELSE 'Rescheduled'
    END AS "RescheduleFlag",
    CASE
        WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") = '1111-11-11' THEN 'No Commit Date'
        ELSE 'OK'
    END AS "DueDateRiskFlag",
    T0."Confirmed" AS "Confirmed",
    CASE
        WHEN T1."OpenQty" = 0 THEN 'Complete'
        WHEN T1."OpenQty" > 0 AND T1."DelivrdQty" > 0 THEN 'Partial'
        ELSE 'Not Shipped'
    END AS "FillStatus",
    CASE
        WHEN T1."OpenQty" = 0 THEN 'Closed'
        WHEN T1."OpenQty" > 0 AND T1."ShipDate" < CURRENT_DATE THEN 'Past Due'
        WHEN T1."OpenQty" > 0 AND T1."ShipDate" <= ADD_DAYS(CURRENT_DATE, 30) THEN 'Upcoming'
        ELSE 'Pending'
    END AS "OnTimeStatus",
    CASE WHEN T1."Price" = 0 OR T1."Price" IS NULL THEN 'Missing Price' ELSE 'OK' END AS "PriceFlag",
    CASE
        WHEN T1."OpenQty" = 0
             AND COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") IS NULL
        THEN 'Perfect'
        ELSE 'Not Perfect'
    END AS "PerfectOrder",
    CASE WHEN T1."OpenQty" = 0 THEN 'Covered' ELSE 'Uncovered' END AS "CoverageType",
    COALESCE(I."InvntItem", 'N') AS "InventoryItem",
    CAST(NULL AS NVARCHAR(100)) AS "TrackingNumber",
    CAST(NULL AS NVARCHAR(100)) AS "ShipMethod",
    CAST(NULL AS NVARCHAR(3)) AS "ShipToCountry",
    CAST(NULL AS DATE) AS "ShipDate"
FROM ORDR T0
INNER JOIN RDR1 T1 ON T0."DocEntry" = T1."DocEntry"
LEFT JOIN OCRD C ON T0."CardCode" = C."CardCode"
LEFT JOIN OCRG G ON C."GroupCode" = G."GroupCode"
LEFT JOIN OSLP S ON T0."SlpCode" = S."SlpCode"
LEFT JOIN OHEM E ON T0."OwnerCode" = E."empID"
LEFT JOIN OUDP D ON E."dept" = D."Code"
LEFT JOIN OITM I ON T1."ItemCode" = I."ItemCode"
LEFT JOIN OITB IG ON I."ItmsGrpCod" = IG."ItmsGrpCod"
WHERE
    (T0."DocDate" >= ADD_DAYS(CURRENT_DATE, -365)
        OR (T0."DocStatus" = 'O' AND T1."LineStatus" = 'O'))
    AND T0."CANCELED" = 'N'

UNION ALL

SELECT
    CURRENT_DATE AS "ExportDate",
    'Sales Order' AS "SourceType",
    S0."DocEntry" AS "DocEntry",
    S0."DocNum" AS "DocumentNumber",
    S1."LineNum" AS "LineNumber",
    S0."NumAtCard" AS "CustomerPONumber",
    S0."DocDate" AS "DocumentDate",
    D0."DocDate" AS "ClosedDate",
    'Delivery' AS "CloseDateSource",
    TO_NVARCHAR(D0."DocNum") AS "DeliveryDocNumber",
    S1."ShipDate" AS "DueDate",
    S0."DocDueDate" AS "HeaderDueDate",
    S1."ShipDate" AS "LineDueDate",
    D0."CardCode" AS "CustomerVendorCode",
    D0."CardName" AS "CustomerVendorName",
    C."GroupCode" AS "GroupCode",
    G."GroupName" AS "CustomerVendorGroup",
    COALESCE(E."firstName" || ' ' || E."lastName", SL."SlpName", 'Unassigned') AS "DocumentOwner",
    'Sales Delivery' AS "OrderType",
    COALESCE(DEP."Name", 'Unassigned') AS "Department",
    COALESCE(D1."Project", S1."Project", S0."Project", '') AS "Project",
    D1."ItemCode" AS "ItemCode",
    D1."Dscription" AS "ItemDescription",
    IG."ItmsGrpNam" AS "ItemGroupName",
    CASE D1."WhsCode"
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
        ELSE 'Unclassified'
    END AS "Warehouse",
    D1."Quantity" AS "Quantity",
    0 AS "OpenQuantity",
    D1."LineTotal" AS "AmountLocal",
    D1."TotalFrgn" AS "AmountForeign",
    D0."DocCur" AS "Currency",
    'Closed' AS "Status",
    DAYS_BETWEEN(S1."ShipDate", D0."DocDate") AS "DaysPastDue",
    'Closed' AS "KPIStatus",
    'Sales Orders' AS "KPIArea",
    CASE WHEN S1."U_PLS_Reschedule_Date_Line" IS NULL THEN NULL ELSE S1."U_PLS_Reschedule_Date_Line" END AS "LineRescheduleDate",
    CASE
        WHEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") IS NULL THEN NULL
        ELSE COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date")
    END AS "RescheduleDate",
    CASE
        WHEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") IS NULL THEN 'No Reschedule'
        WHEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") = '1111-11-11' THEN 'No Commit Date'
        WHEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") > S1."ShipDate" THEN 'Rescheduled Later Than Original Delivery'
        WHEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") < S1."ShipDate" THEN 'Rescheduled Earlier Than Original Delivery'
        ELSE 'Rescheduled'
    END AS "RescheduleFlag",
    CASE
        WHEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") = '1111-11-11' THEN 'No Commit Date'
        ELSE 'OK'
    END AS "DueDateRiskFlag",
    S0."Confirmed" AS "Confirmed",
    'Complete' AS "FillStatus",
    CASE
        WHEN D0."DocDate" <= CASE
            WHEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") IS NOT NULL
                 AND COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") <> '1111-11-11'
            THEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date")
            ELSE S1."ShipDate"
        END THEN 'On Time'
        ELSE 'Late'
    END AS "OnTimeStatus",
    CASE WHEN D1."Price" = 0 OR D1."Price" IS NULL THEN 'Missing Price' ELSE 'OK' END AS "PriceFlag",
    CASE
        WHEN D0."DocDate" <= CASE
            WHEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") IS NOT NULL
                 AND COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date") <> '1111-11-11'
            THEN COALESCE(S1."U_PLS_Reschedule_Date_Line", S0."U_PLS_Reschedule_Date")
            ELSE S1."ShipDate"
        END THEN 'Perfect'
        ELSE 'Not Perfect'
    END AS "PerfectOrder",
    'Covered' AS "CoverageType",
    COALESCE(I."InvntItem", 'N') AS "InventoryItem",
    -- Shipping page (2v1): the delivery's tracking number, shipping type, ship-to country and delivery date.
    CAST(D0."TrackNo" AS NVARCHAR(100)) AS "TrackingNumber",
    CAST(SHP."TrnspName" AS NVARCHAR(100)) AS "ShipMethod",
    CAST(A."Country" AS NVARCHAR(3)) AS "ShipToCountry",
    D0."DocDate" AS "ShipDate"
FROM ODLN D0
INNER JOIN DLN1 D1 ON D0."DocEntry" = D1."DocEntry"
LEFT JOIN RDR1 S1 ON D1."BaseType" = 17 AND D1."BaseEntry" = S1."DocEntry" AND D1."BaseLine" = S1."LineNum"
LEFT JOIN ORDR S0 ON S1."DocEntry" = S0."DocEntry"
LEFT JOIN OCRD C ON D0."CardCode" = C."CardCode"
LEFT JOIN OCRG G ON C."GroupCode" = G."GroupCode"
LEFT JOIN OSLP SL ON S0."SlpCode" = SL."SlpCode"
LEFT JOIN OHEM E ON S0."OwnerCode" = E."empID"
LEFT JOIN OUDP DEP ON E."dept" = DEP."Code"
LEFT JOIN OITM I ON D1."ItemCode" = I."ItemCode"
LEFT JOIN OITB IG ON I."ItmsGrpCod" = IG."ItmsGrpCod"
-- Shipping type (OSHP) and the ship-to address on the business partner (CRD1, address type S) the delivery names.
LEFT JOIN OSHP SHP ON D0."TrnspCode" = SHP."TrnspCode"
LEFT JOIN CRD1 A ON A."CardCode" = D0."CardCode" AND A."Address" = D0."ShipToCode" AND A."AdresType" = 'S'
WHERE
    D0."DocDate" >= ADD_MONTHS(ADD_YEARS(ADD_DAYS(CURRENT_DATE, 1 - DAYOFYEAR(CURRENT_DATE)), -1), -1)
    AND D0."CANCELED" = 'N'
    AND S0."CANCELED" = 'N'

UNION ALL

SELECT
    CURRENT_DATE AS "ExportDate",
    'Purchase Order' AS "SourceType",
    T0."DocEntry" AS "DocEntry",
    T0."DocNum" AS "DocumentNumber",
    T1."LineNum" AS "LineNumber",
    T0."NumAtCard" AS "CustomerPONumber",
    T0."DocDate" AS "DocumentDate",
    CAST(NULL AS DATE) AS "ClosedDate",
    '' AS "CloseDateSource",
    '' AS "DeliveryDocNumber",
    COALESCE(T1."ShipDate", T0."DocDueDate") AS "DueDate",
    T0."DocDueDate" AS "HeaderDueDate",
    COALESCE(T1."ShipDate", T0."DocDueDate") AS "LineDueDate",
    T0."CardCode" AS "CustomerVendorCode",
    T0."CardName" AS "CustomerVendorName",
    C."GroupCode" AS "GroupCode",
    G."GroupName" AS "CustomerVendorGroup",
    COALESCE(E."firstName" || ' ' || E."lastName", 'Unassigned') AS "DocumentOwner",
    'Purchase Order' AS "OrderType",
    COALESCE(D."Name", 'Unassigned') AS "Department",
    COALESCE(T1."Project", T0."Project", '') AS "Project",
    T1."ItemCode" AS "ItemCode",
    T1."Dscription" AS "ItemDescription",
    IG."ItmsGrpNam" AS "ItemGroupName",
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
        ELSE 'Unclassified'
    END AS "Warehouse",
    T1."Quantity" AS "Quantity",
    T1."OpenQty" AS "OpenQuantity",
    T1."OpenSum" AS "AmountLocal",
    CASE WHEN T0."DocCur" <> 'CAD' THEN T1."OpenSumFC" ELSE T1."OpenSum" END AS "AmountForeign",
    T0."DocCur" AS "Currency",
    CASE
        WHEN T0."DocStatus" = 'C' OR T1."LineStatus" = 'C' THEN 'Closed'
        ELSE 'Open'
    END AS "Status",
    DAYS_BETWEEN(COALESCE(T1."ShipDate", T0."DocDueDate"), CURRENT_DATE) AS "DaysPastDue",
    CASE
        WHEN T0."DocStatus" = 'C' OR T1."LineStatus" = 'C' THEN 'Closed'
        WHEN T1."LineStatus" = 'O' AND T1."U_PLS_Reschedule_Date_Line" = '1111-11-11' THEN 'No Commit'
        WHEN T1."LineStatus" = 'O'
             AND T1."U_PLS_Reschedule_Date_Line" IS NOT NULL
             AND T1."U_PLS_Reschedule_Date_Line" <> '1111-11-11' THEN 'Rescheduled'
        WHEN T1."LineStatus" = 'O' AND T1."ShipDate" < CURRENT_DATE THEN 'Late'
        ELSE 'Open'
    END AS "KPIStatus",
    'Purchase Orders' AS "KPIArea",
    CASE WHEN T1."U_PLS_Reschedule_Date_Line" IS NULL THEN NULL ELSE T1."U_PLS_Reschedule_Date_Line" END AS "LineRescheduleDate",
    CASE WHEN T1."U_PLS_Reschedule_Date_Line" IS NULL THEN NULL ELSE T1."U_PLS_Reschedule_Date_Line" END AS "RescheduleDate",
    CASE
        WHEN T1."U_PLS_Reschedule_Date_Line" IS NULL THEN 'No Reschedule'
        WHEN T1."U_PLS_Reschedule_Date_Line" = '1111-11-11' THEN 'No Commit Date'
        WHEN T1."U_PLS_Reschedule_Date_Line" > T1."ShipDate" THEN 'Rescheduled Later Than Original Delivery'
        WHEN T1."U_PLS_Reschedule_Date_Line" < T1."ShipDate" THEN 'Rescheduled Earlier Than Original Delivery'
        ELSE 'Rescheduled'
    END AS "RescheduleFlag",
    CASE
        WHEN T1."U_PLS_Reschedule_Date_Line" = '1111-11-11' THEN 'No Commit Date'
        WHEN T0."Confirmed" = 'N' THEN 'Unconfirmed PO'
        ELSE 'OK'
    END AS "DueDateRiskFlag",
    T0."Confirmed" AS "Confirmed",
    CASE
        WHEN T1."OpenQty" = 0 THEN 'Complete'
        WHEN T1."OpenQty" > 0 AND T1."OpenQty" < T1."Quantity" THEN 'Partial'
        ELSE 'Not Received'
    END AS "FillStatus",
    CASE
        WHEN T1."OpenQty" = 0 THEN 'Closed'
        WHEN T1."OpenQty" > 0 AND T1."ShipDate" < CURRENT_DATE THEN 'Past Due'
        WHEN T1."OpenQty" > 0 AND T1."ShipDate" <= ADD_DAYS(CURRENT_DATE, 30) THEN 'Upcoming'
        ELSE 'Pending'
    END AS "OnTimeStatus",
    CASE WHEN T1."Price" = 0 OR T1."Price" IS NULL THEN 'Missing Price' ELSE 'OK' END AS "PriceFlag",
    CASE
        WHEN T1."OpenQty" = 0
             AND (T1."U_PLS_Reschedule_Date_Line" IS NULL OR T1."U_PLS_Reschedule_Date_Line" = '1111-11-11')
        THEN 'Perfect'
        ELSE 'Not Perfect'
    END AS "PerfectOrder",
    CASE WHEN T1."OpenQty" = 0 THEN 'Covered' ELSE 'PO Open' END AS "CoverageType",
    COALESCE(I."InvntItem", 'N') AS "InventoryItem",
    CAST(NULL AS NVARCHAR(100)) AS "TrackingNumber",
    CAST(NULL AS NVARCHAR(100)) AS "ShipMethod",
    CAST(NULL AS NVARCHAR(3)) AS "ShipToCountry",
    CAST(NULL AS DATE) AS "ShipDate"
FROM OPOR T0
INNER JOIN POR1 T1 ON T0."DocEntry" = T1."DocEntry"
LEFT JOIN OCRD C ON T0."CardCode" = C."CardCode"
LEFT JOIN OCRG G ON C."GroupCode" = G."GroupCode"
LEFT JOIN OHEM E ON T0."OwnerCode" = E."empID"
LEFT JOIN OUDP D ON E."dept" = D."Code"
LEFT JOIN OITM I ON T1."ItemCode" = I."ItemCode"
LEFT JOIN OITB IG ON I."ItmsGrpCod" = IG."ItmsGrpCod"
WHERE
    (T0."DocDate" >= ADD_DAYS(CURRENT_DATE, -365)
        OR (T0."DocStatus" = 'O' AND T1."LineStatus" = 'O'))
    AND T0."CANCELED" = 'N'

UNION ALL

SELECT
    CURRENT_DATE AS "ExportDate",
    'Purchase Order' AS "SourceType",
    P0."DocEntry" AS "DocEntry",
    P0."DocNum" AS "DocumentNumber",
    P1."LineNum" AS "LineNumber",
    P0."NumAtCard" AS "CustomerPONumber",
    P0."DocDate" AS "DocumentDate",
    R0."DocDate" AS "ClosedDate",
    'Receipt' AS "CloseDateSource",
    TO_NVARCHAR(R0."DocNum") AS "DeliveryDocNumber",
    P1."ShipDate" AS "DueDate",
    P0."DocDueDate" AS "HeaderDueDate",
    P1."ShipDate" AS "LineDueDate",
    R0."CardCode" AS "CustomerVendorCode",
    R0."CardName" AS "CustomerVendorName",
    C."GroupCode" AS "GroupCode",
    G."GroupName" AS "CustomerVendorGroup",
    COALESCE(E."firstName" || ' ' || E."lastName", 'Unassigned') AS "DocumentOwner",
    'PO Receipt' AS "OrderType",
    COALESCE(DEP."Name", 'Unassigned') AS "Department",
    COALESCE(R1."Project", P1."Project", P0."Project", '') AS "Project",
    R1."ItemCode" AS "ItemCode",
    R1."Dscription" AS "ItemDescription",
    IG."ItmsGrpNam" AS "ItemGroupName",
    CASE R1."WhsCode"
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
        ELSE 'Unclassified'
    END AS "Warehouse",
    R1."Quantity" AS "Quantity",
    0 AS "OpenQuantity",
    R1."LineTotal" AS "AmountLocal",
    R1."TotalFrgn" AS "AmountForeign",
    R0."DocCur" AS "Currency",
    'Closed' AS "Status",
    DAYS_BETWEEN(P1."ShipDate", R0."DocDate") AS "DaysPastDue",
    'Closed' AS "KPIStatus",
    'Purchase Orders' AS "KPIArea",
    CASE WHEN P1."U_PLS_Reschedule_Date_Line" IS NULL THEN NULL ELSE P1."U_PLS_Reschedule_Date_Line" END AS "LineRescheduleDate",
    CASE WHEN P1."U_PLS_Reschedule_Date_Line" IS NULL THEN NULL ELSE P1."U_PLS_Reschedule_Date_Line" END AS "RescheduleDate",
    CASE
        WHEN P1."U_PLS_Reschedule_Date_Line" IS NULL THEN 'No Reschedule'
        WHEN P1."U_PLS_Reschedule_Date_Line" = '1111-11-11' THEN 'No Commit Date'
        WHEN P1."U_PLS_Reschedule_Date_Line" > P1."ShipDate" THEN 'Rescheduled Later Than Original Delivery'
        WHEN P1."U_PLS_Reschedule_Date_Line" < P1."ShipDate" THEN 'Rescheduled Earlier Than Original Delivery'
        ELSE 'Rescheduled'
    END AS "RescheduleFlag",
    CASE
        WHEN P1."U_PLS_Reschedule_Date_Line" = '1111-11-11' THEN 'No Commit Date'
        ELSE 'OK'
    END AS "DueDateRiskFlag",
    P0."Confirmed" AS "Confirmed",
    'Complete' AS "FillStatus",
    CASE
        WHEN R0."DocDate" <= CASE
            WHEN P1."U_PLS_Reschedule_Date_Line" IS NOT NULL
                 AND P1."U_PLS_Reschedule_Date_Line" <> '1111-11-11'
            THEN P1."U_PLS_Reschedule_Date_Line"
            ELSE P1."ShipDate"
        END THEN 'On Time'
        ELSE 'Late'
    END AS "OnTimeStatus",
    CASE WHEN R1."Price" = 0 OR R1."Price" IS NULL THEN 'Missing Price' ELSE 'OK' END AS "PriceFlag",
    CASE
        WHEN R0."DocDate" <= CASE
            WHEN P1."U_PLS_Reschedule_Date_Line" IS NOT NULL
                 AND P1."U_PLS_Reschedule_Date_Line" <> '1111-11-11'
            THEN P1."U_PLS_Reschedule_Date_Line"
            ELSE P1."ShipDate"
        END THEN 'Perfect'
        ELSE 'Not Perfect'
    END AS "PerfectOrder",
    'Covered' AS "CoverageType",
    COALESCE(I."InvntItem", 'N') AS "InventoryItem",
    CAST(NULL AS NVARCHAR(100)) AS "TrackingNumber",
    CAST(NULL AS NVARCHAR(100)) AS "ShipMethod",
    CAST(NULL AS NVARCHAR(3)) AS "ShipToCountry",
    CAST(NULL AS DATE) AS "ShipDate"
FROM OPDN R0
INNER JOIN PDN1 R1 ON R0."DocEntry" = R1."DocEntry"
LEFT JOIN POR1 P1 ON R1."BaseType" = 22 AND R1."BaseEntry" = P1."DocEntry" AND R1."BaseLine" = P1."LineNum"
LEFT JOIN OPOR P0 ON P1."DocEntry" = P0."DocEntry"
LEFT JOIN OCRD C ON R0."CardCode" = C."CardCode"
LEFT JOIN OCRG G ON C."GroupCode" = G."GroupCode"
LEFT JOIN OHEM E ON P0."OwnerCode" = E."empID"
LEFT JOIN OUDP DEP ON E."dept" = DEP."Code"
LEFT JOIN OITM I ON R1."ItemCode" = I."ItemCode"
LEFT JOIN OITB IG ON I."ItmsGrpCod" = IG."ItmsGrpCod"
WHERE
    R0."DocDate" >= ADD_MONTHS(ADD_YEARS(ADD_DAYS(CURRENT_DATE, 1 - DAYOFYEAR(CURRENT_DATE)), -1), -1)
    AND R0."CANCELED" = 'N'
    AND P0."CANCELED" = 'N'

ORDER BY
    "SourceType",
    "Status",
    "DueDate",
    "DocumentNumber",
    "LineNumber";
