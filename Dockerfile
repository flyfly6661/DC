FROM python:3.10-slim

# 更新系統並安裝 FFmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean

WORKDIR /app

# 複製並安裝 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製其餘所有程式碼
COPY . .

# 啟動機器人
CMD ["python", "bot.py"]