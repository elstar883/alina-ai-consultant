"""Загрузка и проверка настроек из переменных окружения / файла .env."""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "prompts" / "alina_system_prompt.md"
DEFAULT_KB_PATH = PROJECT_ROOT / "knowledge" / "zerocoder.md"


class ConfigError(RuntimeError):
    """Ошибка конфигурации: сообщение безопасно показывать пользователю."""


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _get_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом, получено: {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} должно быть не меньше {minimum}.")
    return value


def _get_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = _get(name, str(default)).replace(",", ".")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть числом, получено: {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} должно быть не меньше {minimum}.")
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if raw in {"0", "false", "no", "n", "off", "нет"}:
        return False
    raise ConfigError(f"{name} должно быть true или false, получено: {raw!r}")


def _parse_group_id(raw: str) -> int:
    value = raw.strip().lstrip("-")
    if value.lower().startswith("club"):
        value = value[4:]
    if not value.isdigit() or int(value) <= 0:
        raise ConfigError(
            "VK_GROUP_ID должен быть числовым ID сообщества без минуса "
            "(например, 12345678)."
        )
    return int(value)


@dataclass(frozen=True)
class Settings:
    # ВКонтакте
    vk_group_token: str
    vk_group_id: int
    vk_api_version: str = "5.199"

    # GigaChat
    gigachat_credentials: str = ""
    gigachat_client_id: str = ""
    gigachat_client_secret: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2"
    gigachat_base_url: str = "https://api.giga.chat/v1"
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_ca_bundle_file: str = ""
    gigachat_verify_ssl: bool = True
    gigachat_temperature: float = 0.5
    gigachat_max_tokens: int = 700
    gigachat_timeout: float = 60.0

    # Бот
    sales_form_url: str = "https://zerocoder.ru/#popup:call"
    history_turns: int = 8
    history_ttl_minutes: int = 180
    kb_top_k: int = 6
    kb_max_chars: int = 14_000
    log_level: str = "INFO"

    prompt_path: Path = field(default=DEFAULT_PROMPT_PATH)
    kb_path: Path = field(default=DEFAULT_KB_PATH)

    @property
    def scope(self) -> str:
        return self.gigachat_scope

    @property
    def authorization_key(self) -> str:
        """Готовый ключ авторизации для Basic-заголовка.

        Приоритет у GIGACHAT_CREDENTIALS. Если заданы Client ID и Client Secret,
        собираем ключ из них, как это делает личный кабинет GigaChat API.
        """
        if self.gigachat_credentials:
            return self.gigachat_credentials
        pair = f"{self.gigachat_client_id}:{self.gigachat_client_secret}".encode(
            "utf-8"
        )
        return base64.b64encode(pair).decode("ascii")


def load_settings(env_file: Path | None = None) -> Settings:
    """Читает .env (если есть) и переменные окружения, проверяет обязательные поля."""

    dotenv_path = env_file or (PROJECT_ROOT / ".env")
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=False, encoding="utf-8")

    # Основное имя — VK_TOKEN; VK_GROUP_TOKEN поддерживается для совместимости.
    vk_group_token = _get("VK_TOKEN") or _get("VK_GROUP_TOKEN")
    if not vk_group_token:
        raise ConfigError(
            "Не задан VK_TOKEN. Добавьте ключ доступа сообщества в .env "
            "(см. .env.example и README.md)."
        )

    vk_group_id = _parse_group_id(_get("VK_GROUP_ID"))

    credentials = _get("GIGACHAT_CREDENTIALS")
    client_id = _get("GIGACHAT_CLIENT_ID")
    client_secret = _get("GIGACHAT_CLIENT_SECRET")
    if not credentials and not (client_id and client_secret):
        raise ConfigError(
            "Не заданы данные авторизации GigaChat. Укажите GIGACHAT_CREDENTIALS "
            "или пару GIGACHAT_CLIENT_ID + GIGACHAT_CLIENT_SECRET в .env."
        )
    if not credentials and bool(client_id) != bool(client_secret):
        raise ConfigError(
            "Нужно указать и GIGACHAT_CLIENT_ID, и GIGACHAT_CLIENT_SECRET "
            "(либо только GIGACHAT_CREDENTIALS)."
        )

    scope = _get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    allowed_scopes = {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}
    if scope not in allowed_scopes:
        raise ConfigError(
            "GIGACHAT_SCOPE должен быть одним из: " + ", ".join(sorted(allowed_scopes))
        )

    model = _get("GIGACHAT_MODEL", "GigaChat-2")
    if not model:
        raise ConfigError("GIGACHAT_MODEL не должен быть пустым.")

    base_url = _get("GIGACHAT_BASE_URL", "https://api.giga.chat/v1").rstrip("/")
    auth_url = _get(
        "GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    )
    if not base_url.startswith("https://") or not auth_url.startswith("https://"):
        raise ConfigError("GIGACHAT_BASE_URL и GIGACHAT_AUTH_URL должны начинаться с https://")

    ca_bundle = _get("GIGACHAT_CA_BUNDLE_FILE")
    if ca_bundle:
        ca_path = Path(ca_bundle).expanduser()
        if not ca_path.is_absolute():
            ca_path = (PROJECT_ROOT / ca_path).resolve()
        if not ca_path.is_file():
            raise ConfigError(
                f"Файл сертификата из GIGACHAT_CA_BUNDLE_FILE не найден: {ca_path}"
            )
        ca_bundle = str(ca_path)

    log_level = _get("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("LOG_LEVEL должен быть DEBUG, INFO, WARNING, ERROR или CRITICAL.")

    sales_form_url = _get(
        "ZEROCODER_SALES_FORM_URL", "https://zerocoder.ru/#popup:call"
    )
    if not sales_form_url.startswith(("https://", "http://")):
        raise ConfigError(
            "ZEROCODER_SALES_FORM_URL должен быть полной ссылкой, начинающейся с https://."
        )

    settings = Settings(
        vk_group_token=vk_group_token,
        vk_group_id=vk_group_id,
        vk_api_version=_get("VK_API_VERSION", "5.199"),
        gigachat_credentials=credentials,
        gigachat_client_id=client_id,
        gigachat_client_secret=client_secret,
        gigachat_scope=scope,
        gigachat_model=model,
        gigachat_base_url=base_url,
        gigachat_auth_url=auth_url,
        gigachat_ca_bundle_file=ca_bundle,
        gigachat_verify_ssl=_get_bool("GIGACHAT_VERIFY_SSL", True),
        gigachat_temperature=_get_float("GIGACHAT_TEMPERATURE", 0.5, minimum=0.0),
        gigachat_max_tokens=_get_int("GIGACHAT_MAX_TOKENS", 700, minimum=1),
        gigachat_timeout=_get_float("GIGACHAT_TIMEOUT", 60.0, minimum=1.0),
        sales_form_url=sales_form_url,
        history_turns=_get_int("HISTORY_TURNS", 8, minimum=1),
        history_ttl_minutes=_get_int("HISTORY_TTL_MINUTES", 180, minimum=1),
        kb_top_k=_get_int("KB_TOP_K", 6, minimum=1),
        kb_max_chars=_get_int("KB_MAX_CHARS", 14_000, minimum=1000),
        log_level=log_level,
        prompt_path=DEFAULT_PROMPT_PATH,
        kb_path=DEFAULT_KB_PATH,
    )

    if not settings.prompt_path.is_file():
        raise ConfigError(f"Не найден системный промпт: {settings.prompt_path}")
    if not settings.kb_path.is_file():
        raise ConfigError(f"Не найдена база знаний: {settings.kb_path}")

    return settings


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Не пишем в логи полные URL запросов: в Long Poll URL попадает ключ доступа.
    logging.getLogger("httpx").setLevel(logging.WARNING)
