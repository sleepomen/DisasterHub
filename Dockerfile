FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip

ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt ./
RUN if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir -r requirements-dev.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

COPY . .

# 應用程式不用 root 跑：容器被打穿時拿到的只是一般使用者。
# 向量索引的 volume 可能是舊版（root 擁有）留下來的，entrypoint 會先把它改成 app 再降權
# home 放 /home/app 而不是 /app：開發模式把專案目錄掛進 /app，套件快取才不會寫進原始碼樹
RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /data/chroma \
    && chown -R app:app /app /data/chroma

# 用 readiness 而非 liveness：資料庫或向量索引壞掉時容器要顯示 unhealthy
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/health/ready', timeout=8)"

# 透過 sh 執行：開發模式 bind mount 進來的檔案不一定有執行位元
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8501"]
