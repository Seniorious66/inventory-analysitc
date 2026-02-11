import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path='src/.env')

conn = psycopg2.connect(
    dbname=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT')
)
cur = conn.cursor()

# 查找鸡蛋相关的项目
cur.execute("""
    SELECT id, item_name, status, parent_id, 
           created_at, updated_at
    FROM inventory 
    WHERE item_name LIKE '%鸡蛋%' OR item_name LIKE '%蛋%'
    ORDER BY COALESCE(updated_at, created_at) DESC
    LIMIT 10;
""")

print('\n鸡蛋相关项目：')
print(f"{'ID':>4} {'名称':20} {'状态':12} {'父ID':>6} {'创建时间':20} {'更新时间':20}")
print('-' * 100)
for row in cur.fetchall():
    item_id, name, status, parent_id, created, updated = row
    parent_str = str(parent_id) if parent_id else 'NULL'
    created_str = created.strftime('%Y-%m-%d %H:%M:%S') if created else 'NULL'
    updated_str = updated.strftime('%Y-%m-%d %H:%M:%S') if updated else 'NULL'
    print(f'{item_id:4} {name:20} {status:12} {parent_str:>6} {created_str:20} {updated_str:20}')

print('\n查看所有非 in_stock 的父项（最后活动时间）：')
cur.execute("""
    WITH parent_activity AS (
        SELECT 
            p.id, p.item_name, p.status, 
            p.updated_at,
            COALESCE((
                SELECT MAX(child.created_at) 
                FROM inventory child 
                WHERE child.parent_id = p.id
            ), p.updated_at) as max_child_created,
            GREATEST(
                p.updated_at,
                COALESCE((
                    SELECT MAX(child.created_at) 
                    FROM inventory child 
                    WHERE child.parent_id = p.id
                ), p.updated_at)
            ) as last_activity_time
        FROM inventory p
        WHERE p.parent_id IS NULL 
        AND p.status != 'in_stock'
    )
    SELECT id, item_name, status, updated_at, max_child_created, last_activity_time
    FROM parent_activity
    ORDER BY last_activity_time DESC
    LIMIT 5;
""")

print(f"\n{'ID':>4} {'名称':20} {'状态':12} {'父更新时间':20} {'子最新创建':20} {'最后活动':20}")
print('-' * 120)
for row in cur.fetchall():
    item_id, name, status, updated, max_child, last_act = row
    updated_str = updated.strftime('%m-%d %H:%M:%S') if updated else 'NULL'
    max_child_str = max_child.strftime('%m-%d %H:%M:%S') if max_child else 'NULL'
    last_act_str = last_act.strftime('%m-%d %H:%M:%S') if last_act else 'NULL'
    print(f'{item_id:4} {name:20} {status:12} {updated_str:20} {max_child_str:20} {last_act_str:20}')

cur.close()
conn.close()
