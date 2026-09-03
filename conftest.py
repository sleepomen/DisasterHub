import os

# config.validate() 會要求這些變數，測試不連真的資料庫，給假值即可。
# 必須在任何測試模組 import config 之前設定，所以放在 rootdir 的 conftest。
TEST_ENV = {
    "POSTGRES_DB": "test_db",
    "POSTGRES_USER": "test_user",
    "POSTGRES_PASSWORD": "test_password",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
}

for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)
