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

# params: (name, type, default, min, max). param_map: order the values go into the ? placeholders.
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
}
