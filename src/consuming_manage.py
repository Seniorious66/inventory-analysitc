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
                # 用于：移动位置、修改剩余数量、调整保质期
                # 动态构建 SQL，只更新提供的字段
                update_fields = []
                update_values = []
                
                if 'location' in action:
                    update_fields.append("location=%s")
                    update_values.append(action['location'])
                
                if 'quantity' in action:
                    update_fields.append("quantity=%s")
                    update_values.append(action['quantity'])
                
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
                print(f"   🔧 修改 ID {action['id']}: 剩 {action.get('quantity', '?')}{location_info}{expiry_info}")

            elif act_type == 'INSERT':
                # 用于：切割出来的新肉块
                sql = """
                    INSERT INTO inventory (item_name, category, location, quantity, unit, expiry_date, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                # 默认 category 和 unit 需要 AI 补全，或者从父级继承
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

            elif act_type == 'CONSUME_LOG':
                # 用于：完全消耗掉的，或者切割消耗掉的部分
                # 实际上我们可以选择 update status='CONSUMED' 或者 insert 一条 consumer 记录
                # 这里简单起见，如果 ID 存在，就 Update；如果是新产生的消耗，就 Insert
                # 简化逻辑：如果是切割场景，通常是把母体标记为 CONSUMED/SPLIT，然后生成新的
                if 'id' in action:
                    cur.execute("UPDATE inventory SET status='consumed', quantity=0 WHERE id=%s", (action['id'],))
                    print(f"   🗑️ 消耗/归零 ID {action['id']}")
        
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
    - status: (string) "in_stock" or "consumed" (lowercase with underscore)
    
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
    
    3. **Logic**:
       - If consuming part of an item: UPDATE the quantity (and status if needed). DO NOT include location unless moving it.
       - If consuming all: UPDATE status to 'consumed', quantity to 0. DO NOT include location.
       - If moving location: UPDATE location AND expiry_date (YOU must recalculate based on the item and new environment). Include the item's current quantity and status.
       - If SPLITTING (e.g., cut 1kg into 3 parts):
         - Action 1: Mark the original parent ID as 'consumed' (or quantity 0).
         - Action 2: INSERT new items for the parts that are kept.
         - Action 3: (Optional) INSERT new items for parts consumed immediately (with status 'consumed') OR just ignore them if user only tracks stock.
    
    4. **Output Format** (Strict JSON list):
    Examples:
    [\n      // Consuming (no location change - don't include location field):
      {{ "action": "UPDATE", "id": 10, "quantity": 5, "status": "in_stock", "expiry_date": "2026-02-15" }},
      
      // Fully consumed (no location field needed):
      {{ "action": "CONSUME_LOG", "id": 13 }},
      
      // Moving location (MUST include location and recalculate expiry):
      {{ "action": "UPDATE", "id": 12, "quantity": 0.5, "location": "fridge", "status": "in_stock", "expiry_date": "2026-02-16" }},
      
      // Creating new item (MUST include location):
      {{ "action": "INSERT", "item_name": "切片猪肉", "quantity": 0.35, "unit": "kg", "location": "freezer", "category": "meat", "expiry_date": "2026-08-09" }}
    ]
    
    CRITICAL REQUIREMENTS:
    - Include "location" field ONLY when moving items or creating new items (INSERT)
    - When only consuming/reducing quantity, DO NOT include "location" field
    - Always include "expiry_date" in UPDATE and INSERT actions
    - Calculate expiry_date intelligently based on storage location and item category
    - ALL field values MUST be lowercase (location: "fridge"/"freezer"/"pantry", category: "meat"/"vegetable"/etc., status: "in_stock"/"consumed")
    - NEVER use capitalized location names like "Fridge", "Freezer", "Room Temperature"
    - NEVER use Chinese for location (不要用"冰箱"/"冷冻"/"冷冻室"/"室温"等中文)
    - When user says "冰箱" → use "fridge", "冷冻/冷冻室" → use "freezer", "室温/常温" → use "pantry"
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