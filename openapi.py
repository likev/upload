import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

UPSTREAM_URL = os.environ.get("OPENAPI_UPSTREAM_URL", "http://example.com/api/chat-messages")
DEFAULT_MODEL = os.environ.get("OPENAPI_DEFAULT_MODEL", "upstream-chat")
STREAM_CHUNK_SIZE = max(1, int(os.environ.get("OPENAPI_STREAM_CHUNK_SIZE", "32")))

app = FastAPI()


class MessagePart(BaseModel):
    type: str
    text: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[MessagePart], None] = None


class ChatCompletionsRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: List[ChatMessage]
    stream: bool = False
    inputs: Dict[str, Any] = Field(default_factory=dict)


def _extract_text_content(content: Union[str, List[MessagePart], None]) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    parts: List[str] = []
    for part in content:
        if part.type == "text" and part.text:
            parts.append(part.text)
    return "".join(parts)


def _messages_to_query(messages: List[ChatMessage]) -> str:
    user_messages = [msg for msg in messages if msg.role == "user"]
    if user_messages:
        return _extract_text_content(user_messages[-1].content).strip()

    text_parts = [_extract_text_content(msg.content).strip() for msg in messages]
    query = "\n".join(part for part in text_parts if part)
    return query.strip()


def _call_upstream(query: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "inputs": inputs}).encode("utf-8")
    req = urllib.request.Request(
        UPSTREAM_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Upstream HTTP error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream connection error: {exc}") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Upstream returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Upstream returned unexpected payload")
    return data


def _build_completion_response(request: ChatCompletionsRequest, answer: str, completion_id: str) -> Dict[str, Any]:
    created = int(time.time())
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _stream_chunks(text: str, size: int) -> List[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


def _sse_event(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_stream(request: ChatCompletionsRequest, answer: str, completion_id: str):
    created = int(time.time())

    def _gen():
        yield _sse_event(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        )

        for piece in _stream_chunks(answer, STREAM_CHUNK_SIZE):
            yield _sse_event(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model,
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
            )

        yield _sse_event(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionsRequest):
    query = _messages_to_query(request.messages)
    if not query:
        raise HTTPException(status_code=400, detail="No usable message content found")

    upstream_data = _call_upstream(query, request.inputs)
    answer = upstream_data.get("answer")
    if not isinstance(answer, str):
        raise HTTPException(status_code=502, detail="Upstream response missing string field 'answer'")

    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    if request.stream:
        return _build_stream(request, answer, completion_id)

    return JSONResponse(_build_completion_response(request, answer, completion_id))
