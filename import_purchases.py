import pandas as pd
import sqlite3
import re

conn = sqlite3.connect('erp.db')
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sales_invoice_no TEXT,
        supplier_code TEXT,
        supplier_invoice_no TEXT,
        order_date TEXT,
        expect_ready_date TEXT,
        actual_ready_date TEXT,
        spec TEXT,
        qty REAL,
        unit_price REAL,
        total_amount REAL,
        paid_amount REAL,
        balance REAL,
        status TEXT,
        notes TEXT
    )
''')
cursor.execute('DELETE FROM purchase_orders')

def safe_float(val):
    if pd.isna(val): return 0.0
    s = str(val).replace(',', '').strip()
    # 处理 =xxx 这类公式残留
    if s.startswith('='):
        return 0.0
    m = re.search(r'-?\d+\.?\d*', s)
    return float(m.group()) if m else 0.0

def safe_str(val):
    if pd.isna(val): return ''
    return str(val).strip()

# 读取 Excel 大表格，跳过第一行标题，用第二行做表头
df = pd.read_excel('big_excel.xlsx', sheet_name=0, header=1)

print("识别到的列（前 35 列）：")
for i, col in enumerate(df.columns[:35]):
    print(f"  [{i}] {col}")

# 按位置读取（大表格的列顺序是固定的）
count = 0
for index, row in df.iterrows():
    # 第 3 列（索引3）是外贸发票号
    sales_inv = safe_str(row.iloc[3])
    if not sales_inv or sales_inv.lower() == 'nan':
        continue
    if not any(p in sales_inv for p in ['TJTW', 'INTW', 'PT-', 'NBE']):
        continue

    supplier = safe_str(row.iloc[17])   # Supplyer
    supplier_inv = safe_str(row.iloc[18])  # Inv No 供应商
    order_date = safe_str(row.iloc[19])    # Order date
    expect_ready = safe_str(row.iloc[20])  # Expect preparation date
    actual_ready = safe_str(row.iloc[21])  # Actual preparation date
    notes = safe_str(row.iloc[23])         # Notes
    qty = safe_float(row.iloc[24])         # QTY
    unit_price = safe_float(row.iloc[25])  # CNY/ton
    total = safe_float(row.iloc[26])       # Total
    paid = safe_float(row.iloc[27])        # PAID ADV
    balance = safe_float(row.iloc[28])     # Balance

    # 如果供应商和数量都为空，跳过
    if not supplier and qty == 0:
        continue

    # 自动计算总价和尾款
    if total == 0 and qty > 0 and unit_price > 0:
        total = qty * unit_price
    if balance == 0 and total > 0:
        balance = total - paid

    cursor.execute('''
        INSERT INTO purchase_orders 
        (sales_invoice_no, supplier_code, supplier_invoice_no, order_date, expect_ready_date, 
         actual_ready_date, spec, qty, unit_price, total_amount, paid_amount, balance, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (sales_inv, supplier, supplier_inv, order_date, expect_ready, actual_ready,
          '', qty, unit_price, total, paid, balance, '', notes))
    count += 1

conn.commit()
conn.close()
print(f"\n采购数据导入完成！共导入 {count} 条采购记录。")