import pandas as pd
import sqlite3
import re

conn = sqlite3.connect('erp.db')
cursor = conn.cursor()

# 建库存表
cursor.execute('''
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT,
        spec TEXT,
        qty REAL,
        client_code TEXT,
        warehouse_date TEXT,
        status TEXT,
        location TEXT
    )
''')
cursor.execute('DELETE FROM inventory')

def safe_float(val):
    if pd.isna(val): return 0.0
    val_str = str(val).replace(',', '').strip()
    match = re.search(r'-?\d+\.?\d*', val_str)
    return float(match.group()) if match else 0.0

df = pd.read_excel('inventory.xlsx', header=0)
df.columns = df.columns.astype(str).str.strip().str.lower()

def find_col(df, keywords):
    for col in df.columns:
        for kw in keywords:
            if kw in col:
                return col
    return None

inv_col = find_col(df, ['发票号', 'invoice'])
spec_col = find_col(df, ['规格', 'spec'])
qty_col = find_col(df, ['数量', 'qty'])
client_col = find_col(df, ['客户', 'client'])
date_col = find_col(df, ['入库时间', 'warehouse'])
status_col = find_col(df, ['状态', 'status'])
loc_col = find_col(df, ['位置', 'location'])

count = 0
for index, row in df.iterrows():
    inv_no = str(row[inv_col]).strip() if inv_col and pd.notna(row[inv_col]) else ''
    if not inv_no or inv_no == 'nan' or 'invoice' in inv_no.lower():
        continue

    spec = str(row[spec_col]).strip() if spec_col and pd.notna(row[spec_col]) else ''
    qty = safe_float(row[qty_col]) if qty_col else 0.0
    client = str(row[client_col]).strip() if client_col and pd.notna(row[client_col]) else ''
    date_str = str(row[date_col]).strip() if date_col and pd.notna(row[date_col]) else ''
    status = str(row[status_col]).strip() if status_col and pd.notna(row[status_col]) else ''
    loc = str(row[loc_col]).strip() if loc_col and pd.notna(row[loc_col]) else ''

    cursor.execute('''
        INSERT INTO inventory (invoice_no, spec, qty, client_code, warehouse_date, status, location)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (inv_no, spec, qty, client, date_str, status, loc))
    count += 1

conn.commit()
conn.close()
print(f"库存数据导入完成！共导入 {count} 条记录。")