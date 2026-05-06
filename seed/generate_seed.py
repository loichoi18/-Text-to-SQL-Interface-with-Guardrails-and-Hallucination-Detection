"""Generate deterministic seed data for the e-commerce demo schema.

Run from repo root:
    python seed/generate_seed.py

Writes to seed/02_seed_data.sql. Deterministic via fixed random seed,
so eval golden answers stay stable.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "02_seed_data.sql"
RNG = random.Random(20260506)

COUNTRIES = ["US", "GB", "DE", "FR", "CA", "AU", "JP", "BR", "IN", "MX"]
COUNTRY_WEIGHTS = [40, 12, 10, 8, 7, 6, 5, 5, 4, 3]

FIRST_NAMES = [
    "Alex", "Bailey", "Chen", "Dakota", "Emery", "Finley", "Gray", "Harper",
    "Indi", "Jordan", "Kai", "Logan", "Morgan", "Noor", "Ollie", "Parker",
    "Quinn", "Rowan", "Sage", "Tatum", "Umi", "Vega", "Wren", "Xen",
    "Yael", "Zion", "Avery", "Blair", "Casey", "Drew",
]
LAST_NAMES = [
    "Adler", "Brooks", "Carrillo", "Dixit", "Esposito", "Fournier", "Gomez",
    "Haldar", "Ibarra", "Jansen", "Kapoor", "Liang", "Marek", "Nakagawa",
    "Okafor", "Pham", "Quintero", "Rossi", "Saito", "Tanaka", "Ueda",
    "Vasquez", "Whitlock", "Xu", "Yang", "Zaman",
]

CATEGORIES = [
    ("Electronics", None),
    ("Computers", "Electronics"),
    ("Audio", "Electronics"),
    ("Home & Kitchen", None),
    ("Cookware", "Home & Kitchen"),
    ("Small Appliances", "Home & Kitchen"),
    ("Apparel", None),
    ("Mens Apparel", "Apparel"),
    ("Womens Apparel", "Apparel"),
    ("Books", None),
]

PRODUCTS = [
    # (sku, name, category, unit_price, cost)
    ("ELEC-LAP-001", "Aurora 14 Laptop",            "Computers",         1299.00, 820.00),
    ("ELEC-LAP-002", "Aurora 16 Pro Laptop",        "Computers",         1899.00, 1180.00),
    ("ELEC-MON-001", "Lumen 27 4K Monitor",         "Computers",          399.00, 240.00),
    ("ELEC-KEY-001", "Click75 Mechanical Keyboard", "Computers",          129.00,  62.00),
    ("ELEC-MOU-001", "Glide M2 Wireless Mouse",     "Computers",           59.00,  21.00),
    ("ELEC-HDP-001", "Echo Studio Headphones",      "Audio",              249.00, 110.00),
    ("ELEC-HDP-002", "Echo Buds 2",                 "Audio",              129.00,  48.00),
    ("ELEC-SPK-001", "BoomDeck Bluetooth Speaker",  "Audio",               89.00,  31.00),
    ("ELEC-SPK-002", "BoomDeck XL Speaker",         "Audio",              179.00,  72.00),
    ("HOME-PAN-001", "ChefPro 10in Skillet",        "Cookware",            79.00,  28.00),
    ("HOME-PAN-002", "ChefPro 12in Skillet",        "Cookware",            99.00,  35.00),
    ("HOME-POT-001", "ChefPro 6qt Dutch Oven",      "Cookware",           149.00,  60.00),
    ("HOME-KNF-001", "Edge 8in Chefs Knife",        "Cookware",            59.00,  22.00),
    ("HOME-APP-001", "BrewMate Drip Coffee Maker",  "Small Appliances",    89.00,  34.00),
    ("HOME-APP-002", "BrewMate Espresso Machine",   "Small Appliances",   349.00, 165.00),
    ("HOME-APP-003", "Whisk Stand Mixer",           "Small Appliances",   299.00, 130.00),
    ("APP-MEN-001",  "Heritage Wool Sweater (M)",   "Mens Apparel",        89.00,  32.00),
    ("APP-MEN-002",  "All-Day Chinos (M)",          "Mens Apparel",        69.00,  21.00),
    ("APP-MEN-003",  "Trail Running Shoes (M)",     "Mens Apparel",       119.00,  44.00),
    ("APP-WOM-001",  "Heritage Wool Sweater (W)",   "Womens Apparel",      89.00,  32.00),
    ("APP-WOM-002",  "All-Day Chinos (W)",          "Womens Apparel",      69.00,  21.00),
    ("APP-WOM-003",  "Trail Running Shoes (W)",     "Womens Apparel",     119.00,  44.00),
    ("BOOK-001",     "The Pragmatic Programmer",    "Books",               39.00,  15.00),
    ("BOOK-002",     "Designing Data-Intensive Applications", "Books",     49.00,  19.00),
    ("BOOK-003",     "Clean Code",                  "Books",               36.00,  14.00),
    ("BOOK-004",     "The Phoenix Project",         "Books",               29.00,  11.00),
    ("BOOK-005",     "Accelerate",                  "Books",               32.00,  13.00),
    ("ELEC-CAM-001", "PixelView 4K Webcam",         "Electronics",        119.00,  46.00),
    ("ELEC-CHG-001", "PowerCore 65W Charger",       "Electronics",         49.00,  16.00),
    ("ELEC-CHG-002", "PowerCore 100W Charger",      "Electronics",         79.00,  28.00),
]

ORDER_STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled", "refunded"]
STATUS_WEIGHTS = [3, 8, 12, 65, 8, 4]

NUM_CUSTOMERS = 60
NUM_ORDERS    = 240


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def build_customers() -> list[str]:
    rows: list[str] = []
    used_emails: set[str] = set()
    start = date(2023, 1, 1)
    for i in range(1, NUM_CUSTOMERS + 1):
        first = RNG.choice(FIRST_NAMES)
        last  = RNG.choice(LAST_NAMES)
        email = f"{first.lower()}.{last.lower()}{i}@example.com"
        while email in used_emails:
            email = f"{first.lower()}.{last.lower()}{i}{RNG.randint(0,99)}@example.com"
        used_emails.add(email)
        country = RNG.choices(COUNTRIES, weights=COUNTRY_WEIGHTS)[0]
        days_offset = RNG.randint(0, 1095)  # 3 years window
        signup = start + timedelta(days=days_offset)
        is_active = RNG.random() > 0.08
        rows.append(
            f"({i}, {sql_str(email)}, {sql_str(first)}, {sql_str(last)}, "
            f"{sql_str(country)}, DATE {sql_str(signup.isoformat())}, {str(is_active).upper()})"
        )
    return rows


def build_categories() -> list[str]:
    name_to_id = {name: i + 1 for i, (name, _) in enumerate(CATEGORIES)}
    rows: list[str] = []
    for cat_id, (name, parent) in enumerate(CATEGORIES, start=1):
        parent_sql = "NULL" if parent is None else str(name_to_id[parent])
        rows.append(f"({cat_id}, {sql_str(name)}, {parent_sql})")
    return rows


def build_products() -> tuple[list[str], dict[str, int]]:
    name_to_id = {name: i + 1 for i, (name, _) in enumerate(CATEGORIES)}
    rows: list[str] = []
    sku_to_id: dict[str, int] = {}
    for pid, (sku, pname, cat, price, cost) in enumerate(PRODUCTS, start=1):
        sku_to_id[sku] = pid
        discontinued = RNG.random() < 0.07
        rows.append(
            f"({pid}, {sql_str(sku)}, {sql_str(pname)}, {name_to_id[cat]}, "
            f"{price:.2f}, {cost:.2f}, {str(discontinued).upper()})"
        )
    return rows, sku_to_id


def build_orders_and_items(num_customers: int, num_products: int):
    order_rows: list[str] = []
    item_rows: list[str] = []
    item_id = 1
    start = datetime(2024, 1, 1)
    for oid in range(1, NUM_ORDERS + 1):
        customer_id = RNG.randint(1, num_customers)
        # cluster more recent orders so date filters return varied counts
        days_offset = int(RNG.triangular(0, 760, 600))
        order_dt = start + timedelta(days=days_offset, hours=RNG.randint(0, 23), minutes=RNG.randint(0, 59))
        status = RNG.choices(ORDER_STATUSES, weights=STATUS_WEIGHTS)[0]
        country = RNG.choices(COUNTRIES, weights=COUNTRY_WEIGHTS)[0]
        order_rows.append(
            f"({oid}, {customer_id}, TIMESTAMP {sql_str(order_dt.isoformat(sep=' '))}, "
            f"{sql_str(status)}, {sql_str(country)})"
        )
        # 1-5 line items
        n_items = RNG.randint(1, 5)
        chosen: set[int] = set()
        for _ in range(n_items):
            pid = RNG.randint(1, num_products)
            if pid in chosen:
                continue
            chosen.add(pid)
            qty = RNG.randint(1, 4)
            base_price = float(PRODUCTS[pid - 1][3])
            # occasional small variance from list price
            sale_price = base_price if RNG.random() > 0.2 else round(base_price * RNG.uniform(0.85, 1.0), 2)
            discount = 0.0 if RNG.random() > 0.25 else round(sale_price * qty * RNG.uniform(0.05, 0.2), 2)
            item_rows.append(
                f"({item_id}, {oid}, {pid}, {qty}, {sale_price:.2f}, {discount:.2f})"
            )
            item_id += 1
    return order_rows, item_rows


def main() -> None:
    customers   = build_customers()
    categories  = build_categories()
    products, _ = build_products()
    orders, items = build_orders_and_items(NUM_CUSTOMERS, len(PRODUCTS))

    parts: list[str] = []
    parts.append("-- Auto-generated by seed/generate_seed.py. Do not edit by hand.")
    parts.append("BEGIN;")
    parts.append("TRUNCATE order_items, orders, products, categories, customers RESTART IDENTITY CASCADE;")
    parts.append("")

    def insert(table: str, columns: str, rows: list[str]) -> None:
        parts.append(f"INSERT INTO {table} ({columns}) VALUES")
        parts.append(",\n".join(rows) + ";")
        parts.append("")

    insert("customers",
           "customer_id, email, first_name, last_name, country, signup_date, is_active",
           customers)
    insert("categories", "category_id, name, parent_id", categories)
    insert("products",
           "product_id, sku, name, category_id, unit_price, cost, is_discontinued",
           products)
    insert("orders",
           "order_id, customer_id, order_date, status, shipping_country",
           orders)
    insert("order_items",
           "order_item_id, order_id, product_id, quantity, unit_price, discount",
           items)

    # Reset sequences so further inserts continue cleanly.
    parts.append("SELECT setval('customers_customer_id_seq',     (SELECT MAX(customer_id)     FROM customers));")
    parts.append("SELECT setval('categories_category_id_seq',    (SELECT MAX(category_id)    FROM categories));")
    parts.append("SELECT setval('products_product_id_seq',       (SELECT MAX(product_id)     FROM products));")
    parts.append("SELECT setval('orders_order_id_seq',           (SELECT MAX(order_id)       FROM orders));")
    parts.append("SELECT setval('order_items_order_item_id_seq', (SELECT MAX(order_item_id)  FROM order_items));")
    parts.append("COMMIT;")

    OUT.write_text("\n".join(parts) + "\n")
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    print(f"  customers:   {len(customers)}")
    print(f"  categories:  {len(categories)}")
    print(f"  products:    {len(products)}")
    print(f"  orders:      {len(orders)}")
    print(f"  order_items: {len(items)}")


if __name__ == "__main__":
    main()
