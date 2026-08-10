# 使用官方 Python 3.10 輕量版映像檔
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /app

# 先安裝系統依賴：ffmpeg、curl、unzip
RUN apt-get update && \
    apt-get install -y ffmpeg curl unzip && \
    apt-get clean

# 安裝 Deno
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:$PATH"

# 先複製 requirements.txt 並立刻安裝 Python 套件（確保環境正確）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 最後才複製專案其餘檔案
COPY . .

# 啟動機器人與網頁伺服器
CMD ["python", "bot.py"]
