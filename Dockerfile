# 使用官方 Python 3.10 輕量版映像檔
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /app

# 安裝系統依賴：ffmpeg、curl 以及安裝 Deno 必備的 unzip
RUN apt-get update && \
    apt-get install -y ffmpeg curl unzip && \
    apt-get clean

# 安裝 Deno (yt-dlp 破解 YouTube 加密必備)
RUN curl -fsSL https://deno.land/install.sh | sh

# 將 Deno 加入系統環境變數 PATH (預設安裝在 root 的 .deno 內)
ENV PATH="/root/.deno/bin:$PATH"

# 複製 requirements.txt 並安裝 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案其餘所有檔案到容器中
COPY . .

# 啟動機器人與網頁伺服器
CMD ["python", "bot.py"]
