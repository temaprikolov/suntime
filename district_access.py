#!/usr/bin/env python3
"""
Права администраторов/курьеров по районам.

• ZAOZERNOE_ONLY_ADMINS — курьеры Заозёрного: видят и получают ТОЛЬКО Заозёрный.
• RYABKOVO_ONLY_ADMINS  — курьеры Рябково:   видят и получают ТОЛЬКО Рябково.
• Остальные администраторы (владельцы) — видят оба района, активный район
  переключается в админке (/admin, /admin1 или кнопкой в меню).
"""
from sqlalchemy import text

import config
from database import engine

RYABKOVO = "ryabkovo"
ZAOZERNOE = "zaozernoe"


# ── Хранилище активного района (память + таблица admin_settings) ─────────────

_admin_district: dict[int, str] = {}


def _ensure_admin_settings_table():
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS admin_settings "
                "(user_id INTEGER PRIMARY KEY, district VARCHAR(20) DEFAULT 'ryabkovo')"
            ))
            conn.commit()
    except Exception:
        pass


_ensure_admin_settings_table()


def _db_get_admin_district(user_id: int) -> str | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT district FROM admin_settings WHERE user_id = :uid"),
                {"uid": user_id},
            ).fetchone()
            if row and row[0] in (RYABKOVO, ZAOZERNOE):
                return row[0]
    except Exception:
        pass
    return None


def _db_set_admin_district(user_id: int, district: str):
    try:
        with engine.connect() as conn:
            conn.execute(
                text("""INSERT INTO admin_settings (user_id, district) VALUES (:uid, :d)
                        ON CONFLICT(user_id) DO UPDATE SET district = :d"""),
                {"uid": user_id, "d": district},
            )
            conn.commit()
    except Exception:
        pass


# ── Ограничения по районам ───────────────────────────────────────────────────

def locked_district(user_id: int) -> str | None:
    """Район, к которому админ «прикреплён» жёстко (курьер), либо None."""
    if user_id in config.config.ZAOZERNOE_ONLY_ADMINS:
        return ZAOZERNOE
    if user_id in config.config.RYABKOVO_ONLY_ADMINS:
        return RYABKOVO
    return None


def is_zaozernoe_only(user_id: int) -> bool:
    return user_id in config.config.ZAOZERNOE_ONLY_ADMINS


def is_ryabkovo_only(user_id: int) -> bool:
    return user_id in config.config.RYABKOVO_ONLY_ADMINS


def get_admin_district(user_id: int) -> str:
    locked = locked_district(user_id)
    if locked:
        return locked
    if user_id in _admin_district:
        return _admin_district[user_id]
    saved = _db_get_admin_district(user_id)
    district = saved if saved else RYABKOVO
    _admin_district[user_id] = district
    return district


def set_admin_district(user_id: int, district: str):
    locked = locked_district(user_id)
    if locked:
        district = locked  # курьер не может «переехать» в чужой район
    _admin_district[user_id] = district
    _db_set_admin_district(user_id, district)


def order_district(value: str | None) -> str:
    """Старые заказы без района — это Рябково (до появления районов был только он)."""
    return value or RYABKOVO


def order_recipients(district: str | None) -> list[int]:
    """Кому слать уведомление о новом заказе из района district."""
    district = order_district(district)
    result = []
    for admin_id in config.config.ADMINS:
        locked = locked_district(admin_id)
        if locked is None or locked == district:
            result.append(admin_id)
    return result
