"""
库存恢复工具
用途：手动修正库存中某个物品的数量

使用方法：
    uv run src/restore_item.py <item_id> <new_quantity>
    
示例：
    # 恢复 ID 17 的鸡腿肉到 1000g
    uv run src/restore_item.py 17 1000
    
    # 恢复 ID 19 的鸡蛋到 15 pack
    uv run src/restore_item.py 19 15

注意：
    - 此脚本直接修改数据库，请谨慎使用
    - 不会修改其他字段（位置、保质期等）
    - 仅用于测试或紧急修正数据
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

def restore_item_quantity(item_id, new_quantity):
    """恢复指定物品的数量"""
    try:
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT')
        )
        cur = conn.cursor()
        
        # 先查询当前状态
        cur.execute('SELECT id, item_name, quantity, unit, location, status FROM inventory WHERE id=%s', (item_id,))
        row = cur.fetchone()
        
        if not row:
            print(f"❌ 错误：ID {item_id} 不存在于库存中")
            return False
        
        old_qty = row[2]
        unit = row[3]
        item_name = row[1]
        
        print(f"\n📦 物品信息：{item_name}")
        print(f"   当前数量：{old_qty}{unit}")
        print(f"   目标数量：{new_quantity}{unit}")
        
        # 更新数量
        cur.execute('UPDATE inventory SET quantity=%s WHERE id=%s', (new_quantity, item_id))
        conn.commit()
        
        print(f"✅ 已成功恢复 ID {item_id} 的数量到 {new_quantity}{unit}\n")
        
        cur.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ 操作失败: {e}")
        if 'conn' in locals():
            conn.rollback()
        return False

def list_all_items():
    """列出所有库存物品"""
    try:
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT')
        )
        cur = conn.cursor()
        
        cur.execute('SELECT id, item_name, quantity, unit, location FROM inventory WHERE status != %s ORDER BY id', ('consumed',))
        rows = cur.fetchall()
        
        print("\n📋 当前库存列表：\n")
        for row in rows:
            print(f"   ID {row[0]:>3}: {row[1]:40} = {row[2]:>8}{row[3]:<6} @ {row[4]}")
        print()
        
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ 查询失败: {e}")

if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(__doc__)
        print("\n💡 提示：使用 'list' 参数查看所有库存物品")
        print("   uv run src/restore_item.py list\n")
    
    elif len(sys.argv) == 2 and sys.argv[1] == 'list':
        list_all_items()
    
    elif len(sys.argv) == 3:
        try:
            item_id = int(sys.argv[1])
            new_quantity = float(sys.argv[2])
            restore_item_quantity(item_id, new_quantity)
        except ValueError:
            print("❌ 错误：参数格式不正确")
            print("   正确格式：uv run src/restore_item.py <item_id> <quantity>")
            print("   示例：uv run src/restore_item.py 17 1000")
    
    else:
        print("❌ 错误：参数数量不正确")
        print(__doc__)
