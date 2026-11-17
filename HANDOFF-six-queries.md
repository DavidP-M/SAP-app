# For the dashboard owner: the six Query Manager queries, live

The SAP bridge now serves the six queries saved in Query Manager under **Kareena - Qry** (version 10.19), each byte for
byte, as tab-delimited files in the same layout as `kpi` and `supply-demand`:

| Bridge report | Query Manager | Dashboard today |
|---|---|---|
| `sales-orders` | 1. KPI_Sales Orders _ 10.19 | Read as a KPI export |
| `sales-deliveries` | 2. KPI_Sales Deliveries_10.19 | Read as a KPI export |
| `purchase-orders` | 3. KPI_Purchase Order Lines_10.19 | Read as a KPI export |
| `po-receipts` | 4. KPI_PO Receipts GRPO_10.19 | Read as a KPI export |
| `production-kitting` | 5.KPI_Production Orders and Kitting_10.19 | Skipped: no page reads it |
| `mrp-forecast` | 6.KPI_MRP Forecast_10.19 | Skipped: no page reads it |

Checked with the dashboard's own code (`main` 2v6.7 and `main-hagkgo` 2v6.9): the four KPI files load together as one
report, every line once, the same as uploading them one by one. `kpi` and `supply-demand` are unchanged.

## 1. A decision first: which KPI query is the right one?

Live SAP reads `queries/sap_b1_hana_kpi_current_dashboard_so_po_with_lagging.sql`. The four saved queries 1–4 are not
the same data:

| | `queries/` KPI query (live today) | Saved queries 1–4 (10.19) |
|---|---|---|
| Deliveries and receipts | From Dec 1 of the year before last (for YoY / YTD) | Last 365 days only |
| Deliveries without a sales order, receipts without a PO | Left out | Included (`Delivery - No SO`, `Receipt - No PO`) |
| Down payment before delivery (bill and hold) | Not considered | On-time measured at the down payment date; `KPIStatus` / `OnTimeStatus` `Billed - Held` |
| Ship-to country | Business partner's ship-to address (`CRD1`) | The delivery's own address (`DLN12`) |
| Extra columns | none | `DeliveredQuantity`, `BillAndHold`, `DownPaymentDocNumber`, `DownPaymentDate`, `ItemOnHand`, `Buyer` (PO) |

So a board loaded live does not show the same numbers as one loaded from the six exports. The dashboard reads
`DeliveredQuantity` already; `BillAndHold`, the down payment columns, `ItemOnHand` and the `Billed - Held` status are
not read by 2v6.9.

Pick one set. If the saved queries are right, either use them for Live SAP (section 2), or update `queries/` to match
them and send the new file for the bridge's `kpi` report. If `queries/` is right, update the saved queries.

## 2. To load the four reports live

The bridge side is done. The dashboard side is ready for review on branch **`live-six-queries`** of
`spy-kareena/spyder-kpi-dashboard` (from `main-hagkgo`, all 49 test files pass). What it changes:

1. `functions/api/sap/[[path]].js`: allow the new names.
   ```js
   const REPORTS = new Set(['kpi', 'supply-demand', 'sales-orders', 'sales-deliveries', 'purchase-orders',
     'po-receipts', 'production-kitting', 'mrp-forecast']);
   ```
2. `index.html`, both places that read `kpi` (Load from SAP: `sapLiveFile('kpi', true)`; live mode:
   `liveFetch('kpi', …)`): read the four reports and pass all four files to `handleFiles`, which already merges them.
   Decide what happens when one of the four fails (all required is the simple rule), and build the live-mode
   signature from all four texts.
3. Each report is one HANA run with its own 5-minute cache on the bridge. Four reports instead of one is about four
   times the HANA work per refresh; the queries are smaller each, so measure on the bridge log (`ran report … in …s`).

## 3. Production orders and the forecast

The bridge serves both; the dashboard needs a page (or a reader) for them before they show anywhere. Columns:

- `production-kitting`: ExportDate, SourceType, DocEntry, ProductionOrder, ProductionStatus, ParentItem,
  ParentDescription, ParentPlannedQty, ParentCompletedQty, ParentOpenQty, Warehouse, StartDate, DueDate, SalesOrder,
  SalesOrderStatus, Owner, CustomerCode, Customer, ComponentLine, Component, ComponentDescription, ComponentWarehouse,
  ComponentPlannedQty, ComponentIssuedQty, ComponentRemaining, ComponentOnHand, ComponentOnOrder, IssueMethod,
  InventoryItem.
- `mrp-forecast`: ExportDate, ForecastCode, ForecastName, ItemCode, ItemDescription, Warehouse, ForecastMonth
  (`YYYY-MM`), ForecastQty.

Note: a forecast file has the word "forecast" in it, which sends it to the Logican sheet check first; it fails that
check (no month columns) and is then skipped. A new reader should be tested before that check.
