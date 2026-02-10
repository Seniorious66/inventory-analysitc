"""
通用回滚工具 - 自动恢复所有非 in_stock 状态的父项

功能：
1. 自动检测所有 status != 'in_stock' 的**父项**（parent_id IS NULL）
2. 删除它们的所有子项
3. 将父项恢复到 in_stock 状态
4. 提供交互式确认

使用方法：
    uv run src/rollback_all.py                    # 查看需要回滚的项目
    uv run src/rollback_all.py --confirm          # 执行回滚

注意：
- 此脚本只恢复父项（parent_id IS NULL）
- 子项会被自动删除
- 父项的 quantity 保持不变（已经按设计保留原值）
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

def find_items_to_rollback():
    """查找需要回滚的项目"""
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT')
    )
    cur = conn.cursor()
    
    # 查找所有非 in_stock 的父项
    cur.execute("""
        SELECT id, item_name, quantity, unit, status
        FROM inventory 
        WHERE parent_id IS NULL 
        AND status != 'in_stock'
        ORDER BY id;
    """)
    items = cur.fetchall()
    
    # 统计每个父项有多少子项
    items_with_children = []
    for item in items:
        item_id = item[0]
        cur.execute("SELECT COUNT(*) FROM inventory WHERE parent_id=%s", (item_id,))
        child_count = cur.fetchone()[0]
        items_with_children.append((*item, child_count))
    
    cur.close()
    conn.close()
    
    return items_with_children

def execute_rollback(items):
    """执行回滚操作"""
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT')
    )
    cur = conn.cursor()
    
    print("\n🔄 开始执行回滚...\n")
    
    total_children_deleted = 0
    
    for item in items:
        item_id, item_name, quantity, unit, status, child_count = item
        
        # 1. 删除子项
        if child_count > 0:
            cur.execute("DELETE FROM inventory WHERE parent_id=%s", (item_id,))
            deleted = cur.rowcount
            total_children_deleted += deleted
            print(f"   🗑️  删除 ID {item_id} 的 {deleted} 个子项")
        
        # 2. 恢复父项状态
        cur.execute("UPDATE inventory SET status='in_stock' WHERE id=%s", (item_id,))
        print(f"   ✅ 恢复 ID {item_id} ({item_name[:30]}) = {quantity}{unit} -> in_stock")
    
    conn.commit()
    
    print(f"\n📊 统计：")
    print(f"   - 恢复父项: {len(items)} 个")
    print(f"   - 删除子项: {total_children_deleted} 个")
    print(f"\n✅ 回滚完成！\n")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    items = find_items_to_rollback()
    
    if not items:
        print("\n✅ 没有需要回滚的项目（所有父项都是 in_stock 状态）\n")
        sys.exit(0)
    
    # 显示待回滚项目
    print("\n📋 发现以下需要回滚的父项：\n")
    for item in items:
        item_id, item_name, quantity, unit, status, child_count = item
        child_info = f", {child_count} 个子项" if child_count > 0 else ""
        print(f"   ID {item_id:>3}: {item_name[:40]:40} = {quantity:>8}{unit:<6} [{status}]{child_info}")
    
    print(f"\n   共 {len(items)} 个父项需要回滚\n")
    
    # 检查是否确认执行
    if '--confirm' in sys.argv:
        execute_rollback(items)
    else:
        print("💡 提示：使用 --confirm 参数执行回滚")
        print("   uv run src/rollback_all.py --confirm\n")
