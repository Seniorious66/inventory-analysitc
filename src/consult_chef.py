import os
import datetime
import psycopg2
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ==========================================
# 1. 配置与初始化
# ==========================================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
env_path = os.path.join(current_dir, '.env')
suggestions_dir = os.path.join(project_root, 'suggestions')

load_dotenv(dotenv_path=env_path)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ==========================================
# 2. 你的核心资产：Prompt
# ==========================================
# ⚠️⚠️⚠️ 请把你那套“经过检验的 Prompt”完整粘贴在下面两个三引号之间 ⚠️⚠️⚠️
# 只需要粘贴 System Instruction 部分（身份、规则、输出格式）。
# 不需要粘贴具体的库存数据（代码会自动查出来插进去）。

USER_DEFINED_PROMPT = """
# IDENTITY_AND_PURPOSE
你是一个 **专业管家**。
你的核心目标是 **根据用户的需求，提供科学合理的饮食建议和美味的食谱，或者购买食材的建议列表。**

# KNOWLEDGE_BASE
<context>

## 口味偏好画像
并非追求极致的复杂调味，而是偏向**“食材本味+强劲点缀”**的北方或内陆家常口味。
1. “鲜咸口”为主，重原汁原味。
证据： 非常在意羊肉汤的“清炖”口感，利用蘑菇（口蘑、鹿茸菇）、洋葱、海带等富含氨基酸的食材来“吊鲜味”，而不是依赖味精或耗油。
2. 钟爱“辛香与酸爽” ，潜意识里是喜欢重口味刺激的。
证据：
孜然 & 辣椒： 烤鸡翅、炒肉时手里常备孜然粉和辣椒粉，这是典型的烧烤/大漠风味爱好者。
醋 & 酸菜： 自己泡腊八蒜（重醋酸、蒜辣），家里常备酸菜。这说明非常喜欢开胃、解腻的酸爽口感。
3. “肉食者”的底色，但讲究荤素搭配 绝对不是素食主义者，无肉不欢，但非常注重每一顿饭都要有蔬菜。
证据： 羊腿、猪肉条、鸡翅、熏肉、香肠。同时每次都会问“怎么配油菜”、“怎么煮白菜”。
4. 拒绝水煮无味蔬菜

## 烹饪风格画像
烹饪哲学是：高效、不浪费、设备利用率极高。属于**“快手家常菜”**流派。
1. “空气炸锅”御用玩家（懒人硬菜类） 非常擅长利用空气炸锅来处理肉类和菌类。喜欢那种**“丢进去不用管，出来就是大菜”**的模式。
具体举例：
空气炸锅烤口蘑/酿肉： 利用高温锁住水分。
空气炸锅烤鸡翅/熏肉： 追求焦香口感，且把多余油脂逼出来。
2. “一锅出”的炖煮流派（暖身汤水类） 在寒冷的瑞典，依赖热乎乎的汤水。不喜欢太复杂的煎炒烹炸（比如宽油炸东西），更喜欢食材按顺序下锅，时间到了就能吃的做法。
具体举例：
清炖羊肉汤： 经典剧目。极其讲究投放顺序（耐煮的先放，易熟的后放），追求肉烂汤清。
3. “极简爆炒”流派（下饭小炒类） 对于蔬菜和腌过的肉，倾向于用最快的时间炒熟，保留口感。
具体举例：
滑蛋/炒肉丝： 葱香油菜炒肉、酸菜炒肉。这都是典型的中式“爆炒”逻辑，讲究火候和速度，配米饭是一绝。
4.饮食原则（减重期）：
    午餐（能量版）：高蛋白 + 适量主食（米饭/意面）+ 蔬菜。1手掌肉 + 2拳头菜 + 1拳头主食。
    晚餐（减脂版）：0 碳水。只吃蛋白质 + 大量蔬菜（如黄瓜、西兰花）。1手掌肉 + 2拳头菜 + 0 主食。
    补剂安排：鱼油、AD、B族、C均在早餐随鸡蛋（脂肪）同服。

## 目前居住地
目前身处瑞典哥德堡，食材购买来源为Willys和lidl等常见超市。

## 可用厨房设备
- 2.6l空气炸锅
- 一大一小电陶炉
- 3.2l小炖锅
- 微波炉
- 32cm平底锅
- 26cm不粘锅
- 电饭煲（可开盖）

## 其他
你应该认为一般调味料（瑞典超市常见）是可用的，但是需要在提供食谱时明确指出需要使用哪些调味料。
口味和烹饪偏好并非严格限定，如果用户prompt提出尝试请求需要以它为准，如果没有则以画像为偏好。
食谱推荐需要以现有食材为基础，可以使用瑞典超市常见食材进行补充。
用户为独居，工作日需要为午餐备餐**（Lunchbox，严禁有汤汤水水导致漏的风险的菜）**。没有特殊需求的情况下**一次**备餐需要提供至少2餐午餐和1餐晚餐的量。一次备餐的定义是**不刷锅**能做出的一道或几道菜。
每次提供食谱需要以plain格式单独输出一行使用到的食材及其用量
</context>

# OPERATIONAL_PROTOCOLS (操作协议)

### 1.Reasoning Strategy (思维策略)
在生成最终回复前，请在后台进行以下逻辑检查:
- 识别用户的**核心意图**，而非字面意思。
- 检查是否存在逻辑漏洞或安全风险。
- **一致性检查**;确保回复完全符合下方的“负面约束”

### 2.Style & Tone (风格基调）
- **语言**:默认使用**简体中文**回复《除非用户指定其他语言)。
- **语气**:**专业、温暖**。
- **格式**:结构清晰，普用列表和粗体强调重点。
- **细节**:提供具体的食材用量（**必须提供精确到克或毫升的参考用量**）和烹饪步骤，避免模糊描述。

### 3，Negative Constraints (严令禁止)
**禁止废话**:不要说“希望能帮到你”、“这是一个很好的问题”等客套话。
**禁止幻觉**:如果背景资料中没有答案，请直接说明“资料不足”，严禁编造数据
# RESPONSE_FORMAT (输出格式)
请严格按照以下 **[Markdown]** 格式输出:
## 深度分析
(简要的逻辑分析)
## 解决方案
(正式的回复内容)
"""

# ==========================================
# 3. 数据检索 (Retrieval)
# ==========================================
def get_inventory_context():
    """从数据库获取 in_stock 物品，并计算过期天数"""
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT")
        )
        cur = conn.cursor()
        
        # 按过期时间排序，快过期的排前面
        cur.execute("""
            SELECT item_name, quantity, unit, expiry_date, category, location
            FROM inventory 
            WHERE status = 'in_stock'
            ORDER BY expiry_date ASC NULLS LAST
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return "ERROR: Unable to retrieve inventory from database."

    today = datetime.date.today()
    
    # 将数据转化为自然语言描述，方便 AI 理解
    inventory_lines = []
    inventory_lines.append(f"Current Date: {today}")
    inventory_lines.append("### CURRENT FRIDGE INVENTORY:")
    
    for row in rows:
        name, qty, unit, exp, cat, loc = row
        
        # 计算还有几天过期
        if exp:
            days_left = (exp - today).days
            if days_left < 0:
                status_note = f"[EXPIRED {abs(days_left)} days ago!]"
            elif days_left <= 3:
                status_note = f"[WARNING: Expires in {days_left} days]"
            else:
                status_note = f"(Expires in {days_left} days)"
        else:
            status_note = "(No expiry date)"

        line = f"- {name}: {qty} {unit} | {loc} | {cat} {status_note}"
        inventory_lines.append(line)
    
    return "\n".join(inventory_lines)

# ==========================================
# 4. 生成建议 (Generation) - 支持连续对话
# ==========================================
def save_conversation_history(contents, inventory_context):
    """保存对话历史到 markdown 文件"""
    if not contents:
        print("📝 无对话内容，不生成记录。")
        return
    
    try:
        os.makedirs(suggestions_dir, exist_ok=True)
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"conversation_{timestamp}.md"
        filepath = os.path.join(suggestions_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# 厨师对话记录\n\n")
            f.write(f"**时间**: {datetime.datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}\n\n")
            f.write(f"---\n\n")
            f.write(f"## 库存快照\n\n```\n{inventory_context}\n```\n\n")
            f.write(f"---\n\n")
            f.write(f"## 对话内容\n\n")
            
            for i, msg in enumerate(contents, 1):
                role = "🍳 你" if msg.role == "user" else "👨‍🍳 厨师"
                text = msg.parts[0].text if msg.parts else ""
                f.write(f"### {role}\n\n{text}\n\n")
            
        print(f"✅ 对话记录已保存到: suggestions/{filename}")
        
    except Exception as e:
        print(f"❌ 保存对话记录失败: {e}")


def ask_chef_continuous():
    """连续对话模式的主函数"""
    import time
    
    # 初始化对话历史
    contents = []
    
    # 获取库存上下文（仅在开始时获取一次，或者可以在每次询问时更新）
    inventory_context = get_inventory_context()
    
    # 配置生成参数
    tools = [
        types.Tool(url_context=types.UrlContext()),
        types.Tool(googleSearch=types.GoogleSearch()),
    ]
    
    generate_content_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level="HIGH",
        ),
        tools=tools,
        system_instruction=[
            types.Part.from_text(text=f"""
{USER_DEFINED_PROMPT}

{inventory_context}
"""),
        ],
    )
    
    print("👨‍🍳 厨师已就位！输入 'quit' 或 'exit' 退出对话，输入 'refresh' 刷新库存信息。\n")
    
    while True:
        # 获取用户输入
        try:
            user_input = input("🍳 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 再见！")
            save_conversation_history(contents, inventory_context)
            break
            
        if not user_input:
            continue
            
        # 退出命令
        if user_input.lower() in ['quit', 'exit', '退出']:
            print("\n👋 再见！")
            save_conversation_history(contents, inventory_context)
            break
            
        # 刷新库存命令
        if user_input.lower() in ['refresh', '刷新']:
            inventory_context = get_inventory_context()
            generate_content_config.system_instruction = [
                types.Part.from_text(text=f"""
{USER_DEFINED_PROMPT}

{inventory_context}
"""),
            ]
            print("✅ 库存信息已刷新\n")
            continue
        
        # 将用户消息添加到对话历史
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_input)],
            )
        )
        
        print("\n👨‍🍳 厨师: ", end="", flush=True)
        
        # 添加重试机制
        max_retries = 3
        retry_delay = 2  # 秒
        
        for attempt in range(max_retries):
            try:
                # 使用流式生成
                assistant_response = ""
                for chunk in client.models.generate_content_stream(
                    model='gemini-3-pro-preview',
                    contents=contents,
                    config=generate_content_config,
                ):
                    if chunk.text:
                        print(chunk.text, end="", flush=True)
                        assistant_response += chunk.text
                
                print("\n")
                
                # 将助手回复添加到对话历史
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=assistant_response)],
                    )
                )
                break  # 成功则跳出重试循环
                
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"\n⚠️  连接失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    print(f"🔄 {retry_delay} 秒后重试...\n")
                    time.sleep(retry_delay)
                    print("👨‍🍳 厨师: ", end="", flush=True)
                else:
                    print(f"\n❌ AI 生成失败 (已重试 {max_retries} 次): {e}\n")
                    # 移除失败的用户消息
                    contents.pop()


def ask_chef(user_request):
    """单次对话模式（保持向后兼容）"""
    # 1. 准备数据
    inventory_context = get_inventory_context()
    
    # 2. 组装最终 Prompt
    final_prompt = f"""
    {USER_DEFINED_PROMPT}

    {inventory_context}

    ### USER REQUEST:
    {user_request}
    """

    print("👨‍🍳 厨师正在查看冰箱并思考菜谱...")

    try:
        # 3. 调用 AI
        response = client.models.generate_content(
            model='gemini-3-pro-preview',
            contents=final_prompt
        )
        
        content = response.text
        
        # 4. 输出并保存
        print("\n" + "="*30)
        print(content)
        print("="*30)
        
        # 确保目录存在
        os.makedirs(suggestions_dir, exist_ok=True)
        
        # 保存为 Markdown 文件
        filename = f"plan_{datetime.date.today()}.md"
        filepath = os.path.join(suggestions_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
            
        print(f"\n✅ 建议已保存到: suggestions/{filename}")

    except Exception as e:
        print(f"❌ AI 生成失败: {e}")

# ==========================================
# 5. 入口
# ==========================================
if __name__ == "__main__":
    import sys
    
    # 检查是否使用交互模式
    if len(sys.argv) > 1 and sys.argv[1] in ['-i', '--interactive', 'chat', '对话']:
        # 连续对话模式
        ask_chef_continuous()
    else:
        # 单次查询模式（保持向后兼容）
        default_request = "请根据我现在的库存，1. 提供一次备餐建议，2. 分析库存结构并给出采购建议。"
        
        if len(sys.argv) > 1 and sys.argv[1] not in ['-i', '--interactive', 'chat', '对话']:
            request = sys.argv[1]
        else:
            print(f"💡 提示: 你可以在命令行输入具体需求，例如: uv run src/consult_chef.py '我想吃辣的'")
            print(f"💡 提示: 使用 'uv run src/consult_chef.py -i' 或 'uv run src/consult_chef.py chat' 进入连续对话模式")
            print(f"👉 使用默认需求: {default_request}\n")
            request = default_request
            
        ask_chef(request)