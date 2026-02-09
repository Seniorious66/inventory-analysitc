import sys
import json
import os
import psycopg2
from dotenv import load_dotenv

# ==========================================
# 1. 路径导航系统 (Path Navigation)
# ==========================================

# 当前脚本所在位置 -> .../inventory-analytic/src
current_script_path = os.path.abspath(__file__)
src_dir = os.path.dirname(current_script_path)

# 项目根目录 (src的上一层) -> .../inventory-analytic
project_root = os.path.dirname(src_dir)

# 数据文件夹位置 -> .../inventory-analytic/data
data_dir = os.path.join(project_root, 'data')

# .env 文件位置 (你说它在 src 里) -> .../inventory-analytic/src/.env
env_path = os.path.join(src_dir, '.env')

# 加载环境变量
load_dotenv(dotenv_path=env_path)

# ==========================================
# 2. 通用入库函数 (The Loader)
# ==========================================

def load_json_to_db(filename):
    """
    参数:
    filename (str): data文件夹下的文件名，例如 'data.json' 或 'new_items.json'
    """
    
    # 自动拼接完整路径
    target_file = os.path.join(data_dir, filename)
    
    print(f"\n📂 准备处理文件: {target_file}")

    try:
        # 1. 读取 JSON
        with open(target_file, 'r', encoding='utf-8') as f:
            inventory_data = json.load(f)
        print(f"   ✅ 读取成功，共 {len(inventory_data)} 条数据")

        # 2. 连接数据库
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT")
        )
        cur = conn.cursor()

        # 3. 准备 SQL
        sql_query = """
            INSERT INTO inventory 
            (item_name, category, location, quantity, unit, expiry_date, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """

        # 4. 循环写入
        print("   🚀 开始写入数据库...")
        success_count = 0
        
        for item in inventory_data:
            # 数据清洗：确保必要的字段存在
            # 这里的 .get('key', default) 是为了防止 JSON 缺字段导致报错
            record = (
                item.get('item_name'), # 必填
                item.get('category'),
                item.get('location'),  # 必填
                item.get('quantity', 1),
                item.get('unit', '个'),
                item.get('expiry_date'),
                item.get('status', 'in_stock')
            )
            
            cur.execute(sql_query, record)
            success_count += 1

        # 5. 提交事务
        conn.commit()
        print(f"   💾 成功入库 {success_count} 条记录！")

        # 6. 关闭
        cur.close()
        conn.close()

    except FileNotFoundError:
        print(f"❌ 错误：在 data 文件夹里找不到 {filename}")
    except Exception as e:
        print(f"❌ 发生未知错误: {e}")
        if 'conn' in locals():
            conn.rollback()

# ==========================================
# 3. 主程序入口
# ==========================================
if __name__ == "__main__":
    # sys.argv 是一个列表，包含了你在终端里输入的所有东西
    # sys.argv[0] 是脚本名字 (src/main.py)
    # sys.argv[1] 是你跟在后面的第一个参数
    
    if len(sys.argv) > 1:
        # 如果你输入了文件名，就用你输入的
        target_filename = sys.argv[1]
    else:
        # 如果你懒得输，就默认用 data.json
        print("⚠️ 未指定文件名，默认使用 data.json")
        target_filename = 'data.json'
    
    # 只要改了这里，以后就可以灵活调用了
    load_json_to_db(target_filename)