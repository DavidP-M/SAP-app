-- Copied from SAP Query Manager, folder "Kareena - Qry", query "1. KPI_Sales Orders _ 10.19" (received 2026-11-17).
-- Keep the rest of this file identical to that saved query; restart the bridge after changing it.
SELECT
CURRENT_DATE AS "ExportDate",
'Sales Order' AS "SourceType",
X."DocEntry" AS "DocEntry",
X."DocNum" AS "DocumentNumber",
X."LineNum" AS "LineNumber",
X."NumAtCard" AS "CustomerPONumber",
X."DocDate" AS "DocumentDate",
CAST(NULL AS DATE) AS "ClosedDate",
'' AS "CloseDateSource",
'' AS "DeliveryDocNumber",
X."ShipDate" AS "DueDate",
X."DocDueDate" AS "HeaderDueDate",
X."ShipDate" AS "LineDueDate",
X."CardCode" AS "CustomerVendorCode",
X."CardName" AS "CustomerVendorName",
X."GroupCode" AS "GroupCode",
X."GroupName" AS "CustomerVendorGroup",
X."DocumentOwner" AS "DocumentOwner",
'Sales Order' AS "OrderType",
X."Department" AS "Department",
X."Project" AS "Project",
X."ItemCode" AS "ItemCode",
X."Dscription" AS "ItemDescription",
X."ItmsGrpNam" AS "ItemGroupName",
X."Warehouse" AS "Warehouse",
X."Quantity" AS "Quantity",
CASE WHEN X."LineState" = 'C' THEN 0 ELSE X."OpenQty" END AS "OpenQuantity",
CASE WHEN X."LineState" = 'C' THEN 0 ELSE X."OpenSum" END AS "AmountLocal",
CASE
    WHEN X."LineState" = 'C' THEN 0
    WHEN X."DocCur" = 'CAD' THEN X."OpenSum"
    ELSE X."OpenSumFC"
END AS "AmountForeign",
X."DocCur" AS "Currency",
CASE WHEN X."LineState" = 'C' THEN 'Closed' ELSE 'Open' END AS "Status",
CASE
    WHEN X."DPDate" IS NOT NULL AND X."PerfDate" > X."EffDue" THEN DAYS_BETWEEN(X."EffDue", X."PerfDate")
    WHEN X."DPDate" IS NOT NULL THEN 0
    WHEN X."LineState" = 'O' AND X."EffDue" < CURRENT_DATE THEN DAYS_BETWEEN(X."EffDue", CURRENT_DATE)
    WHEN X."LineState" = 'O' THEN 0
    WHEN X."PerfDate" IS NULL THEN NULL
    WHEN X."PerfDate" > X."EffDue" THEN DAYS_BETWEEN(X."EffDue", X."PerfDate")
    ELSE 0
END AS "DaysPastDue",
CASE
    WHEN X."LineState" = 'C' THEN 'Closed'
    WHEN X."DPDate" IS NOT NULL THEN 'Billed - Held'
    WHEN X."EffDue" < CURRENT_DATE THEN 'Late'
    WHEN X."Resched" = '1111-11-11' THEN 'No Commit'
    WHEN X."Resched" IS NOT NULL THEN 'Rescheduled'
    ELSE 'Open'
END AS "KPIStatus",
'Sales Orders' AS "KPIArea",
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
CASE
    WHEN X."ShippedQty" >= X."Quantity" THEN 'Complete'
    WHEN X."LineState" = 'C' THEN 'Closed Short'
    WHEN X."ShippedQty" > 0 THEN 'Partial'
    ELSE 'Not Shipped'
END AS "FillStatus",
CASE
    WHEN X."LineState" = 'O' AND X."DPDate" IS NOT NULL THEN 'Billed - Held'
    WHEN X."LineState" = 'C' AND X."PerfDate" IS NULL THEN 'Closed'
    WHEN X."LineState" = 'C' AND X."PerfDate" <= X."EffDue" THEN 'On Time'
    WHEN X."LineState" = 'C' THEN 'Late'
    WHEN X."EffDue" < CURRENT_DATE THEN 'Past Due'
    WHEN X."EffDue" <= ADD_DAYS(CURRENT_DATE, 30) THEN 'Upcoming'
    ELSE 'Pending'
END AS "OnTimeStatus",
CASE WHEN X."Price" = 0 OR X."Price" IS NULL THEN 'Missing Price' ELSE 'OK' END AS "PriceFlag",
CASE
    WHEN X."LineState" = 'O' THEN 'Pending'
    WHEN X."ShippedQty" >= X."Quantity"
     AND X."PerfDate" <= X."ShipDate"
     AND X."Resched" IS NULL THEN 'Perfect'
    ELSE 'Not Perfect'
END AS "PerfectOrder",
CASE WHEN X."LineState" = 'C' THEN 'Covered' ELSE 'Uncovered' END AS "CoverageType",
X."ShippedQty" AS "DeliveredQuantity",
X."InvntItem" AS "InventoryItem",
CASE WHEN X."LineState" = 'O' AND X."DPDate" IS NOT NULL THEN 'Y' ELSE 'N' END AS "BillAndHold",
X."DPDocNum" AS "DownPaymentDocNumber",
X."DPDate" AS "DownPaymentDate",
X."ItemOnHand" AS "ItemOnHand"
FROM (
    SELECT
    T0."DocEntry", T0."DocNum", T1."LineNum", T0."NumAtCard", T0."DocDate",
    T1."ShipDate", T0."DocDueDate", T0."CardCode", T0."CardName",
    C."GroupCode", G."GroupName",
    COALESCE(NULLIF(TRIM(COALESCE(E."firstName", '') || ' ' || COALESCE(E."lastName", '')), ''), S."SlpName", 'Unassigned') AS "DocumentOwner",
    COALESCE(D."Name", 'Unassigned') AS "Department",
    COALESCE(T1."Project", T0."Project", '') AS "Project",
    T1."ItemCode", T1."Dscription", IG."ItmsGrpNam",
    -- Service lines (service-type documents have no item) and non-inventory items (freight, labour, charges) are N.
    COALESCE(I."InvntItem", 'N') AS "InvntItem",
    COALESCE(OH."ItemOnHand", 0) AS "ItemOnHand",
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
        ELSE COALESCE(W."WhsName", T1."WhsCode")
    END AS "Warehouse",
    T1."Quantity", T1."OpenQty", T1."OpenSum", T1."OpenSumFC", T1."Price", T0."DocCur", T0."Confirmed",
    CASE WHEN T0."DocStatus" = 'C' OR T1."LineStatus" = 'C' THEN 'C' ELSE 'O' END AS "LineState",
    T1."U_PLS_Reschedule_Date_Line" AS "LineResched",
    COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") AS "Resched",
    CASE WHEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") IS NOT NULL AND COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") <> '1111-11-11' THEN COALESCE(T1."U_PLS_Reschedule_Date_Line", T0."U_PLS_Reschedule_Date") ELSE T1."ShipDate" END AS "EffDue",
    COALESCE(LS."ShippedQty", 0) AS "ShippedQty",
    LS."LastShipDate",
    DP."DPDate", DP."DPDocNum",
    CASE WHEN DP."DPDate" IS NOT NULL AND (LS."LastShipDate" IS NULL OR DP."DPDate" < LS."LastShipDate") THEN DP."DPDate" ELSE LS."LastShipDate" END AS "PerfDate"
    FROM ORDR T0
    INNER JOIN RDR1 T1 ON T0."DocEntry" = T1."DocEntry"
    LEFT JOIN (
        SELECT RL."BaseEntry", RL."BaseLine",
               SUM(RL."Quantity") AS "ShippedQty",
               MAX(RH."DocDate") AS "LastShipDate"
        FROM DLN1 RL
        INNER JOIN ODLN RH ON RH."DocEntry" = RL."DocEntry"
        WHERE RL."BaseType" = 17 AND RH."CANCELED" = 'N'
        GROUP BY RL."BaseEntry", RL."BaseLine"
    ) LS ON LS."BaseEntry" = T1."DocEntry" AND LS."BaseLine" = T1."LineNum"
    LEFT JOIN (
        SELECT DL."BaseEntry", DL."BaseLine",
               SUM(DL."Quantity") AS "DPQty",
               MIN(DH."DocDate") AS "DPDate",
               MIN(DH."DocNum") AS "DPDocNum"
        FROM DPI1 DL
        INNER JOIN ODPI DH ON DH."DocEntry" = DL."DocEntry"
        WHERE DL."BaseType" = 17 AND DH."CANCELED" = 'N'
        GROUP BY DL."BaseEntry", DL."BaseLine"
    ) DP ON DP."BaseEntry" = T1."DocEntry" AND DP."BaseLine" = T1."LineNum"
    LEFT JOIN OCRD C ON T0."CardCode" = C."CardCode"
    LEFT JOIN OCRG G ON C."GroupCode" = G."GroupCode"
    LEFT JOIN OSLP S ON T0."SlpCode" = S."SlpCode"
    LEFT JOIN OHEM E ON T0."OwnerCode" = E."empID"
    LEFT JOIN OUDP D ON E."dept" = D."Code"
    LEFT JOIN OITM I ON T1."ItemCode" = I."ItemCode"
    LEFT JOIN OITB IG ON I."ItmsGrpCod" = IG."ItmsGrpCod"
    LEFT JOIN OWHS W ON T1."WhsCode" = W."WhsCode"
    LEFT JOIN (
        SELECT "ItemCode", SUM("OnHand") AS "ItemOnHand"
        FROM OITW
        WHERE "WhsCode" NOT IN ('19', '29')
        GROUP BY "ItemCode"
    ) OH ON OH."ItemCode" = T1."ItemCode"
    WHERE
    (T0."DocDate" >= ADD_DAYS(CURRENT_DATE, -365) OR (T0."DocStatus" = 'O' AND T1."LineStatus" = 'O'))
    AND T0."CANCELED" = 'N'
) X
ORDER BY "Status", "DueDate", "DocumentNumber", "LineNumber";