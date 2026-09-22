"""Normalize user-selected exports; never infer a workspace from chat text."""
from __future__ import annotations

import hashlib
import json

from .chat_history import MAX_MESSAGES, MAX_TEXT, text_content, timestamp

MAX_IMPORT_BYTES = 8 * 1024 * 1024
MAX_CONVERSATIONS = 1000


def _chatgpt_messages(conversation):
    mapping = conversation["mapping"]
    if not isinstance(mapping, dict):
        raise TypeError("Invalid ChatGPT conversation mapping.")
    current = conversation.get("current_node")
    if not current:
        parents = {node.get("parent") for node in mapping.values() if isinstance(node, dict)}
        leaves = [key for key in mapping if key not in parents]
        if len(leaves) != 1:
            raise ValueError("Export the active conversation branch before importing.")
        current = leaves[0]
    chain, seen = [], set()
    while current is not None:
        if not isinstance(current, str) or current in seen or current not in mapping:
            raise ValueError("Invalid conversation branch in the export.")
        seen.add(current)
        node = mapping[current]
        if not isinstance(node, dict):
            raise TypeError("Invalid conversation node in the export.")
        message = node.get("message")
        if isinstance(message, dict):
            chain.append(message)
        current = node.get("parent")
    for message in reversed(chain):
        if message.get("channel") not in {None, "final"}:
            continue
        if message.get("recipient") not in {None, "all"}:
            continue
        if message.get("metadata", {}).get("is_visually_hidden_from_conversation"):
            continue
        content = message.get("content", {})
        if not isinstance(content, dict) or content.get("content_type") != "text":
            continue
        yield {"role": message.get("author", {}).get("role"),
               "content": "\n".join(p for p in content.get("parts", []) if isinstance(p, str)),
               "timestamp": message.get("create_time")}


def _messages(conversation):
    if "mapping" in conversation:
        yield from _chatgpt_messages(conversation)
        return
    if "chat_messages" in conversation:
        for message in conversation["chat_messages"]:
            if isinstance(message, dict):
                yield {"role": "user" if message.get("sender") == "human" else message.get("sender"),
                       "content": message.get("text") or text_content(message.get("content")),
                       "timestamp": message.get("created_at")}
        return
    for message in conversation.get("messages", []):
        if isinstance(message, dict):
            yield {"role": message.get("role"), "content": text_content(message.get("content")),
                   "timestamp": message.get("timestamp") or message.get("created_at")}


def parse_export(value: object) -> list[dict]:
    """Return bounded, text-only conversations suitable for preview and explicit selection."""
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_IMPORT_BYTES:
        raise ValueError("Choose a JSON export smaller than 8 MB.")
    if isinstance(value, dict) and "conversations" in value:
        value = value["conversations"]
    conversations = value if isinstance(value, list) else [value]
    if not conversations or len(conversations) > MAX_CONVERSATIONS:
        raise ValueError("Choose an export containing between 1 and 1,000 conversations.")
    result = []
    for index, conversation in enumerate(conversations):
        if not isinstance(conversation, dict):
            raise TypeError("Each conversation must be a JSON object.")
        # Open WebUI's export wraps the visible transcript inside `chat`.
        conversation = conversation.get("chat", conversation)
        if not isinstance(conversation, dict) or not any(
            key in conversation for key in ("messages", "chat_messages", "mapping")
        ):
            raise ValueError("Choose a ChatGPT, Claude, Open WebUI, or role/content JSON export.")
        messages = []
        for message in _messages(conversation):
            if message["role"] not in {"user", "assistant"} or not message["content"].strip():
                continue
            if len(message["content"]) > MAX_TEXT or len(messages) >= MAX_MESSAGES:
                raise ValueError("This transcript is too large. Split it into smaller conversations.")
            messages.append({**message, "timestamp": timestamp(message.get("timestamp"))})
        if not messages:
            continue
        title = conversation.get("title") or conversation.get("name") or messages[0]["content"]
        if not isinstance(title, str):
            raise TypeError("Conversation titles must be text.")
        record = {"title": title[:240], "messages": messages,
                  "updated_at": timestamp(conversation.get("update_time") or conversation.get("updated_at"))
                  or next((m["timestamp"] for m in reversed(messages) if m["timestamp"]), "")}
        identity = json.dumps(record, sort_keys=True, ensure_ascii=False).encode()
        result.append({**record, "id": hashlib.sha256(identity).hexdigest(), "index": index})
    if not result:
        raise ValueError("No visible user or assistant messages were found in this export.")
    return result
