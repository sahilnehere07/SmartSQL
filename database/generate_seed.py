"""
Generates database/seed.sql with realistic, synthetic (not real) sample data.

Run locally with:  python database/generate_seed.py
It only writes a .sql file -- it does not touch any live database.
"""
import random
from datetime import date, timedelta

random.seed(42)  # reproducible output

FIRST_NAMES = [
    "John", "John", "John",  # intentionally 3 "John"s -> used for the
                              # "Show me John's orders" ambiguity test case
    "Priya", "Rahul", "Aisha", "Vikram", "Sara", "Arjun", "Neha", "Kabir",
    "Meera", "Rohan", "Divya", "Karan", "Simran", "Aditya", "Pooja", "Yusuf",
    "Ananya", "Farhan", "Ritika", "Dev", "Lisa", "Omar", "Tanya", "Zoe",
]
LAST_NAMES = [
    "Smith", "Patel", "Shah", "Khan", "Nair", "Reddy", "Mehta", "Gupta",
    "Fernandes", "Rao", "Iyer", "Singh", "Chowdhury", "D'Souza", "Bose",
]
CITIES = ["Mumbai", "Delhi", "Bengaluru", "Pune", "Hyderabad", "Chennai", "Kolkata", "Jaipur"]

CATEGORIES = {
    "Electronics": [("Wireless Earbuds", 1999), ("Bluetooth Speaker", 2499),
                    ("Smartwatch", 4999), ("Power Bank 10000mAh", 1299),
                    ("Laptop Stand", 899)],
    "Home": [("Ceramic Mug Set", 599), ("LED Desk Lamp", 1099),
             ("Cotton Bedsheet Set", 1799), ("Storage Organizer", 699)],
    "Fashion": [("Cotton T-Shirt", 499), ("Running Shoes", 2999),
                ("Leather Wallet", 899), ("Sunglasses", 1299)],
    "Books": [("Python Programming Guide", 599), ("Sci-Fi Novel Bundle", 899)],
}

PAYMENT_METHODS = ["card", "upi", "netbanking", "wallet"]
ORDER_STATUSES = ["completed", "completed", "completed", "pending", "cancelled", "refunded"]

def esc(s):
    return s.replace("'", "''")

def random_date(start, end):
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))

START = date(2025, 10, 1)
END = date(2026, 9, 30)

lines = []

# ---- customers ----
customers = []
used_emails = set()
for i in range(1, 41):
    first = FIRST_NAMES[(i - 1) % len(FIRST_NAMES)]
    last = random.choice(LAST_NAMES)
    name = f"{first} {last}"
    email_base = f"{first.lower()}.{last.lower()}{i}@example.com"
    while email_base in used_emails:
        email_base = f"{first.lower()}.{last.lower()}{i}{random.randint(1,999)}@example.com"
    used_emails.add(email_base)
    signup = random_date(START, END)
    city = random.choice(CITIES)
    customers.append({"id": i, "name": name, "city": city, "signup": signup})
    lines.append(
        f"INSERT INTO customers (id, name, email, signup_date, city) VALUES "
        f"({i}, '{esc(name)}', '{email_base}', '{signup.isoformat()}', '{esc(city)}');"
    )

# ---- products ----
products = []
pid = 1
for category, items in CATEGORIES.items():
    for pname, price in items:
        products.append({"id": pid, "name": pname, "category": category, "price": price})
        lines.append(
            f"INSERT INTO products (id, name, category, price) VALUES "
            f"({pid}, '{esc(pname)}', '{esc(category)}', {price}.00);"
        )
        pid += 1

# ---- orders + order_items + payments ----
order_id = 1
order_item_id = 1
payment_id = 1

for c in customers:
    num_orders = random.randint(0, 6)
    for _ in range(num_orders):
        order_date = random_date(max(START, c["signup"]), END)
        status = random.choice(ORDER_STATUSES)

        num_items = random.randint(1, 4)
        chosen = random.sample(products, num_items)
        total = 0.0
        item_rows = []
        for p in chosen:
            qty = random.randint(1, 3)
            unit_price = p["price"]
            total += qty * unit_price
            item_rows.append((order_item_id, order_id, p["id"], qty, unit_price))
            order_item_id += 1

        lines.append(
            f"INSERT INTO orders (id, customer_id, order_date, status, total_amount) VALUES "
            f"({order_id}, {c['id']}, '{order_date.isoformat()}', '{status}', {total:.2f});"
        )
        for (oiid, oid, prod_id, qty, unit_price) in item_rows:
            lines.append(
                f"INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES "
                f"({oiid}, {oid}, {prod_id}, {qty}, {unit_price}.00);"
            )

        if status in ("completed", "refunded"):
            pay_status = "refunded" if status == "refunded" else "success"
            payment_date = order_date + timedelta(days=random.randint(0, 2))
            method = random.choice(PAYMENT_METHODS)
            lines.append(
                f"INSERT INTO payments (id, order_id, payment_date, amount, payment_method, status) VALUES "
                f"({payment_id}, {order_id}, '{payment_date.isoformat()}', {total:.2f}, '{method}', '{pay_status}');"
            )
            payment_id += 1
        elif status == "pending":
            pass  # no payment yet
        else:  # cancelled
            payment_date = order_date
            method = random.choice(PAYMENT_METHODS)
            lines.append(
                f"INSERT INTO payments (id, order_id, payment_date, amount, payment_method, status) VALUES "
                f"({payment_id}, {order_id}, '{payment_date.isoformat()}', {total:.2f}, '{method}', 'failed');"
            )
            payment_id += 1

        order_id += 1

# ---- fix sequences so future inserts via the app (if any) don't collide ----
lines.append("SELECT setval('customers_id_seq', (SELECT MAX(id) FROM customers));")
lines.append("SELECT setval('products_id_seq', (SELECT MAX(id) FROM products));")
lines.append("SELECT setval('orders_id_seq', (SELECT MAX(id) FROM orders));")
lines.append("SELECT setval('order_items_id_seq', (SELECT MAX(id) FROM order_items));")
lines.append("SELECT setval('payments_id_seq', (SELECT MAX(id) FROM payments));")

with open("database/seed.sql", "w") as f:
    f.write("-- Auto-generated synthetic sample data. Run AFTER schema.sql.\n")
    f.write("-- Regenerate with: python database/generate_seed.py\n\n")
    f.write("\n".join(lines))
    f.write("\n")

print(f"Wrote database/seed.sql with {len(customers)} customers, {len(products)} products, "
      f"{order_id - 1} orders, {order_item_id - 1} order_items, {payment_id - 1} payments.")
