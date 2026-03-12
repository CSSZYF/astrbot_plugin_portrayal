from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any

from astrbot.api import logger
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .config import PluginConfig


@dataclass
class _CachedMessages:
    texts: list[str]
    timestamp: float


@dataclass
class MessageQueryResult:
    """
    消息查询结果对象
    """

    texts: list[str]
    scanned_messages: int
    from_cache: bool
    error_message: str | None = None

    @property
    def count(self) -> int:
        return len(self.texts)

    @property
    def is_empty(self) -> bool:
        return not self.texts


class MessageManager:
    """
    群级扫描 + 用户级缓存的消息管理器
    """

    _HISTORY_FETCH_ERROR = (
        "当前 OneBot 实现未返回可识别的群历史消息格式，无法获取群历史消息。"
    )

    def __init__(self, config: PluginConfig):
        self.cfg = config.message

        # user cache: group:user -> messages
        self._user_cache: dict[str, _CachedMessages] = {}

        # group cursor: group -> message_seq
        self._group_cursor: dict[str, int] = {}

    def _user_key(self, group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    def _get_user_cache(self, group_id: str, user_id: str) -> list[str] | None:
        key = self._user_key(group_id, user_id)
        cached = self._user_cache.get(key)
        if not cached:
            return None

        if time() - cached.timestamp > self.cfg.cache_ttl:
            del self._user_cache[key]
            self._group_cursor.pop(group_id, None)
            return None

        return cached.texts

    def _clear_group_cache(self, group_id: str):
        prefix = f"{group_id}:"
        for key in list(self._user_cache):
            if key.startswith(prefix):
                del self._user_cache[key]
        self._group_cursor.pop(group_id, None)

    def clear_cache(self):
        self._user_cache.clear()
        self._group_cursor.clear()

    def _collect_messages(
        self,
        group_id: str,
        messages: list[dict[str, Any]],
    ):
        now = time()

        for msg in messages:
            sender = msg.get("sender")
            user_id = None
            if isinstance(sender, dict):
                user_id = sender.get("user_id")
            if user_id is None:
                user_id = msg.get("user_id")
            if user_id is None:
                continue

            text = "".join(
                seg.get("data", {}).get("text", "")
                for seg in msg.get("message", [])
                if seg.get("type") == "text" and isinstance(seg.get("data"), dict)
            ).strip()

            if not text:
                continue

            key = self._user_key(group_id, str(user_id))
            cached = self._user_cache.get(key)
            if not cached:
                self._user_cache[key] = _CachedMessages(
                    texts=[text],
                    timestamp=now,
                )
            else:
                cached.texts.append(text)
                cached.timestamp = now

    def _unwrap_history_messages(
        self,
        result: Any,
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        if not isinstance(result, dict):
            return None, f"unexpected response type: {type(result).__name__}"

        raw_messages = result.get("messages")
        if raw_messages is None:
            data = result.get("data")
            if isinstance(data, dict):
                raw_messages = data.get("messages", [])
            else:
                keys = ",".join(sorted(map(str, result.keys()))) or "<empty>"
                return None, f"missing messages/data.messages keys, keys={keys}"

        if raw_messages is None:
            return [], None
        if not isinstance(raw_messages, list):
            return [], None

        return raw_messages, None

    def _summarize_history_result(self, result: Any) -> str:
        if not isinstance(result, dict):
            return f"type={type(result).__name__}"

        keys = ",".join(sorted(map(str, result.keys()))) or "<empty>"
        data = result.get("data")
        if isinstance(data, dict):
            data_keys = ",".join(sorted(map(str, data.keys()))) or "<empty>"
            return f"keys={keys}; data_keys={data_keys}"

        return f"keys={keys}; data_type={type(data).__name__}"

    async def _fetch_group_history_page(
        self,
        event: AiocqhttpMessageEvent,
        group_id: str,
        message_seq: int,
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        attempts = (
            {"reverseOrder": True},
            {"reverse_order": True},
        )
        errors: list[str] = []

        for extra in attempts:
            params = {
                "group_id": group_id,
                "message_seq": message_seq,
                "count": self.cfg.per_query_count,
                **extra,
            }
            try:
                result = await event.bot.api.call_action(
                    "get_group_msg_history",
                    **params,
                )
            except Exception as exc:
                errors.append(f"params={extra}: {exc}")
                continue

            messages, parse_error = self._unwrap_history_messages(result)
            if parse_error is None:
                return messages, None

            errors.append(
                f"params={extra}: {parse_error}; {self._summarize_history_result(result)}"
            )

        for error in errors:
            logger.warning(f"get_group_msg_history incompatible response: {error}")

        return None, self._HISTORY_FETCH_ERROR

    async def get_user_texts(
        self,
        event: AiocqhttpMessageEvent,
        target_id: str,
        *,
        max_rounds: int,
    ) -> MessageQueryResult:
        group_id = str(event.get_group_id())
        target_id = str(target_id)

        cached = self._get_user_cache(group_id, target_id)
        if cached and len(cached) >= self.cfg.max_msg_count:
            return MessageQueryResult(
                texts=cached[: self.cfg.max_msg_count],
                scanned_messages=0,
                from_cache=True,
            )

        texts = cached[:] if cached else []
        rounds = 0
        error_message = None
        message_seq = self._group_cursor.get(group_id, 0)

        while rounds < max_rounds and len(texts) < self.cfg.max_msg_count:
            try:
                messages, fetch_error = await self._fetch_group_history_page(
                    event,
                    group_id,
                    message_seq,
                )
                if fetch_error:
                    error_message = fetch_error
                    self._clear_group_cache(group_id)
                    break
                if not messages:
                    break

                next_seq = messages[0].get("message_seq") or messages[0].get(
                    "message_id"
                )
                if next_seq is None:
                    logger.warning("get_group_msg_history returned message without cursor")
                    break

                message_seq = int(next_seq)
                self._group_cursor[group_id] = message_seq
                self._collect_messages(group_id, messages)

                cached = self._get_user_cache(group_id, target_id)
                if cached:
                    texts = cached[:]
            except Exception as exc:
                logger.error(exc)
                error_message = self._HISTORY_FETCH_ERROR
                self._clear_group_cache(group_id)
                break

            rounds += 1

        return MessageQueryResult(
            texts=texts[: self.cfg.max_msg_count],
            scanned_messages=rounds * self.cfg.per_query_count,
            from_cache=cached is not None,
            error_message=error_message,
        )
