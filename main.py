import os
import re
import sqlite3
import psycopg2
from psycopg2 import pool
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
from datetime import datetime, timedelta

# ---------- 数据库连接池 ----------
DATABASE_URL = os.environ.get("DATABASE_URL")
connection_pool = None

def init_pool():
    global connection_pool
    if connection_pool is None:
        connection_pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)
    return connection_pool

def get_connection():
    if connection_pool is None:
        init_pool()
    return connection_pool.getconn()

def release_connection(conn):
    if connection_pool:
        connection_pool.putconn(conn)

# ---------- 工具函数 ----------
def find_root(inv):
    m = re.match(r'^([A-Za-z]+-\d+[A-Za-z]*)(?:[-\(（].*)?$', inv)
    if m: return m.group(1)
    return inv

def build_hierarchy(all_invoices):
    nos = set([str(n).strip() for n in all_invoices if n])
    parent_map = {}; has_parent = set()
    for inv in nos:
        root = find_root(inv)
        if root != inv:
            parent_map.setdefault(root, []).append(inv)
            has_parent.add(inv)
    virtual = set(parent_map.keys()) - nos
    result = {}
    for top in (nos - has_parent) | virtual:
        group = [top] if top in nos else []
        group.extend(sorted(parent_map.get(top, [])))
        seen = set(); uniq = []
        for x in group:
            if x not in seen: seen.add(x); uniq.append(x)
        result[top] = uniq
    return result

def get_sub_invoices(main_invoice):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT invoice_no FROM orders')
    all_nos = [r[0] for r in cursor.fetchall() if r[0]]
    release_connection(conn)
    return build_hierarchy(all_nos).get(main_invoice, [main_invoice])

def natural_key(inv):
    m = re.match(r'^([A-Za-z]+)-(\d+)(.*)$', inv)
    if m: return (m.group(1).upper(), int(m.group(2)), m.group(3))
    return (inv.upper(), 0, "")

# ---------- 初始化数据库 ----------
def init_db():
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY, invoice_no TEXT, client_code TEXT, spec TEXT,
        qty REAL, unit_price REAL, total_amount REAL, status TEXT, order_date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY, invoice_no TEXT, payer TEXT, spec TEXT,
        total_amount REAL, paid_amount REAL, unpaid_amount REAL, ready_date TEXT, status TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id SERIAL PRIMARY KEY, invoice_no TEXT, spec TEXT, qty REAL,
        client_code TEXT, warehouse_date TEXT, status TEXT, location TEXT, out_date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS shipments (
        id SERIAL PRIMARY KEY, bl_no TEXT, etd TEXT, pod TEXT, containers TEXT,
        container_no TEXT, eta TEXT, spec TEXT, net_weight REAL, invoice_no TEXT,
        telex TEXT, status TEXT, transport_company TEXT, shipping_line TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS purchase_orders (
        id SERIAL PRIMARY KEY, sales_invoice_no TEXT, supplier_code TEXT,
        supplier_invoice_no TEXT, order_date TEXT, expect_ready_date TEXT,
        actual_ready_date TEXT, spec TEXT, qty REAL, unit_price REAL,
        total_amount REAL, paid_amount REAL, balance REAL, status TEXT, notes TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS bank_accounts (
        id SERIAL PRIMARY KEY, name TEXT, currency TEXT DEFAULT 'CNY',
        balance REAL DEFAULT 0, note TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS cash_flows (
        id SERIAL PRIMARY KEY, flow_date TEXT, direction TEXT, category TEXT,
        party TEXT, amount REAL, currency TEXT DEFAULT 'CNY', account_id INTEGER,
        invoice_no TEXT, status TEXT DEFAULT 'actual', note TEXT)''')
    conn.commit()
    release_connection(conn)

init_db()

def migrate_db():
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='inventory'")
    cols = [r[0] for r in cursor.fetchall()]
    if 'out_date' not in cols:
        cursor.execute("ALTER TABLE inventory ADD COLUMN out_date TEXT")
        conn.commit()
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='orders'")
    order_cols = [r[0] for r in cursor.fetchall()]
    if 'expect_payment_date' not in order_cols:
        cursor.execute("ALTER TABLE orders ADD COLUMN expect_payment_date TEXT")
        conn.commit()
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='purchase_orders'")
    po_cols = [r[0] for r in cursor.fetchall()]
    if 'expect_payment_date' not in po_cols:
        cursor.execute("ALTER TABLE purchase_orders ADD COLUMN expect_payment_date TEXT")
        conn.commit()
    release_connection(conn)

migrate_db()

app = FastAPI()

# ---------- Pydantic 模型 ----------
class OrderItem(BaseModel):
    spec: str; qty: float; unit_price: float
class OrderCreate(BaseModel):
    invoice_no: str; client_code: str; status: str; items: List[OrderItem]
class OrderRowUpdate(BaseModel):
    invoice_no: str; client_code: str = ""; spec: str = ""; qty: float = 0.0
    unit_price: float = 0.0; status: str = ""; order_date: str = ""; expect_payment_date: str = ""
class StatusUpdate(BaseModel):
    new_status: str
class PurchaseCreate(BaseModel):
    sales_invoice_no: str; supplier_code: str; supplier_invoice_no: str = ""
    order_date: str = ""; expect_ready_date: str = ""; actual_ready_date: str = ""
    spec: str = ""; qty: float = 0.0; unit_price: float = 0.0; paid_amount: float = 0.0; notes: str = ""
class PaymentUpdate(BaseModel):
    invoice_no: str = ""; payer: str = ""; spec: str = ""
    paid_amount: float = 0.0
class InventoryUpdate(BaseModel):
    invoice_no: str = ""; spec: str = ""; qty: float = 0.0
    client_code: str = ""; warehouse_date: str = ""; status: str = ""; location: str = ""
    out_date: str = ""
class ShipmentUpdate(BaseModel):
    bl_no: str = ""; etd: str = ""; pod: str = ""; containers: str = ""; container_no: str = ""
    eta: str = ""; spec: str = ""; net_weight: float = 0.0; invoice_no: str = ""
    telex: str = ""; status: str = ""; transport_company: str = ""; shipping_line: str = ""
    auto_ship_inventory: bool = True
class PurchaseUpdate(BaseModel):
    sales_invoice_no: str = ""; supplier_code: str = ""; supplier_invoice_no: str = ""
    order_date: str = ""; expect_ready_date: str = ""; actual_ready_date: str = ""
    spec: str = ""; qty: float = 0.0; unit_price: float = 0.0; paid_amount: float = 0.0
    status: str = ""; notes: str = ""; expect_payment_date: str = ""
class BankAccount(BaseModel):
    name: str; currency: str = "CNY"; balance: float = 0.0; note: str = ""
class CashFlow(BaseModel):
    flow_date: str; direction: str; category: str; party: str = ""
    amount: float = 0.0; currency: str = "CNY"; account_id: int = 0
    invoice_no: str = ""; status: str = "actual"; note: str = ""

# ---------- API 路由 ----------
@app.get("/api/orders")
def get_orders():
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('SELECT invoice_no, client_code, spec, qty, total_amount, status, order_date FROM orders ORDER BY invoice_no, id')
    rows = cursor.fetchall()
    cursor.execute('SELECT invoice_no, SUM(paid_amount) FROM payments GROUP BY invoice_no')
    pay_map = {}
    for inv, paid in cursor.fetchall():
        pay_map[inv] = paid or 0
    release_connection(conn)

    by_inv = {}
    for r in rows:
        inv, client, spec, qty, amt, status, order_date = r
        if inv not in by_inv:
            by_inv[inv] = {"invoice_no": inv, "client_code": client, "specs": [spec], "qty": qty or 0, "total_amount": amt or 0, "status": status, "order_date": order_date or ""}
        else:
            by_inv[inv]["specs"].append(spec); by_inv[inv]["qty"] += qty or 0; by_inv[inv]["total_amount"] += amt or 0
            if order_date and (not by_inv[inv]["order_date"] or order_date < by_inv[inv]["order_date"]):
                by_inv[inv]["order_date"] = order_date

    hierarchy = build_hierarchy(by_inv.keys())
    result = []
    for main_inv, sub_list in hierarchy.items():
        main = by_inv.get(main_inv, {})
        agg_total = 0; agg_qty = 0; all_specs = []
        agg_paid = 0
        client = main.get("client_code", "") or ""
        status = main.get("status", "") or ""
        order_date = main.get("order_date", "") or ""
        for sub in sub_list:
            if sub in by_inv:
                agg_total += by_inv[sub]["total_amount"]; agg_qty += by_inv[sub]["qty"]
                all_specs.extend(by_inv[sub]["specs"])
                if not client and by_inv[sub].get("client_code"): client = by_inv[sub]["client_code"]
                if not status and by_inv[sub].get("status"): status = by_inv[sub]["status"]
                od = by_inv[sub].get("order_date", "")
                if od and (not order_date or od < order_date): order_date = od
            if sub in pay_map:
                agg_paid += pay_map[sub]
        result.append({"id": main_inv, "invoice_no": main_inv, "client_code": client,
            "specs": sorted(set(s for s in all_specs if s)), "qty": round(agg_qty, 2),
            "total_amount": round(agg_total, 2), "status": status,
            "order_date": order_date[:10] if order_date else "", "sub_count": len(sub_list),
            "received": round(agg_paid, 2), "unpaid": round(agg_total - agg_paid, 2)})
    result.sort(key=lambda x: natural_key(x["invoice_no"]))
    return result

@app.patch("/api/orders/{main_invoice}/status")
def update_main_status(main_invoice: str, u: StatusUpdate):
    subs = get_sub_invoices(main_invoice)
    if not subs: return {"error": "订单不存在"}
    conn = get_connection(); cursor = conn.cursor()
    ph = ','.join(['%s'] * len(subs))
    cursor.execute(f'UPDATE orders SET status=%s WHERE invoice_no IN ({ph})', [u.new_status] + subs)
    conn.commit(); release_connection(conn)
    return {"message": f"已将 {len(subs)} 条分批状态更新为「{u.new_status}」"}

@app.get("/api/orders/{main_invoice}/sub_orders")
def get_sub_orders(main_invoice: str):
    subs = get_sub_invoices(main_invoice)
    if not subs: return []
    conn = get_connection(); cursor = conn.cursor()
    ph = ','.join(['%s'] * len(subs))
    cursor.execute(f'SELECT id, invoice_no, client_code, spec, qty, unit_price, total_amount, status, order_date, expect_payment_date FROM orders WHERE invoice_no IN ({ph}) ORDER BY invoice_no, id', subs)
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "invoice_no": r[1], "client_code": r[2], "spec": r[3], "qty": r[4], "unit_price": r[5], "total_amount": r[6], "status": r[7], "order_date": r[8][:10] if r[8] else "", "expect_payment_date": r[9][:10] if r[9] else ""} for r in rows]

@app.put("/api/orders/{row_id}")
def update_order_row(row_id: int, u: OrderRowUpdate):
    total = u.qty * u.unit_price
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE orders SET invoice_no=%s, client_code=%s, spec=%s, qty=%s, unit_price=%s, total_amount=%s, status=%s, order_date=%s, expect_payment_date=%s WHERE id=%s',
                   (u.invoice_no, u.client_code, u.spec, u.qty, u.unit_price, total, u.status, u.order_date, u.expect_payment_date, row_id))
    conn.commit(); release_connection(conn)
    return {"message": "修改成功"}

@app.delete("/api/orders/{row_id}")
def delete_order_row(row_id: int):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM orders WHERE id=%s', (row_id,)); conn.commit(); release_connection(conn)
    return {"message": "删除成功"}

@app.post("/api/payments")
def create_payment(u: PaymentUpdate):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('INSERT INTO payments (invoice_no, payer, spec, paid_amount) VALUES (%s, %s, %s, %s)',
                   (u.invoice_no, u.payer, u.spec, u.paid_amount))
    conn.commit(); release_connection(conn); return {"message": "已新增"}
@app.put("/api/payments/{pid}")
def update_payment(pid: int, u: PaymentUpdate):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE payments SET invoice_no=%s, payer=%s, spec=%s, paid_amount=%s WHERE id=%s',
                   (u.invoice_no, u.payer, u.spec, u.paid_amount, pid))
    conn.commit(); release_connection(conn); return {"message": "已更新"}
@app.delete("/api/payments/{pid}")
def delete_payment(pid: int):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM payments WHERE id=%s', (pid,)); conn.commit(); release_connection(conn)
    return {"message": "已删除"}

@app.get("/api/all-inventory")
def get_all_inventory():
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('SELECT * FROM inventory ORDER BY invoice_no, id DESC')
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "invoice_no": r[1], "spec": r[2], "qty": r[3], "client_code": r[4], "warehouse_date": r[5], "status": r[6], "location": r[7], "out_date": r[8] if len(r) > 8 else ""} for r in rows]
@app.post("/api/inventory")
def create_inventory(u: InventoryUpdate):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('INSERT INTO inventory (invoice_no, spec, qty, client_code, warehouse_date, status, location, out_date) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                   (u.invoice_no, u.spec, u.qty, u.client_code, u.warehouse_date, u.status, u.location, u.out_date))
    conn.commit(); release_connection(conn); return {"message": "已新增"}
@app.put("/api/inventory/{iid}")
def update_inventory(iid: int, u: InventoryUpdate):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE inventory SET invoice_no=%s, spec=%s, qty=%s, client_code=%s, warehouse_date=%s, status=%s, location=%s, out_date=%s WHERE id=%s',
                   (u.invoice_no, u.spec, u.qty, u.client_code, u.warehouse_date, u.status, u.location, u.out_date, iid))
    conn.commit(); release_connection(conn); return {"message": "已更新"}
@app.delete("/api/inventory/{iid}")
def delete_inventory(iid: int):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM inventory WHERE id=%s', (iid,)); conn.commit(); release_connection(conn)
    return {"message": "已删除"}

@app.get("/api/all-shipments")
def get_all_shipments():
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('SELECT * FROM shipments ORDER BY etd DESC, invoice_no')
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "bl_no": r[1], "etd": r[2], "pod": r[3], "containers": r[4], "container_no": r[5], "eta": r[6], "spec": r[7], "net_weight": r[8], "invoice_no": r[9], "telex": r[10], "status": r[11], "transport_company": r[12], "shipping_line": r[13]} for r in rows]

@app.post("/api/shipments")
def create_shipment(u: ShipmentUpdate):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('INSERT INTO shipments (bl_no, etd, pod, containers, container_no, eta, spec, net_weight, invoice_no, telex, status, transport_company, shipping_line) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                   (u.bl_no, u.etd, u.pod, u.containers, u.container_no, u.eta, u.spec, u.net_weight, u.invoice_no, u.telex, u.status, u.transport_company, u.shipping_line))
    today = datetime.now().strftime('%Y-%m-%d')
    auto_msg = ""
    if u.invoice_no:
        cursor.execute("UPDATE inventory SET out_date=%s WHERE invoice_no=%s AND (out_date IS NULL OR out_date='')", (today, u.invoice_no))
        if cursor.rowcount > 0:
            auto_msg = f"（已自动标记 {cursor.rowcount} 条库存为出库）"
    conn.commit(); release_connection(conn)
    return {"message": "出运记录已新增" + auto_msg}

@app.put("/api/shipments/{sid}")
def update_shipment(sid: int, u: ShipmentUpdate):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE shipments SET bl_no=%s, etd=%s, pod=%s, containers=%s, container_no=%s, eta=%s, spec=%s, net_weight=%s, invoice_no=%s, telex=%s, status=%s, transport_company=%s, shipping_line=%s WHERE id=%s',
                   (u.bl_no, u.etd, u.pod, u.containers, u.container_no, u.eta, u.spec, u.net_weight, u.invoice_no, u.telex, u.status, u.transport_company, u.shipping_line, sid))
    conn.commit(); release_connection(conn); return {"message": "已更新"}
@app.delete("/api/shipments/{sid}")
def delete_shipment(sid: int):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM shipments WHERE id=%s', (sid,)); conn.commit(); release_connection(conn)
    return {"message": "已删除"}

@app.post("/api/purchases")
def create_purchase(p: PurchaseCreate):
    conn = get_connection(); cursor = conn.cursor()
    total = p.qty * p.unit_price; balance = total - p.paid_amount
    cursor.execute('INSERT INTO purchase_orders (sales_invoice_no, supplier_code, supplier_invoice_no, order_date, expect_ready_date, actual_ready_date, spec, qty, unit_price, total_amount, paid_amount, balance, status, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                   (p.sales_invoice_no, p.supplier_code, p.supplier_invoice_no, p.order_date, p.expect_ready_date, p.actual_ready_date, p.spec, p.qty, p.unit_price, total, p.paid_amount, balance, '', p.notes))
    conn.commit(); release_connection(conn); return {"message": "已新增"}
@app.put("/api/purchases/{pid}")
def update_purchase(pid: int, u: PurchaseUpdate):
    total = u.qty * u.unit_price; balance = total - u.paid_amount
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE purchase_orders SET sales_invoice_no=%s, supplier_code=%s, supplier_invoice_no=%s, order_date=%s, expect_ready_date=%s, actual_ready_date=%s, spec=%s, qty=%s, unit_price=%s, total_amount=%s, paid_amount=%s, balance=%s, status=%s, notes=%s, expect_payment_date=%s WHERE id=%s',
                   (u.sales_invoice_no, u.supplier_code, u.supplier_invoice_no, u.order_date, u.expect_ready_date, u.actual_ready_date, u.spec, u.qty, u.unit_price, total, u.paid_amount, balance, u.status, u.notes, u.expect_payment_date, pid))
    conn.commit(); release_connection(conn); return {"message": "已更新"}
@app.delete("/api/purchases/{pid}")
def delete_purchase(pid: int):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM purchase_orders WHERE id=%s', (pid,)); conn.commit(); release_connection(conn)
    return {"message": "已删除"}

@app.get("/api/bank-accounts")
def get_accounts():
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('SELECT * FROM bank_accounts ORDER BY id')
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "name": r[1], "currency": r[2], "balance": r[3], "note": r[4]} for r in rows]
@app.post("/api/bank-accounts")
def create_account(u: BankAccount):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('INSERT INTO bank_accounts (name, currency, balance, note) VALUES (%s, %s, %s, %s)',
                   (u.name, u.currency, u.balance, u.note))
    conn.commit(); release_connection(conn); return {"message": "已新增"}
@app.put("/api/bank-accounts/{aid}")
def update_account(aid: int, u: BankAccount):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE bank_accounts SET name=%s, currency=%s, balance=%s, note=%s WHERE id=%s',
                   (u.name, u.currency, u.balance, u.note, aid))
    conn.commit(); release_connection(conn); return {"message": "已更新"}
@app.delete("/api/bank-accounts/{aid}")
def delete_account(aid: int):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM bank_accounts WHERE id=%s', (aid,)); conn.commit(); release_connection(conn)
    return {"message": "已删除"}

@app.get("/api/cash-flows")
def get_cash_flows():
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('SELECT * FROM cash_flows ORDER BY flow_date DESC, id DESC')
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "flow_date": r[1], "direction": r[2], "category": r[3], "party": r[4],
             "amount": r[5], "currency": r[6], "account_id": r[7], "invoice_no": r[8], "status": r[9], "note": r[10]} for r in rows]
@app.post("/api/cash-flows")
def create_cash_flow(u: CashFlow):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('INSERT INTO cash_flows (flow_date, direction, category, party, amount, currency, account_id, invoice_no, status, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                   (u.flow_date, u.direction, u.category, u.party, u.amount, u.currency, u.account_id, u.invoice_no, u.status, u.note))
    conn.commit(); release_connection(conn); return {"message": "已新增"}
@app.put("/api/cash-flows/{cid}")
def update_cash_flow(cid: int, u: CashFlow):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE cash_flows SET flow_date=%s, direction=%s, category=%s, party=%s, amount=%s, currency=%s, account_id=%s, invoice_no=%s, status=%s, note=%s WHERE id=%s',
                   (u.flow_date, u.direction, u.category, u.party, u.amount, u.currency, u.account_id, u.invoice_no, u.status, u.note, cid))
    conn.commit(); release_connection(conn); return {"message": "已更新"}
@app.delete("/api/cash-flows/{cid}")
def delete_cash_flow(cid: int):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM cash_flows WHERE id=%s', (cid,)); conn.commit(); release_connection(conn)
    return {"message": "已删除"}

@app.get("/api/cash-forecast")
def get_cash_forecast(days: int = 60, include_pending: bool = True):
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('SELECT SUM(balance) FROM bank_accounts')
    start_balance = cursor.fetchone()[0] or 0
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = today.strftime('%Y-%m-%d')
    cursor.execute('SELECT flow_date, direction, amount FROM cash_flows WHERE flow_date >= %s', (today_str,))
    planned = cursor.fetchall()
    auto_in = {}
    auto_out = {}
    if include_pending:
        cursor.execute('SELECT DISTINCT invoice_no FROM orders')
        all_invoices = [r[0] for r in cursor.fetchall() if r[0]]
        hierarchy = build_hierarchy(all_invoices)
        for main_inv, subs in hierarchy.items():
            ph = ','.join(['%s'] * len(subs))
            cursor.execute(f'SELECT SUM(total_amount), MAX(order_date), MAX(expect_payment_date) FROM orders WHERE invoice_no IN ({ph})', subs)
            total, od, exp_date = cursor.fetchone()
            if not total: continue
            cursor.execute(f'SELECT SUM(paid_amount) FROM payments WHERE invoice_no IN ({ph})', subs)
            paid = cursor.fetchone()[0] or 0
            if total > paid:
                unpaid = total - paid
                cursor.execute(f'SELECT MAX(eta) FROM shipments WHERE invoice_no IN ({ph})', subs)
                eta = cursor.fetchone()[0]
                if exp_date:
                    target_date_str = str(exp_date)[:10]
                elif eta:
                    try:
                        d = datetime.strptime(str(eta)[:10], '%Y-%m-%d') + timedelta(days=30)
                        target_date_str = d.strftime('%Y-%m-%d')
                    except:
                        target_date_str = (today + timedelta(days=30)).strftime('%Y-%m-%d')
                elif od:
                    try:
                        d = datetime.strptime(str(od)[:10], '%Y-%m-%d') + timedelta(days=60)
                        target_date_str = d.strftime('%Y-%m-%d')
                    except:
                        target_date_str = (today + timedelta(days=60)).strftime('%Y-%m-%d')
                else:
                    target_date_str = (today + timedelta(days=30)).strftime('%Y-%m-%d')
                if target_date_str >= today_str:
                    auto_in[target_date_str] = auto_in.get(target_date_str, 0) + unpaid
        cursor.execute('SELECT id, balance, order_date, expect_ready_date, expect_payment_date FROM purchase_orders WHERE balance > 0')
        for pid, balance, od, exp_ready, exp_date in cursor.fetchall():
            if balance <= 0: continue
            if exp_date:
                target_date_str = str(exp_date)[:10]
            elif exp_ready:
                try:
                    d = datetime.strptime(str(exp_ready)[:10], '%Y-%m-%d') + timedelta(days=30)
                    target_date_str = d.strftime('%Y-%m-%d')
                except:
                    target_date_str = (today + timedelta(days=30)).strftime('%Y-%m-%d')
            elif od:
                try:
                    d = datetime.strptime(str(od)[:10], '%Y-%m-%d') + timedelta(days=30)
                    target_date_str = d.strftime('%Y-%m-%d')
                except:
                    target_date_str = (today + timedelta(days=30)).strftime('%Y-%m-%d')
            else:
                target_date_str = (today + timedelta(days=30)).strftime('%Y-%m-%d')
            if target_date_str >= today_str:
                auto_out[target_date_str] = auto_out.get(target_date_str, 0) + balance
    release_connection(conn)
    buckets = {}
    for i in range(days + 1):
        d = today + timedelta(days=i)
        key = d.strftime('%Y-%m-%d')
        buckets[key] = {"date": key, "in": 0.0, "out": 0.0, "auto_in": 0.0, "auto_out": 0.0}
    for fdate, direction, amount in planned:
        if not fdate: continue
        try:
            d = datetime.strptime(str(fdate)[:10], '%Y-%m-%d')
        except:
            continue
        if d < today: continue
        key = d.strftime('%Y-%m-%d')
        if key in buckets:
            if direction == 'in': buckets[key]["in"] += amount or 0
            else: buckets[key]["out"] += amount or 0
    for fdate, amount in auto_in.items():
        if fdate in buckets:
            buckets[fdate]["auto_in"] += amount
            buckets[fdate]["in"] += amount
    for fdate, amount in auto_out.items():
        if fdate in buckets:
            buckets[fdate]["auto_out"] += amount
            buckets[fdate]["out"] += amount
    keys = sorted(buckets.keys())
    result = []
    running = start_balance
    for k in keys:
        b = buckets[k]
        net = b["in"] - b["out"]
        running += net
        result.append({**b, "net": round(net, 2), "running_balance": round(running, 2)})
    return {"start_balance": round(start_balance, 2), "days": result}

@app.get("/api/cash-summary")
def get_cash_summary():
    conn = get_connection(); cursor = conn.cursor()
    cursor.execute('SELECT SUM(balance) FROM bank_accounts')
    total_balance = cursor.fetchone()[0] or 0
    today = datetime.now()
    month_start = today.replace(day=1).strftime('%Y-%m-%d')
    cursor.execute("SELECT direction, SUM(amount) FROM cash_flows WHERE status='actual' AND flow_date >= %s GROUP BY direction", (month_start,))
    month_data = {"in": 0, "out": 0}
    for d, amt in cursor.fetchall(): month_data[d] = amt or 0
    d30 = (today + timedelta(days=30)).strftime('%Y-%m-%d')
    td = today.strftime('%Y-%m-%d')
    cursor.execute("SELECT direction, SUM(amount) FROM cash_flows WHERE status='planned' AND flow_date >= %s AND flow_date <= %s GROUP BY direction", (td, d30))
    next30 = {"in": 0, "out": 0}
    for d, amt in cursor.fetchall(): next30[d] = amt or 0
    release_connection(conn)
    return {"total_balance": round(total_balance, 2), "month_in": round(month_data["in"], 2),
            "month_out": round(month_data["out"], 2), "next30_in": round(next30["in"], 2),
            "next30_out": round(next30["out"], 2), "next30_net": round(next30["in"] - next30["out"], 2)}

@app.get("/api/dashboard")
def get_dashboard():
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute('SELECT invoice_no, total_amount FROM orders')
        order_rows = cursor.fetchall()
        cursor.execute('SELECT SUM(paid_amount) FROM payments')
        total_received = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE out_date IS NULL OR out_date=''"); inv = cursor.fetchone() or (0,)
        cursor.execute('SELECT COUNT(*) FROM shipments'); shp = cursor.fetchone() or (0,)
        cursor.execute('SELECT SUM(paid_amount), SUM(balance), SUM(total_amount) FROM purchase_orders')
        sp = cursor.fetchone() or (0, 0, 0)
        release_connection(conn)
        by_inv = {}
        for inv_no, amt in order_rows:
            by_inv[inv_no] = by_inv.get(inv_no, 0) + (amt or 0)
        hierarchy = build_hierarchy(by_inv.keys())
        total_sales = sum(by_inv.get(s, 0) for subs in hierarchy.values() for s in subs)
        total_purchase = round(sp[2] or 0, 2)
        return {"total_sales": round(total_sales, 2), "total_purchase": total_purchase,
                "gross_profit": round(total_sales - total_purchase, 2),
                "total_received": round(total_received, 2), "total_unpaid": round(total_sales - total_received, 2),
                "total_paid_supplier": round(sp[0] or 0, 2), "total_balance_supplier": round(sp[1] or 0, 2),
                "order_count": len(hierarchy), "inventory_count": inv[0] or 0, "shipment_count": shp[0] or 0}
    except Exception as e:
        import traceback; print(traceback.format_exc())
        return {"error": str(e)}

@app.get("/api/orders/{main_invoice}/payments")
def get_order_payments(main_invoice: str):
    subs = get_sub_invoices(main_invoice)
    if not subs: return []
    conn = get_connection(); cursor = conn.cursor()
    ph = ','.join(['%s'] * len(subs))
    cursor.execute(f'SELECT id, invoice_no, payer, spec, paid_amount FROM payments WHERE invoice_no IN ({ph})', subs)
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "invoice_no": r[1], "payer": r[2], "spec": r[3], "paid_amount": r[4]} for r in rows]
@app.get("/api/orders/{main_invoice}/inventory")
def get_order_inventory(main_invoice: str):
    subs = get_sub_invoices(main_invoice)
    if not subs: return []
    conn = get_connection(); cursor = conn.cursor()
    ph = ','.join(['%s'] * len(subs))
    cursor.execute(f'SELECT * FROM inventory WHERE invoice_no IN ({ph})', subs)
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "invoice_no": r[1], "spec": r[2], "qty": r[3], "client_code": r[4], "warehouse_date": r[5], "status": r[6], "location": r[7], "out_date": r[8] if len(r) > 8 else ""} for r in rows]
@app.get("/api/orders/{main_invoice}/shipments")
def get_order_shipments(main_invoice: str):
    subs = get_sub_invoices(main_invoice)
    if not subs: return []
    conn = get_connection(); cursor = conn.cursor()
    ph = ','.join(['%s'] * len(subs))
    cursor.execute(f'SELECT * FROM shipments WHERE invoice_no IN ({ph})', subs)
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "bl_no": r[1], "etd": r[2], "pod": r[3], "containers": r[4], "container_no": r[5], "eta": r[6], "spec": r[7], "net_weight": r[8], "invoice_no": r[9], "telex": r[10], "status": r[11], "transport_company": r[12], "shipping_line": r[13]} for r in rows]
@app.get("/api/orders/{main_invoice}/purchases")
def get_order_purchases(main_invoice: str):
    subs = get_sub_invoices(main_invoice)
    if not subs: return []
    conn = get_connection(); cursor = conn.cursor()
    ph = ','.join(['%s'] * len(subs))
    cursor.execute(f'SELECT * FROM purchase_orders WHERE sales_invoice_no IN ({ph})', subs)
    rows = cursor.fetchall(); release_connection(conn)
    return [{"id": r[0], "sales_invoice_no": r[1], "supplier_code": r[2], "supplier_invoice_no": r[3], "order_date": r[4], "expect_ready_date": r[5], "actual_ready_date": r[6], "spec": r[7], "qty": r[8], "unit_price": r[9], "total_amount": r[10], "paid_amount": r[11], "balance": r[12], "status": r[13], "notes": r[14]} for r in rows]
@app.post("/api/orders")
def create_order(order: OrderCreate):
    conn = get_connection(); cursor = conn.cursor()
    for item in order.items:
        total_amount = item.qty * item.unit_price
        cursor.execute('INSERT INTO orders (invoice_no, client_code, spec, qty, unit_price, total_amount, status, order_date) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                       (order.invoice_no, order.client_code, item.spec, item.qty, item.unit_price, total_amount, order.status, ''))
    conn.commit(); release_connection(conn); return {"message": "订单创建成功"}

# ---------- 前端页面（原样保留，未改动） ----------
@app.get("/", response_class=HTMLResponse)
def read_root():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Tinwest ERP</title>
        <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
        <style>
            body { font-family: 'Microsoft YaHei', Arial, sans-serif; padding: 0; background-color: #f5f7fa; margin: 0; }
            .topbar { background: #1e3c72; color: white; padding: 0 20px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 2px 8px rgba(0,0,0,0.1);}
            .topbar h1 { font-size: 20px; margin: 0; padding: 15px 0; }
            .nav { display: flex; gap: 5px; }
            .nav button { background: transparent; color: rgba(255,255,255,0.7); border: none; padding: 18px 22px; cursor: pointer; font-size: 15px;}
            .nav button:hover { background: rgba(255,255,255,0.1); color: white;}
            .nav button.active { background: #409eff; color: white; font-weight: bold;}
            .main { padding: 20px; }
            h2 { color: #333; }
            .dashboard { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 25px; }
            .card { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
            .card-title { color: #666; font-size: 14px; margin-bottom: 8px; }
            .card-value { font-size: 24px; font-weight: bold; }
            .card-blue .card-value { color: #409eff; }
            .card-green .card-value { color: #67c23a; }
            .card-orange .card-value { color: #e6a23c; }
            .card-red .card-value { color: #f56c6c; }
            .card-purple .card-value { color: #9c27b0; }
            .card-dark { background: linear-gradient(135deg, #1e3c72, #2a5298); color: white; }
            .card-dark .card-title { color: rgba(255,255,255,0.8); }
            .card-dark .card-value { color: white; }
            .toolbar { margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;}
            .toolbar-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;}
            input, select { padding: 7px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; }
            .search-input { width: 220px; }
            .btn { padding: 7px 14px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
            .btn-quick { background: #f0f2f5; color: #333; border: 1px solid #dcdfe6;}
            .btn-quick:hover { background: #e4e7ed; }
            .btn-quick.active { background: #409eff; color: white; border-color: #409eff; }
            .btn-add { background: #67c23a; color: white; }
            table { border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
            th, td { border: 1px solid #e0e0e0; padding: 8px 10px; text-align: left; }
            th { background-color: #409eff; color: #fff; user-select: none; white-space: nowrap; }
            th.sortable { cursor: pointer; }
            th.sortable:hover { background-color: #66b1ff; }
            th .arrow { display: inline-block; margin-left: 4px; opacity: 0.4; font-size: 11px; }
            th .arrow.active { opacity: 1; }
            tr { cursor: pointer; }
            tr:hover { background-color: #f0f9eb; }
            .badge { background: #f56c6c; color: white; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
            .modal-mask { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 999; }
            .modal-content { background: white; padding: 25px; border-radius: 8px; width: 1100px; max-width: 96%; max-height: 90vh; overflow-y: auto; box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
            .modal-content h2 { margin-top: 0; color: #409eff; border-bottom: 1px solid #eee; padding-bottom: 10px; }
            .modal-item { margin: 10px 0; font-size: 16px; }
            .modal-item label { font-weight: bold; display: inline-block; width: 110px; color: #666; }
            .btn-close { float: right; background: #f56c6c; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
            .btn-edit { background: #409eff; color: white; border: none; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; margin-right: 4px;}
            .btn-del { background: #f56c6c; color: white; border: none; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px;}
            .btn-save { background: #67c23a; color: white; border: none; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; margin-right: 4px;}
            .btn-cancel { background: #909399; color: white; border: none; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px;}
            .status-flow { margin: 15px 0; display: flex; gap: 8px; flex-wrap: wrap;}
            .status-flow button { padding: 8px 16px; border: 1px solid #409eff; background: white; color: #409eff; border-radius: 4px; cursor: pointer; font-size: 14px; }
            .status-flow button:hover { background: #409eff; color: white; }
            .status-flow button.current { background: #409eff; color: white; font-weight: bold; }
            .section { margin-top: 20px; border-top: 2px dashed #eee; padding-top: 15px; }
            .section h3 { margin-bottom: 10px; display: flex; align-items: center; }
            .data-table { width: 100%; font-size: 14px; margin-top: 10px; border-collapse: collapse; }
            .data-table th, .data-table td { padding: 8px; text-align: left; }
            .data-table input { width: 100%; border: 1px solid #409eff; border-radius: 3px; padding: 4px; box-sizing: border-box; font-size: 13px;}
            .payment-table th { background-color: #67c23a; color: white; }
            .inventory-table th { background-color: #e6a23c; color: white; }
            .shipment-table th { background-color: #409eff; color: white; }
            .purchase-table th { background-color: #f56c6c; color: white; }
            .suborder-table th { background-color: #9c27b0; color: white; }
            .profit-box { background: #f0f9eb; border: 1px solid #67c23a; border-radius: 6px; padding: 15px; margin-top: 15px; display: flex; gap: 30px; flex-wrap: wrap; }
            .profit-box div { font-size: 15px; }
            .profit-box label { font-weight: bold; color: #666; }
            .profit-value { color: #67c23a; font-weight: bold; font-size: 18px; }
            .form-group { margin-bottom: 15px; }
            .form-group label { display: block; font-weight: bold; margin-bottom: 5px; color: #333; }
            .form-group input, .form-group select { width: 100%; padding: 8px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
            .btn-submit { background: #409eff; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 15px; }
            .btn-add-sm { background: #67c23a; color: white; border: none; padding: 5px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; margin-left: 10px;}
            .btn-add-sm.orange { background: #e6a23c; }
            .btn-add-sm.blue { background: #409eff; }
            .btn-add-spec { background: #e6a23c; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; margin-bottom: 10px;}
            .btn-del-spec { background: #f56c6c; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }
            .spec-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
            .spec-table th { background: #909399; color: white; padding: 6px; }
            .spec-table td { border: 1px solid #e0e0e0; padding: 6px; }
            .spec-table input { width: 100%; border: none; padding: 6px; }
            .total-row { text-align: right; font-weight: bold; font-size: 16px; margin-top: 10px; color: #f56c6c; }
            .hint { color: #999; font-size: 13px; }
            .in-row { color: #67c23a; font-weight: bold; }
            .out-row { color: #f56c6c; font-weight: bold; }
            .forecast-table td { font-size: 13px; padding: 6px 10px;}
            .forecast-table tr.today-row { background: #fffbe6; }
            .forecast-table tr.has-flow { background: #f0f9eb; }
            .neg { color: #f56c6c; font-weight: bold; background: #fef0f0; }
            .forecast-scroll { max-height: 600px; overflow-y: auto; }
            .orders-table { table-layout: fixed; }
            .orders-table th, .orders-table td { overflow: hidden; text-overflow: ellipsis; }
            .orders-table .col-inv { width: 110px; }
            .orders-table .col-date { width: 100px; }
            .orders-table .col-client { width: 80px; }
            .orders-table .col-spec { width: 240px; }
            .orders-table .col-qty { width: 90px; text-align: right;}
            .orders-table .col-amt { width: 130px; text-align: right;}
            .orders-table .col-paid { width: 130px; text-align: right;}
            .orders-table .col-unpaid { width: 130px; text-align: right;}
            .orders-table .col-sub { width: 70px; text-align: center;}
            .orders-table .col-status { width: 90px; }
            .orders-table td.col-qty, .orders-table td.col-amt, .orders-table td.col-paid, .orders-table td.col-unpaid { font-variant-numeric: tabular-nums; }
            .orders-table .cell-wrap { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
            .shipped-row { background: #f5f5f5; color: #999; }
        </style>
    </head>
    <body>
        <div id="app">
            <div class="topbar">
                <h1>🏭 Tinwest ERP</h1>
                <div class="nav">
                    <button :class="{active: activeTab==='orders'}" @click="activeTab='orders'">📋 订单</button>
                    <button :class="{active: activeTab==='inventory'}" @click="switchTab('inventory')">📦 库存</button>
                    <button :class="{active: activeTab==='shipments'}" @click="switchTab('shipments')">🚢 发货</button>
                    <button :class="{active: activeTab==='cash'}" @click="switchTab('cash')">💰 现金流</button>
                </div>
            </div>

            <div class="main">
                <div v-show="activeTab==='orders'">
                    <h2>经营看板</h2>
                    <div class="dashboard">
                        <div class="card card-blue"><div class="card-title">主订单数</div><div class="card-value">{{ dashboard.order_count || 0 }}</div></div>
                        <div class="card card-dark"><div class="card-title">订单总额（客户）</div><div class="card-value">¥ {{ formatMoney(dashboard.total_sales) }}</div></div>
                        <div class="card card-orange"><div class="card-title">采购总额（工厂）</div><div class="card-value">¥ {{ formatMoney(dashboard.total_purchase) }}</div></div>
                        <div class="card card-green"><div class="card-title">预估毛利</div><div class="card-value">¥ {{ formatMoney(dashboard.gross_profit) }}</div></div>
                        <div class="card card-green"><div class="card-title">已收客户款</div><div class="card-value">¥ {{ formatMoney(dashboard.total_received) }}</div></div>
                        <div class="card card-red"><div class="card-title">客户欠款</div><div class="card-value">¥ {{ formatMoney(dashboard.total_unpaid) }}</div></div>
                        <div class="card card-purple"><div class="card-title">已付供应商</div><div class="card-value">¥ {{ formatMoney(dashboard.total_paid_supplier) }}</div></div>
                        <div class="card card-red"><div class="card-title">欠供应商</div><div class="card-value">¥ {{ formatMoney(dashboard.total_balance_supplier) }}</div></div>
                    </div>
                    <h2 style="font-size: 20px;">订单列表（主订单）</h2>
                    <div class="toolbar">
                        <div class="toolbar-left">
                            <input v-model="searchQuery" class="search-input" placeholder="发票号 / 客户 / 状态">
                            <select v-model="filterClient"><option value="">全部客户</option><option v-for="c in clientOptions" :key="c" :value="c">{{ c }}</option></select>
                            <select v-model="filterStatus"><option value="">全部状态</option><option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option></select>
                            <input type="date" v-model="startDate"><span>~</span><input type="date" v-model="endDate">
                            <button class="btn btn-quick" :class="{active: quickRange==='thisMonth'}" @click="setQuickRange('thisMonth')">本月</button>
                            <button class="btn btn-quick" :class="{active: quickRange==='lastMonth'}" @click="setQuickRange('lastMonth')">上月</button>
                            <button class="btn btn-quick" :class="{active: quickRange==='thisQuarter'}" @click="setQuickRange('thisQuarter')">本季度</button>
                            <button class="btn btn-quick" @click="clearFilters">清空</button>
                        </div>
                        <button class="btn btn-add" @click="openAddModal">➕ 新增订单</button>
                    </div>
                    <div class="hint" style="margin-bottom:10px;">筛选结果：{{ filteredOrders.length }} 张 / 共 {{ orders.length }} 张</div>
                    <table class="orders-table">
                        <thead>
                            <tr>
                                <th class="col-inv sortable" @click="sortBy('invoice_no')">主发票号<span class="arrow" :class="{active: sortKey==='invoice_no'}">{{ sortKey==='invoice_no' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="col-date sortable" @click="sortBy('order_date')">下单日期<span class="arrow" :class="{active: sortKey==='order_date'}">{{ sortKey==='order_date' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="col-client sortable" @click="sortBy('client_code')">客户<span class="arrow" :class="{active: sortKey==='client_code'}">{{ sortKey==='client_code' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="col-spec">规格</th>
                                <th class="col-qty sortable" @click="sortBy('qty')">总数量<span class="arrow" :class="{active: sortKey==='qty'}">{{ sortKey==='qty' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="col-amt sortable" @click="sortBy('total_amount')">总金额<span class="arrow" :class="{active: sortKey==='total_amount'}">{{ sortKey==='total_amount' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="col-paid sortable" @click="sortBy('received')">已收<span class="arrow" :class="{active: sortKey==='received'}">{{ sortKey==='received' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="col-unpaid sortable" @click="sortBy('unpaid')">未收<span class="arrow" :class="{active: sortKey==='unpaid'}">{{ sortKey==='unpaid' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="col-sub sortable" @click="sortBy('sub_count')">分批<span class="arrow" :class="{active: sortKey==='sub_count'}">{{ sortKey==='sub_count' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="col-status sortable" @click="sortBy('status')">状态<span class="arrow" :class="{active: sortKey==='status'}">{{ sortKey==='status' ? (sortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="order in filteredOrders" :key="order.id" @click="showDetail(order)">
                                <td class="col-inv" style="color: #409eff; font-weight: bold;">{{ order.invoice_no }}</td>
                                <td class="col-date">{{ order.order_date }}</td>
                                <td class="col-client">{{ order.client_code }}</td>
                                <td class="col-spec"><span class="cell-wrap" :title="order.specs.join(' / ')">{{ order.specs.join(' / ') }}</span></td>
                                <td class="col-qty">{{ order.qty }}</td>
                                <td class="col-amt">{{ formatMoney(order.total_amount) }}</td>
                                <td class="col-paid" style="color:#67c23a;">{{ formatMoney(order.received) }}</td>
                                <td class="col-unpaid" :style="{color: order.unpaid > 0 ? '#f56c6c' : '#999'}">{{ formatMoney(order.unpaid) }}</td>
                                <td class="col-sub"><span class="badge">{{ order.sub_count }} 批</span></td>
                                <td class="col-status">{{ order.status }}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <div v-show="activeTab==='inventory'">
                    <h2>📦 全部库存</h2>
                    <div class="toolbar">
                        <div class="toolbar-left">
                            <input v-model="inventoryQuery" class="search-input" placeholder="发票号 / 客户 / 规格">
                            <label style="display:flex; align-items:center; gap:5px; font-size:13px;">
                                <input type="checkbox" v-model="showShippedInventory"> 显示已出库
                            </label>
                            <span class="hint">共 {{ filteredInventory.length }} 条 / 在库 {{ inStockCount }} 条</span>
                        </div>
                        <button class="btn btn-add" @click="openStandaloneAdd('inventory')">➕ 新增库存</button>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th class="sortable" @click="invSortBy('invoice_no')">发票号<span class="arrow" :class="{active: invSortKey==='invoice_no'}">{{ invSortKey==='invoice_no' ? (invSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="invSortBy('spec')">规格<span class="arrow" :class="{active: invSortKey==='spec'}">{{ invSortKey==='spec' ? (invSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="invSortBy('qty')">数量<span class="arrow" :class="{active: invSortKey==='qty'}">{{ invSortKey==='qty' ? (invSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="invSortBy('client_code')">客户<span class="arrow" :class="{active: invSortKey==='client_code'}">{{ invSortKey==='client_code' ? (invSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="invSortBy('warehouse_date')">入库时间<span class="arrow" :class="{active: invSortKey==='warehouse_date'}">{{ invSortKey==='warehouse_date' ? (invSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th>在库天数</th>
                                <th>出库日期</th>
                                <th class="sortable" @click="invSortBy('status')">状态<span class="arrow" :class="{active: invSortKey==='status'}">{{ invSortKey==='status' ? (invSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="invSortBy('location')">位置<span class="arrow" :class="{active: invSortKey==='location'}">{{ invSortKey==='location' ? (invSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th style="width:140px;">操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-if="isStandaloneAdding('inventory')">
                                <td><input v-model="standaloneAdd.invoice_no"></td><td><input v-model="standaloneAdd.spec"></td>
                                <td><input type="number" v-model.number="standaloneAdd.qty"></td><td><input v-model="standaloneAdd.client_code"></td>
                                <td><input v-model="standaloneAdd.warehouse_date"></td><td>-</td><td>-</td>
                                <td><input v-model="standaloneAdd.status"></td><td><input v-model="standaloneAdd.location"></td>
                                <td style="text-align: center;"><button class="btn-save" @click="saveStandaloneAdd('inventory')">保存</button><button class="btn-cancel" @click="cancelStandaloneAdd">取消</button></td>
                            </tr>
                            <tr v-for="i in filteredInventory" :key="i.id" :class="{'shipped-row': i.out_date}">
                                <template v-if="isEditing('inventory', i.id)">
                                    <td><input v-model="editData.invoice_no"></td><td><input v-model="editData.spec"></td>
                                    <td><input type="number" v-model.number="editData.qty"></td><td><input v-model="editData.client_code"></td>
                                    <td><input v-model="editData.warehouse_date"></td><td>{{ daysInStock(i.warehouse_date, i.out_date) }}</td>
                                    <td><input v-model="editData.out_date" placeholder="YYYY-MM-DD 或留空"></td>
                                    <td><input v-model="editData.status"></td><td><input v-model="editData.location"></td>
                                    <td style="text-align: center;"><button class="btn-save" @click="saveEdit('inventory', i.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                </template>
                                <template v-else>
                                    <td style="color:#409eff; font-weight:bold;">{{ i.invoice_no }}</td><td>{{ i.spec }}</td><td>{{ i.qty }}</td>
                                    <td>{{ i.client_code }}</td><td>{{ i.warehouse_date }}</td>
                                    <td :style="{color: (daysInStock(i.warehouse_date, i.out_date) !== '-' && daysInStock(i.warehouse_date, i.out_date) > 90) ? '#f56c6c' : '#333', fontWeight: (daysInStock(i.warehouse_date, i.out_date) !== '-' && daysInStock(i.warehouse_date, i.out_date) > 90) ? 'bold' : 'normal'}">{{ daysInStock(i.warehouse_date, i.out_date) }}{{ daysInStock(i.warehouse_date, i.out_date) !== '-' ? ' 天' : '' }}</td>
                                    <td style="color:#999;">{{ i.out_date || '-' }}</td>
                                    <td>{{ i.status }}</td><td>{{ i.location }}</td>
                                    <td style="text-align: center;">
                                        <button class="btn-edit" @click="startEdit('inventory', i)">编辑</button>
                                        <button class="btn-del" @click="deleteRow('inventory', i.id)">删除</button>
                                    </td>
                                </template>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <div v-show="activeTab==='shipments'">
                    <h2>🚢 全部发货记录</h2>
                    <div class="toolbar">
                        <div class="toolbar-left">
                            <input v-model="shipmentQuery" class="search-input" placeholder="发票号 / 提单号 / 目的港">
                            <span class="hint">共 {{ filteredShipments.length }} 条</span>
                        </div>
                        <button class="btn btn-add" @click="openStandaloneAdd('shipments')">➕ 新增发货</button>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th class="sortable" @click="shipSortBy('invoice_no')">发票号<span class="arrow" :class="{active: shipSortKey==='invoice_no'}">{{ shipSortKey==='invoice_no' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="shipSortBy('bl_no')">提单号<span class="arrow" :class="{active: shipSortKey==='bl_no'}">{{ shipSortKey==='bl_no' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="shipSortBy('etd')">ETD<span class="arrow" :class="{active: shipSortKey==='etd'}">{{ shipSortKey==='etd' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="shipSortBy('eta')">ETA<span class="arrow" :class="{active: shipSortKey==='eta'}">{{ shipSortKey==='eta' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="shipSortBy('pod')">目的港<span class="arrow" :class="{active: shipSortKey==='pod'}">{{ shipSortKey==='pod' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="shipSortBy('containers')">柜数<span class="arrow" :class="{active: shipSortKey==='containers'}">{{ shipSortKey==='containers' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th>箱号</th><th>规格</th>
                                <th class="sortable" @click="shipSortBy('net_weight')">净重<span class="arrow" :class="{active: shipSortKey==='net_weight'}">{{ shipSortKey==='net_weight' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="shipSortBy('shipping_line')">船公司<span class="arrow" :class="{active: shipSortKey==='shipping_line'}">{{ shipSortKey==='shipping_line' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th class="sortable" @click="shipSortBy('status')">状态<span class="arrow" :class="{active: shipSortKey==='status'}">{{ shipSortKey==='status' ? (shipSortOrder==='asc' ? '▲' : '▼') : '⇅' }}</span></th>
                                <th style="width:140px;">操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-if="isStandaloneAdding('shipments')">
                                <td><input v-model="standaloneAdd.invoice_no"></td><td><input v-model="standaloneAdd.bl_no"></td>
                                <td><input v-model="standaloneAdd.etd"></td><td><input v-model="standaloneAdd.eta"></td>
                                <td><input v-model="standaloneAdd.pod"></td><td><input v-model="standaloneAdd.containers"></td>
                                <td><input v-model="standaloneAdd.container_no"></td><td><input v-model="standaloneAdd.spec"></td>
                                <td><input type="number" v-model.number="standaloneAdd.net_weight"></td><td><input v-model="standaloneAdd.shipping_line"></td>
                                <td><input v-model="standaloneAdd.status"></td>
                                <td style="text-align: center;"><button class="btn-save" @click="saveStandaloneAdd('shipments')">保存</button><button class="btn-cancel" @click="cancelStandaloneAdd">取消</button></td>
                            </tr>
                            <tr v-for="s in filteredShipments" :key="s.id">
                                <template v-if="isEditing('shipments', s.id)">
                                    <td><input v-model="editData.invoice_no"></td><td><input v-model="editData.bl_no"></td>
                                    <td><input v-model="editData.etd"></td><td><input v-model="editData.eta"></td>
                                    <td><input v-model="editData.pod"></td><td><input v-model="editData.containers"></td>
                                    <td><input v-model="editData.container_no"></td><td><input v-model="editData.spec"></td>
                                    <td><input type="number" v-model.number="editData.net_weight"></td><td><input v-model="editData.shipping_line"></td>
                                    <td><input v-model="editData.status"></td>
                                    <td style="text-align: center;"><button class="btn-save" @click="saveEdit('shipments', s.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                </template>
                                <template v-else>
                                    <td style="color:#409eff; font-weight:bold;">{{ s.invoice_no }}</td><td>{{ s.bl_no }}</td>
                                    <td>{{ s.etd }}</td><td>{{ s.eta }}</td><td>{{ s.pod }}</td><td>{{ s.containers }}</td>
                                    <td>{{ s.container_no }}</td><td>{{ s.spec }}</td><td>{{ s.net_weight }}</td>
                                    <td>{{ s.shipping_line }}</td><td>{{ s.status }}</td>
                                    <td style="text-align: center;"><button class="btn-edit" @click="startEdit('shipments', s)">编辑</button><button class="btn-del" @click="deleteRow('shipments', s.id)">删除</button></td>
                                </template>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <div v-show="activeTab==='cash'">
                    <h2>💰 现金流预测</h2>
                    <div class="dashboard">
                        <div class="card card-dark"><div class="card-title">账上总余额</div><div class="card-value">¥ {{ formatMoney(cashSummary.total_balance) }}</div></div>
                        <div class="card card-green"><div class="card-title">本月已收</div><div class="card-value">¥ {{ formatMoney(cashSummary.month_in) }}</div></div>
                        <div class="card card-red"><div class="card-title">本月已付</div><div class="card-value">¥ {{ formatMoney(cashSummary.month_out) }}</div></div>
                        <div class="card" :class="cashSummary.next30_net >= 0 ? 'card-green' : 'card-red'">
                            <div class="card-title">未来30天净流入</div>
                            <div class="card-value">¥ {{ formatMoney(cashSummary.next30_net) }}</div>
                        </div>
                    </div>

                    <div class="toolbar">
                        <div class="toolbar-left">
                            <button class="btn btn-quick" :class="{active: cashSubTab==='forecast'}" @click="cashSubTab='forecast'">📈 未来预测</button>
                            <button class="btn btn-quick" :class="{active: cashSubTab==='flows'}" @click="cashSubTab='flows'">📝 流水明细</button>
                            <button class="btn btn-quick" :class="{active: cashSubTab==='accounts'}" @click="cashSubTab='accounts'">🏦 账户</button>
                        </div>
                        <div v-if="cashSubTab==='forecast'" class="toolbar-left">
                            <span class="hint">显示天数：</span>
                            <button class="btn btn-quick" :class="{active: forecastDays===30}" @click="setForecastDays(30)">30天</button>
                            <button class="btn btn-quick" :class="{active: forecastDays===60}" @click="setForecastDays(60)">60天</button>
                            <button class="btn btn-quick" :class="{active: forecastDays===90}" @click="setForecastDays(90)">90天</button>
                            <button class="btn btn-quick" :class="{active: forecastDays===180}" @click="setForecastDays(180)">180天</button>
                            <label style="margin-left:12px; display:flex; align-items:center; gap:5px; font-size:13px;">
                                <input type="checkbox" v-model="hideEmptyDays"> 只看有收付的日期
                            </label>
                        </div>
                        <div v-if="cashSubTab==='flows'">
                            <select v-model="flowFilter">
                                <option value="">全部</option><option value="actual">已发生</option><option value="planned">计划中</option>
                            </select>
                            <button class="btn btn-add" style="margin-left:8px;" @click="openFlowAdd">➕ 新增流水</button>
                        </div>
                        <div v-if="cashSubTab==='accounts'">
                            <button class="btn btn-add" @click="openAccountAdd">➕ 新增账户</button>
                        </div>
                    </div>

                    <div v-if="cashSubTab==='forecast'">
                        <div class="hint" style="margin-bottom:10px; display:flex; align-items:center; gap:20px;">
                            <div>起始余额：<strong>¥ {{ formatMoney(forecast.start_balance) }}</strong></div>
                            <label style="display:flex; align-items:center; gap:5px; cursor:pointer;">
                                <input type="checkbox" v-model="includePendingForecast" @change="loadForecast()">
                                包含未收客户款 & 欠供应商尾款
                            </label>
                            <div style="color:#e6a23c;">🟡 带 * 的为系统自动预测</div>
                        </div>
                        <div class="forecast-scroll">
                            <table class="forecast-table">
                                <thead>
                                    <tr>
                                        <th>日期</th><th>星期</th>
                                        <th style="background:#67c23a;">预计收款</th>
                                        <th style="background:#f56c6c;">预计付款</th>
                                        <th>净流入</th>
                                        <th>期末余额</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <tr v-for="w in displayWeeks" :key="w.date" :class="{'today-row': isToday(w.date), 'has-flow': w.in > 0 || w.out > 0}">
                                        <td><strong>{{ w.date }}</strong></td>
                                        <td style="color:#666;">{{ getWeekday(w.date) }}</td>
                                        <td class="in-row">
                                            <span v-if="w.auto_in > 0" style="color:#e6a23c; font-size:12px;">*{{ formatMoney(w.auto_in) }} </span>
                                            {{ w.in > 0 ? '+'+formatMoney(w.in) : '-' }}
                                        </td>
                                        <td class="out-row">
                                            <span v-if="w.auto_out > 0" style="color:#e6a23c; font-size:12px;">*{{ formatMoney(w.auto_out) }} </span>
                                            {{ w.out > 0 ? '-'-formatMoney(w.out) : '-' }}
                                        </td>
                                        <td :class="w.net !== 0 ? (w.net > 0 ? 'in-row' : 'out-row') : ''">{{ w.net !== 0 ? formatMoney(w.net) : '-' }}</td>
                                        <td :class="w.running_balance < 0 ? 'neg' : ''" style="font-weight:bold;">¥ {{ formatMoney(w.running_balance) }}</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div v-if="cashSubTab==='flows'">
                        <table>
                            <thead>
                                <tr><th>日期</th><th>方向</th><th>类别</th><th>交易对象</th><th>金额</th><th>发票号</th><th>状态</th><th>备注</th><th style="width:140px;">操作</th></tr>
                            </thead>
                            <tbody>
                                <tr v-if="addingFlow">
                                    <td><input v-model="newFlow.flow_date" placeholder="2026-01-01"></td>
                                    <td><select v-model="newFlow.direction"><option value="in">收款</option><option value="out">付款</option></select></td>
                                    <td><input v-model="newFlow.category" placeholder="client_payment 等"></td>
                                    <td><input v-model="newFlow.party"></td>
                                    <td><input type="number" v-model.number="newFlow.amount"></td>
                                    <td><input v-model="newFlow.invoice_no"></td>
                                    <td><select v-model="newFlow.status"><option value="actual">已发生</option><option value="planned">计划中</option></select></td>
                                    <td><input v-model="newFlow.note"></td>
                                    <td style="text-align: center;"><button class="btn-save" @click="saveFlowAdd">保存</button><button class="btn-cancel" @click="addingFlow=false">取消</button></td>
                                </tr>
                                <tr v-for="f in filteredFlows" :key="f.id">
                                    <template v-if="isEditing('cash', f.id)">
                                        <td><input v-model="editData.flow_date"></td>
                                        <td><select v-model="editData.direction"><option value="in">收款</option><option value="out">付款</option></select></td>
                                        <td><input v-model="editData.category"></td>
                                        <td><input v-model="editData.party"></td>
                                        <td><input type="number" v-model.number="editData.amount"></td>
                                        <td><input v-model="editData.invoice_no"></td>
                                        <td><select v-model="editData.status"><option value="actual">已发生</option><option value="planned">计划中</option></select></td>
                                        <td><input v-model="editData.note"></td>
                                        <td style="text-align: center;"><button class="btn-save" @click="saveEdit('cash', f.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                    </template>
                                    <template v-else>
                                        <td>{{ f.flow_date }}</td>
                                        <td :class="f.direction==='in' ? 'in-row' : 'out-row'">{{ f.direction==='in' ? '收款' : '付款' }}</td>
                                        <td>{{ f.category }}</td><td>{{ f.party }}</td>
                                        <td :class="f.direction==='in' ? 'in-row' : 'out-row'">{{ formatMoney(f.amount) }}</td>
                                        <td>{{ f.invoice_no }}</td>
                                        <td><span :style="{padding:'2px 8px', borderRadius:'10px', fontSize:'12px', color:'white', background: f.status==='actual' ? '#67c23a' : '#e6a23c'}">{{ f.status==='actual' ? '已发生' : '计划中' }}</span></td>
                                        <td>{{ f.note }}</td>
                                        <td style="text-align: center;"><button class="btn-edit" @click="startEdit('cash', f)">编辑</button><button class="btn-del" @click="deleteRow('cash', f.id)">删除</button></td>
                                    </template>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <div v-if="cashSubTab==='accounts'">
                        <table>
                            <thead><tr><th>账户名</th><th>币种</th><th>余额</th><th>备注</th><th style="width:140px;">操作</th></tr></thead>
                            <tbody>
                                <tr v-if="addingAccount">
                                    <td><input v-model="newAccount.name"></td>
                                    <td><input v-model="newAccount.currency"></td>
                                    <td><input type="number" v-model.number="newAccount.balance"></td>
                                    <td><input v-model="newAccount.note"></td>
                                    <td style="text-align: center;"><button class="btn-save" @click="saveAccountAdd">保存</button><button class="btn-cancel" @click="addingAccount=false">取消</button></td>
                                </tr>
                                <tr v-for="a in accounts" :key="a.id">
                                    <template v-if="isEditing('account', a.id)">
                                        <td><input v-model="editData.name"></td>
                                        <td><input v-model="editData.currency"></td>
                                        <td><input type="number" v-model.number="editData.balance"></td>
                                        <td><input v-model="editData.note"></td>
                                        <td style="text-align: center;"><button class="btn-save" @click="saveEdit('account', a.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                    </template>
                                    <template v-else>
                                        <td>{{ a.name }}</td><td>{{ a.currency }}</td>
                                        <td style="color:#67c23a; font-weight:bold;">¥ {{ formatMoney(a.balance) }}</td>
                                        <td>{{ a.note }}</td>
                                        <td style="text-align: center;"><button class="btn-edit" @click="startEdit('account', a)">编辑</button><button class="btn-del" @click="deleteRow('account', a.id)">删除</button></td>
                                    </template>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div v-if="selectedOrder" class="modal-mask" @click.self="closeModal">
                <div class="modal-content">
                    <button class="btn-close" @click="closeModal">关闭</button>
                    <h2>订单详情 - {{ selectedOrder.invoice_no }}</h2>
                    <div class="modal-item"><label>客户：</label>{{ selectedOrder.client_code }}</div>
                    <div class="modal-item"><label>下单日期：</label>{{ selectedOrder.order_date }}</div>
                    <div class="modal-item"><label>分批数量：</label>{{ subOrders.length }} 批</div>
                    <div style="margin-top: 15px;">
                        <label style="font-weight:bold; color:#666;">状态流转：</label>
                        <div class="status-flow">
                            <button v-for="s in statusFlow" :key="s" :class="{current: selectedOrder.status === s}" @click="changeStatus(s)">{{ s }}</button>
                        </div>
                    </div>
                    <div class="profit-box">
                        <div><label>客户总价：</label> <span>¥ {{ formatMoney(selectedOrder.total_amount) }}</span></div>
                        <div><label>已收款：</label> <span>¥ {{ formatMoney(selectedOrder.received) }}</span></div>
                        <div><label>未收款：</label> <span style="color:#f56c6c;">¥ {{ formatMoney(selectedOrder.unpaid) }}</span></div>
                        <div><label>采购总价：</label> <span>¥ {{ formatMoney(purchaseTotal) }}</span></div>
                        <div><label>毛利：</label> <span class="profit-value">¥ {{ formatMoney(selectedOrder.total_amount - purchaseTotal) }}</span></div>
                        <div><label>毛利率：</label> <span class="profit-value">{{ margin }}%</span></div>
                    </div>
                    <div class="section">
                        <h3 style="color: #9c27b0;">📑 分批明细</h3>
                        <div v-if="subOrders.length === 0" class="hint">暂无</div>
                        <table v-else class="data-table suborder-table">
                            <thead><tr><th>发票号</th><th>客户</th><th>规格</th><th>数量</th><th>单价</th><th>总价</th><th>状态</th><th>下单日期</th><th>预计收款日</th><th style="width:120px;">操作</th></tr></thead>
                            <tbody>
                                <tr v-for="s in subOrders" :key="s.id">
                                    <template v-if="isEditing('orders', s.id)">
                                        <td><input v-model="editData.invoice_no"></td><td><input v-model="editData.client_code"></td>
                                        <td><input v-model="editData.spec"></td><td><input type="number" v-model.number="editData.qty"></td>
                                        <td><input type="number" v-model.number="editData.unit_price"></td>
                                        <td>{{ ((editData.qty||0) * (editData.unit_price||0)).toFixed(2) }}</td>
                                        <td><input v-model="editData.status"></td><td><input v-model="editData.order_date"></td>
                                        <td><input v-model="editData.expect_payment_date" placeholder="YYYY-MM-DD"></td>
                                        <td style="text-align: center;"><button class="btn-save" @click="saveEdit('orders', s.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                    </template>
                                    <template v-else>
                                        <td>{{ s.invoice_no }}</td><td>{{ s.client_code }}</td><td>{{ s.spec }}</td>
                                        <td>{{ s.qty }}</td><td>{{ s.unit_price }}</td><td>{{ s.total_amount }}</td>
                                        <td>{{ s.status }}</td><td>{{ s.order_date }}</td>
                                        <td>{{ s.expect_payment_date || '-' }}</td>
                                        <td style="text-align: center;"><button class="btn-edit" @click="startEdit('orders', s)">编辑</button><button class="btn-del" @click="deleteRow('orders', s.id)">删除</button></td>
                                    </template>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="section">
                        <h3 style="color: #67c23a;">💰 收付款记录<button class="btn-add-sm" @click="openAdd('payments')">➕ 新增</button></h3>
                        <div v-if="payments.length === 0 && !isAdding('payments')" class="hint">暂无</div>
                        <table v-if="payments.length > 0 || isAdding('payments')" class="data-table payment-table">
                            <thead><tr><th>发票号</th><th>付款方</th><th>已付</th><th style="width:160px;">操作</th></tr></thead>
                            <tbody>
                                <tr v-if="isAdding('payments')">
                                    <td><input v-model="addData.invoice_no"></td><td><input v-model="addData.payer"></td>
                                    <td><input type="number" v-model.number="addData.paid_amount"></td>
                                    <td style="text-align: center;"><button class="btn-save" @click="saveAdd('payments')">保存</button><button class="btn-cancel" @click="cancelAdd">取消</button></td>
                                </tr>
                                <tr v-for="p in payments" :key="p.id">
                                    <template v-if="isEditing('payments', p.id)">
                                        <td><input v-model="editData.invoice_no"></td><td><input v-model="editData.payer"></td>
                                        <td><input type="number" v-model.number="editData.paid_amount"></td>
                                        <td style="text-align: center;"><button class="btn-save" @click="saveEdit('payments', p.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                    </template>
                                    <template v-else>
                                        <td>{{ p.invoice_no }}</td><td>{{ p.payer }}</td>
                                        <td style="color:#67c23a;">{{ p.paid_amount }}</td>
                                        <td style="text-align: center;"><button class="btn-edit" @click="startEdit('payments', p)">编辑</button><button class="btn-del" @click="deleteRow('payments', p.id)">删除</button></td>
                                    </template>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="section">
                        <h3 style="color: #f56c6c;">🏭 内贸采购记录<button class="btn-add-sm orange" @click="openPurchaseModal(selectedOrder.invoice_no)">➕ 新增</button></h3>
                        <div v-if="purchases.length === 0" class="hint">暂无</div>
                        <table v-else class="data-table purchase-table">
                            <thead><tr><th>外贸订单</th><th>供应商</th><th>合同号</th><th>规格</th><th>数量</th><th>单价</th><th>总价</th><th>已付</th><th>尾款</th><th style="width:120px;">操作</th></tr></thead>
                            <tbody>
                                <tr v-for="pu in purchases" :key="pu.id">
                                    <template v-if="isEditing('purchases', pu.id)">
                                        <td><input v-model="editData.sales_invoice_no"></td><td><input v-model="editData.supplier_code"></td>
                                        <td><input v-model="editData.supplier_invoice_no"></td><td><input v-model="editData.spec"></td>
                                        <td><input type="number" v-model.number="editData.qty"></td>
                                        <td><input type="number" v-model.number="editData.unit_price"></td>
                                        <td>{{ ((editData.qty||0) * (editData.unit_price||0)).toFixed(2) }}</td>
                                        <td><input type="number" v-model.number="editData.paid_amount"></td>
                                        <td>{{ (((editData.qty||0)*(editData.unit_price||0)) - (editData.paid_amount||0)).toFixed(2) }}</td>
                                        <td style="text-align: center;"><button class="btn-save" @click="saveEdit('purchases', pu.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                    </template>
                                    <template v-else>
                                        <td>{{ pu.sales_invoice_no }}</td><td>{{ pu.supplier_code }}</td>
                                        <td>{{ pu.supplier_invoice_no }}</td><td>{{ pu.spec }}</td>
                                        <td>{{ pu.qty }}</td><td>{{ pu.unit_price }}</td><td>{{ pu.total_amount }}</td>
                                        <td style="color:#67c23a;">{{ pu.paid_amount }}</td>
                                        <td style="color:#f56c6c;">{{ pu.balance }}</td>
                                        <td style="text-align: center;"><button class="btn-edit" @click="startEdit('purchases', pu)">编辑</button><button class="btn-del" @click="deleteRow('purchases', pu.id)">删除</button></td>
                                    </template>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="section">
                        <h3 style="color: #e6a23c;">📦 库存记录<button class="btn-add-sm orange" @click="openAdd('inventory')">➕ 新增</button></h3>
                        <div v-if="inventory.length === 0 && !isAdding('inventory')" class="hint">暂无</div>
                        <table v-if="inventory.length > 0 || isAdding('inventory')" class="data-table inventory-table">
                            <thead><tr><th>发票号</th><th>规格</th><th>数量</th><th>客户</th><th>入库时间</th><th>出库日期</th><th>状态</th><th>位置</th><th style="width:140px;">操作</th></tr></thead>
                            <tbody>
                                <tr v-if="isAdding('inventory')">
                                    <td><input v-model="addData.invoice_no"></td><td><input v-model="addData.spec"></td>
                                    <td><input type="number" v-model.number="addData.qty"></td><td><input v-model="addData.client_code"></td>
                                    <td><input v-model="addData.warehouse_date"></td><td><input v-model="addData.out_date" placeholder="留空=在库"></td>
                                    <td><input v-model="addData.status"></td>
                                    <td><input v-model="addData.location"></td>
                                    <td style="text-align: center;"><button class="btn-save" @click="saveAdd('inventory')">保存</button><button class="btn-cancel" @click="cancelAdd">取消</button></td>
                                </tr>
                                <tr v-for="i in inventory" :key="i.id">
                                    <template v-if="isEditing('inventory', i.id)">
                                        <td><input v-model="editData.invoice_no"></td><td><input v-model="editData.spec"></td>
                                        <td><input type="number" v-model.number="editData.qty"></td><td><input v-model="editData.client_code"></td>
                                        <td><input v-model="editData.warehouse_date"></td><td><input v-model="editData.out_date"></td>
                                        <td><input v-model="editData.status"></td>
                                        <td><input v-model="editData.location"></td>
                                        <td style="text-align: center;"><button class="btn-save" @click="saveEdit('inventory', i.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                    </template>
                                    <template v-else>
                                        <td>{{ i.invoice_no }}</td><td>{{ i.spec }}</td><td>{{ i.qty }}</td>
                                        <td>{{ i.client_code }}</td><td>{{ i.warehouse_date }}</td>
                                        <td style="color:#999;">{{ i.out_date || '-' }}</td>
                                        <td>{{ i.status }}</td><td>{{ i.location }}</td>
                                        <td style="text-align: center;"><button class="btn-edit" @click="startEdit('inventory', i)">编辑</button><button class="btn-del" @click="deleteRow('inventory', i.id)">删除</button></td>
                                    </template>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="section">
                        <h3 style="color: #409eff;">🚢 出运记录<button class="btn-add-sm blue" @click="openAdd('shipments')">➕ 新增</button></h3>
                        <div v-if="shipments.length === 0 && !isAdding('shipments')" class="hint">暂无</div>
                        <table v-if="shipments.length > 0 || isAdding('shipments')" class="data-table shipment-table">
                            <thead><tr><th>发票号</th><th>提单号</th><th>ETD</th><th>ETA</th><th>目的港</th><th>柜数</th><th>净重</th><th>船公司</th><th style="width:140px;">操作</th></tr></thead>
                            <tbody>
                                <tr v-if="isAdding('shipments')">
                                    <td><input v-model="addData.invoice_no"></td><td><input v-model="addData.bl_no"></td>
                                    <td><input v-model="addData.etd"></td><td><input v-model="addData.eta"></td>
                                    <td><input v-model="addData.pod"></td><td><input v-model="addData.containers"></td>
                                    <td><input type="number" v-model.number="addData.net_weight"></td><td><input v-model="addData.shipping_line"></td>
                                    <td style="text-align: center;"><button class="btn-save" @click="saveAdd('shipments')">保存</button><button class="btn-cancel" @click="cancelAdd">取消</button></td>
                                </tr>
                                <tr v-for="s in shipments" :key="s.id">
                                    <template v-if="isEditing('shipments', s.id)">
                                        <td><input v-model="editData.invoice_no"></td><td><input v-model="editData.bl_no"></td>
                                        <td><input v-model="editData.etd"></td><td><input v-model="editData.eta"></td>
                                        <td><input v-model="editData.pod"></td><td><input v-model="editData.containers"></td>
                                        <td><input type="number" v-model.number="editData.net_weight"></td><td><input v-model="editData.shipping_line"></td>
                                        <td style="text-align: center;"><button class="btn-save" @click="saveEdit('shipments', s.id)">保存</button><button class="btn-cancel" @click="cancelEdit">取消</button></td>
                                    </template>
                                    <template v-else>
                                        <td>{{ s.invoice_no }}</td><td>{{ s.bl_no }}</td><td>{{ s.etd }}</td><td>{{ s.eta }}</td>
                                        <td>{{ s.pod }}</td><td>{{ s.containers }}</td><td>{{ s.net_weight }}</td><td>{{ s.shipping_line }}</td>
                                        <td style="text-align: center;"><button class="btn-edit" @click="startEdit('shipments', s)">编辑</button><button class="btn-del" @click="deleteRow('shipments', s.id)">删除</button></td>
                                    </template>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div v-if="showAddModal" class="modal-mask" @click.self="showAddModal = false">
                <div class="modal-content" style="width: 750px;">
                    <button class="btn-close" @click="showAddModal = false">关闭</button>
                    <h2>新增订单</h2>
                    <div style="display: flex; gap: 20px;">
                        <div class="form-group" style="flex: 1;"><label>发票号 *</label><input v-model="newOrder.invoice_no"></div>
                        <div class="form-group" style="flex: 1;"><label>客户</label><input v-model="newOrder.client_code"></div>
                        <div class="form-group" style="flex: 1;"><label>状态</label><input v-model="newOrder.status"></div>
                    </div>
                    <label style="font-weight: bold; display: block; margin-top: 10px;">规格明细</label>
                    <button class="btn-add-spec" @click="addSpecRow">➕ 添加规格</button>
                    <table class="spec-table">
                        <thead><tr><th style="width: 40%;">规格</th><th style="width: 20%;">数量</th><th style="width: 20%;">单价</th><th style="width: 15%;">小计</th><th style="width: 5%;">操作</th></tr></thead>
                        <tbody>
                            <tr v-for="(item, index) in newOrder.items" :key="index">
                                <td><input v-model="item.spec"></td>
                                <td><input type="number" v-model.number="item.qty"></td>
                                <td><input type="number" v-model.number="item.unit_price"></td>
                                <td style="text-align: right; color: #409eff;">{{ (item.qty * item.unit_price).toFixed(2) }}</td>
                                <td style="text-align: center;"><button class="btn-del-spec" @click="removeSpecRow(index)">删</button></td>
                            </tr>
                        </tbody>
                    </table>
                    <div class="total-row">总金额：{{ grandTotal }}</div>
                    <button class="btn-submit" style="margin-top: 15px;" @click="submitOrder">提交订单</button>
                </div>
            </div>

            <div v-if="showPurchaseModal" class="modal-mask" @click.self="showPurchaseModal = false">
                <div class="modal-content" style="width: 600px;">
                    <button class="btn-close" @click="showPurchaseModal = false">关闭</button>
                    <h2>新增采购单 - {{ newPurchase.sales_invoice_no }}</h2>
                    <div style="display: flex; gap: 15px;">
                        <div class="form-group" style="flex: 1;"><label>供应商 *</label><input v-model="newPurchase.supplier_code" placeholder="ZM / JHL / LS..."></div>
                        <div class="form-group" style="flex: 1;"><label>合同号</label><input v-model="newPurchase.supplier_invoice_no"></div>
                    </div>
                    <div style="display: flex; gap: 15px;">
                        <div class="form-group" style="flex: 1;"><label>规格</label><input v-model="newPurchase.spec"></div>
                        <div class="form-group" style="flex: 1;"><label>数量</label><input type="number" v-model.number="newPurchase.qty"></div>
                        <div class="form-group" style="flex: 1;"><label>单价</label><input type="number" v-model.number="newPurchase.unit_price"></div>
                    </div>
                    <div style="display: flex; gap: 15px;">
                        <div class="form-group" style="flex: 1;"><label>下单日期</label><input v-model="newPurchase.order_date" placeholder="2026-01-01"></div>
                        <div class="form-group" style="flex: 1;"><label>预计货好</label><input v-model="newPurchase.expect_ready_date"></div>
                        <div class="form-group" style="flex: 1;"><label>已付金额</label><input type="number" v-model.number="newPurchase.paid_amount"></div>
                    </div>
                    <div class="form-group"><label>备注</label><input v-model="newPurchase.notes"></div>
                    <div class="total-row">采购总价：{{ ((newPurchase.qty || 0) * (newPurchase.unit_price || 0)).toFixed(2) }}</div>
                    <button class="btn-submit" style="margin-top: 15px;" @click="submitPurchase">提交采购单</button>
                </div>
            </div>
        </div>

        <script>
            const { createApp } = Vue
            function naturalCompare(a, b) {
                const re = /^([A-Za-z]+)-(\d+)(.*)$/;
                const ma = String(a).match(re); const mb = String(b).match(re);
                if (ma && mb) {
                    if (ma[1].toUpperCase() !== mb[1].toUpperCase()) return ma[1].toUpperCase() < mb[1].toUpperCase() ? -1 : 1;
                    const na = parseInt(ma[2]); const nb = parseInt(mb[2]);
                    if (na !== nb) return na - nb;
                    return ma[3].localeCompare(mb[3]);
                }
                return String(a).localeCompare(String(b));
            }
            const ADD_TEMPLATES = {
                payments: { invoice_no: '', payer: '', spec: '', paid_amount: 0 },
                inventory: { invoice_no: '', spec: '', qty: 0, client_code: '', warehouse_date: '', status: '', location: '', out_date: '' },
                shipments: { invoice_no: '', bl_no: '', etd: '', eta: '', pod: '', containers: '', container_no: '', spec: '', net_weight: 0, telex: '', status: '', transport_company: '', shipping_line: '' }
            };
            const WEEKDAYS = ['周日','周一','周二','周三','周四','周五','周六'];
            createApp({
                data() {
                    return {
                        activeTab: 'orders', cashSubTab: 'forecast',
                        orders: [], searchQuery: '', selectedOrder: null,
                        payments: [], inventory: [], shipments: [], purchases: [], subOrders: [],
                        allInventory: [], allShipments: [],
                        inventoryQuery: '', shipmentQuery: '',
                        showShippedInventory: false,
                        dashboard: {},
                        accounts: [], cashFlows: [], cashSummary: {}, forecast: {start_balance: 0, days: []},
                        forecastDays: 60, hideEmptyDays: false, includePendingForecast: true,
                        flowFilter: '', addingFlow: false, addingAccount: false,
                        newFlow: {}, newAccount: {},
                        showAddModal: false, showPurchaseModal: false,
                        newOrder: { invoice_no: '', client_code: '', status: 'proforma', items: [{ spec: '', qty: 1, unit_price: 0 }] },
                        newPurchase: {},
                        sortKey: 'invoice_no', sortOrder: 'asc',
                        invSortKey: 'invoice_no', invSortOrder: 'asc',
                        shipSortKey: 'etd', shipSortOrder: 'desc',
                        editing: { type: null, id: null }, editData: {},
                        adding: { type: null }, addData: {},
                        standaloneAdding: { type: null }, standaloneAdd: {},
                        filterClient: '', filterStatus: '', startDate: '', endDate: '', quickRange: '',
                        statusFlow: ['proforma', 'production', 'ready', 'shipped', 'closed']
                    }
                },
                computed: {
                    clientOptions() { const s = new Set(); this.orders.forEach(o => { if (o.client_code) s.add(o.client_code); }); return [...s].sort(); },
                    statusOptions() { const s = new Set(); this.orders.forEach(o => { if (o.status) s.add(o.status); }); return [...s].sort(); },
                    filteredOrders() {
                        let list = this.orders;
                        if (this.searchQuery) { const q = this.searchQuery.toLowerCase(); list = list.filter(o => (o.invoice_no && o.invoice_no.toLowerCase().includes(q)) || (o.client_code && o.client_code.toLowerCase().includes(q)) || (o.status && o.status.toLowerCase().includes(q))); }
                        if (this.filterClient) list = list.filter(o => o.client_code === this.filterClient);
                        if (this.filterStatus) list = list.filter(o => o.status === this.filterStatus);
                        if (this.startDate) list = list.filter(o => o.order_date && o.order_date >= this.startDate);
                        if (this.endDate) list = list.filter(o => o.order_date && o.order_date <= this.endDate);
                        const key = this.sortKey; const order = this.sortOrder === 'asc' ? 1 : -1;
                        return [...list].sort((a, b) => {
                            let va = a[key], vb = b[key];
                            if (key === 'invoice_no') return naturalCompare(va, vb) * order;
                            if (key === 'qty' || key === 'total_amount' || key === 'sub_count' || key === 'received' || key === 'unpaid') return ((Number(va)||0) - (Number(vb)||0)) * order;
                            return String(va||'').localeCompare(String(vb||'')) * order;
                        });
                    },
                    inStockCount() { return this.allInventory.filter(i => !i.out_date).length; },
                    filteredInventory() {
                        let list = this.allInventory;
                        if (!this.showShippedInventory) list = list.filter(i => !i.out_date);
                        if (this.inventoryQuery) { const q = this.inventoryQuery.toLowerCase(); list = list.filter(i => (i.invoice_no && i.invoice_no.toLowerCase().includes(q)) || (i.client_code && i.client_code.toLowerCase().includes(q)) || (i.spec && i.spec.toLowerCase().includes(q))); }
                        const key = this.invSortKey; const order = this.invSortOrder === 'asc' ? 1 : -1;
                        return [...list].sort((a, b) => {
                            let va = a[key], vb = b[key];
                            if (key === 'invoice_no') return naturalCompare(va, vb) * order;
                            if (key === 'qty') return ((Number(va)||0) - (Number(vb)||0)) * order;
                            return String(va||'').localeCompare(String(vb||'')) * order;
                        });
                    },
                    filteredShipments() {
                        let list = this.allShipments;
                        if (this.shipmentQuery) { const q = this.shipmentQuery.toLowerCase(); list = list.filter(s => (s.invoice_no && s.invoice_no.toLowerCase().includes(q)) || (s.bl_no && s.bl_no.toLowerCase().includes(q)) || (s.pod && s.pod.toLowerCase().includes(q)) || (s.shipping_line && s.shipping_line.toLowerCase().includes(q))); }
                        const key = this.shipSortKey; const order = this.shipSortOrder === 'asc' ? 1 : -1;
                        return [...list].sort((a, b) => {
                            let va = a[key], vb = b[key];
                            if (key === 'invoice_no') return naturalCompare(va, vb) * order;
                            if (key === 'net_weight') return ((Number(va)||0) - (Number(vb)||0)) * order;
                            return String(va||'').localeCompare(String(vb||'')) * order;
                        });
                    },
                    filteredFlows() {
                        if (!this.flowFilter) return this.cashFlows;
                        return this.cashFlows.filter(f => f.status === this.flowFilter);
                    },
                    displayWeeks() {
                        if (!this.forecast.days) return [];
                        if (!this.hideEmptyDays) return this.forecast.days;
                        return this.forecast.days.filter(d => d.in > 0 || d.out > 0);
                    },
                    grandTotal() { return this.newOrder.items.reduce((s, i) => s + (i.qty || 0) * (i.unit_price || 0), 0).toFixed(2); },
                    purchaseTotal() { return this.purchases.reduce((s, p) => s + (p.total_amount || 0), 0); },
                    margin() { if (!this.selectedOrder || !this.selectedOrder.total_amount) return 0; return (((this.selectedOrder.total_amount - this.purchaseTotal) / this.selectedOrder.total_amount) * 100).toFixed(1); }
                },
                methods: {
                    setForecastDays(n) { this.forecastDays = n; this.loadForecast(); },
                    isToday(ds) { return ds === new Date().toISOString().slice(0,10); },
                    getWeekday(ds) { try { const d = new Date(ds); if (isNaN(d.getTime())) return ''; return WEEKDAYS[d.getDay()]; } catch(e) { return ''; } },
                    setQuickRange(range) {
                        this.quickRange = range; const now = new Date();
                        const y = now.getFullYear(); const m = now.getMonth();
                        const pad = n => String(n).padStart(2, '0');
                        const fmt = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
                        if (range === 'thisMonth') { this.startDate = fmt(new Date(y, m, 1)); this.endDate = fmt(new Date(y, m + 1, 0)); }
                        else if (range === 'lastMonth') { this.startDate = fmt(new Date(y, m - 1, 1)); this.endDate = fmt(new Date(y, m, 0)); }
                        else if (range === 'thisQuarter') { const q = Math.floor(m / 3) * 3; this.startDate = fmt(new Date(y, q, 1)); this.endDate = fmt(new Date(y, q + 3, 0)); }
                    },
                    clearFilters() { this.searchQuery = ''; this.filterClient = ''; this.filterStatus = ''; this.startDate = ''; this.endDate = ''; this.quickRange = ''; },
                    switchTab(tab) {
                        this.activeTab = tab;
                        if (tab === 'inventory') this.loadAllInventory();
                        if (tab === 'shipments') this.loadAllShipments();
                        if (tab === 'cash') { this.loadCashData(); this.loadForecast(); }
                    },
                    loadAllInventory() { fetch('/api/all-inventory').then(r => r.json()).then(d => { this.allInventory = d }); },
                    loadAllShipments() { fetch('/api/all-shipments').then(r => r.json()).then(d => { this.allShipments = d }); },
                    loadCashData() {
                        fetch('/api/cash-flows').then(r => r.json()).then(d => { this.cashFlows = d });
                        fetch('/api/bank-accounts').then(r => r.json()).then(d => { this.accounts = d });
                        fetch('/api/cash-summary').then(r => r.json()).then(d => { this.cashSummary = d });
                    },
                    loadForecast() { 
                        fetch('/api/cash-forecast?days=' + this.forecastDays + '&include_pending=' + this.includePendingForecast)
                            .then(r => r.json())
                            .then(d => { this.forecast = d; }); 
                    },
                    openFlowAdd() { this.addingFlow = true; this.newFlow = { flow_date: '', direction: 'in', category: '', party: '', amount: 0, currency: 'CNY', account_id: 0, invoice_no: '', status: 'planned', note: '' }; },
                    saveFlowAdd() {
                        if (!this.newFlow.flow_date) { alert('请填日期'); return; }
                        fetch('/api/cash-flows', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(this.newFlow) })
                            .then(r => r.json()).then(d => { alert(d.message); this.addingFlow = false; this.loadCashData(); this.loadForecast(); });
                    },
                    openAccountAdd() { this.addingAccount = true; this.newAccount = { name: '', currency: 'CNY', balance: 0, note: '' }; },
                    saveAccountAdd() {
                        if (!this.newAccount.name) { alert('请填账户名'); return; }
                        fetch('/api/bank-accounts', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(this.newAccount) })
                            .then(r => r.json()).then(d => { alert(d.message); this.addingAccount = false; this.loadCashData(); this.loadForecast(); });
                    },
                    changeStatus(newStatus) {
                        if (!this.selectedOrder) return;
                        if (!confirm('将订单 ' + this.selectedOrder.invoice_no + ' 下所有分批的状态改为「' + newStatus + '」吗？')) return;
                        fetch('/api/orders/' + this.selectedOrder.invoice_no + '/status', { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({new_status: newStatus}) })
                            .then(r => r.json()).then(d => { alert(d.message); this.selectedOrder.status = newStatus; this.loadOrders(); this.loadDashboard(); this.refreshCurrentOrder(); });
                    },
                    invSortBy(key) { if (this.invSortKey === key) this.invSortOrder = this.invSortOrder === 'asc' ? 'desc' : 'asc'; else { this.invSortKey = key; this.invSortOrder = 'asc'; } },
                    shipSortBy(key) { if (this.shipSortKey === key) this.shipSortOrder = this.shipSortOrder === 'asc' ? 'desc' : 'asc'; else { this.shipSortKey = key; this.shipSortOrder = 'asc'; } },
                    daysInStock(ws, os) {
                        if (!ws) return '-';
                        try {
                            const start = new Date(ws);
                            if (isNaN(start.getTime())) return '-';
                            const end = os ? new Date(os) : new Date();
                            if (isNaN(end.getTime())) return '-';
                            const diff = Math.floor((end.getTime() - start.getTime()) / 86400000);
                            return diff >= 0 ? diff : '-';
                        } catch(e) { return '-'; }
                    },
                    openStandaloneAdd(type) { this.cancelEdit(); this.standaloneAdding = { type }; this.standaloneAdd = JSON.parse(JSON.stringify(ADD_TEMPLATES[type])); },
                    cancelStandaloneAdd() { this.standaloneAdding = { type: null }; this.standaloneAdd = {}; },
                    saveStandaloneAdd(type) {
                        fetch('/api/' + type, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(this.standaloneAdd) })
                            .then(r => r.json()).then(d => { alert(d.message); this.cancelStandaloneAdd(); if (type === 'inventory') this.loadAllInventory(); if (type === 'shipments') this.loadAllShipments(); this.loadDashboard(); });
                    },
                    sortBy(key) { if (this.sortKey === key) this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc'; else { this.sortKey = key; this.sortOrder = 'asc'; } },
                    formatMoney(v) { if (!v && v !== 0) return '0'; return Number(v).toLocaleString('en-US', {maximumFractionDigits: 2}); },
                    isEditing(type, id) { return this.editing.type === type && this.editing.id === id; },
                    startEdit(type, row) { this.cancelAdd(); this.cancelStandaloneAdd(); this.editing = { type, id: row.id }; this.editData = JSON.parse(JSON.stringify(row)); },
                    cancelEdit() { this.editing = { type: null, id: null }; this.editData = {}; },
                    isAdding(type) { return this.adding.type === type; },
                    isStandaloneAdding(type) { return this.standaloneAdding.type === type; },
                    openAdd(type) { this.cancelEdit(); this.adding = { type }; this.addData = JSON.parse(JSON.stringify(ADD_TEMPLATES[type])); if (this.selectedOrder) this.addData.invoice_no = this.selectedOrder.invoice_no; },
                    cancelAdd() { this.adding = { type: null }; this.addData = {}; },
                    saveAdd(type) {
                        fetch('/api/' + type, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(this.addData) })
                            .then(r => r.json()).then(d => { alert(d.message); this.cancelAdd(); this.refreshCurrentOrder(); this.loadAllInventory(); this.loadAllShipments(); this.loadDashboard(); });
                    },
                    saveEdit(type, id) {
                        let url;
                        if (type === 'cash') url = '/api/cash-flows/' + id;
                        else if (type === 'account') url = '/api/bank-accounts/' + id;
                        else url = '/api/' + type + '/' + id;
                        fetch(url, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(this.editData) })
                            .then(r => r.json()).then(d => {
                                alert(d.message); this.cancelEdit();
                                if (type === 'cash' || type === 'account') { this.loadCashData(); this.loadForecast(); return; }
                                this.refreshCurrentOrder(); this.loadOrders();
                                if (type === 'inventory') this.loadAllInventory();
                                if (type === 'shipments') this.loadAllShipments();
                                this.loadDashboard();
                            });
                    },
                    deleteRow(type, id) {
                        if (!confirm('确认删除这条记录吗？删除后无法恢复。')) return;
                        let url;
                        if (type === 'cash') url = '/api/cash-flows/' + id;
                        else if (type === 'account') url = '/api/bank-accounts/' + id;
                        else url = '/api/' + type + '/' + id;
                        fetch(url, { method: 'DELETE' }).then(r => r.json()).then(d => {
                            alert(d.message);
                            if (type === 'cash' || type === 'account') { this.loadCashData(); this.loadForecast(); return; }
                            this.refreshCurrentOrder(); this.loadOrders();
                            if (type === 'inventory') this.loadAllInventory();
                            if (type === 'shipments') this.loadAllShipments();
                            this.loadDashboard();
                        });
                    },
                    refreshCurrentOrder() {
                        if (!this.selectedOrder) return;
                        const inv = this.selectedOrder.invoice_no;
                        fetch('/api/orders/' + inv + '/sub_orders').then(r => r.json()).then(d => { this.subOrders = d; });
                        fetch('/api/orders/' + inv + '/payments').then(r => r.json()).then(d => { this.payments = d; });
                        fetch('/api/orders/' + inv + '/purchases').then(r => r.json()).then(d => { this.purchases = d; });
                        fetch('/api/orders/' + inv + '/inventory').then(r => r.json()).then(d => { this.inventory = d; });
                        fetch('/api/orders/' + inv + '/shipments').then(r => r.json()).then(d => { this.shipments = d; });
                        this.loadOrders();
                    },
                    loadDashboard() { fetch('/api/dashboard').then(r => r.json()).then(d => { if (!d.error) this.dashboard = d; }); },
                    loadOrders() { fetch('/api/orders').then(r => r.json()).then(d => { this.orders = d }); },
                    openAddModal() { this.newOrder = { invoice_no: '', client_code: '', status: 'proforma', items: [{ spec: '', qty: 1, unit_price: 0 }] }; this.showAddModal = true; },
                    addSpecRow() { this.newOrder.items.push({ spec: '', qty: 1, unit_price: 0 }); },
                    removeSpecRow(i) { if (this.newOrder.items.length > 1) this.newOrder.items.splice(i, 1); else alert('至少保留一行规格'); },
                    submitOrder() {
                        if (!this.newOrder.invoice_no) { alert('发票号不能为空'); return; }
                        if (this.newOrder.items.some(i => !i.spec)) { alert('规格不能为空'); return; }
                        fetch('/api/orders', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(this.newOrder) })
                            .then(r => r.json()).then(d => { alert(d.message); this.showAddModal = false; this.loadOrders(); this.loadDashboard(); });
                    },
                    openPurchaseModal(invoiceNo) {
                        this.newPurchase = { sales_invoice_no: invoiceNo, supplier_code: '', supplier_invoice_no: '', order_date: '', expect_ready_date: '', actual_ready_date: '', spec: '', qty: 0, unit_price: 0, paid_amount: 0, notes: '' };
                        this.showPurchaseModal = true;
                    },
                    submitPurchase() {
                        if (!this.newPurchase.supplier_code) { alert('供应商不能为空'); return; }
                        fetch('/api/purchases', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(this.newPurchase) })
                            .then(r => r.json()).then(d => { alert(d.message); this.showPurchaseModal = false; this.refreshCurrentOrder(); this.loadDashboard(); });
                    },
                    showDetail(order) {
                        this.selectedOrder = order;
                        this.payments = []; this.inventory = []; this.shipments = []; this.purchases = []; this.subOrders = [];
                        this.cancelEdit(); this.cancelAdd();
                        fetch('/api/orders/' + order.invoice_no + '/sub_orders').then(r => r.json()).then(d => { this.subOrders = d });
                        fetch('/api/orders/' + order.invoice_no + '/payments').then(r => r.json()).then(d => { this.payments = d });
                        fetch('/api/orders/' + order.invoice_no + '/inventory').then(r => r.json()).then(d => { this.inventory = d });
                        fetch('/api/orders/' + order.invoice_no + '/shipments').then(r => r.json()).then(d => { this.shipments = d });
                        fetch('/api/orders/' + order.invoice_no + '/purchases').then(r => r.json()).then(d => { this.purchases = d });
                    },
                    closeModal() {
                        this.selectedOrder = null;
                        this.payments = []; this.inventory = []; this.shipments = []; this.purchases = []; this.subOrders = [];
                        this.cancelEdit(); this.cancelAdd();
                    }
                },
                mounted() { this.loadOrders(); this.loadDashboard(); }
            }).mount('#app')
        </script>
    </body>
    </html>
    """
    return html_content