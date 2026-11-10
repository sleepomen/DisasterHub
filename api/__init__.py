"""
HTTP API，依功能分檔，由 app.py 掛上去：

    deps.py        各端點共用的物件（資料庫、索引、聊天服務…）、同步與收容人數回寫、權限檢查
    models.py      請求內容的格式與範圍檢查
    session.py     /api/login、/api/logout、/api/me
    map_data.py    地圖要的資料：避難所、潛勢範圍、警戒、人口、選點名稱、最近避難所
    simulation.py  /api/simulate_disaster、/api/occupancy、/api/reset_simulation
    chat.py        /api/chat（SSE 串流）
    admin.py       /api/sync、/api/stats

2026-11-10 從一個 655 行的 app.py 拆出來；app.py 只剩啟動、中介層、健康檢查與首頁
"""
