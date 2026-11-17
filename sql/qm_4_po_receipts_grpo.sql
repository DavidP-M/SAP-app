-- Copied from SAP Query Manager, folder "Kareena - Qry", query "4. KPI_PO Receipts GRPO_10.19" (received 2026-11-17).
-- Keep the rest of this file identical to that saved query; restart the bridge after changing it.
SELECT
CURRENT_DATE AS "ExportDate",
'Purchase Order' AS "SourceType",
X."DocEntry" AS "DocEntry",
X."DocNum" AS "DocumentNumber",
X."LineNum" AS "LineNumber",
X."NumAtCard" AS "CustomerPONumber",
X."DocDate" AS "DocumentDate",
X."RcvDate" AS "ClosedDate",
CASE
    WHEN X."LineNum" IS NULL THEN 'Receipt - No PO'
    WHEN X."WasHeld" = 1 THEN 'Receipt - Billed & Held'
    ELSE 'Receipt'
END AS "CloseDateSource",
TO_NVARCHAR(X."RcvDocNum") AS "DeliveryDocNumber",
X."ShipDate" AS "DueDate",
X."DocDueDate" AS "HeaderDueDate",
X."ShipDate" AS "LineDueDate",
X."CardCode" AS "CustomerVendorCode",
X."CardName" AS "CustomerVendorName",
X."GroupCode" AS "GroupCode",
X."GroupName" AS "CustomerVendorGroup",
X."DocumentOwner" AS "DocumentOwner",
'PO Receipt' AS "OrderType",
X."Department" AS "Department",
X."Project" AS "Project",
X."ItemCode" AS "ItemCode",
X."Dscription" AS "ItemDescription",
X."ItmsGrpNam" AS "ItemGroupName",
X."Warehouse" AS "Warehouse",
X."Quantity" AS "Quantity",
0 AS "OpenQuantity",
X."LineTotal" AS "AmountLocal",
CASE WHEN X."DocCur" = 'CAD' THEN X."LineTotal" ELSE X."TotalFrgn" END AS "AmountForeign",
X."DocCur" AS "Currency",
'Closed' AS "Status",
CASE
    WHEN X."EffDue" IS NULL THEN NULL
    WHEN X."PerfDate" > X."EffDue" THEN DAYS_BETWEEN(X."EffDue", X."PerfDate")
    ELSE 0
END AS "DaysPastDue",
'Closed' AS "KPIStatus",
'Purchase Orders' AS "KPIArea",
X."LineResched" AS "LineRescheduleDate",
X."Resched" AS "RescheduleDate",
CASE
    WHEN X."Resched" IS NULL THEN 'No Reschedule'
    WHEN X."Resched" = '1111-11-11' THEN 'No Commit Date'
    WHEN X."Resched" > X."ShipDate" THEN 'Rescheduled Later Than Original Delivery'
    WHEN X."Resched" < X."ShipDate" THEN 'Rescheduled Earlier Than Original Delivery'
    ELSE 'Rescheduled'
END AS "RescheduleFlag",
CASE
    WHEN X."Resched" = '1111-11-11' THEN 'No Commit Date'
    ELSE 'OK'
END AS "DueDateRiskFlag",
X."Confirmed" AS "Confirmed",
'Received' AS "FillStatus",
CASE
    WHEN X."LineNum" IS NULL THEN 'No PO'
    WHEN X."PerfDate" <= X."EffDue" THEN 'On Time'
    ELSE 'Late'
END AS "OnTimeStatus",
CASE WHEN X."Price" = 0 OR X."Price" IS NULL THEN 'Missing Price' ELSE 'OK' END AS "PriceFlag",
CASE
    WHEN X."LineNum" IS NULL THEN 'No PO'
    WHEN X."PerfDate" <= X."ShipDate" AND X."Resched" IS NULL THEN 'Perfect'
    ELSE 'Not Perfect'
END AS "PerfectOrder",
'Covered' AS "CoverageType",
X."Quantity" AS "DeliveredQuantity",
X."InvntItem" AS "InventoryItem",
CASE WHEN X."WasHeld" = 1 THEN 'Y' ELSE 'N' END AS "BillAndHold",
X."DPDocNum" AS "DownPaymentDocNumber",
X."DPDate" AS "DownPaymentDate",
CAST(NULL AS DECIMAL(19,6)) AS "ItemOnHand",
X."Buyer" AS "Buyer"
FROM (
    SELECT
    S0."DocEntry", S0."DocNum", S1."LineNum", S0."NumAtCard", S0."DocDate",
    S1."ShipDate", S0."DocDueDate", R0."CardCode", R0."CardName",
    C."GroupCode", G."GroupName",
    COALESCE(NULLIF(TRIM(COALESCE(E."firstName", '') || ' ' || COALESCE(E."lastName", '')), ''), 'Unassigned') AS "DocumentOwner",
    COALESCE(DEP."Name", 'Unassigned') AS "Department",
    COALESCE(R1."Project", S1."Project", S0."Project", '') AS "Project",
    R1."ItemCode", R1."Dscription", IG."ItmsGrpNam",
    -- Service lines (service-type documents have no item) and non-inventory items (freight, labour, charges) are N.
    COALESCE(I."InvntItem", 'N') AS "InvntItem",
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
        ELSE COALESCE(W."WhsName", R1."WhsCode")
    END AS "Warehouse",
    R1."Quantity", R1."LineTotal", R1."TotalFrgn", R1."Price", R0."DocCur", S0."Confirmed",
    R0."DocDate" AS "RcvDate", R0."DocNum" AS "RcvDocNum",
    R0."DocDate" AS "PerfDate",
    0 AS "WasHeld",
    CAST(NULL AS DATE) AS "DPDate", CAST(NULL AS INTEGER) AS "DPDocNum",
    CASE WHEN S0."SlpCode" > 0 THEN BYR."SlpName" ELSE NULL END AS "Buyer",
    S1."U_PLS_Reschedule_Date_Line" AS "LineResched",
    S1."U_PLS_Reschedule_Date_Line" AS "Resched",
    CASE WHEN S1."U_PLS_Reschedule_Date_Line" IS NOT NULL AND S1."U_PLS_Reschedule_Date_Line" <> '1111-11-11' THEN S1."U_PLS_Reschedule_Date_Line" ELSE S1."ShipDate" END AS "EffDue"
    FROM OPDN R0
    INNER JOIN PDN1 R1 ON R0."DocEntry" = R1."DocEntry"
    LEFT JOIN POR1 S1 ON R1."BaseType" = 22 AND R1."BaseEntry" = S1."DocEntry" AND R1."BaseLine" = S1."LineNum"
    LEFT JOIN OPOR S0 ON S1."DocEntry" = S0."DocEntry"
    LEFT JOIN OCRD C ON R0."CardCode" = C."CardCode"
    LEFT JOIN OCRG G ON C."GroupCode" = G."GroupCode"
    LEFT JOIN OHEM E ON S0."OwnerCode" = E."empID"
    LEFT JOIN OUDP DEP ON E."dept" = DEP."Code"
    LEFT JOIN OITM I ON R1."ItemCode" = I."ItemCode"
    LEFT JOIN OITB IG ON I."ItmsGrpCod" = IG."ItmsGrpCod"
    LEFT JOIN OWHS W ON R1."WhsCode" = W."WhsCode"
    LEFT JOIN OSLP BYR ON S0."SlpCode" = BYR."SlpCode"
    WHERE
    R0."DocDate" >= ADD_DAYS(CURRENT_DATE, -365)
    AND R0."CANCELED" = 'N'
    AND COALESCE(S0."CANCELED", 'N') = 'N'
) X
ORDER BY "DueDate", "DocumentNumber", "LineNumber";