from sqlalchemy import create_engine, event, Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Float
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool
from datetime import datetime
import os
import secrets
import string

os.makedirs("data", exist_ok=True)

# ВАЖНО: раньше стоял пул по умолчанию (QueuePool: 5 + 10 overflow). Хэндлеры
# aiogram выполняются параллельно и держат сессию на время сетевых запросов к
# Telegram (а иногда открывают вложенную вторую сессию), поэтому пул
# исчерпывался и бот падал с "QueuePool limit of size 5 overflow 10 reached".
# Для SQLite-файла пул не нужен: NullPool открывает соединение на каждую
# сессию и закрывает его в session.close() — лимита нет, утечек нет.
engine = create_engine(
    "sqlite:///data/database.db",
    echo=False,
    poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")    # читатели не блокируют писателя
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")  # ждать блокировку, а не падать
    finally:
        cur.close()


Base = declarative_base()
Session = sessionmaker(bind=engine, expire_on_commit=False)

BRAND_CATEGORY_IDS = {1, 2, 3, 7, 8}

DEFAULT_BRANDS = [
    ("DUALL",         "🔵"),
    ("Skala",         "⚡"),
    ("Мишка",         "🐻"),
    ("Злая Монашка",  "😈"),
    ("Monstervapor",  "👾"),
    ("Husky",         "🐺"),
    ("PODONKI",       "🔥"),
    ("PODONKI + MAD", "💥"),
    ("PUFFMI",        "💨"),
    ("GEEK",          "🤓"),
    ("LOST MARY",     "🌸"),
]

DISTRICT_NAMES = {
    "ryabkovo":  "🏘 Рябково",
    "zaozernoe": "🌊 Заозёрный",
}


class User(Base):
    __tablename__ = "users"
    id                    = Column(Integer, primary_key=True)
    user_id               = Column(Integer, unique=True)
    username              = Column(String(100), nullable=True)
    first_name            = Column(String(100), nullable=True)
    last_name             = Column(String(100), nullable=True)
    joined_at             = Column(DateTime, default=datetime.now)
    is_admin              = Column(Boolean, default=False)
    nickname              = Column(String(100), nullable=True)
    avatar_file_id        = Column(String(300), nullable=True)
    referral_code         = Column(String(20),  nullable=True)
    referral_discount_pct = Column(Float, default=0.0)
    district              = Column(String(20), nullable=True)  # "ryabkovo" | "zaozernoe"
    is_blocked            = Column(Boolean, default=False)   # ⛔ РУЧНОЙ бан админом — ЕДИНСТВЕННОЕ основание для is_blocked
    blocked_reason        = Column(String(300), nullable=True)
    channel_shown         = Column(Boolean, default=False)  # показывали ли экран подписки
    left_bot               = Column(Boolean, default=False)  # 🚫 Telegram вернул Forbidden (юзер сам заблокировал бота/удалил акк).
                                                               # Это НЕ бан — доступ в бот не ограничивается. Только статистика.


class Brand(Base):
    __tablename__ = "brands"
    id    = Column(Integer, primary_key=True, autoincrement=True)
    name  = Column(String(100), unique=True)
    emoji = Column(String(10), default="🏷")


class Product(Base):
    __tablename__ = "products"
    id                  = Column(Integer, primary_key=True)
    item_name           = Column(String(50))
    item_text           = Column(Text)
    # ── ИСПРАВЛЕНИЕ 1: раздельные тексты расписания по районам ──
    item_text_ryabkovo  = Column(Text, nullable=True)   # расписание Рябково
    item_text_zaozernoe = Column(Text, nullable=True)   # расписание Заозёрный
    updated_at          = Column(DateTime, default=datetime.now)


class Flavor(Base):
    __tablename__ = "flavors"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    product_id    = Column(Integer, ForeignKey("products.id"))
    brand_id      = Column(Integer, ForeignKey("brands.id"), nullable=True)
    name          = Column(String(200))
    price         = Column(Integer, nullable=True)
    is_available  = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=datetime.now)
    district      = Column(String(20), default="ryabkovo")   # "ryabkovo" | "zaozernoe"
    photo_file_id = Column(String(300), nullable=True)
    kit_info      = Column(Text, nullable=True)   # комплектация (для POD-устройств, товар #5)


class CartItem(Base):
    __tablename__ = "cart_items"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    user_id      = Column(Integer)
    flavor_id    = Column(Integer, ForeignKey("flavors.id"))
    product_id   = Column(Integer)
    product_name = Column(String(200))
    brand_name   = Column(String(100), nullable=True)
    flavor_name  = Column(String(200))
    price        = Column(Integer, nullable=True)
    quantity     = Column(Integer, default=1)
    added_at     = Column(DateTime, default=datetime.now)


class Order(Base):
    __tablename__ = "orders"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    user_id         = Column(Integer)
    username        = Column(String(100), nullable=True)
    first_name      = Column(String(100), nullable=True)
    contact_info    = Column(String(500), nullable=True)
    status          = Column(String(20), default="pending")
    discount_pct    = Column(Float, default=0.0)
    promo_code      = Column(String(50), nullable=True)   # использованный промокод
    promo_discount  = Column(Integer, default=0)          # скидка в рублях от промокода
    district        = Column(String(20), nullable=True)   # "ryabkovo" | "zaozernoe"
    created_at      = Column(DateTime, default=datetime.now)
    confirmed_at    = Column(DateTime, nullable=True)
    confirmed_by    = Column(Integer, nullable=True)


class OrderItem(Base):
    __tablename__ = "order_items"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    order_id     = Column(Integer, ForeignKey("orders.id"))
    product_name = Column(String(200))
    brand_name   = Column(String(100), nullable=True)
    flavor_name  = Column(String(200))
    price        = Column(Integer, nullable=True)
    quantity     = Column(Integer, default=1)


class ProductView(Base):
    __tablename__ = "product_views"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer)
    user_id    = Column(Integer)
    viewed_at  = Column(DateTime, default=datetime.now)


class Referral(Base):
    __tablename__ = "referrals"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    referrer_user_id = Column(Integer)
    referred_user_id = Column(Integer, unique=True)
    purchased        = Column(Boolean, default=False)
    created_at       = Column(DateTime, default=datetime.now)


class PromoCode(Base):
    __tablename__ = "promo_codes"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    code           = Column(String(50), unique=True)
    discount_rub   = Column(Integer, default=0)       # скидка в рублях
    is_reusable    = Column(Boolean, default=False)   # многоразовый?
    is_active      = Column(Boolean, default=True)
    uses_left      = Column(Integer, nullable=True)   # None = неограниченно (если многоразовый)
    used_count     = Column(Integer, default=0)
    created_at     = Column(DateTime, default=datetime.now)


class AdminLog(Base):
    """Лог действий администраторов — кто, что и когда сделал в админке."""
    __tablename__ = "admin_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    admin_id    = Column(Integer)
    admin_name  = Column(String(150), nullable=True)
    action      = Column(String(100))           # короткий код действия, напр. "order_confirm"
    details     = Column(Text, nullable=True)    # человекочитаемое описание
    created_at  = Column(DateTime, default=datetime.now)


def log_admin_action(admin_id: int, admin_name: str | None, action: str, details: str = ""):
    """Пишет запись в лог действий администратора. Не бросает исключений наружу."""
    session = Session()
    try:
        session.add(AdminLog(
            admin_id=admin_id,
            admin_name=admin_name or str(admin_id),
            action=action,
            details=details,
        ))
        session.commit()
    except Exception as e:
        print(f"⚠️ Не удалось записать admin_log: {e}")
        session.rollback()
    finally:
        session.close()


# ── Утилиты ───────────────────────────────────────────────────────────────────

def set_channel_shown(user_id: int) -> None:
    """Отмечаем, что пользователю уже показывали экран подписки на канал."""
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=user_id).first()
        if user:
            user.channel_shown = True
            session.commit()
    except Exception as e:
        session.rollback()
        print(f"set_channel_shown error: {e}")
    finally:
        session.close()


def is_channel_shown(user_id: int) -> bool:
    """Возвращает True, если экран подписки уже показывали."""
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=user_id).first()
        return bool(user and user.channel_shown)
    except Exception as e:
        print(f"is_channel_shown error: {e}")
        return False
    finally:
        session.close()


def _generate_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(8))


def get_or_create_referral_code(user_id: int) -> str:
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=user_id).first()
        if user and user.referral_code:
            return user.referral_code
        code = _generate_code()
        while session.query(User).filter_by(referral_code=code).first():
            code = _generate_code()
        if user:
            user.referral_code = code
            session.commit()
        return code
    finally:
        session.close()


def _add_column_if_missing(table: str, col_name: str, col_def: str):
    from sqlalchemy import text, inspect
    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns(table)]
    if col_name not in cols:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
            conn.commit()


# ── Инициализация ─────────────────────────────────────────────────────────────

def init_db():
    Base.metadata.create_all(engine)

    for table, col, definition in [
        ("users",       "nickname",              "VARCHAR(100)"),
        ("users",       "avatar_file_id",        "VARCHAR(300)"),
        ("users",       "referral_code",         "VARCHAR(20)"),
        ("users",       "referral_discount_pct", "FLOAT DEFAULT 0.0"),
        ("users",       "district",              "VARCHAR(20)"),
        ("flavors",     "brand_id",              "INTEGER"),
        ("flavors",     "price",                 "INTEGER"),
        ("flavors",     "district",              "VARCHAR(20) DEFAULT 'ryabkovo'"),
        ("flavors",     "photo_file_id",         "VARCHAR(300)"),
        ("flavors",     "kit_info",              "TEXT"),
        ("cart_items",  "brand_name",            "VARCHAR(100)"),
        ("cart_items",  "price",                 "INTEGER"),
        ("orders",      "discount_pct",          "FLOAT DEFAULT 0.0"),
        ("orders",      "district",              "VARCHAR(20)"),
        ("orders",      "promo_code",            "VARCHAR(50)"),
        ("orders",      "promo_discount",        "INTEGER DEFAULT 0"),
        ("order_items", "brand_name",            "VARCHAR(100)"),
        ("order_items", "price",                 "INTEGER"),
        # ── ИСПРАВЛЕНИЕ 1: миграция новых колонок расписания ──
        ("products",    "item_text_ryabkovo",    "TEXT"),
        ("products",    "item_text_zaozernoe",   "TEXT"),
        # ── Блокировка пользователей ──
        ("users",       "is_blocked",            "BOOLEAN DEFAULT 0"),
        ("users",       "blocked_reason",        "VARCHAR(300)"),
        # ── Экран подписки на канал ──
        ("users",       "channel_shown",         "BOOLEAN DEFAULT 0"),
        # ── Юзер сам заблокировал бота (статистика, НЕ бан) ──
        ("users",       "left_bot",              "BOOLEAN DEFAULT 0"),
    ]:
        try:
            _add_column_if_missing(table, col, definition)
        except Exception:
            pass

    # Существующие вкусы без района → Рябково
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("UPDATE flavors SET district='ryabkovo' WHERE district IS NULL OR district=''"))
            conn.commit()
    except Exception:
        pass

    from config import config

    PRODUCTS = [
        (7,  "🔥 РАСПРОДАЖА",            config.ITEM_SALE_TEXT),
        (8,  "🎲 РАНДОМНЫЙ ТОВАР",        config.ITEM_RANDOM_TEXT),
        (1,  "НАЛИЧИЕ ЖИДКОСТЕЙ",         config.ITEM1_TEXT),
        (2,  "НАЛИЧИЕ СН*СА И ПЛАСТИНОК", config.ITEM2_TEXT),
        (3,  "НАЛИЧИЕ ОДНОРАЗОВЫХ ОЭС",   config.ITEM3_TEXT),
        (4,  "НАЛИЧИЕ РАСХОДНИКОВ",        config.ITEM4_TEXT),
        (5,  "НАЛИЧИЕ POD-УСТРОЙСТВ",      config.ITEM5_TEXT),
        (6,  "ИНФОРМАЦИЯ О ЗАВОЗЕ",        config.INFO_TEXT),
        (9,  "РАСПИСАНИЕ",                 config.SCHEDULE_TEXT),
    ]

    session = Session()
    try:
        for pid, name, text in PRODUCTS:
            p = session.query(Product).filter_by(id=pid).first()
            if not p:
                session.add(Product(id=pid, item_name=name, item_text=text))
            else:
                p.item_name = name
                if not p.item_text:
                    p.item_text = text

        if session.query(Brand).count() == 0:
            for b_name, b_emoji in DEFAULT_BRANDS:
                session.add(Brand(name=b_name, emoji=b_emoji))

        session.commit()
        print("✅ База данных инициализирована")
    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка init_db: {e}")
        raise
    finally:
        session.close()


# ══════════════════════════════════════════
# РЫНОК (Б/У ОБЪЯВЛЕНИЯ)
# ══════════════════════════════════════════

class MarketListing(Base):
    __tablename__ = "market_listings"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    user_id         = Column(Integer)
    username        = Column(String(100), nullable=True)
    first_name      = Column(String(100), nullable=True)
    contact         = Column(String(300))           # контакт продавца
    title           = Column(String(200))           # название товара
    description     = Column(Text)                  # описание
    price           = Column(String(100))           # цена (текст, напр. "500 руб.")
    district        = Column(String(100))           # район
    photo_file_id   = Column(String(300), nullable=True)  # фото товара
    payment_photo   = Column(String(300), nullable=True)  # скрин оплаты
    status          = Column(String(20), default="pending")  # pending / active / rejected
    created_at      = Column(DateTime, default=datetime.now)
    confirmed_at    = Column(DateTime, nullable=True)
    confirmed_by    = Column(Integer, nullable=True)


def init_market_db():
    """Создаёт таблицу market_listings если её нет."""
    MarketListing.__table__.create(engine, checkfirst=True)
