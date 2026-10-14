import pandas as pd
import sqlite3
import re

# 连接数据库
conn = sqlite3.connect('erp.db')
cursor = conn.cursor()

# 清空旧数据
cursor.execute('DELETE FROM orders')

excel_file = 'orders.xlsx'
try:
    df = pd.read_excel(excel_file, header=0)  # 之前改成了 header=0
except FileNotFoundError:
    print(f"错误：找不到文件 '{excel_file}'，请确认文件已在文件夹中并重命名！")
    exit()

# 清理列名：去除前后空格，转小写
df.columns = df.columns.astype(str).str.strip().str.lower()

def find_col(df, keyword):
    for col in df.columns:
        if keyword in col:
            return col
    return None

# 查找真实列名
invoice_col = find_col(df, 'invoice')
client_col = find_col(df, 'client')
spec_col = find_col(df, 'goods')
qty_col = find_col(df, 'container')
amount_col = find_col(df, 'payment for goods')
status_col = find_col(df, 'status')

if not invoice_col:
    print("\n错误：找不到包含 'invoice' 的列，请检查 Excel 表头！")
    exit()

# 新增：安全转换数字的函数
def safe_float(val):
    if pd.isna(val):
        return 0.0
    val_str = str(val).replace(',', '').strip()
    match = re.search(r'-?\d+\.?\d*', val_str) # 提取字符串里的数字
    if match:
        return float(match.group())
    return 0.0

# 只删除发票号列为空的行
df = df.dropna(subset=[invoice_col])

count = 0
for index, row in df.iterrows():
    invoice_no = str(row[invoice_col]).strip()
    
    if 'freight' in invoice_no.lower() or 'salary' in invoice_no.lower() or invoice_no == 'nan':
        continue

    client_code = str(row[client_col]).strip() if client_col and pd.notna(row[client_col]) else ''
    spec = str(row[spec_col]).strip() if spec_col and pd.notna(row[spec_col]) else ''
    
    # 使用安全转换函数
    qty = safe_float(row[qty_col]) if qty_col else 0.0
    total_amount = safe_float(row[amount_col]) if amount_col else 0.0
    
    status = str(row[status_col]).strip() if status_col and pd.notna(row[status_col]) else ''

    cursor.execute('''
        INSERT INTO orders (invoice_no, client_code, spec, qty, unit_price, total_amount, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (invoice_no, client_code, spec, qty, 0.0, total_amount, status))
    
    count += 1

conn.commit()
conn.close()

print(f"\n=== 导入结果 ===")
print(f"导入完成！一共导入了 {count} 条订单数据。")