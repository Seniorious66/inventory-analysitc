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
    # 我们只需要 ID, 名字, 位置, 数量, 单位，用来给 AI 做匹配
    cur.execute("""
        SELECT id, item_name, quantity, unit, location, expiry_date, status
        FROM inventory 
        WHERE UPPER(status) = 'IN_STOCK'
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
            "exp": str(row[5])
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
        
        for action in actions:
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
                    
                    # 检查 4: 信息性提示（正常消耗）
                    elif consumed_amount > 0:
                        print(f"   ✓ ID {item_id} ({item_name}): {original_qty}{original_unit} → {new_qty}{original_unit} (消耗 {consumed_amount}{original_unit})")
        
        if not has_warnings:
            print("   ✅ 验证通过，无异常")
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
                    # 只修改 status，不修改 quantity
                    cur.execute("UPDATE inventory SET status='processed' WHERE id=%s", (action['id'],))
                    print(f"   ✂️ 标记为已处理 ID {action['id']} (数量保持不变)")
            
            elif act_type == 'MARK_WASTE':
                # 用于：标记为浪费（数量保持不变）
                if 'id' in action:
                    cur.execute("UPDATE inventory SET status='waste' WHERE id=%s", (action['id'],))
                    print(f"   🗑️ 标记为废弃 ID {action['id']} (数量保持不变)")
            
            elif act_type == 'CONSUME_LOG':
                # 用于：完全消耗掉的（数量保持不变）
                if 'id' in action:
                    cur.execute("UPDATE inventory SET status='consumed' WHERE id=%s", (action['id'],))
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
    print("📦 相关物品当前库存快照:")
    for item in current_inventory[:5]:  # 只显示前5个作为示例
        print(f"   ID {item['id']}: {item['name']} = {item['qty']}{item['unit']} @ {item['loc']}")
    if len(current_inventory) > 5:
        print(f"   ... 以及其他 {len(current_inventory) - 5} 项")

    prompt = f"""
    You are a database administrator for a home inventory system.
    Today is {today}.
    
    ### CURRENT INVENTORY (Database State):
    {inventory_str}

    ### USER COMMAND:
    "{user_command}"

    ### YOUR TASK:
    Generate a JSON plan to update the database to reflect the user's command.
    
    ### FIELD FORMAT REQUIREMENTS (STRICT):
    ALL fields must use lowercase with underscores (snake_case):
    - item_name: (string) Item name in Chinese/original language
    - category: (string) "vegetable", "dairy", "meat", "seafood", "staple", "fruit", "snack", "beverage", or "uncategorized"
    - location: (string) ONLY "fridge", "freezer", or "pantry" (lowercase, no other values allowed)
    - quantity: (number) MUST maintain the SAME UNIT as the original item. DO NOT convert units (e.g., if item is in "g", keep it in "g", don't convert to "kg")
    - unit: (string) e.g., "kg", "g", "个", "瓶" - MUST match the original item's unit
    - expiry_date: (string) YYYY-MM-DD format
    - status: (string) "in_stock", "consumed", "processed", or "waste" (lowercase with underscore)
    - parent_id: (number, optional) Used in split scenarios to track which parent item was split
    
    CRITICAL UNIT HANDLING:
    - When calculating remaining quantity, NEVER change the unit
    - Example: If item has 1000g and user consumes 500g, result should be 500 (in "g"), NOT 0.5 (mistakenly thinking it's kg)
    - Always check the original "unit" field and perform calculations in that exact unit
    - 1000g - 500g = 500g (quantity: 500, unit: "g")
    - 1.5kg - 0.5kg = 1kg (quantity: 1, unit: "kg")
    
    CALCULATION VERIFICATION:
    - When doing quantity updates, double-check your math
    - For consumption: NEW quantity = ORIGINAL quantity (from inventory) - CONSUMED amount
    - Example from inventory: {{"id": 10, "qty": 1000, "unit": "g"}} 
      User consumes 500g → NEW quantity = 1000 - 500 = 500g (NOT 400g, NOT 0.5)
    - If consuming ALL, quantity becomes 0
    
    ### RULES:
    1. **Identify**: Find the correct item ID from the inventory list based on the user's description (e.g., "270g meat").
    
    2. **Storage Environment & Expiry Date Intelligence**:
       - When storage location changes (freezer ↔ fridge ↔ pantry), YOU MUST intelligently recalculate the expiry_date
       - Consider the SPECIFIC item type and category:
         * Fresh meat, seafood: very sensitive to temperature changes
         * Dairy products: different shelf life patterns
         * Vegetables/Fruits: varies by type (leafy greens vs root vegetables)
         * Frozen foods: may degrade quickly when thawed
         * Processed/canned foods: more stable
       - Use your knowledge of food science to determine realistic expiry dates based on:
         * Current expiry date and remaining shelf life
         * Item's original state (was it fresh or frozen?)
         * New storage environment (fridge/freezer/pantry)
         * Item category and specific food type
       - Be conservative for safety: when in doubt, use shorter expiry dates
    
    3. **Logic** - CRITICAL: NEVER modify parent item's quantity:
       - Parent items must keep their original quantity for statistical tracking
       - All quantity changes must create new child items with parent_id
       
       - If consuming part of an item (e.g., use 500g from 1000g):
         * Action 1: MARK_PROCESSED on parent (keeps original 1000g intact)
         * Action 2: INSERT child with remaining amount (500g, status='in_stock', parent_id)
         * Action 3: INSERT child for consumed amount (500g, status='consumed', parent_id)
       
       - If consuming all (eaten/used up entire item):
         * Use CONSUME_LOG action (keeps quantity, only changes status to 'consumed')
       
       - If item is wasted (spoiled, tastes bad, thrown away):
         * Use MARK_WASTE action (keeps quantity, only changes status to 'waste')
       
       - If moving location ONLY (quantity unchanged):
         * UPDATE location AND expiry_date (recalculate based on new environment)
         * DO NOT include quantity in UPDATE
       
       - If SPLITTING/DIVIDING (e.g., cut 1kg meat into 250g, 350g, 400g pieces):
         * Action 1: MARK_PROCESSED on parent (keeps original 1kg)
         * Action 2+: INSERT child items with parent_id:
           - For pieces to be stored: INSERT with status='in_stock', include location, expiry_date, parent_id
           - For pieces consumed immediately: INSERT with status='consumed', parent_id
         * Each child must have the same item_name, category, unit as parent
         * Sum of all children quantities should equal original parent quantity
    
    4. **Output Format** (Strict JSON list):
    Examples:
    [
      // Consuming PART (500g from 1000g) - MUST use MARK_PROCESSED + INSERT children:
      {{ "action": "MARK_PROCESSED", "id": 10 }},
      {{ "action": "INSERT", "item_name": "猪肉", "quantity": 500, "unit": "g", "location": "fridge", "category": "meat", "expiry_date": "2026-02-15", "parent_id": 10, "status": "in_stock" }},
      {{ "action": "INSERT", "item_name": "猪肉", "quantity": 500, "unit": "g", "category": "meat", "parent_id": 10, "status": "consumed" }},
      
      // Consuming ALL (entire item eaten/used):
      {{ "action": "CONSUME_LOG", "id": 13 }},
      
      // Wasted ALL (entire item spoiled/thrown away):
      {{ "action": "MARK_WASTE", "id": 14 }},
      
      // Moving location ONLY (quantity unchanged - no MARK_PROCESSED needed):
      {{ "action": "UPDATE", "id": 12, "location": "fridge", "expiry_date": "2026-02-16" }},
      
      // SPLITTING scenario - cut 1kg meat (ID=15) into 3 pieces (all stored):
      {{ "action": "MARK_PROCESSED", "id": 15 }},
      {{ "action": "INSERT", "item_name": "猪肉", "quantity": 250, "unit": "g", "location": "freezer", "category": "meat", "expiry_date": "2026-08-10", "parent_id": 15, "status": "in_stock" }},
      {{ "action": "INSERT", "item_name": "猪肉", "quantity": 350, "unit": "g", "location": "fridge", "category": "meat", "expiry_date": "2026-02-16", "parent_id": 15, "status": "consumed" }},
      {{ "action": "INSERT", "item_name": "猪肉", "quantity": 400, "unit": "g", "location": "freezer", "category": "meat", "expiry_date": "2026-08-10", "parent_id": 15, "status": "in_stock" }}
    ]
    
    CRITICAL REQUIREMENTS:
    - NEVER modify parent item's quantity - it must remain intact for statistical purposes
    - For partial consumption, use MARK_PROCESSED + INSERT children (one for remaining, one for consumed)
    - UPDATE is ONLY for location/expiry changes, NEVER for quantity changes
    - Include "location" field for INSERT with status='in_stock', can omit for status='consumed'
    - Always include "expiry_date" in UPDATE and INSERT actions with status='in_stock'
    - Calculate expiry_date intelligently based on storage location and item category
    - ALL field values MUST be lowercase (location: "fridge"/"freezer"/"pantry", category: "meat"/"vegetable"/etc., status: "in_stock"/"consumed"/"processed"/"waste")
    - NEVER use capitalized location names like "Fridge", "Freezer", "Room Temperature"
    - NEVER use Chinese for location (不要用"冰箱"/"冷冻"/"冷冻室"/"室温"等中文)
    - When user says "冰箱" → use "fridge", "冷冻/冷冻室" → use "freezer", "室温/常温" → use "pantry"
    
    STATUS DECISION GUIDE:
    - "consumed": Normal consumption (eaten, used up) - use CONSUME_LOG
    - "waste": Spoiled, tastes bad, thrown away, discarded - use MARK_WASTE
    - "processed": Item was split/divided into multiple parts - use MARK_PROCESSED (then INSERT children with parent_id)
    - "in_stock": Currently available in storage
    
    WASTE TRIGGERS (use MARK_WASTE when user says):
    - "坏了", "变质了", "发霉了", "过期了"
    - "难吃", "太难吃了", "不好吃"
    - "扔了", "扔掉了", "丢了"
    - "不要了", "不想要了"
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
        # print("🔍 调试：AI 返回的计划：")
        # print(json.dumps(plan, indent=2, ensure_ascii=False))
        
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