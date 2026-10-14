import pandas as pd
import sqlite3
import re

conn = sqlite3.connect('erp.db')
cursor = conn.cursor()

# 建立付款表
cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT,
        payer TEXT,
        spec TEXT,
        total_amount REAL,
        paid_amount REAL,
        unpaid_amount REAL,
        ready_date TEXT,
        status TEXT
    )
''')
cursor.execute('DELETE FROM payments') # 清空旧数据

def safe_float(val):
    if pd.isna(val): return 0.0
    val_str = str(val).replace(',', '').strip()
    match = re.search(r'-?\d+\.?\d*', val_str)
    return float(match.group()) if match else 0.0

df = pd.read_excel('payments.xlsx', header=0)
df.columns = df.columns.astype(str).str.strip().str.lower()

def find_col(df, keyword):
    for col in df.columns:
        if keyword in col:
            return col
    return None

inv_col = find_col(df, 'invoice')
payer_col = find_col(df, 'payer')
spec_col = find_col(df, 'specification')
total_col = find_col(df, 'total')
paid_col = find_col(df, 'paid')
unpaid_col = find_col(df, 'unpaid')
date_col = find_col(df, 'ready')
status_col = find_col(df, 'status')

count = 0
for index, row in df.iterrows():
    inv_no = str(row[inv_col]).strip() if inv_col and pd.notna(row[inv_col]) else ''
    if not inv_no or inv_no == 'nan' or 'invoice' in inv_no.lower():
        continue

    payer = str(row[payer_col]).strip() if payer_col and pd.notna(row[payer_col]) else ''
    spec = str(row[spec_col]).strip() if spec_col and pd.notna(row[spec_col]) else ''
    total = safe_float(row[total_col]) if total_col else 0.0
    paid = safe_float(row[paid_col]) if paid_col else 0.0
    unpaid = safe_float(row[unpaid_col]) if unpaid_col else 0.0
    ready_date = str(row[date_col]).strip() if date_col and pd.notna(row[date_col]) else ''
    status = str(row[status_col]).strip() if status_col and pd.notna(row[status_col]) else ''

    cursor.execute('''
        INSERT INTO payments (invoice_no, payer, spec, total_amount, paid_amount, unpaid_amount, ready_date, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (inv_no, payer, spec, total, paid, unpaid, ready_date, status))
    count += 1

conn.commit()
conn.close()
print(f"付款数据导入完成！共导入 {count} 条记录。")