# Python 3.12：3.10 的安全支援在 2026-10-31 結束。
# 上限是 3.12 而不是更新的版本，因為 chromadb 0.5.x 依賴的 chroma-hnswlib
# 沒有 cp313 以上的 wheel（換掉或升級 chromadb 才解得開）
FROM python:3.12-slim

WORKDIR /app

# 依賴一律從 lock 檔安裝，重建才會得到同一組版本（評測數字綁在這組版本上）。
# --only-binary :all: 把「不需要編譯器」變成會被檢查的前提：將來若有依賴沒有
# 預編譯 wheel，建置會直接失敗，而不是悄悄回頭要求 gcc。
# 因此這個映像不裝 gcc / python3-dev / libpq-dev：psycopg2-binary 自帶 libpq，
# chromadb 與其他依賴在 cp312/linux-amd64 都有 wheel（省下約 250MB）
ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt requirements.lock requirements-dev.lock ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir --only-binary :all: -r requirements-dev.lock; \
    else \
        pip install --no-cache-dir --only-binary :all: -r requirements.lock; \
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
