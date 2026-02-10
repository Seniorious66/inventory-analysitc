"""
回滚测试数据
- 恢复被分割的 ID 18（牛肉）
- 删除分割产生的子项 (ID 21, 22, 23)
- 恢复被标记为 waste 的 ID 6（酸菜）
- 恢复部分消耗的 ID 17（鸡腿肉）
- 删除 ID 17 的子项 (ID 24, 25)
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

conn = psycopg2.connect(
    dbname=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT')
)
cur = conn.cursor()

print("🔄 开始回滚测试数据...\n")

# 1. 恢复 ID 17（鸡腿肉）到原始状态
print("1️⃣ 恢复 ID 17（鸡腿肉）")
cur.execute("UPDATE inventory SET status='in_stock', quantity=500 WHERE id=17")
print("   ✅ ID 17 已恢复到 in_stock, 500g")

# 2. 删除 ID 17 的子项
print("\n2️⃣ 删除 ID 17 的子项")
cur.execute("DELETE FROM inventory WHERE parent_id=17")
deleted = cur.rowcount
print(f"   ✅ 删除了 {deleted} 个子项")

# 3. 恢复 ID 18（牛肉）到原始状态
print("\n3️⃣ 恢复 ID 18（牛肉）")
cur.execute("UPDATE inventory SET status='in_stock', quantity=1.18 WHERE id=18")
print("   ✅ ID 18 已恢复到 in_stock, 1.18kg")

# 4. 删除 ID 18 的子项
print("\n4️⃣ 删除 ID 18 的子项")
cur.execute("DELETE FROM inventory WHERE parent_id=18")
deleted = cur.rowcount
print(f"   ✅ 删除了 {deleted} 个子项")

# 5. 恢复 ID 6（酸菜）
print("\n5️⃣ 恢复 ID 6（酸菜）")
cur.execute("UPDATE inventory SET status='in_stock', quantity=0.3 WHERE id=6")
print("   ✅ ID 6 已恢复到 in_stock, 0.3kg")

conn.commit()

# 验证
print("\n6️⃣ 验证：")
cur.execute("SELECT id, item_name, quantity, unit, status FROM inventory WHERE id IN (6, 17, 18)")
rows = cur.fetchall()
for row in rows:
    print(f"   ID {row[0]}: {row[1][:40]} = {row[2]}{row[3]} [{row[4]}]")

cur.close()
conn.close()

print("\n✅ 回滚完成！\n")
