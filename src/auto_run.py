import time
import os
import sys
import shutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from gemini_stoking import analyze_image  # 复用我们写好的视觉函数
from add_items import load_json_to_db # 复用入库函数

# ==========================================
# 1. 配置路径
# ==========================================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
watch_dir = os.path.join(project_root, 'images')           # 监听这里
processed_dir = os.path.join(project_root, 'images', 'processed') # 处理完放这里

# 确保归档目录存在
os.makedirs(processed_dir, exist_ok=True)

# ==========================================
# 2. 定义事件处理器
# ==========================================
class NewImageHandler(FileSystemEventHandler):
    def on_created(self, event):
        # 过滤掉文件夹和非图片文件
        if event.is_directory:
            return
        
        filename = os.path.basename(event.src_path)
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.heic')):
            return

        print(f"\n👀 发现新图片: {filename}")
        
        # 等待 1 秒，确保文件完全写入/同步完成（防止 OneDrive 还在同步时就读取）
        time.sleep(2)
        
        self.process_image(filename)

    def process_image(self, filename):
        try:
            # --- Step 1: 调用 AI 识别 ---
            print("   🚀 1. 开始 AI 识别...")
            json_filename = analyze_image(filename)
            
            if not json_filename:
                print("   ❌ AI 识别失败或未生成 JSON，跳过入库。")
                return
            
            print(f"   ✅ JSON 生成完毕: {json_filename}")

            # --- Step 2: 自动入库 ---
            print("   🚀 2. 开始写入数据库...")
            load_json_to_db(json_filename)
            
            # --- Step 3: 归档图片 ---
            print("   🧹 3. 归档图片...")
            src_path = os.path.join(watch_dir, filename)
            dst_path = os.path.join(processed_dir, filename)
            shutil.move(src_path, dst_path)
            print("   🎉 全流程完成！等待下一张...")

        except Exception as e:
            print(f"   ❌ 处理出错: {e}")

# ==========================================
# 3. 启动监听
# ==========================================
if __name__ == "__main__":
    observer = Observer()
    event_handler = NewImageHandler()
    
    # recursive=False 表示只监听当前目录，不监听子目录
    observer.schedule(event_handler, watch_dir, recursive=False)
    
    print(f"🕵️  监控已启动: {watch_dir}")
    print("👉 请将照片放入该文件夹，程序将自动处理...")
    
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()