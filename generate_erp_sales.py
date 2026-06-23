"""
generate_erp_sales.py -- Phase 4 Demo: zweite SQL-Quelle (eigener Connection-String).

Erzeugt data/erp_sales.db mit T_SALES_ORDERS, das auf die bestehenden CUST-/MAT-IDs
referenziert. Dient als Beweis, dass der generische SQL-Connector mehrere Quellen mit
beliebigen Connection-Strings ansprechen kann (nicht nur die SAP-SQLite).
"""
import os
import sqlite3
import random

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "erp_sales.db")
random.seed(42)

STATUSES = ["OPEN", "SHIPPED", "INVOICED", "CANCELLED"]


def main():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute(
        "CREATE TABLE T_SALES_ORDERS ("
        "ORDER_ID TEXT PRIMARY KEY, CUSTOMER_ID TEXT, MAT_NR TEXT, "
        "QTY INTEGER, NET_EUR REAL, STATUS TEXT)"
    )
    rows = []
    for i in range(1, 121):
        oid = f"SO-{1000 + i}"
        cust = f"CUST-{random.randint(1, 120):03d}"
        mat = f"MAT-{random.randint(1, 100):03d}"
        qty = random.randint(1, 200)
        net = round(qty * random.uniform(10, 500), 2)
        status = random.choice(STATUSES)
        rows.append((oid, cust, mat, qty, net, status))
    cur.executemany(
        "INSERT INTO T_SALES_ORDERS VALUES (?,?,?,?,?,?)", rows
    )
    con.commit()
    n = cur.execute("SELECT COUNT(*) FROM T_SALES_ORDERS").fetchone()[0]
    print(f"erp_sales.db erstellt: {n} Auftraege")
    print("Beispiel:", cur.execute("SELECT * FROM T_SALES_ORDERS LIMIT 3").fetchall())
    con.close()


if __name__ == "__main__":
    main()
