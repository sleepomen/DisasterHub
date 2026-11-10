"""
/api/chat：問題交給 ChatService，回答以 SSE 逐段送回前端，每個事件是一個
{"type": "delta"|"error"|"done", "text": ...}。不需要登入，所以按來源限流，並限制同時生成的數量
"""
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from api import deps
from api.models import ChatRequest
from services.web.metrics import metrics

router = APIRouter()

AI_BUSY = "AI 助手正在回答其他問題，請稍後再試。"
AI_BUSY_EN = "The AI assistant is answering other questions. Please try again shortly."

# 列舉題（一個縣 20 至 30 筆）生成要 1 分鐘以上，一次給完的話使用者只能看著轉圈等，
# 所以改用 SSE 邊生成邊送。no-cache / X-Accel-Buffering 是給前面的反向代理看的：
# nginx 預設會把整個回應緩衝起來再轉出，串流會整段失效
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/api/chat")
async def chat(request: ChatRequest, http_request: Request):
    # 來源用量先看：被限流的請求不該佔用生成名額
    wait = deps.chat_limiter.hit(deps.client_key(http_request))
    if wait > 0:
        metrics.incr("chat.rejected.rate_limit")
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many questions. Please try again in {wait} seconds." if request.lang == "en"
                else f"提問太頻繁，請 {wait} 秒後再試。"
            ),
            headers={"Retry-After": str(wait)},
        )
    # 名額要在開始串流前就拿到，拿不到才有機會回 429；
    # 串流一開始送出，狀態碼就定了，之後的錯誤只能以 error 事件表達
    slots = deps.chat_slots
    if not slots.acquire(blocking=False):
        metrics.incr("chat.rejected.busy")
        raise HTTPException(status_code=429, detail=AI_BUSY_EN if request.lang == "en" else AI_BUSY)

    def stream():
        # 同步產生器由 Starlette 丟到執行緒池逐段取，阻塞的 requests 呼叫不會卡住事件迴圈。
        # 名額撐到整段結束（含使用者中途關掉頁面）才還，否則併發上限等於沒設
        try:
            for event in deps.chat_service.chat_stream(request.message, request.lang):
                yield sse_event(event)
            yield sse_event({"type": "done"})
        finally:
            slots.release()

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
