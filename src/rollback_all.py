"""
通用回滚工具 - 自动恢复非 in_stock 状态的父项（带时间限制）

功能：
1. 自动检测符合条件的 status != 'in_stock' 的**父项**（parent_id IS NULL）
2. 删除它们的所有子项
3. 将父项恢复到 in_stock 状态
4. 提供交互式确认

使用方法：
    uv run src/rollback_all.py                    # 查看今天修改的需要回滚的项目
    uv run src/rollback_all.py --confirm          # 执行回滚今天的修改
    uv run src/rollback_all.py --days=7           # 查看最近7天的项目
    uv run src/rollback_all.py --days=7 --confirm # 回滚最近7天的修改
    uv run src/rollback_all.py --all              # 查看所有需要回滚的项目
    uv run src/rollback_all.py --all --confirm    # 回滚所有（危险！）

时间限制：
- 默认：只处理今天（当日）修改的项目
- --days=N：处理最近 N 天修改的项目
- --all：处理所有项目（无时间限制，谨慎使用）

回滚逻辑：
- 父项：基于 updated_at（最后修改时间）判断
- 子项：基于 created_at（创建时间）判断
- 会删除时间范围内创建的子项，并恢复对应的父项

注意：
- 此脚本只恢复父项（parent_id IS NULL）
- 子项会被自动删除
- 父项的 quantity 保持不变（已经按设计保留原值）
"""

import os
import sys
import psycopg2
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

def parse_args():
    """解析命令行参数"""
    args = {
        'confirm': '--confirm' in sys.argv,
        'all': '--all' in sys.argv,
        'days': None
    }
    
    # 解析 --days=N 参数
    for arg in sys.argv:
        if arg.startswith('--days='):
            try:
                args['days'] = int(arg.split('=')[1])
            except ValueError:
                print("❌ 错误：--days 参数必须是整数（如 --days=7）\n")
                sys.exit(1)
    
    return args

def find_items_to_rollback(days=None, all_items=False):
    """查找需要回滚的项目
    
    逻辑：
    1. 找到 updated_at 在时间范围内且 status != 'in_stock' 的父项
    2. 找到 created_at 在时间范围内的子项，并回滚它们的父项
    3. 合并去重这两个结果集
    
    Args:
        days: 回滚最近 N 天的项目，None 表示仅今天
        all_items: True 表示回滚所有项目（无时间限制）
    """
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT')
    )
    cur = conn.cursor()
    
    # 构建时间过滤条件
    time_filter_updated = ""
    time_filter_created = ""
    time_desc = ""
    
    if all_items:
        time_desc = "所有时间"
    elif days is not None:
        cutoff_date = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff_date.strftime('%Y-%m-%d')
        time_filter_updated = f"AND p.updated_at >= '{cutoff_str}'"
        time_filter_created = f"AND c.created_at >= '{cutoff_str}'"
        time_desc = f"最近 {days} 天"
    else:
        # 默认：只回滚今天的
        today = datetime.now().strftime('%Y-%m-%d')
        time_filter_updated = f"AND p.updated_at >= '{today}'"
        time_filter_created = f"AND c.created_at >= '{today}'"
        time_desc = "今天"
    
    # 查找需要回滚的父项（两种情况的并集）
    query = f"""
        SELECT DISTINCT 
            p.id, p.item_name, p.quantity, p.unit, p.status, 
            p.created_at, p.updated_at
        FROM inventory p
        WHERE p.parent_id IS NULL 
        AND p.status != 'in_stock'
        AND (
            -- 情况1：父项本身在时间范围内被修改过
            (1=1 {time_filter_updated})
            -- 情况2：父项有在时间范围内创建的子项
            OR EXISTS (
                SELECT 1 FROM inventory child
                WHERE child.parent_id = p.id 
                {time_filter_created.replace('c.created_at', 'child.created_at') if time_filter_created else ''}
            )
        )
        ORDER BY p.id;
    """
    cur.execute(query)
    items = cur.fetchall()
    
    # 统计每个父项有多少子项（以及有多少在时间范围内创建）
    items_with_children = []
    for item in items:
        item_id = item[0]
        
        # 总子项数
        cur.execute("SELECT COUNT(*) FROM inventory WHERE parent_id=%s", (item_id,))
        total_children = cur.fetchone()[0]
        
        # 时间范围内创建的子项数
        if all_items:
            recent_children = total_children
        else:
            if days is not None:
                cutoff_date = datetime.now() - timedelta(days=days)
                cutoff_str = cutoff_date.strftime('%Y-%m-%d')
            else:
                cutoff_str = datetime.now().strftime('%Y-%m-%d')
            
            cur.execute(
                "SELECT COUNT(*) FROM inventory WHERE parent_id=%s AND created_at >= %s",
                (item_id, cutoff_str)
            )
            recent_children = cur.fetchone()[0]
        
        items_with_children.append((*item, total_children, recent_children))
    
    cur.close()
    conn.close()
    
    return items_with_children, time_desc

def execute_rollback(items):
    """执行回滚操作
    
    会删除父项的所有子项（不管何时创建），并恢复父项状态
    """
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
        item_id, item_name, quantity, unit, status, created_at, updated_at, total_children, recent_children = item
        
        # 1. 删除所有子项（不管何时创建）
        if total_children > 0:
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
    args = parse_args()
    
    # 获取需要回滚的项目
    items, time_desc = find_items_to_rollback(days=args['days'], all_items=args['all'])
    
    if not items:
        print(f"\n✅ 没有需要回滚的项目（{time_desc}内没有符合条件的修改）\n")
        sys.exit(0)
    
    # 显示待回滚项目
    print(f"\n📋 发现以下需要回滚的父项（{time_desc}）：\n")
    for item in items:
        item_id, item_name, quantity, unit, status, created_at, updated_at, total_children, recent_children = item
        
        # 格式化时间
        created_str = created_at.strftime('%m-%d %H:%M')
        updated_str = updated_at.strftime('%m-%d %H:%M') if updated_at else '未修改'
        
        # 子项信息
        if total_children > 0:
            if recent_children == total_children:
                child_info = f", {total_children}子项"
            else:
                child_info = f", {total_children}子项(其中{recent_children}个在范围内)"
        else:
            child_info = ""
        
        # 主要显示
        print(f"   ID {item_id:>3}: {item_name[:30]:30} = {quantity:>7}{unit:<5} [{status:10}]")
        print(f"           创建: {created_str}  修改: {updated_str}{child_info}")
    
    print(f"\n   共 {len(items)} 个父项需要回滚（范围：{time_desc}）")
    print(f"   注意：回滚时会删除父项的**所有**子项（不管何时创建）\n")
    
    # 检查是否确认执行
    if args['confirm']:
        execute_rollback(items)
    else:
        print("💡 提示：使用 --confirm 参数执行回滚")
        print(f"   uv run src/rollback_all.py --confirm")
        if args['all']:
            print("\n⚠️  警告：使用 --all 将回滚所有历史数据，请谨慎确认！")
        elif args['days']:
            print(f"   当前范围：最近 {args['days']} 天")
        else:
            print(f"   当前范围：今天（默认）")
        print("\n其他选项：")
        print("   --days=N      # 回滚最近 N 天的数据")
        print("   --all         # 回滚所有数据（危险！）")
        print()

