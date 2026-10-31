import os

# config.validate() 會要求這些變數，測試不連真的資料庫，給假值即可。
# 必須在任何測試模組 import config 之前設定，所以放在 rootdir 的 conftest。
TEST_ENV = {
    "POSTGRES_DB": "test_db",
    "POSTGRES_USER": "test_user",
    "POSTGRES_PASSWORD": "test_password",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    # 測試不能連外抓即時警戒
    "ALERT_POLL_SECONDS": "0",
}

for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)

# 向量索引一律放記憶體。在容器裡跑測試時環境已經有 CHROMA_PATH=/data/chroma，
# 不蓋掉的話測試會跟正在服務的那個 process 共用同一份索引：測試建索引時換掉 collection，
# 服務那邊重建到一半就會出現「Collection ... does not exist」，啟動同步失敗、網頁卡住
os.environ["CHROMA_PATH"] = ""
