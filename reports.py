"""What the bridge can run.

REPORTS  - whole saved queries (.sql files in ./sql), returned as a tab-delimited file exactly like
           SAP's Export to Excel. The KPI dashboard loads these in place of an uploaded export.
QUERIES  - small named queries returned as JSON, for tiles or other apps (the SAP gateway).

Callers can only pick a name from these lists; they never send SQL.
"""

REPORTS = {
    "kpi": {
        "file": "kpi_dashboard.sql",
        "filename": "SAP_live_KPI_{date}.txt",
        "description": "SO / delivery / PO lines for the KPI dashboard (same as the Query Manager KPI query)",
        "ttl": 300,
    },
    "supply-demand": {
        "file": "supply_demand.sql",
        "filename": "SAP_live_supply_demand_{date}.txt",
        "description": "Monthly demand vs PO supply snapshot for the Supply / Demand and Supply Mix pages",
        "ttl": 300,
    },
}

# params: (name, type, default, min, max[, choices]).
#   int: min / max are the allowed range.  str: max is the longest value allowed (min unused); values are stripped and
#   may hold only letters, digits, spaces and . _ / -.  default None = required.  choices: the only values accepted.
# param_map: order the values go into the ? placeholders (default: the order of params).
# max_rows: the most rows returned; the JSON then says truncated: true.
QUERIES = {
    "open_sales_orders": {
        "description": "Open sales orders with remaining open value (local currency)",
        "ttl": 120,
        "sql": """
            SELECT T0."DocNum", T0."CardCode", T0."CardName", T0."DocDate", T0."DocDueDate",
                   SUM(T1."OpenSum") AS "OpenValue"
            FROM "ORDR" T0 JOIN "RDR1" T1 ON T0."DocEntry" = T1."DocEntry"
            WHERE T0."DocStatus" = 'O' AND T1."LineStatus" = 'O'
            GROUP BY T0."DocNum", T0."CardCode", T0."CardName", T0."DocDate", T0."DocDueDate"
            ORDER BY T0."DocDueDate"
        """,
    },
    "sales_by_month": {
        "description": "Net sales (invoices minus credit notes, before tax) per month",
        "params": [("months", int, 12, 1, 60)],
        "param_map": ["months", "months"],
        "ttl": 300,
        "sql": """
            SELECT "Period", SUM("Amount") AS "NetSales"
            FROM (
                SELECT TO_VARCHAR(T0."DocDate", 'YYYY-MM') AS "Period", T1."LineTotal" AS "Amount"
                FROM "OINV" T0 JOIN "INV1" T1 ON T0."DocEntry" = T1."DocEntry"
                WHERE T0."CANCELED" = 'N' AND T0."DocDate" >= ADD_MONTHS(CURRENT_DATE, -?)
                UNION ALL
                SELECT TO_VARCHAR(T0."DocDate", 'YYYY-MM'), -T1."LineTotal"
                FROM "ORIN" T0 JOIN "RIN1" T1 ON T0."DocEntry" = T1."DocEntry"
                WHERE T0."CANCELED" = 'N' AND T0."DocDate" >= ADD_MONTHS(CURRENT_DATE, -?)
            )
            GROUP BY "Period"
            ORDER BY "Period"
        """,
    },
    "stock_by_warehouse": {
        "description": "On-hand quantity and value per warehouse (warehouse average cost)",
        "ttl": 300,
        "sql": """
            SELECT T0."WhsCode", W."WhsName",
                   SUM(T0."OnHand") AS "OnHandQty",
                   SUM(T0."OnHand" * T0."AvgPrice") AS "StockValue"
            FROM "OITW" T0 JOIN "OWHS" W ON T0."WhsCode" = W."WhsCode"
            GROUP BY T0."WhsCode", W."WhsName"
            ORDER BY "StockValue" DESC
        """,
    },
    "open_purchase_orders": {
        "description": "Open purchase orders with remaining open value",
        "ttl": 300,
        "sql": """
            SELECT T0."DocNum", T0."CardCode", T0."CardName", T0."DocDueDate",
                   SUM(T1."OpenSum") AS "OpenValue"
            FROM "OPOR" T0 JOIN "POR1" T1 ON T0."DocEntry" = T1."DocEntry"
            WHERE T0."DocStatus" = 'O' AND T1."LineStatus" = 'O'
            GROUP BY T0."DocNum", T0."CardCode", T0."CardName", T0."DocDueDate"
            ORDER BY T0."DocDueDate"
        """,
    },

    # ------------------------------------------------------------ lookups for Ask AI (one document, item or customer)
    "so_detail": {
        "description": "The lines of one sales order: items, quantities still open, prices and ship dates",
        "params": [("docnum", int, None, 1, 2147483647)],
        "max_rows": 200,
        "ttl": 60,
        "sql": """
            SELECT T0."DocNum", T0."CardCode", T0."CardName", T0."DocDate", T0."DocDueDate", T0."DocStatus",
                   T1."LineNum", T1."ItemCode", T1."Dscription", T1."Quantity", T1."OpenQty", T1."Price",
                   T1."Currency", T1."LineTotal", T1."OpenSum", T1."WhsCode", T1."ShipDate", T1."LineStatus"
            FROM "ORDR" T0 JOIN "RDR1" T1 ON T0."DocEntry" = T1."DocEntry"
            WHERE T0."DocNum" = ?
            ORDER BY T0."DocEntry", T1."LineNum"
        """,
    },
    "po_detail": {
        "description": "The lines of one purchase order: items, quantities still to receive, prices and due dates",
        "params": [("docnum", int, None, 1, 2147483647)],
        "max_rows": 200,
        "ttl": 60,
        "sql": """
            SELECT T0."DocNum", T0."CardCode", T0."CardName", T0."DocDate", T0."DocDueDate", T0."DocStatus",
                   T1."LineNum", T1."ItemCode", T1."Dscription", T1."Quantity", T1."OpenQty", T1."Price",
                   T1."Currency", T1."LineTotal", T1."OpenSum", T1."WhsCode", T1."ShipDate", T1."LineStatus"
            FROM "OPOR" T0 JOIN "POR1" T1 ON T0."DocEntry" = T1."DocEntry"
            WHERE T0."DocNum" = ?
            ORDER BY T0."DocEntry", T1."LineNum"
        """,
    },
    "item_stock": {
        "description": "Stock of one item in each warehouse: on hand, committed to orders, on order and available",
        "params": [("item", str, None, None, 50)],
        "max_rows": 200,
        "ttl": 60,
        "sql": """
            SELECT T0."ItemCode", I."ItemName", T0."WhsCode", W."WhsName",
                   T0."OnHand", T0."IsCommited" AS "Committed", T0."OnOrder",
                   T0."OnHand" - T0."IsCommited" + T0."OnOrder" AS "Available"
            FROM "OITW" T0
            JOIN "OITM" I ON I."ItemCode" = T0."ItemCode"
            JOIN "OWHS" W ON W."WhsCode" = T0."WhsCode"
            WHERE T0."ItemCode" = ?
            ORDER BY T0."WhsCode"
        """,
    },
    "customer_orders": {
        "description": "A customer's sales orders in recent months, with open value and how late the last delivery was",
        "params": [("cardcode", str, None, None, 15), ("months", int, 12, 1, 60)],
        "max_rows": 200,
        "ttl": 120,
        # DaysLate: last delivery date minus the order's due date (never below 0), only once the order is closed.
        "sql": """
            WITH O AS (
                SELECT "DocEntry", "DocNum", "DocDate", "DocDueDate", "DocStatus", "DocTotal"
                FROM "ORDR"
                WHERE "CardCode" = ? AND "DocDate" >= ADD_MONTHS(CURRENT_DATE, -?) AND "CANCELED" = 'N'
            ),
            D AS (
                SELECT L."BaseEntry" AS "OrderEntry", MAX(H."DocDate") AS "LastDeliveryDate"
                FROM "DLN1" L JOIN "ODLN" H ON H."DocEntry" = L."DocEntry"
                WHERE L."BaseType" = 17 AND L."BaseEntry" IN (SELECT "DocEntry" FROM O) AND H."CANCELED" = 'N'
                GROUP BY L."BaseEntry"
            ),
            V AS (
                SELECT L."DocEntry" AS "OrderEntry", SUM(L."OpenSum") AS "OpenValue"
                FROM "RDR1" L
                WHERE L."DocEntry" IN (SELECT "DocEntry" FROM O) AND L."LineStatus" = 'O'
                GROUP BY L."DocEntry"
            )
            SELECT O."DocNum", O."DocDate", O."DocDueDate", O."DocStatus", O."DocTotal",
                   COALESCE(V."OpenValue", 0) AS "OpenValue",
                   D."LastDeliveryDate",
                   CASE WHEN O."DocStatus" = 'C' AND D."LastDeliveryDate" IS NOT NULL
                        THEN GREATEST(DAYS_BETWEEN(O."DocDueDate", D."LastDeliveryDate"), 0) END AS "DaysLate"
            FROM O
            LEFT JOIN D ON D."OrderEntry" = O."DocEntry"
            LEFT JOIN V ON V."OrderEntry" = O."DocEntry"
            ORDER BY O."DocDate" DESC, O."DocNum" DESC
        """,
    },
    "order_deliveries": {
        "description": "Deliveries and invoices made against one sales order",
        "params": [("docnum", int, None, 1, 2147483647)],
        "max_rows": 200,
        "ttl": 60,
        # Invoices count when made from the order (BaseType 17) or from one of its delivery lines (BaseType 15).
        "sql": """
            WITH S AS (
                SELECT "DocEntry", "DocNum" FROM "ORDR" WHERE "DocNum" = ?
            ),
            D AS (
                SELECT H."DocEntry", H."DocNum", H."DocDate", L."LineNum", L."ItemCode", L."Quantity", L."LineTotal",
                       S."DocNum" AS "BaseDocNum"
                FROM S
                JOIN "DLN1" L ON L."BaseType" = 17 AND L."BaseEntry" = S."DocEntry"
                JOIN "ODLN" H ON H."DocEntry" = L."DocEntry"
                WHERE H."CANCELED" = 'N'
            )
            SELECT "Kind", "DocNum", "DocDate", "ItemCode", "Quantity", "LineTotal", "BaseDocNum"
            FROM (
                SELECT 'Delivery' AS "Kind", D."DocNum", D."DocDate", D."ItemCode", D."Quantity", D."LineTotal",
                       D."BaseDocNum"
                FROM D
                UNION ALL
                SELECT 'Invoice', H."DocNum", H."DocDate", L."ItemCode", L."Quantity", L."LineTotal", S."DocNum"
                FROM S
                JOIN "INV1" L ON L."BaseType" = 17 AND L."BaseEntry" = S."DocEntry"
                JOIN "OINV" H ON H."DocEntry" = L."DocEntry"
                WHERE H."CANCELED" = 'N'
                UNION ALL
                SELECT 'Invoice', H."DocNum", H."DocDate", L."ItemCode", L."Quantity", L."LineTotal", D."BaseDocNum"
                FROM D
                JOIN "INV1" L ON L."BaseType" = 15 AND L."BaseEntry" = D."DocEntry" AND L."BaseLine" = D."LineNum"
                JOIN "OINV" H ON H."DocEntry" = L."DocEntry"
                WHERE H."CANCELED" = 'N'
            )
            ORDER BY "DocDate", "Kind", "DocNum"
        """,
    },
    "item_info": {
        "description": "Facts about one item: group, unit, lead time, minimum stock, last purchase price, "
                       "list price and default vendor",
        "params": [("item", str, None, None, 50)],
        "max_rows": 200,
        "ttl": 120,
        # DefaultVendor: the item's preferred vendor as "code - name".
        "sql": """
            SELECT I."ItemCode", I."ItemName", G."ItmsGrpNam" AS "ItemGroup", I."InvntryUom" AS "UoM",
                   I."LeadTime" AS "LeadTimeDays", I."MinLevel" AS "MinStock",
                   I."LastPurPrc" AS "LastPurchasePrice", P."Price" AS "ListPrice",
                   CASE WHEN I."CardCode" IS NULL OR I."CardCode" = '' THEN NULL
                        ELSE I."CardCode" || ' - ' || COALESCE(V."CardName", '') END AS "DefaultVendor"
            FROM "OITM" I
            LEFT JOIN "OITB" G ON G."ItmsGrpCod" = I."ItmsGrpCod"
            LEFT JOIN "ITM1" P ON P."ItemCode" = I."ItemCode" AND P."PriceList" = 1
            LEFT JOIN "OCRD" V ON V."CardCode" = I."CardCode"
            WHERE I."ItemCode" = ?
        """,
    },
    "production_orders": {
        "description": "Production orders (open ones unless asked for all), for one item or every item: "
                       "planned and completed quantity and due date",
        "params": [("item", str, "", None, 50), ("status", str, "open", None, 4, ("open", "all"))],
        "param_map": ["item", "item", "status"],
        "max_rows": 200,
        "ttl": 60,
        # Open = Planned (P) or Released (R). Open orders come first, soonest due first; then the rest, newest first.
        "sql": """
            SELECT T0."DocNum", T0."ItemCode", T0."ProdName", T0."PlannedQty", T0."CmpltQty" AS "CompletedQty",
                   T0."DueDate",
                   CASE T0."Status" WHEN 'P' THEN 'Planned' WHEN 'R' THEN 'Released'
                                    WHEN 'L' THEN 'Closed' WHEN 'C' THEN 'Cancelled' END AS "Status",
                   T0."Warehouse"
            FROM "OWOR" T0
            WHERE (CAST(? AS NVARCHAR(50)) = '' OR T0."ItemCode" = ?)
              AND (CAST(? AS NVARCHAR(4)) = 'all' OR T0."Status" IN ('P', 'R'))
            ORDER BY CASE WHEN T0."Status" IN ('P', 'R') THEN 0 ELSE 1 END,
                     CASE WHEN T0."Status" IN ('P', 'R') THEN T0."DueDate" END,
                     T0."DueDate" DESC, T0."DocNum" DESC
        """,
    },
}
