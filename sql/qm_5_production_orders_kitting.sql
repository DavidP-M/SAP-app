-- Copied from SAP Query Manager, folder "Kareena - Qry", query "5.KPI_Production Orders and Kitting_10.19" (received 2026-11-17).
-- Keep the rest of this file identical to that saved query; restart the bridge after changing it.
SELECT
CURRENT_DATE AS "ExportDate",
'Production Order' AS "SourceType",
W0."DocEntry" AS "DocEntry",
W0."DocNum" AS "ProductionOrder",
CASE W0."Status" WHEN 'P' THEN 'Planned' WHEN 'R' THEN 'Released' ELSE W0."Status" END AS "ProductionStatus",
W0."ItemCode" AS "ParentItem",
PI."ItemName" AS "ParentDescription",
W0."PlannedQty" AS "ParentPlannedQty",
W0."CmpltQty" AS "ParentCompletedQty",
W0."PlannedQty" - W0."CmpltQty" AS "ParentOpenQty",
CASE W0."Warehouse"
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
    ELSE COALESCE(WH."WhsName", W0."Warehouse")
END AS "Warehouse",
W0."StartDate" AS "StartDate",
W0."DueDate" AS "DueDate",
W0."OriginNum" AS "SalesOrder",
CASE
    WHEN SOH."DocEntry" IS NULL THEN ''
    WHEN SOH."CANCELED" = 'Y' THEN 'Cancelled'
    WHEN SOH."DocStatus" = 'C' THEN 'Closed'
    ELSE 'Open'
END AS "SalesOrderStatus",
COALESCE(U."U_NAME", U."USER_CODE", 'Unassigned') AS "Owner",
W0."CardCode" AS "CustomerCode",
C."CardName" AS "Customer",
W1."LineNum" AS "ComponentLine",
W1."ItemCode" AS "Component",
CI."ItemName" AS "ComponentDescription",
CASE W1."wareHouse"
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
    ELSE COALESCE(CW."WhsName", W1."wareHouse")
END AS "ComponentWarehouse",
W1."PlannedQty" AS "ComponentPlannedQty",
W1."IssuedQty" AS "ComponentIssuedQty",
CASE WHEN W1."PlannedQty" > W1."IssuedQty" THEN W1."PlannedQty" - W1."IssuedQty" ELSE 0 END AS "ComponentRemaining",
COALESCE(IW."OnHand", 0) AS "ComponentOnHand",
COALESCE(IW."OnOrder", 0) AS "ComponentOnOrder",
CASE W1."IssueType" WHEN 'B' THEN 'Backflush' ELSE 'Manual' END AS "IssueMethod",
COALESCE(CI."InvntItem", 'N') AS "InventoryItem"
FROM OWOR W0
INNER JOIN WOR1 W1 ON W0."DocEntry" = W1."DocEntry"
LEFT JOIN OITM PI ON W0."ItemCode" = PI."ItemCode"
LEFT JOIN OITM CI ON W1."ItemCode" = CI."ItemCode"
LEFT JOIN OITW IW ON IW."ItemCode" = W1."ItemCode" AND IW."WhsCode" = W1."wareHouse"
LEFT JOIN OCRD C ON W0."CardCode" = C."CardCode"
LEFT JOIN ORDR SOH ON SOH."DocEntry" = W0."OriginAbs"
LEFT JOIN OUSR U ON W0."UserSign" = U."USERID"
LEFT JOIN OWHS WH ON W0."Warehouse" = WH."WhsCode"
LEFT JOIN OWHS CW ON W1."wareHouse" = CW."WhsCode"
WHERE
W0."Status" IN ('P', 'R')
AND CI."ItemCode" IS NOT NULL
ORDER BY "DueDate", "ProductionOrder", "ComponentLine";