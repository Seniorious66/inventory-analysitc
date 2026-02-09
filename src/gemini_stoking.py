import os
import json
import time
import glob
import datetime
from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types

# ==========================================
# 1. 配置环境与路径
# ==========================================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
env_path = os.path.join(current_dir, '.env')
images_dir = os.path.join(project_root, 'images')
data_dir = os.path.join(project_root, 'data')

# 加载环境变量
load_dotenv(dotenv_path=env_path)
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("❌ 找不到 GEMINI_API_KEY，请检查 .env 文件！")

# 初始化新版客户端
client = genai.Client(api_key=api_key)

# ==========================================
# 2. 辅助工具：自动寻找图片
# ==========================================
def find_latest_image():
    """
    在 images 文件夹里寻找最新的图片文件 (.jpg, .png, .jpeg, .webp)
    """
    # 定义支持的格式
    extensions = ['*.jpg', '*.jpeg', '*.png', '*.webp', '*.HEIC']
    files = []
    
    for ext in extensions:
        # glob 用来匹配文件名
        files.extend(glob.glob(os.path.join(images_dir, ext)))
        # 也要匹配大写后缀 (比如 .JPG)
        files.extend(glob.glob(os.path.join(images_dir, ext.upper())))
    
    if not files:
        return None
    
    # 按修改时间排序，取最新的一个
    latest_file = max(files, key=os.path.getmtime)
    return os.path.basename(latest_file)

# ==========================================
# 3. 核心逻辑：AI 视觉识别 (新版 SDK)
# ==========================================
def analyze_image(image_filename=None):
    # 1. 确定图片路径
    if image_filename is None:
        print("🔍 未指定文件名，正在寻找 images 文件夹里最新的图片...")
        image_filename = find_latest_image()
        
    if not image_filename:
        print("❌ images 文件夹里没有任何图片！请放入 .jpg 或 .png 文件。")
        return

    image_path = os.path.join(images_dir, image_filename)
    print(f"📸 正在读取照片: {image_filename}")

    # 2. 准备 Prompt (专用于提取 JSON)
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # 这里的 Prompt 必须专注，不要加“管家”人设，只要“数据录入员”人设
    prompt_text = f"""
    Identify all food items in this image.
    Today is {today_str}. Calculate expiry dates based on this.
    
    Return a list of objects with these exact fields:
    - item_name: (string) 中文翻译(原名)格式
    - category: (string) e.g., Vegetable, Dairy, Meat.
    - location: (string) "Fridge", "Freezer", or "Pantry".
    - quantity: (number)
    - unit: (string)
    - expiry_date: (string) YYYY-MM-DD.
    - status: (string) "IN_STOCK"
    """

    # 3. 读取图片
    try:
        image = Image.open(image_path)
    except Exception as e:
        print(f"❌ 图片文件损坏或无法读取: {e}")
        return

    print("🤖 正在发送给 Gemini (使用新版 google-genai 库)...")
    
    try:
        # --- 新版 API 调用核心 ---
        response = client.models.generate_content(
            model='gemini-2.0-flash', # 推荐先用 2.0 Flash，稳定且快
            contents=[prompt_text, image],
            config=types.GenerateContentConfig(
                response_mime_type='application/json' # 🔥 核心功能：强制返回 JSON
            )
        )
        
        # 4. 处理结果
        # 因为强制了 JSON 模式，我们可以直接解析，不需要再去 replace ```json
        json_str = response.text
        inventory_data = json.loads(json_str)
        
        print(f"✅ 识别成功！发现了 {len(inventory_data)} 个物品。")
        
        # 5. 保存结果
        output_filename = f"scan_{int(time.time())}.json"
        output_path = os.path.join(data_dir, output_filename)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(inventory_data, f, indent=2, ensure_ascii=False)
            
        print(f"💾 JSON 已保存: data/{output_filename}")
        print(f"💡 自动入库命令: uv run src/add_items.py {output_filename}")

    except Exception as e:
        print(f"❌ AI 分析失败: {e}")
        # 如果是 API Key 问题，这里会报错

# ==========================================
# 4. 入口
# ==========================================
if __name__ == "__main__":
    import sys
    # 如果命令行传了参数 (比如 uv run src/scan_photo.py my_pic.png)，就用参数
    # 如果没传，就自动找最新的
    target = sys.argv[1] if len(sys.argv) > 1 else None
    analyze_image(target)