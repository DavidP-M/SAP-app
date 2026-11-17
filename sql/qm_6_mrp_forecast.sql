-- Copied from SAP Query Manager, folder "Kareena - Qry", query "6.KPI_MRP Forecast_10.19" (received 2026-11-17).
-- Keep the rest of this file identical to that saved query; restart the bridge after changing it.
SELECT
CURRENT_DATE AS "ExportDate",
F0."Code" AS "ForecastCode",
F0."Name" AS "ForecastName",
F1."ItemCode" AS "ItemCode",
I."ItemName" AS "ItemDescription",
F1."WhsCode" AS "Warehouse",
TO_VARCHAR(F1."Date", 'YYYY-MM') AS "ForecastMonth",
SUM(F1."Quantity") AS "ForecastQty"
FROM OFCT F0
INNER JOIN FCT1 F1 ON F0."AbsID" = F1."AbsID"
LEFT JOIN OITM I ON F1."ItemCode" = I."ItemCode"
WHERE F0."AbsID" = (SELECT MAX("AbsID") FROM OFCT)
GROUP BY F0."Code", F0."Name", F1."ItemCode", I."ItemName", F1."WhsCode", TO_VARCHAR(F1."Date", 'YYYY-MM')
ORDER BY "ItemCode", "Warehouse", "ForecastMonth";