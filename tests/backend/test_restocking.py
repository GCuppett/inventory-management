"""
Tests for the restocking recommendation and order endpoints.

Note: the `client` fixture reuses the same imported `main` module (and its
`submitted_restock_orders` list) across the whole test run, since Python only
imports a module once. POST tests therefore accumulate orders across the
session -- assertions here are relative to each test's own response (e.g.
"the list grew by one"), never an absolute count or a hardcoded order number.
"""


def test_get_recommendations_shape(client):
    response = client.get("/api/restocking/recommendations?budget=10000")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"budget", "total_selected_cost", "remaining_budget", "recommendations"}
    assert data["budget"] == 10000

    for rec in data["recommendations"]:
        assert set(rec.keys()) == {
            "sku", "item_name", "category", "warehouse", "quantity_on_hand",
            "forecasted_demand", "days_of_supply", "lead_time_days", "trend",
            "urgency_score", "urgency_reasons", "recommended_quantity",
            "unit_cost", "recommended_cost", "affordable"
        }


def test_recommendations_exclude_overstocked_items(client):
    response = client.get("/api/restocking/recommendations?budget=50000")
    data = response.json()
    skus = {rec["sku"] for rec in data["recommendations"]}
    # CTL-330 and PSU-501 are already stocked at/above their forecasted demand
    assert "CTL-330" not in skus
    assert "PSU-501" not in skus


def test_recommendations_sorted_by_urgency_desc(client):
    response = client.get("/api/restocking/recommendations?budget=50000")
    scores = [rec["urgency_score"] for rec in response.json()["recommendations"]]
    assert scores == sorted(scores, reverse=True)


def test_budget_zero_returns_no_affordable_items(client):
    response = client.get("/api/restocking/recommendations?budget=0")
    data = response.json()
    assert data["total_selected_cost"] == 0
    assert all(not rec["affordable"] for rec in data["recommendations"])


def test_higher_budget_never_decreases_affordable_count(client):
    low = client.get("/api/restocking/recommendations?budget=1000").json()
    high = client.get("/api/restocking/recommendations?budget=50000").json()
    low_count = sum(1 for r in low["recommendations"] if r["affordable"])
    high_count = sum(1 for r in high["recommendations"] if r["affordable"])
    assert high_count >= low_count


def test_create_order_requires_positive_budget(client):
    response = client.post("/api/restocking/orders", json={"budget": 0})
    assert response.status_code == 400


def test_create_order_insufficient_budget(client):
    response = client.post("/api/restocking/orders", json={"budget": 1})
    assert response.status_code == 400


def test_create_order_success(client):
    response = client.post("/api/restocking/orders", json={"budget": 50000})
    assert response.status_code == 201
    order = response.json()

    assert order["order_number"].startswith("RST-2025-")
    assert len(order["items"]) > 0
    assert order["status"] == "Submitted"
    assert order["delivery_lead_time_days"] == max(i["lead_time_days"] for i in order["items"])
    assert order["total_cost"] > 0


def test_create_order_appears_in_list(client):
    before = client.get("/api/restocking/orders").json()
    created = client.post("/api/restocking/orders", json={"budget": 25000}).json()
    after = client.get("/api/restocking/orders").json()

    assert len(after) == len(before) + 1
    assert any(o["id"] == created["id"] for o in after)
