"""
数据库迁移：添加 parent_id 字段和新状态支持

功能：
1. 添加 parent_id 字段用于物品分割追溯
2. 更新 status 约束，支持 processed 和 waste 状态
3. 创建必要的索引

使用方法：
    uv run src/migrate_add_parent_tracking.py

注意：
- 此脚本是幂等的，可以安全地重复执行
- 会自动检查字段是否已存在，避免重复添加
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

def run_migration():
    try:
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT')
        )
        cur = conn.cursor()
        
        print("🔧 开始数据库迁移...\n")
        
        # 1. 检查并添加 parent_id 字段
        print("1️⃣ 检查 parent_id 字段...")
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name='inventory' AND column_name='parent_id'
            );
        """)
        parent_id_exists = cur.fetchone()[0]
        
        if not parent_id_exists:
            print("   ➕ 添加 parent_id 字段...")
            cur.execute("ALTER TABLE inventory ADD COLUMN parent_id INTEGER;")
            cur.execute("""
                ALTER TABLE inventory ADD CONSTRAINT fk_parent
                    FOREIGN KEY (parent_id) REFERENCES inventory(id);
            """)
            print("   ✅ parent_id 字段已添加")
        else:
            print("   ✓ parent_id 字段已存在，跳过")
        
        # 2. 更新 status 约束
        print("\n2️⃣ 更新 status 约束...")
        
        # 先删除旧约束
        cur.execute("""
            SELECT constraint_name 
            FROM information_schema.table_constraints 
            WHERE table_name='inventory' 
            AND constraint_type='CHECK' 
            AND constraint_name LIKE '%status%';
        """)
        old_constraint = cur.fetchone()
        
        if old_constraint:
            constraint_name = old_constraint[0]
            print(f"   🗑️  删除旧约束: {constraint_name}")
            cur.execute(f"ALTER TABLE inventory DROP CONSTRAINT {constraint_name};")
        
        print("   ➕ 添加新 status 约束（支持 processed 和 waste）...")
        cur.execute("""
            ALTER TABLE inventory ADD CONSTRAINT inventory_status_check
                CHECK (status IN ('in_stock', 'consumed', 'processed', 'waste'));
        """)
        print("   ✅ status 约束已更新")
        
        # 3. 创建索引
        print("\n3️⃣ 创建索引...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_parent_id ON inventory(parent_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory(status);")
        print("   ✅ 索引已创建")
        
        # 4. 提交更改
        conn.commit()
        
        # 5. 验证
        print("\n4️⃣ 验证迁移结果...")
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns 
            WHERE table_name = 'inventory' 
            AND column_name IN ('parent_id', 'status')
            ORDER BY column_name;
        """)
        columns = cur.fetchall()
        print("   当前字段状态:")
        for col in columns:
            print(f"     - {col[0]}: {col[1]} (nullable: {col[2]})")
        
        print("\n✅ 迁移成功完成！\n")
        print("📋 新功能说明:")
        print("   - processed: 物品已被分割/处理（quantity=0）")
        print("   - waste: 物品已丢弃/浪费（坏了、难吃等）")
        print("   - parent_id: 追溯分割来源\n")
        
        cur.close()
        conn.close()
        
    except psycopg2.errors.DuplicateObject as e:
        print(f"⚠️  约束或索引已存在，跳过: {e}")
        conn.rollback()
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        if 'conn' in locals():
            conn.rollback()
        raise

if __name__ == "__main__":
    run_migration()
