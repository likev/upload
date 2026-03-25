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
    "tool_calls",
).strip() or "tool_calls"
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


class ToolFunction(BaseModel):
    name: str
    arguments: Union[str, Dict[str, Any], None] = None


class ToolCallMessage(BaseModel):
    id: Optional[str] = None
    type: str = "function"
    function: ToolFunction


class ToolDefinitionFunction(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class ToolDefinition(BaseModel):
    type: str = "function"
    function: ToolDefinitionFunction


class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[MessagePart], None] = None
    tool_calls: Optional[List[ToolCallMessage]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ChatCompletionsRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: List[ChatMessage]
    stream: bool = False
    inputs: Dict[str, Any] = Field(default_factory=dict)
    tools: Optional[List[ToolDefinition]] = None


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


def _messages_to_query(messages: List[ChatMessage], tools: Optional[List[ToolDefinition]] = None) -> str:
    normalized = []
    for msg in messages:
        role = msg.role.lower()
        text = _extract_text_content(msg.content).strip()

        if role == "assistant" and msg.tool_calls:
            if text:
                normalized.append({"role": role, "content": text})
            for tool_call in msg.tool_calls:
                tool_text = _assistant_tool_call_text(tool_call)
                if tool_text:
                    normalized.append({"role": role, "content": tool_text})
            continue

        if role == "tool":
            tool_text = _tool_result_text(msg, text)
            if tool_text:
                normalized.append({"role": role, "content": tool_text})
            continue

        if text:
            normalized.append({"role": role, "content": text})

    if not normalized:
        return ""

    if len(normalized) == 1:
        return _prepend_tool_use_tip(normalized[0]["content"], tools)

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

    return _prepend_tool_use_tip("\n".join(lines).strip(), tools)


def _role_label(role: str) -> str:
    if role == "assistant":
        return "Assistant"
    if role == "system":
        return "System"
    if role == "tool":
        return "Tool"
    return "User"


def _prepend_tool_use_tip(query: str, tools: Optional[List[ToolDefinition]] = None) -> str:
    tip = _build_tool_use_tip(tools)
    query = query.strip()
    if not query:
        return tip
    if not tip:
        return query
    return f"{tip}\n\n{query}"


def _build_tool_use_tip(tools: Optional[List[ToolDefinition]]) -> str:
    if not tools:
        return ""

    lines = [
        "# Tool Use Respond Format",
        "If you need to use a declared tool/function, respond with XML only, no explanation or extra details.",
        "The tool response must always include all three fields: name, command, and description.",
        "Use only tool names declared in this request.",
        "For bash, put the shell command string in command.",
        "For other tools/functions, put a JSON object string with the function arguments in command.",
        "",
        "Available tools:",
    ]

    for tool in tools:
        if tool.type != "function":
            continue
        lines.append(_tool_definition_line(tool))

    lines.extend(
        [
            "",
            "<example>",
            "user: what files are in the directory src/?",
            "assistant: <tool><name>bash</name><command>ls -alh src/</command><description>list all files</description></tool>",
            "</example>",
        ]
    )
    return "\n".join(lines).strip()


def _tool_definition_line(tool: ToolDefinition) -> str:
    name = tool.function.name
    description = (tool.function.description or "").strip()
    parameter_names = _tool_parameter_names(tool)

    line = f"- {name}"
    if description:
        line += f": {description}"
    if parameter_names:
        line += f" | arguments: {', '.join(parameter_names)}"
    return line


def _tool_parameter_names(tool: ToolDefinition) -> List[str]:
    parameters = tool.function.parameters
    if not isinstance(parameters, dict):
        return []
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return []
    return [str(name) for name in properties.keys()]


def _assistant_tool_call_text(tool_call: ToolCallMessage) -> str:
    if tool_call.type != "function":
        return ""

    arguments = _parse_tool_arguments(tool_call.function.arguments)
    command = str(arguments.get("command", "")).strip()
    description = str(arguments.get("description", "")).strip()
    name = tool_call.function.name.strip() or TOOL_NAME

    if not command:
        raw_arguments = tool_call.function.arguments
        if isinstance(raw_arguments, str):
            command = raw_arguments.strip()
        elif isinstance(raw_arguments, dict):
            command = json.dumps(raw_arguments, ensure_ascii=False)

    parts = [
        "<tool>",
        f"<name>{_xml_escape(name)}</name>",
        f"<command>{_xml_escape(command)}</command>",
        f"<description>{_xml_escape(description)}</description>",
        "</tool>",
    ]
    text = "".join(parts)
    if tool_call.id:
        return f"{text} [tool_call_id={tool_call.id}]"
    return text


def _tool_result_text(msg: ChatMessage, text: str) -> str:
    lines: List[str] = []
    if msg.tool_call_id:
        lines.append(f"tool_call_id: {msg.tool_call_id}")
    if msg.name:
        lines.append(f"name: {msg.name}")
    if text:
        lines.append("result:")
        lines.append(text)
    return "\n".join(lines).strip()


def _parse_tool_arguments(arguments: Union[str, Dict[str, Any], None]) -> Dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return {}

    text = arguments.strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"command": text}
    if isinstance(parsed, dict):
        return parsed
    return {"command": text}


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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
    tool_call = _tool_call_from_answer(answer, request.tools)
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
    tool_call = _tool_call_from_answer(answer, request.tools)

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


def _tool_call_from_answer(answer: str, tools: Optional[List[ToolDefinition]] = None) -> Optional[Dict[str, Any]]:
    if not tools:
        return None

    xml_tool_call = _xml_tool_call(answer, tools)
    if xml_tool_call:
        return xml_tool_call
    return _command_tool_call(answer, tools)


def _xml_tool_call(answer: str, tools: Optional[List[ToolDefinition]] = None) -> Optional[Dict[str, Any]]:
    text = answer.strip()
    if not text:
        return None

    tool_match = re.search(r"<tool>(.*?)</tool>", text, flags=re.IGNORECASE | re.DOTALL)
    if not tool_match:
        return None

    block = tool_match.group(1)
    name = _extract_xml_field(block, "name") or TOOL_NAME
    tool_def = _find_tool_definition(tools, name)
    if tool_def is None:
        return None
    command = _extract_xml_field(block, "command")
    description = _extract_xml_field(block, "description")
    if not command:
        return None

    arguments = _tool_arguments_from_xml(tool_def, command, description)

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


def _command_tool_call(answer: str, tools: Optional[List[ToolDefinition]] = None) -> Optional[Dict[str, Any]]:
    command = answer.strip()
    if not command:
        return None

    bash_tool = _find_tool_definition(tools, TOOL_NAME)
    if bash_tool is None:
        return None

    first_token = command.split(None, 1)[0].lower()
    if first_token not in COMMAND_PREFIXES:
        return None

    arguments = _tool_arguments_from_xml(bash_tool, command, "")
    return {
        "id": f"call_{uuid.uuid4().hex}",
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _find_tool_definition(tools: Optional[List[ToolDefinition]], name: str) -> Optional[ToolDefinition]:
    if not tools:
        return None
    for tool in tools:
        if tool.type == "function" and tool.function.name == name:
            return tool
    return None


def _tool_arguments_from_xml(
    tool: ToolDefinition,
    command: str,
    description: Optional[str],
) -> Dict[str, Any]:
    stripped_command = command.strip()
    parsed_json = _json_object_or_none(stripped_command)
    if parsed_json is not None:
        return parsed_json

    arguments: Dict[str, Any] = {}
    if _tool_accepts_argument(tool, "command") or tool.function.name == TOOL_NAME:
        arguments["command"] = stripped_command
    elif _tool_accepts_argument(tool, "input"):
        arguments["input"] = stripped_command
    else:
        arguments["command"] = stripped_command

    cleaned_description = (description or "").strip()
    if cleaned_description and _tool_accepts_argument(tool, "description"):
        arguments["description"] = cleaned_description
    return arguments


def _json_object_or_none(value: str) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _tool_accepts_argument(tool: ToolDefinition, name: str) -> bool:
    parameters = tool.function.parameters
    if not isinstance(parameters, dict):
        return False
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return False
    return name in properties


@app.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionsRequest):
    query = _messages_to_query(request.messages, request.tools)
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
