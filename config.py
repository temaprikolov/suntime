from dataclasses import dataclass, field
from typing import List
import os

from dotenv import load_dotenv

load_dotenv()  # подхватывает переменные из файла .env (если он есть)


def _env_int_list(name: str, default: List[int]) -> List[int]:
    """Читает список int из переменной окружения вида '123,456,789'."""
    raw = os.getenv(name)
    if not raw:
        return list(default)
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            print(f"⚠️  Пропущено некорректное значение в {name}: {part!r}")
    return result or list(default)


def _env_str(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"⚠️  Некорректное значение {name}={raw!r}, использую значение по умолчанию {default}")
        return default


@dataclass
class Config:
    # ── Секреты и базовые настройки берутся из .env ──────────────────────
    # Если переменная не найдена в окружении/.env, используется значение
    # по умолчанию (старое, "захардкоженное"), чтобы бот не падал при
    # отсутствии .env. Но для продакшена .env обязателен!
    BOT_TOKEN: str = field(default_factory=lambda: _env_str(
        "BOT_TOKEN", "8531440769:AAHq5WDbziH5VNBY1n_J2X_AucquN9EHEFM"))

    ADMINS: List[int] = field(default_factory=lambda: _env_int_list(
        "ADMINS", [8033943956, 1531016526, 1299972384, 6815560227, 6744790703]))

    # Администраторы только для Заозёрного (/admin1), без доступа к Рябково (/admin)
    ZAOZERNOE_ONLY_ADMINS: List[int] = field(default_factory=lambda: _env_int_list(
        "ZAOZERNOE_ONLY_ADMINS", [6744790703]))

    # Курьеры только для Рябково (доступ только к /admin, заказы Заозёрного им не приходят).
    # 6815560227 (@ShopManager45) убран отсюда — он получает заказы обоих районов.
    RYABKOVO_ONLY_ADMINS: List[int] = field(default_factory=lambda: _env_int_list(
        "RYABKOVO_ONLY_ADMINS", []))

    CHANNEL_ID: int = field(default_factory=lambda: _env_int("CHANNEL_ID", -1002278339501))
    CHANNEL_USERNAME: str = field(default_factory=lambda: _env_str("CHANNEL_USERNAME", "SUNTIMENEWS"))

    SHOP_MANAGER: str = field(default_factory=lambda: _env_str("SHOP_MANAGER", "ShopManager45"))
    SHOP_MANAGER_ZAOZERNOE: str = field(default_factory=lambda: _env_str(
        "SHOP_MANAGER_ZAOZERNOE", "ShopManagerZz"))

    # Тексты категорий — заполняются через Админку → Редактировать тексты
    ITEM_SALE_TEXT: str   = ""
    ITEM_RANDOM_TEXT: str = ""
    ITEM1_TEXT: str       = ""
    ITEM2_TEXT: str       = ""
    ITEM3_TEXT: str       = ""
    ITEM4_TEXT: str       = ""
    ITEM5_TEXT: str       = ""
    INFO_TEXT: str        = ""
    SCHEDULE_TEXT: str    = ""


config = Config()
