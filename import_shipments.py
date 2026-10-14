import pandas as pd
import sqlite3
import re

conn = sqlite3.connect('erp.db')
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS shipments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bl_no TEXT,
        etd TEXT,
        pod TEXT,
        containers TEXT,
        container_no TEXT,
        eta TEXT,
        spec TEXT,
        net_weight REAL,
        invoice_no TEXT,
        telex TEXT,
        status TEXT,
        transport_company TEXT,
        shipping_line TEXT
    )
''')
cursor.execute('DELETE FROM shipments')

def safe_float(val):
    if pd.isna(val): return 0.0
    s = str(val).replace(',', '').strip()
    m = re.search(r'-?\d+\.?\d*', s)
    return float(m.group()) if m else 0.0

# 读取 export 这个 sheet
df = pd.read_excel('shipments.xlsx', sheet_name='export', header=0)
df.columns = df.columns.astype(str).str.strip().str.lower()

def find_col(df, keywords):
    for col in df.columns:
        for kw in keywords:
            if kw in col:
                return col
    return None

bl_col = find_col(df, ['b/l', '提单'])
etd_col = find_col(df, ['etd'])
pod_col = find_col(df, ['pod', '目的港'])
qty_col = find_col(df, ['柜数', 'qty'])
cno_col = find_col(df, ['箱号', 'container no'])
eta_col = find_col(df, ['eta'])
spec_col = find_col(df, ['description', '规格'])
wt_col = find_col(df, ['n.wt', '净重'])
inv_col = find_col(df, ['nr invoice'])
telex_col = find_col(df, ['telex'])
status_col = find_col(df, ['status'])
trans_col = find_col(df, ['transport'])
ship_col = find_col(df, ['shipping company'])

count = 0
for index, row in df.iterrows():
    inv_no = str(row[inv_col]).strip() if inv_col and pd.notna(row[inv_col]) else ''
    if not inv_no or inv_no == 'nan' or inv_no.lower() == 'none':
        continue

    bl_no = str(row[bl_col]).strip() if bl_col and pd.notna(row[bl_col]) else ''
    etd = str(row[etd_col]).strip() if etd_col and pd.notna(row[etd_col]) else ''
    pod = str(row[pod_col]).strip() if pod_col and pd.notna(row[pod_col]) else ''
    containers = str(row[qty_col]).strip() if qty_col and pd.notna(row[qty_col]) else ''
    cno = str(row[cno_col]).strip() if cno_col and pd.notna(row[cno_col]) else ''
    eta = str(row[eta_col]).strip() if eta_col and pd.notna(row[eta_col]) else ''
    spec = str(row[spec_col]).strip() if spec_col and pd.notna(row[spec_col]) else ''
    wt = safe_float(row[wt_col]) if wt_col else 0.0
    telex = str(row[telex_col]).strip() if telex_col and pd.notna(row[telex_col]) else ''
    status = str(row[status_col]).strip() if status_col and pd.notna(row[status_col]) else ''
    trans = str(row[trans_col]).strip() if trans_col and pd.notna(row[trans_col]) else ''
    ship = str(row[ship_col]).strip() if ship_col and pd.notna(row[ship_col]) else ''

    cursor.execute('''
        INSERT INTO shipments (bl_no, etd, pod, containers, container_no, eta, spec, net_weight, invoice_no, telex, status, transport_company, shipping_line)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (bl_no, etd, pod, containers, cno, eta, spec, wt, inv_no, telex, status, trans, ship))
    count += 1

conn.commit()
conn.close()
print(f"出运数据导入完成！共导入 {count} 条记录。")