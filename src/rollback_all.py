"""
通用回滚工具 - 回滚最近的修改操作

功能：
1. 自动检测 status = 'processed' 的项目（表示被分割/处理过）
2. 删除它们的所有子项
3. 将项目恢复到 in_stock 状态
4. 提供交互式确认

使用方法：
    uv run src/rollback_all.py                    # 查看今天修改的需要回滚的项目
    uv run src/rollback_all.py --confirm          # 执行回滚今天的修改
    uv run src/rollback_all.py --last=1           # 查看最近1次修改
    uv run src/rollback_all.py --last=1 --confirm # 回滚最近1次修改
    uv run src/rollback_all.py --last=5 --confirm # 回滚最近5次修改
    uv run src/rollback_all.py --days=7           # 查看最近7天的项目
    uv run src/rollback_all.py --days=7 --confirm # 回滚最近7天的修改
    uv run src/rollback_all.py --all              # 查看所有需要回滚的项目
    uv run src/rollback_all.py --all --confirm    # 回滚所有（危险！）

时间限制：
- 默认：只处理今天（当日）修改的项目
- --last=N：只处理最近 N 次修改（按最后活动时间排序）
- --days=N：处理最近 N 天修改的项目
- --all：处理所有项目（无时间限制，谨慎使用）

回滚逻辑：
- 只回滚 status = 'processed' 的项目（表示被分割/处理，有子项）
- consumed 和 waste 是终态，不会被回滚
- 最后活动时间 = MAX(项目的 updated_at, 所有子项的 MAX(created_at, updated_at))
- 支持多层嵌套：如果子项被再次处理，只回滚最后一次操作的项目
- 会删除该项目的所有子项，并恢复项目状态为 in_stock

注意：
- 此脚本只处理 status = 'processed' 的项目（不限于顶层父项）
- 子项会被自动删除
- 项目的 quantity 和 parent_id 保持不变
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
        'days': None,
        'last': None
    }
    
    # 解析 --days=N 和 --last=N 参数
    for arg in sys.argv:
        if arg.startswith('--days='):
            try:
                args['days'] = int(arg.split('=')[1])
            except ValueError:
                print("❌ 错误：--days 参数必须是整数（如 --days=7）\n")
                sys.exit(1)
        elif arg.startswith('--last='):
            try:
                args['last'] = int(arg.split('=')[1])
            except ValueError:
                print("❌ 错误：--last 参数必须是整数（如 --last=1）\n")
                sys.exit(1)
    
    # 参数互斥检查
    exclusive_params = sum([args['all'], args['days'] is not None, args['last'] is not None])
    if exclusive_params > 1:
        print("❌ 错误：--all、--days 和 --last 参数不能同时使用\n")
        sys.exit(1)
    
    return args

def find_items_to_rollback(days=None, all_items=False, last=None):
    """查找需要回滚的项目
    
    逻辑：
    1. 找到所有 status = 'processed' 的项目（被分割/处理过，有子项）
    2. 计算每个项目的"最后活动时间" = MAX(项目的 updated_at, 所有子项的 MAX(created_at, updated_at))
    3. 按最后活动时间排序
    
    Args:
        days: 回滚最近 N 天的项目，None 表示使用默认
        all_items: True 表示回滚所有项目（无时间限制）
        last: 回滚最近 N 次修改，None 表示不使用此模式
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
    limit_clause = ""
    
    if all_items:
        time_desc = "所有时间"
    elif last is not None:
        # 按最近N次修改限制（不使用时间过滤，使用LIMIT）
        time_desc = f"最近 {last} 次修改" if last > 1 else "最近一次修改"
        limit_clause = f"LIMIT {last}"
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
    # 对于 --last 模式，需要计算每个父项的"最后活动时间"（考虑子项创建时间）
    if last is not None:
        query = f"""
            WITH parent_activity AS (
                SELECT 
                    p.id, p.item_name, p.quantity, p.unit, p.status, 
                    p.created_at, p.updated_at, p.parent_id,
                    -- 计算最后活动时间：父项更新时间 vs 子项最新的创建/更新时间
                    GREATEST(
                        p.updated_at,
                        COALESCE((
                            SELECT MAX(GREATEST(child.created_at, child.updated_at))
                            FROM inventory child 
                            WHERE child.parent_id = p.id
                        ), p.updated_at)
                    ) as last_activity_time
                FROM inventory p
                WHERE p.status = 'processed'
            )
            SELECT id, item_name, quantity, unit, status, created_at, updated_at, last_activity_time, parent_id
            FROM parent_activity
            ORDER BY last_activity_time DESC, id DESC
            LIMIT {last};
        """
    else:
        # 原有的时间范围过滤逻辑
        query = f"""
            SELECT DISTINCT 
                p.id, p.item_name, p.quantity, p.unit, p.status, 
                p.created_at, p.updated_at,
                NULL as last_activity_time,
                p.parent_id
            FROM inventory p
            WHERE p.status = 'processed'
            AND (
                -- 情况1：项本身在时间范围内被修改过
                (1=1 {time_filter_updated})
                -- 情况2：项有在时间范围内创建的子项
                OR EXISTS (
                    SELECT 1 FROM inventory child
                    WHERE child.parent_id = p.id 
                    {time_filter_created.replace('c.created_at', 'child.created_at') if time_filter_created else ''}
                )
            )
            ORDER BY p.updated_at DESC, p.id DESC;
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
    
    会删除项目的所有子项（不管何时创建），并恢复项目状态为 in_stock
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
        # item 包含: id, item_name, quantity, unit, status, created_at, updated_at, last_activity_time, parent_id, total_children, recent_children
        item_id = item[0]
        item_name = item[1]
        quantity = item[2]
        unit = item[3]
        total_children = item[9]
        
        # 1. 删除所有子项（不管何时创建）
        if total_children > 0:
            cur.execute("DELETE FROM inventory WHERE parent_id=%s", (item_id,))
            deleted = cur.rowcount
            total_children_deleted += deleted
            print(f"   🗑️  删除 ID {item_id} 的 {deleted} 个子项")
        
        # 2. 恢复项目状态
        cur.execute("UPDATE inventory SET status='in_stock' WHERE id=%s", (item_id,))
        print(f"   ✅ 恢复 ID {item_id} ({item_name[:30]}) = {quantity}{unit} -> in_stock")
    
    conn.commit()
    
    print(f"\n📊 统计：")
    print(f"   - 恢复项目: {len(items)} 个")
    print(f"   - 删除子项: {total_children_deleted} 个")
    print(f"\n✅ 回滚完成！\n")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    args = parse_args()
    
    # 获取需要回滚的项目
    items, time_desc = find_items_to_rollback(days=args['days'], all_items=args['all'], last=args['last'])
    
    if not items:
        if args['count'] is not None or (args['days'] is None and not args['all']):
            print(f"\n✅ 没有需要回滚的项目（没有符合条件的修改）\n")
        else:
            print(f"\n✅ 没有需要回滚的项目（{time_desc}内没有符合条件的修改）\n")
        sys.exit(0)
    
    # 显示待回滚项目
    print(f"\n📋 发现以下需要回滚的项目（{time_desc}）：\n")
    for item in items:
        # item 包含: id, item_name, quantity, unit, status, created_at, updated_at, last_activity_time, parent_id, total_children, recent_children
        item_id = item[0]
        item_name = item[1]
        quantity = item[2]
        unit = item[3]
        status = item[4]
        created_at = item[5]
        updated_at = item[6]
        last_activity_time = item[7]
        parent_id = item[8]
        total_children = item[9]
        recent_children = item[10]
        
        # 格式化时间
        created_str = created_at.strftime('%m-%d %H:%M')
        updated_str = updated_at.strftime('%m-%d %H:%M') if updated_at else '未修改'
        last_activity_str = last_activity_time.strftime('%m-%d %H:%M:%S') if last_activity_time else updated_str
        
        # 父项信息
        parent_info = f" (父ID={parent_id})" if parent_id else " (顶层)"
        
        # 子项信息
        if total_children > 0:
            if recent_children == total_children:
                child_info = f", {total_children}子项"
            else:
                child_info = f", {total_children}子项(其中{recent_children}个在范围内)"
        else:
            child_info = ""
        
        # 主要显示
        print(f"   ID {item_id:>3}{parent_info}: {item_name[:25]:25} = {quantity:>7}{unit:<5} [{status:10}]")
        print(f"           创建: {created_str}  修改: {updated_str}  最后活动: {last_activity_str}{child_info}")
    
    print(f"\n   共 {len(items)} 个项目需要回滚（范围：{time_desc}）")
    print(f"   注意：回滚时会删除该项目的**所有**子项，并将其恢复为 in_stock\n")
    
    # 检查是否确认执行
    if args['confirm']:
        execute_rollback(items)
    else:
        print("💡 提示：使用 --confirm 参数执行回滚")
        print(f"   uv run src/rollback_all.py --confirm")
        if args['all']:
            print("\n⚠️  警告：使用 --all 将回滚所有历史数据，请谨慎确认！")
        elif args['last']:
            print(f"   当前范围：最近 {args['last']} 次修改")
        elif args['days']:
            print(f"   当前范围：最近 {args['days']} 天")
        else:
            print(f"   当前范围：今天（默认）")
        print("\n其他选项：")
        print("   --last=N      # 回滚最近 N 次修改")
        print("   --days=N      # 回滚最近 N 天的数据")
        print("   --all         # 回滚所有数据（危险！）")
        print()

