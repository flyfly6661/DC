from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "機器人運作中！"

def run():
    # 讀取 Render 分配的動態 Port，預設為 8080
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    # 使用執行緒在背景執行網頁伺服器，不影響機器人主程式
    t = Thread(target=run)
    t.start()