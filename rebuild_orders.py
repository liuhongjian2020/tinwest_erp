import pandas as pd
import sqlite3

conn = sqlite3.connect('erp.db')
cursor = conn.cursor()

# 重建表结构（新增 order_date 字段）
cursor.execute('DROP TABLE IF EXISTS orders')
cursor.execute('''
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT,
        client_code TEXT,
        spec TEXT,
        qty REAL,
        unit_price REAL,
        total_amount REAL,
        status TEXT,
        order_date TEXT
    )
''')

def safe_float(v):
    if pd.isna(v): return 0.0
    try:
        return float(v)
    except:
        return 0.0

def safe_str(v):
    if pd.isna(v): return ''
    return str(v).strip()

df = pd.read_excel('big_excel.xlsx', sheet_name=0, header=1)

count = 0
skipped = 0
for idx, row in df.iterrows():
    invoice = safe_str(row.iloc[3])
    if not invoice or invoice.lower() == 'nan':
        continue
    if not any(p in invoice for p in ['TJTW', 'INTW', 'PT-', 'NBE']):
        continue

    order_date = safe_str(row.iloc[2])   # 第 2 列：下单日期
    buyer = safe_str(row.iloc[7])
    spec = safe_str(row.iloc[4])
    qty = safe_float(row.iloc[9])
    unit_price = safe_float(row.iloc[10])
    total = safe_float(row.iloc[11])

    if qty == 0 and unit_price == 0 and total == 0:
        skipped += 1
        continue

    if total == 0 and qty > 0 and unit_price > 0:
        total = qty * unit_price

    cursor.execute('''
        INSERT INTO orders (invoice_no, client_code, spec, qty, unit_price, total_amount, status, order_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (invoice, buyer, spec, qty, unit_price, total, '', order_date))

    count += 1

conn.commit()
conn.close()
print(f"订单重建完成！")
print(f"  导入：{count} 条销售明细")
print(f"  跳过：{skipped} 条纯采购/杂项行")