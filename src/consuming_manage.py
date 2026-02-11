import os
import json
import psycopg2
import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ==========================================
# 1. 配置
# ==========================================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
env_path = os.path.join(current_dir, '.env')
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

# ==========================================
# 2. 数据库操作工具
# ==========================================
def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )

def fetch_current_inventory():
    """获取所有在库物品，供 AI 参考"""
    conn = get_db_connection()
    cur = conn.cursor()
    # 按创建时间排序，实现先进先出（FIFO）
    cur.execute("""
        SELECT id, item_name, quantity, unit, location, expiry_date, status, created_at
        FROM inventory 
        WHERE UPPER(status) = 'IN_STOCK'
        ORDER BY created_at ASC
    """)
    rows = cur.fetchall()
    
    inventory_list = []
    for row in rows:
        inventory_list.append({
            "id": row[0],
            "name": row[1],
            "qty": float(row[2]), # 转成 float 方便 AI 计算
            "unit": row[3],
            "loc": row[4],
            "exp": str(row[5]),
            "created": row[7].strftime('%Y-%m-%d %H:%M:%S') if row[7] else None
        })
    
    cur.close()
    conn.close()
    return inventory_list

def execute_actions(actions, inventory_snapshot=None):
    """执行 AI 生成的指令"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    print("\n⚡ 正在执行数据库变更...")
    
    # 可选的验证层：检查计算合理性
    if inventory_snapshot:
        print("🔍 验证 AI 计算...")
        has_warnings = False
        has_errors = False
        
        for action in actions:
            if action.get('action') == 'INSERT' and 'parent_id' in action:
                # 检查新增子项的数量合理性
                parent_id = action['parent_id']
                new_qty = action['quantity']
                unit = action['unit']
                
                # 从快照中找到父项数据
                parent = next((item for item in inventory_snapshot if item['id'] == parent_id), None)
                if parent:
                    parent_qty = parent['qty']
                    parent_unit = parent['unit']
                    item_name = parent['name']
                    
                    # 检查：单位不匹配
                    if unit != parent_unit:
                        has_errors = True
                        print(f"\n   ❌ 【严重错误】ID {parent_id} ({item_name})")
                        print(f"       父项单位: {parent_unit}, 但子项使用了: {unit}")
                        print(f"       单位必须保持一致！")
                    
                    # 检查：离散单位不应该有小数
                    discrete_units = ['颗', '个', 'pack', '片', '块', '条', '根']
                    if unit in discrete_units and new_qty != int(new_qty):
                        has_errors = True
                        print(f"\n   ❌ 【严重错误】ID {parent_id} ({item_name})")
                        print(f"       单位 '{unit}' 是离散单位，不应该有小数")
                        print(f"       AI 返回的数量: {new_qty}{unit}")
                        print(f"       这表明 AI 的计算出错了！")
                    
                    # 检查：子项数量大于父项
                    if new_qty > parent_qty:
                        has_warnings = True
                        print(f"\n   ⚠️  【异常】ID {parent_id} ({item_name})")
                        print(f"       父项总量: {parent_qty}{parent_unit}")
                        print(f"       子项数量: {new_qty}{unit}")
                        print(f"       子项数量超过父项！")
            
            if action.get('action') == 'UPDATE' and 'quantity' in action:
                item_id = action['id']
                new_qty = action['quantity']
                
                # 从快照中找到原始数据
                original = next((item for item in inventory_snapshot if item['id'] == item_id), None)
                if original:
                    original_qty = original['qty']
                    original_unit = original['unit']
                    consumed_amount = original_qty - new_qty
                    item_name = original['name']
                    
                    # 检查 1: 如果新数量大于原始数量（除非是移动位置）
                    if new_qty > original_qty and 'location' not in action:
                        has_warnings = True
                        print(f"\n   ⚠️  【异常】ID {item_id} ({item_name})")
                        print(f"       原始库存: {original_qty}{original_unit}")
                        print(f"       AI 计算后: {new_qty}{original_unit}")
                        print(f"       问题: 消耗操作后数量反而增加了 {new_qty - original_qty}{original_unit}！")
                    
                    # 检查 2: 负数检查
                    elif new_qty < 0:
                        has_warnings = True
                        print(f"\n   ❌ 【错误】ID {item_id} ({item_name})")
                        print(f"       AI 返回的数量为负数: {new_qty}{original_unit}")
                        raise ValueError(f"Invalid negative quantity for item {item_id}: {new_qty}")
                    
                    # 检查 3: 消耗量异常大（超过100%）
                    elif consumed_amount < 0:
                        has_warnings = True
                        print(f"\n   ⚠️  【可疑】ID {item_id} ({item_name})")
                        print(f"       原始库存: {original_qty}{original_unit}")
                        print(f"       AI 计算后: {new_qty}{original_unit}")
                        print(f"       计算的消耗量为负: {consumed_amount}{original_unit}")
                    
                    # 检查：信息性提示（正常消耗）
                    elif consumed_amount > 0:
                        print(f"   ✓ ID {item_id} ({item_name}): {original_qty}{original_unit} → {new_qty}{original_unit} (消耗 {consumed_amount}{original_unit})")
        
        # 检查：验证分割操作的总和
        # 统计每个父项的所有子项数量总和
        parent_children_map = {}
        for action in actions:
            if action.get('action') == 'INSERT' and 'parent_id' in action:
                parent_id = action['parent_id']
                quantity = action['quantity']
                if parent_id not in parent_children_map:
                    parent_children_map[parent_id] = []
                parent_children_map[parent_id].append(quantity)
        
        # 验证每个父项的子项总和
        for parent_id, child_quantities in parent_children_map.items():
            parent = next((item for item in inventory_snapshot if item['id'] == parent_id), None)
            if parent:
                parent_qty = parent['qty']
                parent_unit = parent['unit']
                item_name = parent['name']
                children_sum = sum(child_quantities)
                
                # 允许浮点误差
                epsilon = 0.01
                if abs(children_sum - parent_qty) > epsilon:
                    has_errors = True
                    print(f"\n   ❌ 【严重错误】ID {parent_id} ({item_name})")
                    print(f"       父项总量: {parent_qty}{parent_unit}")
                    print(f"       子项总和: {children_sum}{parent_unit}")
                    print(f"       差异: {abs(children_sum - parent_qty)}{parent_unit}")
                    print(f"       子项总和必须等于父项数量！")
                else:
                    print(f"   ✓ ID {parent_id} ({item_name}): 子项总和 {children_sum}{parent_unit} = 父项 {parent_qty}{parent_unit}")
        
        if not has_warnings:
            print("   ✅ 验证通过，无异常")
        elif has_errors:
            print("\n   ❌ 发现严重错误，中止执行！")
            print("   请检查用户命令或AI的理解是否有误")
            conn.close()
            return
        else:
            print("\n   ⚠️  发现异常，但将继续执行。如需中止请按 Ctrl+C")
    
    try:
        for action in actions:
            act_type = action.get('action')
            
            if act_type == 'UPDATE':
                # 用于：仅移动位置、调整保质期（数量不变）
                # 动态构建 SQL，只更新提供的字段
                update_fields = []
                update_values = []
                
                if 'location' in action:
                    update_fields.append("location=%s")
                    update_values.append(action['location'])
                
                # 注意：quantity 不应该在 UPDATE 中出现！
                if 'quantity' in action:
                    print(f"   ⚠️  警告：UPDATE 操作不应修改数量！ID {action['id']}")
                    # 跳过 quantity 更新
                
                if 'status' in action:
                    update_fields.append("status=%s")
                    update_values.append(action['status'])
                
                if 'expiry_date' in action:
                    update_fields.append("expiry_date=%s")
                    update_values.append(action['expiry_date'])
                
                if not update_fields:
                    print(f"   ⚠️  警告：UPDATE 操作 ID {action['id']} 没有提供任何更新字段")
                    continue
                
                # 添加 updated_at 字段更新
                update_fields.append("updated_at=CURRENT_TIMESTAMP")
                update_values.append(action['id'])  # WHERE 条件的 ID
                sql = f"UPDATE inventory SET {', '.join(update_fields)} WHERE id=%s"
                cur.execute(sql, tuple(update_values))
                
                expiry_info = f", 保质期至 {action.get('expiry_date')}" if 'expiry_date' in action else ""
                location_info = f" @ {action['location']}" if 'location' in action else ""
                print(f"   🔧 修改 ID {action['id']}: {location_info}{expiry_info}")

            elif act_type == 'INSERT':
                # 用于：切割出来的新肉块（可能有 parent_id）
                parent_id = action.get('parent_id')  # 分割场景会有父节点 ID
                child_status = action.get('status', 'in_stock')  # 子节点可能是 in_stock 或 consumed
                
                if parent_id:
                    # 有父节点：这是分割子节点
                    sql = """
                        INSERT INTO inventory (item_name, category, location, quantity, unit, expiry_date, status, parent_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    cur.execute(sql, (
                        action['item_name'],
                        action.get('category', 'uncategorized'),
                        action.get('location', 'fridge'),  # consumed 的可能没有 location
                        action['quantity'],
                        action['unit'],
                        action.get('expiry_date'),
                        child_status,
                        parent_id
                    ))
                    status_emoji = "🗑️" if child_status == 'consumed' else "📦"
                    print(f"   {status_emoji} 新增子项 (父ID={parent_id}): {action['item_name']} ({action['quantity']}{action['unit']}) -> {child_status}")
                else:
                    # 无父节点：普通新增
                    sql = """
                        INSERT INTO inventory (item_name, category, location, quantity, unit, expiry_date, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """
                    cur.execute(sql, (
                        action['item_name'],
                        action.get('category', 'uncategorized'),
                        action['location'],
                        action['quantity'],
                        action['unit'],
                        action['expiry_date'],
                        'in_stock'
                    ))
                    print(f"   ➕ 新增: {action['item_name']} ({action['quantity']}) -> {action['location']}")

            elif act_type == 'MARK_PROCESSED':
                # 用于：将父节点标记为 processed（数量保持不变）
                if 'id' in action:
                    # 只修改 status，不修改 quantity，但要更新 updated_at
                    cur.execute("UPDATE inventory SET status='processed', updated_at=CURRENT_TIMESTAMP WHERE id=%s", (action['id'],))
                    print(f"   ✂️ 标记为已处理 ID {action['id']} (数量保持不变)")
            
            elif act_type == 'MARK_WASTE':
                # 用于：标记为浪费（数量保持不变）
                if 'id' in action:
                    cur.execute("UPDATE inventory SET status='waste', updated_at=CURRENT_TIMESTAMP WHERE id=%s", (action['id'],))
                    print(f"   🗑️ 标记为废弃 ID {action['id']} (数量保持不变)")
            
            elif act_type == 'CONSUME_LOG':
                # 用于：完全消耗掉的（数量保持不变）
                if 'id' in action:
                    cur.execute("UPDATE inventory SET status='consumed', updated_at=CURRENT_TIMESTAMP WHERE id=%s", (action['id'],))
                    print(f"   ✅ 标记为已消耗 ID {action['id']} (数量保持不变)")
        
        conn.commit()
        print("✅ 所有操作已提交！")
        
    except Exception as e:
        print(f"❌ 执行出错，回滚: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

# ==========================================
# 3. AI 核心逻辑
# ==========================================
def parse_and_execute(user_command):
    # 1. 抓取当前库存
    print("🔍正在读取当前库存...")
    current_inventory = fetch_current_inventory()
    
    if not current_inventory:
        print("⚠️ 仓库是空的，没法操作。")
        return

    # 2. 构建 Prompt
    inventory_str = json.dumps(current_inventory, ensure_ascii=False, indent=1)
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    # 打印用户命令中提到的物品的当前库存（用于审计）
    print(f"📝 用户命令: {user_command}")
    print("📦 当前所有在库物品（按创建时间排序，先进先出）:")
    for item in current_inventory:
        created_info = f" [创建:{item['created']}]" if item.get('created') else ""
        print(f"   ID {item['id']}: {item['name']} = {item['qty']}{item['unit']} @ {item['loc']}{created_info}")

    prompt = f"""
    你是家庭库存管理系统的数据库管理员。今天是 {today}。

    ### 当前库存（按创建时间排序，最早的在前）：
    {inventory_str}

    ### 用户指令：
    "{user_command}"

    ### 任务：
    生成 JSON 操作计划来执行用户的指令。

    ### 字段格式要求：
    - item_name: 物品名称（中文）
    - category: "vegetable", "dairy", "meat", "seafood", "staple", "fruit", "snack", "beverage", "uncategorized"
    - location: 只能是 "fridge", "freezer", "pantry"（小写，不要用中文）
    - quantity: 数字，必须与原物品保持相同单位
    - unit: "kg", "g", "个", "颗", "瓶" 等，必须与原物品一致
    - expiry_date: YYYY-MM-DD 格式
    - status: "in_stock", "consumed", "processed", "waste"（小写，用下划线）
    - parent_id: 分割场景中的父项ID

    ### 核心规则：

    **1. 物品选择逻辑（重要）：**

    a) 含糊描述（如"鸡蛋"、"牛肉"）：
    → 选择列表中第一个匹配项（最早创建的，实现先进先出）

    b) 带数量描述（如"1.1kg的牛肉"、"500g肉"）：
    → 在同单位的物品中，选择数量最接近的
    → 例：用户说"1.1kg牛肉"，库存有 1.19kg 和 500g，选择 1.19kg（同为kg单位且最接近）
    → 不同单位需要转换后比较：1kg=1000g, 1斤=500g

    c) 带属性描述（如"较大的"、"冰箱里的"）：
    → "较大/最大/最重" → 选数量最大的（注意单位转换）
    → "较小/最小/最轻" → 选数量最小的
    → 位置（"冰箱/冷冻/室温"）→ 匹配 location 字段

    **2. 数量计算：**
    - 分割操作：所有子项数量之和 = 父项数量
    - 单位保持：子项必须与父项使用相同单位
    - 离散单位（颗、个、pack）不能有小数
    - 计算公式：剩余 = 原数量 - 消耗数量

    **3. 状态转换：**
    - 部分消耗：MARK_PROCESSED(父项) + INSERT(剩余子项, status='in_stock') + INSERT(消耗子项, status='consumed')
    - 全部消耗：CONSUME_LOG（直接标记为consumed）
    - 全部扔掉：MARK_WASTE（标记为waste）
    - 仅移动位置：UPDATE（只改location和expiry_date，不改quantity）

    **4. 保质期智能计算：**
    当位置变化时，根据食物类型和新环境重新计算保质期：
    - 肉类/海鲜：温度敏感
    - 冷冻→冷藏：大幅缩短
    - 冷藏→冷冻：延长
    保守估计，宁短勿长。

    ### 输出格式（JSON数组）：
    [
    // 部分消耗示例（从5颗中吃2颗）
    {{ "action": "MARK_PROCESSED", "id": 34 }},
    {{ "action": "INSERT", "item_name": "鸡蛋", "quantity": 3, "unit": "颗", "location": "fridge", "category": "dairy", "expiry_date": "2026-02-20", "parent_id": 34, "status": "in_stock" }},
    {{ "action": "INSERT", "item_name": "鸡蛋", "quantity": 2, "unit": "颗", "category": "dairy", "parent_id": 34, "status": "consumed" }},

    // 全部消耗
    {{ "action": "CONSUME_LOG", "id": 12 }},

    // 全部扔掉
    {{ "action": "MARK_WASTE", "id": 15 }},

    // 仅移动位置
    {{ "action": "UPDATE", "id": 20, "location": "freezer", "expiry_date": "2026-08-15" }}
    ]

    ### 关键约束：
    1. 含糊描述时，必须选择列表中第一个匹配项（FIFO原则）
    2. 数量描述时，选择同单位中数量最接近的
    3. 父项数量永远不变，所有数量变化通过子项实现
    4. 所有子项数量之和必须等于父项数量
    5. 不要在 UPDATE 中修改 quantity
    6. location 只能用英文小写："fridge"、"freezer"、"pantry"
    """

    print("🤖 正在思考如何操作数据库...")
    
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json'
            )
        )
        
        plan = json.loads(response.text)
        print(f"📋 AI 计划执行 {len(plan)} 个动作。")
        print("🔍 AI 返回的完整计划：")
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        
        # 验证 AI 选择的项目是否正确（FIFO检查）
        print("\n🔎 验证项目选择...")
        for action in plan:
            if action.get('action') in ['MARK_PROCESSED', 'CONSUME_LOG', 'MARK_WASTE'] and 'id' in action:
                selected_id = action['id']
                selected_item = next((item for item in current_inventory if item['id'] == selected_id), None)
                if selected_item:
                    item_name = selected_item['name']
                    # 查找同名的其他项目
                    same_name_items = [item for item in current_inventory if item['name'] == item_name]
                    if len(same_name_items) > 1:
                        # 检查是否选择了第一个（最早创建的）
                        first_item = same_name_items[0]
                        if selected_id != first_item['id']:
                            print(f"   ⚠️  警告：发现多个 '{item_name}'")
                            print(f"      AI 选择了 ID {selected_id} (创建:{selected_item.get('created', 'N/A')})")
                            print(f"      但最早的是 ID {first_item['id']} (创建:{first_item.get('created', 'N/A')})")
                            print(f"      如果用户命令没有特别指定，应优先消耗最早的项目（FIFO）")
                        else:
                            print(f"   ✓ 正确选择了最早创建的 '{item_name}' (ID {selected_id})")
                    else:
                        print(f"   ✓ 选择了唯一的 '{item_name}' (ID {selected_id})")
        
        # 3. 执行（传入库存快照用于验证）
        execute_actions(plan, current_inventory)

    except Exception as e:
        print(f"❌ AI 处理失败: {e}")

# ==========================================
# 4. 入口
# ==========================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        command = sys.argv[1]
    else:
        # 默认测试命令
        command = input("请输入操作指令 (例如: 把冰箱里的牛肉移到冷冻室): ")
    
    parse_and_execute(command)