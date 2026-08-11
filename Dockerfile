FROM python:3.11-slim

WORKDIR /app

# 安裝系統依賴
RUN apt-get update && \
    apt-get install -y ffmpeg curl unzip && \
    apt-get clean

# 安裝 Deno
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:$PATH"

# 複製 requirements.txt 並安裝所有 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案其餘檔案
COPY . .

# 啟動機器人
CMD ["python", "bot.py"]
