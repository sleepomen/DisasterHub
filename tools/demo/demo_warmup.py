"""
展示前的預熱：讓 Ollama 先把對話模型與 embedding 模型載進記憶體並留著，再對網站問幾題，
確認每一條回答路徑都通。Ollama 閒置 5 分鐘就會把模型卸下，第一個問題要等它重新載入（十幾秒到一分鐘），
現場最怕這種等待；keep_alive 讓模型在記憶體裡留到展示結束。

只用標準函式庫，在跑著服務的那台電腦上執行：
    python tools/demo_warmup.py
    python tools/demo_warmup.py --keep-alive 4h --ollama http://localhost:11434 --site http://localhost:8501
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# 每一條回答路徑各一題：程式排版的清單、防災知識原文、小地名、交給模型的語意題
QUESTIONS = [
    ("清單（不經過模型）", "花蓮市的國小"),
    ("防災知識原文", "土石流黃色警戒是什麼意思"),
    ("小地名 → 最近的避難所", "七星潭附近有避難所嗎"),
    ("語意檢索 → 模型", "哪裡可以帶寵物避難"),
]


def post(url: str, body: dict, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def step(label: str, action) -> bool:
    started = time.monotonic()
    try:
        detail = action()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        print(f"  ✗ {label}：{e}")
        return False
    print(f"  ✓ {label}（{time.monotonic() - started:.1f} 秒）{detail or ''}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", default="http://localhost:8501")
    parser.add_argument("--ollama", default="http://localhost:11434")
    parser.add_argument("--chat-model", default="llama3.2:3b")
    parser.add_argument("--embed-model", default="bge-m3")
    parser.add_argument("--keep-alive", default="3h", help="模型在記憶體裡留多久（Ollama 的 keep_alive，例如 3h；-1 表示不卸下）")
    args = parser.parse_args()
    ok = True

    print("1. 網站")
    def ready():
        with urllib.request.urlopen(f"{args.site}/health/ready", timeout=30) as res:
            checks = json.loads(res.read())["checks"]
        bad = [name for name, c in checks.items() if not c["ok"]]
        if bad:
            raise ValueError(f"這些檢查沒過：{', '.join(bad)}")
        return "：資料庫、向量索引、Ollama、知識索引都正常"
    ok &= step("readiness", ready)

    print(f"2. 把模型載進記憶體並留 {args.keep_alive}")
    ok &= step(f"對話模型 {args.chat_model}", lambda: post(f"{args.ollama}/api/generate",
               {"model": args.chat_model, "prompt": "", "keep_alive": args.keep_alive}, 300) and None)
    ok &= step(f"embedding 模型 {args.embed_model}", lambda: post(f"{args.ollama}/api/embed",
               {"model": args.embed_model, "input": "暖機", "keep_alive": args.keep_alive}, 300) and None)

    print("3. 每一條回答路徑各問一題")
    for label, question in QUESTIONS:
        def ask(q=question):
            text = post(f"{args.site}/api/chat", {"message": q}, 300).decode("utf-8")
            events = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
            if any(e.get("type") == "error" for e in events):
                raise ValueError("回答裡有錯誤事件")
            answer = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
            return f"：{answer[:30].replace(chr(10), ' ')}…"
        ok &= step(f"{label}「{question}」", ask)

    print("全部正常，可以開始展示。" if ok else "有項目沒過，展示前先處理（見 docs/setup.md 的展示前檢查）。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
