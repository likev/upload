import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

UPSTREAM_URL = os.environ.get("OPENAPI_UPSTREAM_URL", "http://example.com/api/chat-messages")
DEFAULT_MODEL = os.environ.get("OPENAPI_DEFAULT_MODEL", "upstream-chat")
STREAM_CHUNK_SIZE = max(1, int(os.environ.get("OPENAPI_STREAM_CHUNK_SIZE", "32")))
COMMAND_PREFIXES = {
    "cat",
    "cd",
    "chmod",
    "cp",
    "curl",
    "echo",
    "find",
    "git",
    "grep",
    "head",
    "less",
    "ln",
    "ls",
    "mkdir",
    "mv",
    "pwd",
    "rm",
    "sed",
    "tail",
    "tar",
    "touch",
    "wget",
}
TOOL_NAME = "bash"
TOOL_USE_FORMAT_TIP = """# Tool Use Respond Format
If you need run bash/tool commands in the user system, respond with XML only, no explanation or extra details.

<example>
user: what files are in the directory src/?
assistant: <tool><name>bash</name><command>ls -alh src/</command><description>list all files</description></tool>
</example>"""
UPSTREAM_AUTHORIZATION = os.environ.get(
    "OPENAPI_UPSTREAM_AUTHORIZATION",
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI1NzUzNTc1OC03M2YwLTQzYWQtODZjNS1lNzYxYTJhZWM4ZTkiLCJzdWIiOiJXZWIgQVBJIFBhc3Nwb3J0IiwiYXBwX2lkIjoiNTc1MzU3NTgtNzNmMC00M2FkLTg2YzUtZTc2MWEyYWVjOGU5IiwiYXBwX2NvZGUiOiIydGdGWHczMmluQXdZRzR0IiwiZW5kX3VzZXJfaWQiOiIxNjZiYzcyNi04YjlkLTRiZDYtOGUxNC0yMWRlMDI0YjU4ZjUifQ.bqVBdlCzFwzCGE4JfsPMMDr7pq0GXnCnx23YbX0PFYk",
)
UPSTREAM_REFERER = os.environ.get(
    "OPENAPI_UPSTREAM_REFERER",
    "http://10.69.97.196/chatbot/2tgFXw32inAwYG4t",
)
UPSTREAM_TIMEOUT = float(os.environ.get("OPENAPI_UPSTREAM_TIMEOUT", "10"))
STREAM_TOOL_CALL_FINISH_REASON = os.environ.get(
    "OPENAPI_STREAM_TOOL_CALL_FINISH_REASON",
    "stop",
).strip() or "stop"
CHAT_PATH = os.path.join(os.path.dirname(__file__), "chat.html")

app = FastAPI()


@app.get("/chat", response_class=HTMLResponse)
def chat_page():
    try:
        with open(CHAT_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except OSError:
        raise HTTPException(status_code=404, detail="chat.html not found")


@app.get("/v1/models")
def list_models():
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": DEFAULT_MODEL,
                "object": "model",
                "created": created,
                "owned_by": "openapi-proxy",
            }
        ],
    }


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
    normalized = []
    for msg in messages:
        text = _extract_text_content(msg.content).strip()
        if text:
            normalized.append({"role": msg.role.lower(), "content": text})

    if not normalized:
        return ""

    if len(normalized) == 1:
        return _prepend_tool_use_tip(normalized[0]["content"])

    system_parts = [msg["content"] for msg in normalized if msg["role"] == "system"]
    dialogue_parts = [msg for msg in normalized if msg["role"] != "system"]

    lines: List[str] = []
    if system_parts:
        lines.append("System instructions:")
        lines.append("\n\n".join(system_parts))

    if dialogue_parts:
        if lines:
            lines.append("")
        lines.append("Conversation:")
        for msg in dialogue_parts:
            lines.append(f"{_role_label(msg['role'])}: {msg['content']}")

    if dialogue_parts and dialogue_parts[-1]["role"] == "user":
        lines.append("Assistant:")

    return _prepend_tool_use_tip("\n".join(lines).strip())


def _role_label(role: str) -> str:
    if role == "assistant":
        return "Assistant"
    if role == "system":
        return "System"
    if role == "tool":
        return "Tool"
    return "User"


def _prepend_tool_use_tip(query: str) -> str:
    query = query.strip()
    if not query:
        return TOOL_USE_FORMAT_TIP
    return f"{TOOL_USE_FORMAT_TIP}\n\n{query}"


def _call_upstream(query: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "inputs": inputs}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if UPSTREAM_AUTHORIZATION:
        headers["authorization"] = UPSTREAM_AUTHORIZATION
    if UPSTREAM_REFERER:
        headers["Referer"] = UPSTREAM_REFERER

    req = urllib.request.Request(
        UPSTREAM_URL,
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
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
    tool_call = _tool_call_from_answer(answer)
    message: Dict[str, Any] = {"role": "assistant", "content": answer}
    finish_reason = "stop"
    if tool_call:
        message = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
        finish_reason = "tool_calls"

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
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
    tool_call = _tool_call_from_answer(answer)

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

        if tool_call:
            yield _sse_event(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": tool_call["id"],
                                        "type": "function",
                                        "function": {
                                            "name": tool_call["function"]["name"],
                                            "arguments": tool_call["function"]["arguments"],
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
            yield _sse_event(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": STREAM_TOOL_CALL_FINISH_REASON,
                        }
                    ],
                }
            )
            yield "data: [DONE]\n\n"
            return

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


def _tool_call_from_answer(answer: str) -> Optional[Dict[str, Any]]:
    xml_tool_call = _xml_tool_call(answer)
    if xml_tool_call:
        return xml_tool_call
    return _command_tool_call(answer)


def _xml_tool_call(answer: str) -> Optional[Dict[str, Any]]:
    text = answer.strip()
    if not text:
        return None

    tool_match = re.search(r"<tool>(.*?)</tool>", text, flags=re.IGNORECASE | re.DOTALL)
    if not tool_match:
        return None

    block = tool_match.group(1)
    name = _extract_xml_field(block, "name") or TOOL_NAME
    command = _extract_xml_field(block, "command")
    description = _extract_xml_field(block, "description")
    if not command:
        return None

    arguments: Dict[str, Any] = {"command": command}
    if description:
        arguments["description"] = description

    return {
        "id": f"call_{uuid.uuid4().hex}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _extract_xml_field(block: str, field: str) -> Optional[str]:
    match = re.search(
        rf"<{field}>(.*?)</{field}>|<{field}>(.*?)<{field}\s*/>",
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    value = match.group(1) if match.group(1) is not None else match.group(2)
    if value is None:
        return None
    return value.strip()


def _command_tool_call(answer: str) -> Optional[Dict[str, Any]]:
    command = answer.strip()
    if not command:
        return None

    first_token = command.split(None, 1)[0].lower()
    if first_token not in COMMAND_PREFIXES:
        return None

    return {
        "id": f"call_{uuid.uuid4().hex}",
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "arguments": json.dumps({"command": command}, ensure_ascii=False),
        },
    }


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
