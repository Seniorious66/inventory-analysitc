import os
import psycopg2
from dotenv import load_dotenv

# 1. 加载 .env 文件里的配置
load_dotenv()

def connect_to_db():
    try:
        # 2. 尝试连接
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT")
        )
        print("✅ 成功连接到数据库！")
        
        # 3. 创建一个游标 (Cursor) 用来执行 SQL
        cur = conn.cursor()
        
        # 4. 执行一个简单的查询测试
        cur.execute("SELECT version();")
        db_version = cur.fetchone()
        print(f"🐘 PostgreSQL 版本: {db_version[0]}")
        
        # 5. 关闭连接
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ 连接失败: {e}")

if __name__ == "__main__":
    connect_to_db()