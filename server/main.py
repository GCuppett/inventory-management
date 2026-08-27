from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders, submitted_restock_orders

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str
    lead_time_days: int

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

class RestockRecommendation(BaseModel):
    sku: str
    item_name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    forecasted_demand: int
    days_of_supply: float
    lead_time_days: int
    trend: str
    urgency_score: float
    urgency_reasons: List[str]
    recommended_quantity: int
    unit_cost: float
    recommended_cost: float
    affordable: bool

class RestockRecommendationsResponse(BaseModel):
    budget: float
    total_selected_cost: float
    remaining_budget: float
    recommendations: List[RestockRecommendation]

class CreateRestockOrderRequest(BaseModel):
    budget: float
    warehouse: Optional[str] = None
    category: Optional[str] = None

class SubmittedRestockOrderItem(BaseModel):
    sku: str
    name: str
    quantity: int
    unit_cost: float
    lead_time_days: int

class SubmittedRestockOrder(BaseModel):
    id: str
    order_number: str
    items: List[SubmittedRestockOrderItem]
    status: str
    budget: float
    total_cost: float
    order_date: str
    delivery_lead_time_days: int
    expected_delivery: str
    warehouse: Optional[str] = None
    category: Optional[str] = None

# Restocking urgency-scoring helpers

def _days_of_supply(quantity_on_hand: int, forecasted_demand: int) -> float:
    """Estimated days until stock runs out, based on the 30-day forecast window."""
    daily_demand = forecasted_demand / 30
    if daily_demand <= 0:
        return float('inf')
    return quantity_on_hand / daily_demand

def _backlog_score(sku: str):
    """Urgency boost for SKUs with an open backlog item, weighted by priority + delay."""
    priority_base = {"high": 60, "medium": 35, "low": 15}
    entry = next((b for b in backlog_items if b["item_sku"] == sku), None)
    if not entry:
        return 0.0, None
    score = min(100.0, priority_base.get(entry["priority"], 0) + 5 * entry["days_delayed"])
    return score, entry

def _lead_time_risk_score(lead_time_days: int, days_of_supply: float) -> float:
    """How likely the item is to stock out before a restock order could arrive."""
    if days_of_supply <= 0:
        return 100.0
    ratio = lead_time_days / days_of_supply
    if ratio >= 1.0:
        return 100.0
    if ratio >= 0.5:
        return (ratio - 0.5) / 0.5 * 100.0
    return 0.0

def _stockout_score(days_of_supply: float, cap_days: float = 60.0) -> float:
    if days_of_supply == float('inf'):
        return 0.0
    return max(0.0, min(1.0, (cap_days - days_of_supply) / cap_days)) * 100.0

_TREND_SCORE = {"increasing": 100.0, "stable": 50.0, "decreasing": 0.0}

_W_STOCKOUT, _W_BACKLOG, _W_LEADTIME, _W_TREND = 0.50, 0.30, 0.15, 0.05

def compute_restock_recommendations(budget: float, warehouse: Optional[str] = None,
                                     category: Optional[str] = None) -> dict:
    """Rank forecasted items by restocking urgency and greedily select what fits the budget."""
    inv_by_sku = {i["sku"]: i for i in apply_filters(inventory_items, warehouse, category)}
    candidates = []

    for f in demand_forecasts:
        item = inv_by_sku.get(f["item_sku"])
        if not item:
            continue  # no matching inventory record, or filtered out by warehouse/category

        recommended_qty = max(0, f["forecasted_demand"] - item["quantity_on_hand"])
        if recommended_qty == 0:
            continue  # already stocked to meet the forecast

        dos = _days_of_supply(item["quantity_on_hand"], f["forecasted_demand"])
        backlog_score, backlog_entry = _backlog_score(f["item_sku"])
        leadtime_score = _lead_time_risk_score(item["lead_time_days"], dos)
        stockout_score = _stockout_score(dos)
        trend_score = _TREND_SCORE.get(f["trend"], 50.0)

        urgency = (_W_STOCKOUT * stockout_score + _W_BACKLOG * backlog_score +
                   _W_LEADTIME * leadtime_score + _W_TREND * trend_score)

        reasons = [f"{dos:.0f} days of supply remaining (forecast: {f['forecasted_demand']} units / 30 days)"]
        if backlog_entry:
            reasons.append(f"{backlog_entry['priority'].title()} priority backlog, delayed {backlog_entry['days_delayed']} days")
        if leadtime_score >= 100:
            reasons.append(f"Lead time ({item['lead_time_days']}d) exceeds days of supply ({dos:.0f}d) — will stock out before restock arrives")
        elif leadtime_score > 0:
            reasons.append(f"Lead time ({item['lead_time_days']}d) is close to days of supply ({dos:.0f}d) — tight margin")
        if f["trend"] == "increasing":
            reasons.append("Demand trending upward")

        candidates.append({
            "sku": f["item_sku"],
            "item_name": f["item_name"],
            "category": item["category"],
            "warehouse": item["warehouse"],
            "quantity_on_hand": item["quantity_on_hand"],
            "forecasted_demand": f["forecasted_demand"],
            "days_of_supply": round(dos, 1),
            "lead_time_days": item["lead_time_days"],
            "trend": f["trend"],
            "urgency_score": round(urgency, 2),
            "urgency_reasons": reasons,
            "recommended_quantity": recommended_qty,
            "unit_cost": item["unit_cost"],
            "recommended_cost": round(recommended_qty * item["unit_cost"], 2),
        })

    candidates.sort(key=lambda c: c["urgency_score"], reverse=True)

    remaining = budget
    total_selected = 0.0
    for c in candidates:
        if c["recommended_cost"] <= remaining:
            c["affordable"] = True
            remaining -= c["recommended_cost"]
            total_selected += c["recommended_cost"]
        else:
            c["affordable"] = False

    return {
        "budget": budget,
        "total_selected_cost": round(total_selected, 2),
        "remaining_budget": round(remaining, 2),
        "recommendations": candidates,
    }

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/restocking/recommendations", response_model=RestockRecommendationsResponse)
def get_restock_recommendations(
    budget: float = 0.0,
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get demand-driven restock recommendations ranked by urgency, within budget"""
    return compute_restock_recommendations(budget, warehouse, category)

@app.post("/api/restocking/orders", response_model=SubmittedRestockOrder, status_code=201)
def create_restock_order(req: CreateRestockOrderRequest):
    """Submit a restocking order for whatever is affordable within the given budget"""
    if req.budget <= 0:
        raise HTTPException(status_code=400, detail="Budget must be greater than zero")

    result = compute_restock_recommendations(req.budget, req.warehouse, req.category)
    selected = [c for c in result["recommendations"] if c["affordable"]]
    if not selected:
        raise HTTPException(status_code=400, detail="No items could be recommended within this budget")

    items = [
        {
            "sku": c["sku"],
            "name": c["item_name"],
            "quantity": c["recommended_quantity"],
            "unit_cost": c["unit_cost"],
            "lead_time_days": c["lead_time_days"],
        }
        for c in selected
    ]
    delivery_lead_time_days = max(i["lead_time_days"] for i in items)
    order_date = datetime.now()

    order = {
        "id": str(len(submitted_restock_orders) + 1),
        "order_number": f"RST-2025-{len(submitted_restock_orders) + 1:04d}",
        "items": items,
        "status": "Submitted",
        "budget": req.budget,
        "total_cost": result["total_selected_cost"],
        "order_date": order_date.isoformat(),
        "delivery_lead_time_days": delivery_lead_time_days,
        "expected_delivery": (order_date + timedelta(days=delivery_lead_time_days)).isoformat(),
        "warehouse": req.warehouse if req.warehouse and req.warehouse != "all" else None,
        "category": req.category if req.category and req.category != "all" else None,
    }
    submitted_restock_orders.append(order)
    return order

@app.get("/api/restocking/orders", response_model=List[SubmittedRestockOrder])
def get_restock_orders():
    """Get all restocking orders submitted this session"""
    return submitted_restock_orders

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports():
    """Get quarterly performance reports"""
    # Calculate quarterly statistics from orders
    quarters = {}

    for order in orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends():
    """Get month-over-month trends"""
    months = {}

    for order in orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
