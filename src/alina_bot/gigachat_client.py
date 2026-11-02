"""Минимальный клиент GigaChat API (OAuth + chat/completions).

Документация: https://developers.sber.ru/docs/ru/gigachat/api/authorization
Токен доступа живёт 30 минут, поэтому клиент кеширует его и обновляет заранее.
Секреты никогда не пишутся в логи.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Iterable

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
TOKEN_REFRESH_MARGIN_SECONDS = 120


class GigaChatError(RuntimeError):
    """Базовая ошибка интеграции с GigaChat."""


class GigaChatAuthError(GigaChatError):
    """Не удалось получить токен доступа."""


class GigaChatAPIError(GigaChatError):
    """GigaChat API вернул ошибку или пустой ответ."""


class GigaChatClient:
    def __init__(
        self,
        *,
        credentials: str,
        scope: str = "GIGACHAT_API_PERS",
        base_url: str = "https://api.giga.chat/v1",
        auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        model: str = "GigaChat-2",
        verify_ssl: bool = True,
        ca_bundle_file: str = "",
        timeout: float = 60.0,
        temperature: float = 0.5,
        max_tokens: int = 700,
    ) -> None:
        if not credentials:
            raise ValueError("Пустой ключ авторизации GigaChat.")
        self._credentials = credentials
        self._scope = scope
        self._base_url = base_url.rstrip("/")
        self._auth_url = auth_url
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

        verify: bool | str = True
        if not verify_ssl:
            logger.warning(
                "Проверка SSL-сертификатов GigaChat отключена. "
                "Используйте это только для локальной отладки."
            )
            verify = False
        elif ca_bundle_file:
            verify = ca_bundle_file

        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            verify=verify,
            headers={
                "User-Agent": "zerocoder-vk-alina/1.0",
                "Accept": "application/json",
            },
            follow_redirects=True,
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

    # ------------------------------------------------------------------ OAuth
    def _get_token(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            now = time.time()
            if (
                not force_refresh
                and self._token
                and now < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS
            ):
                return self._token

            headers = {
                "Authorization": f"Basic {self._credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "RqUID": str(uuid.uuid4()),
            }
            try:
                response = self._client.post(
                    self._auth_url,
                    headers=headers,
                    data={"scope": self._scope},
                )
            except httpx.HTTPError as exc:
                raise GigaChatAuthError(
                    "Не удалось подключиться к серверу авторизации GigaChat. "
                    "Проверьте интернет, адрес GIGACHAT_AUTH_URL и сертификат Минцифры."
                ) from exc

            if response.status_code != 200:
                raise GigaChatAuthError(
                    "GigaChat отклонил авторизацию "
                    f"(HTTP {response.status_code}). Проверьте GIGACHAT_CREDENTIALS "
                    "и GIGACHAT_SCOPE. Ответ API: "
                    + _safe_body(response)
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise GigaChatAuthError("GigaChat вернул некорректный ответ авторизации.") from exc

            access_token = data.get("access_token")
            if not access_token:
                raise GigaChatAuthError(
                    "В ответе GigaChat нет access_token. Проверьте ключ и scope."
                )

            self._token = str(access_token)
            self._token_expires_at = _parse_expiry(data)
            logger.info(
                "Получен новый токен GigaChat (scope=%s, действует до %s).",
                self._scope,
                time.strftime("%H:%M:%S", time.localtime(self._token_expires_at)),
            )
            return self._token

    def _invalidate_token(self) -> None:
        with self._token_lock:
            self._token = None
            self._token_expires_at = 0.0

    # ------------------------------------------------------------------- chat
    def chat(
        self,
        messages: Iterable[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Отправляет сообщения в /chat/completions и возвращает текст ответа."""

        payload = {
            "model": model or self._model,
            "messages": list(messages),
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": self._max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }

        last_error = "неизвестная ошибка"
        for attempt in range(1, 4):
            token = self._get_token(force_refresh=attempt > 1 and "401" in last_error)
            try:
                response = self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "X-Request-ID": str(uuid.uuid4()),
                    },
                    json=payload,
                )
            except httpx.HTTPError as exc:
                last_error = f"сетевая ошибка: {exc.__class__.__name__}"
                logger.warning("GigaChat: попытка %s/3 не удалась (%s).", attempt, last_error)
                if attempt < 3:
                    time.sleep(attempt)
                    continue
                raise GigaChatAPIError(
                    "Не удалось связаться с GigaChat. Проверьте интернет и настройки."
                ) from exc

            if response.status_code == 401:
                last_error = "HTTP 401"
                logger.warning("GigaChat: токен отклонён, пробую получить новый.")
                self._invalidate_token()
                if attempt < 3:
                    continue
                raise GigaChatAPIError(
                    "GigaChat не принял токен доступа. Проверьте ключ и scope."
                )

            if response.status_code in RETRYABLE_STATUSES:
                last_error = f"HTTP {response.status_code}"
                logger.warning(
                    "GigaChat: временная ошибка %s (попытка %s/3).",
                    response.status_code,
                    attempt,
                )
                if attempt < 3:
                    time.sleep(attempt * 1.5)
                    continue
                raise GigaChatAPIError(
                    "GigaChat временно недоступен. Попробуйте написать чуть позже."
                )

            if response.status_code != 200:
                raise GigaChatAPIError(
                    f"GigaChat вернул ошибку HTTP {response.status_code}. "
                    f"Ответ: {_safe_body(response)}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise GigaChatAPIError("GigaChat вернул некорректный JSON.") from exc

            # Некоторые версии API оборачивают ответ в поле value.
            body = data.get("value", data) if isinstance(data, dict) else {}
            choices = body.get("choices") or []
            if not choices:
                raise GigaChatAPIError("GigaChat вернул пустой список ответов.")

            message = choices[0].get("message") or {}
            content = _extract_content(message.get("content"))
            if not content.strip():
                raise GigaChatAPIError("GigaChat вернул пустой ответ.")

            usage = body.get("usage") or {}
            logger.info(
                "GigaChat: ответ получен (model=%s, prompt_tokens=%s, "
                "completion_tokens=%s, total_tokens=%s).",
                body.get("model", payload["model"]),
                usage.get("prompt_tokens", "?"),
                usage.get("completion_tokens", "?"),
                usage.get("total_tokens", "?"),
            )
            return content.strip()

        raise GigaChatAPIError(f"GigaChat недоступен: {last_error}.")

    def list_models(self) -> list[str]:
        """Возвращает список доступных моделей (используется для проверки подключения)."""

        token = self._get_token()
        try:
            response = self._client.get(
                f"{self._base_url}/models",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise GigaChatAPIError("Не удалось получить список моделей GigaChat.") from exc

        if response.status_code != 200:
            raise GigaChatAPIError(
                f"GigaChat вернул HTTP {response.status_code} при запросе моделей: "
                + _safe_body(response)
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise GigaChatAPIError("GigaChat вернул некорректный JSON со списком моделей.") from exc

        if not isinstance(data, dict):
            raise GigaChatAPIError("GigaChat вернул неожиданный формат списка моделей.")
        value = data.get("value")
        items = data.get("data") or (value.get("data") if isinstance(value, dict) else None) or []
        return [
            str(item["id"])
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GigaChatClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _parse_expiry(data: dict[str, Any]) -> float:
    now = time.time()
    expires_at = data.get("expires_at")
    if isinstance(expires_at, (int, float)):
        # API может вернуть секунды или миллисекунды.
        return float(expires_at) / 1000 if expires_at > 10_000_000_000 else float(expires_at)
    expires_in = data.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        return now + float(expires_in)
    return now + 25 * 60


def _extract_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _safe_body(response: httpx.Response) -> str:
    """Короткий фрагмент ответа для диагностики, без заголовков и токенов."""

    try:
        text = response.text
    except Exception:  # pragma: no cover - защита от неожиданного ответа
        return "<нет тела ответа>"
    text = " ".join(text.split())
    return text[:300] + ("..." if len(text) > 300 else "")
