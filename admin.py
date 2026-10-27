#!/usr/bin/env python3
import asyncio
import csv
import io
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
    BufferedInputFile,
)
from aiogram.exceptions import TelegramForbiddenError

from sqlalchemy import func as sa_func, or_

from database import (
    Session, User, Product, Flavor, Order, OrderItem, Brand, Referral, PromoCode,
    MarketListing, AdminLog, log_admin_action,
    BRAND_CATEGORY_IDS, DISTRICT_NAMES, engine,
)
import config
from antispam import AntiSpamMiddleware
from textutils import esc
from district_access import (
    get_admin_district, set_admin_district, locked_district, order_district,
    is_zaozernoe_only, is_ryabkovo_only,
)

router = Router()
router.message.middleware(AntiSpamMiddleware())
print("✅ admin.py загружен")

# ── Район администратора / курьера (логика — в district_access.py) ───────────
def admin_district_label(user_id: int) -> str:
    return DISTRICT_NAMES.get(get_admin_district(user_id), "🏘 Рябково")


# ══════════════════════════════════════════
# СОСТОЯНИЯ
# ══════════════════════════════════════════

class AdminStates(StatesGroup):
    waiting_for_broadcast_text   = State()
    waiting_for_broadcast_media  = State()
    waiting_for_broadcast_url    = State()
    waiting_for_broadcast_btn    = State()
    waiting_for_new_text     = State()
    waiting_for_username     = State()
    waiting_for_user_message = State()
    waiting_for_admin_id     = State()
    waiting_for_brand_name   = State()
    waiting_for_brand_rename = State()
    waiting_for_brand_emoji  = State()
    waiting_for_flavor_name  = State()
    waiting_for_flavor_price = State()
    waiting_for_flavor_edit  = State()
    waiting_for_price_edit   = State()
    waiting_for_flavor_photo = State()
    waiting_for_flavor_kit   = State()
    # Промокоды
    waiting_for_promo_code       = State()
    waiting_for_promo_discount   = State()
    waiting_for_promo_type       = State()
    # Расписание по районам
    waiting_for_schedule_text    = State()
    # Управление пользователями
    waiting_for_user_search      = State()
    waiting_for_block_reason     = State()
    # Пост в канал
    waiting_for_channel_post     = State()
    # Фильтр заказов
    waiting_for_order_search     = State()


# ══════════════════════════════════════════
# КЛАВИАТУРЫ
# ══════════════════════════════════════════

def admin_menu_kb(user_id: int = 0):
    dist_label = admin_district_label(user_id)
    can_switch = locked_district(user_id) is None and user_id != 0
    switch_cb  = "switch_district" if can_switch else "noop"
    dist_hint  = " ↕" if can_switch else ""
    is_zaoz_only = user_id in config.config.ZAOZERNOE_ONLY_ADMINS

    rows = [
        [InlineKeyboardButton(text=f"📍 {dist_label}{dist_hint}", callback_data=switch_cb)],
        [InlineKeyboardButton(text="━━━━ 🗂 КАТАЛОГ ━━━━", callback_data="noop")],
        [InlineKeyboardButton(text="🏷 Бренды",           callback_data="manage_brands"),
         InlineKeyboardButton(text="🍬 Вкусы / позиции", callback_data="manage_flavors")],
        [InlineKeyboardButton(text="📝 Тексты категорий", callback_data="edit_texts"),
         InlineKeyboardButton(text="💡 Наличие товаров",  callback_data="bulk_availability")],
        [InlineKeyboardButton(text="━━━━ 📦 ЗАКАЗЫ ━━━━", callback_data="noop")],
        [InlineKeyboardButton(text="📋 Все заказы",       callback_data="orders_menu"),
         InlineKeyboardButton(text="⏳ Новые заказы",    callback_data="admin_orders")],
        [InlineKeyboardButton(text="📤 Экспорт заказов в Excel", callback_data="export_orders_excel")],
        [InlineKeyboardButton(text="━━━━ 👥 ПОЛЬЗОВАТЕЛИ ━━━━", callback_data="noop")],
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="users_search"),
         InlineKeyboardButton(text="📤 Выгрузить базу",    callback_data="get_db")],
        [InlineKeyboardButton(text="⛔ Заблокированные",    callback_data="blocked_users:0")],
        [InlineKeyboardButton(text="✉️ Написать пользователю", callback_data="send_to_user"),
         InlineKeyboardButton(text="📢 Рассылка",          callback_data="broadcast")],
        [InlineKeyboardButton(text="━━━━ 📊 АНАЛИТИКА ━━━━", callback_data="noop")],
        [InlineKeyboardButton(text="📊 Статистика",        callback_data="admin_stats"),
         InlineKeyboardButton(text="🏆 Топ товаров",       callback_data="stats_top_items")],
        [InlineKeyboardButton(text="━━━━ ⚙️ ИНСТРУМЕНТЫ ━━━━", callback_data="noop")],
        [InlineKeyboardButton(text="🛍 Рынок",            callback_data="admin_market:0")],
        [InlineKeyboardButton(text="👑 Администраторы",    callback_data="manage_admins"),
         InlineKeyboardButton(text="📜 Лог действий",      callback_data="admin_log:0")],
        [InlineKeyboardButton(text="🔄 Статус бота",       callback_data="restart_bot")],
        [InlineKeyboardButton(text="⬅️ Выйти из админки", callback_data="exit_admin")],
    ]

    if not is_zaoz_only and user_id != 0:
        rows.insert(9, [InlineKeyboardButton(text="🎟 Промокоды", callback_data="manage_promos")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def flavors_category_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 РАСПРОДАЖА",          callback_data="flav_cat_7")],
        [InlineKeyboardButton(text="🎲 РАНДОМНЫЙ ТОВАР",      callback_data="flav_cat_8")],
        [InlineKeyboardButton(text="🥤 ЖИДКОСТИ",             callback_data="flav_cat_1")],
        [InlineKeyboardButton(text="🧪 СН*С И ПЛАСТИНКИ",     callback_data="flav_cat_2")],
        [InlineKeyboardButton(text="💨 ОДНОРАЗОВЫЕ ОЭС",      callback_data="flav_cat_3")],
        [InlineKeyboardButton(text="🔧 РАСХОДНИКИ",           callback_data="flav_cat_4")],
        [InlineKeyboardButton(text="📱 POD-УСТРОЙСТВА",       callback_data="flav_cat_5")],
        [InlineKeyboardButton(text="⬅️ Назад",                callback_data="admin_menu")],
    ])

def edit_texts_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 РАСПРОДАЖА",          callback_data="edit_item_7")],
        [InlineKeyboardButton(text="🎲 РАНДОМНЫЙ ТОВАР",      callback_data="edit_item_8")],
        [InlineKeyboardButton(text="🥤 ЖИДКОСТИ",             callback_data="edit_item_1")],
        [InlineKeyboardButton(text="🧪 СН*С И ПЛАСТИНКИ",     callback_data="edit_item_2")],
        [InlineKeyboardButton(text="💨 ОДНОРАЗОВЫЕ ОЭС",      callback_data="edit_item_3")],
        [InlineKeyboardButton(text="🔧 РАСХОДНИКИ",           callback_data="edit_item_4")],
        [InlineKeyboardButton(text="📱 POD-УСТРОЙСТВА",       callback_data="edit_item_5")],
        [InlineKeyboardButton(text="📦 ИНФОРМАЦИЯ О ЗАВОЗЕ",  callback_data="edit_item_6")],
        [InlineKeyboardButton(text="📅 РАСПИСАНИЕ Рябково",   callback_data="edit_schedule_ryabkovo")],
        [InlineKeyboardButton(text="📅 РАСПИСАНИЕ Заозёрный", callback_data="edit_schedule_zaozernoe")],
        [InlineKeyboardButton(text="⬅️ Назад",                callback_data="admin_menu")],
    ])


# ══════════════════════════════════════════
# ПРАВА
# ══════════════════════════════════════════

async def is_admin(user_id: int) -> bool:
    if user_id in config.config.ADMINS:
        return True
    session = Session()
    try:
        u = session.query(User).filter_by(user_id=user_id).first()
        return bool(u and u.is_admin)
    finally:
        session.close()


# ══════════════════════════════════════════
# ВХОД В АДМИНКУ
# ══════════════════════════════════════════

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа")
        return
    # FIX #4: ZAOZERNOE_ONLY_ADMINS не имеют доступа к /admin
    if message.from_user.id in config.config.ZAOZERNOE_ONLY_ADMINS:
        await message.answer("⛔ Нет доступа")
        return
    set_admin_district(message.from_user.id, "ryabkovo")
    dist_label = admin_district_label(message.from_user.id)
    await message.answer(
        f"👑 <b>Админ-панель SUNTIME SHOP</b>\n📍 Район: <b>{dist_label}</b>\n\nВыберите действие:",
        reply_markup=admin_menu_kb(message.from_user.id), parse_mode="HTML",
    )


@router.message(Command("admin1"))
async def admin_panel1(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа")
        return
    # /admin1 — для суперадминов и курьеров Заозёрного; курьерам Рябково — нельзя
    if is_ryabkovo_only(message.from_user.id):
        await message.answer("⛔ Нет доступа")
        return
    # Если суперадмин — переключаем его район на заозёрный
    set_admin_district(message.from_user.id, "zaozernoe")
    dist_label = admin_district_label(message.from_user.id)
    await message.answer(
        f"👑 <b>Админ-панель SUNTIME SHOP</b>\n📍 Район: <b>{dist_label}</b>\n\nВыберите действие:",
        reply_markup=admin_menu_kb(message.from_user.id), parse_mode="HTML",
    )


@router.callback_query(F.data == "noop")
async def noop_cb(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "switch_district")
async def switch_district_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    _locked = locked_district(callback.from_user.id)
    if _locked:
        await callback.answer(f"Ваш район зафиксирован: {DISTRICT_NAMES.get(_locked, _locked)}"); return
    current  = get_admin_district(callback.from_user.id)
    new_dist = "zaozernoe" if current == "ryabkovo" else "ryabkovo"
    set_admin_district(callback.from_user.id, new_dist)
    await callback.answer(f"Район переключён: {DISTRICT_NAMES.get(new_dist, new_dist)}")
    dist_label = admin_district_label(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"👑 <b>Админ-панель SUNTIME SHOP</b>\n📍 Район: <b>{dist_label}</b>\n\nВыберите действие:",
            reply_markup=admin_menu_kb(callback.from_user.id), parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_menu")
async def go_admin_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    dist_label = admin_district_label(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"👑 <b>Админ-панель SUNTIME SHOP</b>\n📍 Район: <b>{dist_label}</b>\n\nВыберите действие:",
            reply_markup=admin_menu_kb(callback.from_user.id), parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            f"👑 <b>Админ-панель SUNTIME SHOP</b>\n📍 Район: <b>{dist_label}</b>\n\nВыберите действие:",
            reply_markup=admin_menu_kb(callback.from_user.id), parse_mode="HTML",
        )


# ══════════════════════════════════════════
# УПРАВЛЕНИЕ БРЕНДАМИ
# ══════════════════════════════════════════

ADMIN_BRANDS_PER_PAGE  = 8   # брендов на страницу в админке
ADMIN_FLAVORS_PER_PAGE = 8   # вкусов на страницу в списках вкусов (админка)


async def _show_brands(msg: Message, user_id: int = 0, page: int = 0):
    session = Session()
    try:
        dist   = get_admin_district(user_id) if user_id else "ryabkovo"
        brands = session.query(Brand).order_by(Brand.id).all()
        counts = {b.id: session.query(Flavor).filter_by(brand_id=b.id, district=dist).count() for b in brands}
    finally:
        session.close()

    dist_label_local = DISTRICT_NAMES.get(get_admin_district(user_id), "Рябково") if user_id else ""
    text     = f"🏷 <b>Управление брендами</b>  ·  {dist_label_local}\n\n" if dist_label_local else "🏷 <b>Управление брендами</b>\n\n"
    keyboard = []

    # Показываем ТОЛЬКО бренды у которых есть вкусы в текущем районе
    district_brands = [b for b in brands if counts[b.id] > 0]

    if district_brands:
        total_pages = (len(district_brands) - 1) // ADMIN_BRANDS_PER_PAGE + 1
        page = max(0, min(page, total_pages - 1))
        page_brands = district_brands[page * ADMIN_BRANDS_PER_PAGE:(page + 1) * ADMIN_BRANDS_PER_PAGE]

        if total_pages > 1:
            text += f"<i>стр. {page + 1}/{total_pages}  ·  брендов: {len(district_brands)}</i>\n\n"

        for b in page_brands:
            text += f"{b.emoji} <b>{b.name}</b>  ·  {counts[b.id]} вкус(ов)\n"
            keyboard.append([
                InlineKeyboardButton(text=f"✏️ {b.name[:20]}", callback_data=f"brename:{b.id}"),
                InlineKeyboardButton(text=b.emoji,              callback_data=f"breemoji:{b.id}"),
                InlineKeyboardButton(text="🍬",                 callback_data=f"bview:{b.id}"),
                InlineKeyboardButton(text="🗑",                 callback_data=f"bdel:{b.id}"),
            ])

        # Навигация по страницам
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"abpage:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"abpage:{page + 1}"))
        if nav_row:
            keyboard.append(nav_row)
    else:
        text += "<i>В этом районе пока нет брендов с вкусами.</i>\n"

    # Кнопка добавления нового бренда
    keyboard.append([InlineKeyboardButton(text="➕ Добавить новый бренд", callback_data="brand_add")])
    # Кнопка для добавления вкуса уже существующему бренду (из другого района)
    keyboard.append([InlineKeyboardButton(text="📋 Все бренды (добавить вкус)", callback_data="brand_list_all")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад",          callback_data="admin_menu")])

    try:
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
    except Exception:
        await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")


async def _show_all_brands(msg, user_id: int = 0, page: int = 0):
    """Показывает ВСЕ бренды для добавления вкуса в текущий район."""
    session = Session()
    try:
        dist   = get_admin_district(user_id) if user_id else "ryabkovo"
        brands = session.query(Brand).order_by(Brand.id).all()
        counts = {b.id: session.query(Flavor).filter_by(brand_id=b.id, district=dist).count() for b in brands}
    finally:
        session.close()

    dist_label_local = DISTRICT_NAMES.get(dist, "Рябково")

    total_pages = max(1, (len(brands) - 1) // ADMIN_BRANDS_PER_PAGE + 1)
    page = max(0, min(page, total_pages - 1))
    page_brands = brands[page * ADMIN_BRANDS_PER_PAGE:(page + 1) * ADMIN_BRANDS_PER_PAGE]

    text     = f"📋 <b>Все бренды</b>  ·  {dist_label_local}\n<i>Выберите бренд чтобы добавить в него вкус для этого района:</i>\n"
    if total_pages > 1:
        text += f"<i>стр. {page + 1}/{total_pages}  ·  всего: {len(brands)}</i>\n"
    text += "\n"
    keyboard = []

    for b in page_brands:
        cnt  = counts[b.id]
        mark = f"  ✅ {cnt} вкус(ов)" if cnt > 0 else "  ➕ нет вкусов"
        text += f"{b.emoji} <b>{b.name}</b>{mark}\n"
        keyboard.append([InlineKeyboardButton(
            text=f"{b.emoji} {b.name[:22]}{mark}",
            callback_data=f"bview:{b.id}",
        )])

    # Навигация по страницам
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"aallbpage:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"aallbpage:{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(text="⬅️ Назад к брендам", callback_data="manage_brands")])

    try:
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
    except Exception:
        await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")


@router.callback_query(F.data == "manage_brands")
async def manage_brands(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    await _show_brands(callback.message, callback.from_user.id)


@router.callback_query(F.data == "brand_list_all")
async def brand_list_all(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    await _show_all_brands(callback.message, callback.from_user.id)


# ── Листание: страницы в управлении брендами (районные бренды) ──
@router.callback_query(F.data.startswith("abpage:"))
async def admin_brand_page_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка"); return
    await callback.answer()
    await _show_brands(callback.message, callback.from_user.id, page)


# ── Листание: страницы в «Все бренды» ──
@router.callback_query(F.data.startswith("aallbpage:"))
async def admin_all_brand_page_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка"); return
    await callback.answer()
    await _show_all_brands(callback.message, callback.from_user.id, page)


# ── Листание: бренды внутри категории (управление вкусами) ──
@router.callback_query(F.data.startswith("abrandpage:"))
async def admin_brand_page_for_cat_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts = callback.data.split(":")
    try:
        product_id = int(parts[1])
        page       = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка"); return
    await callback.answer()
    await _show_brands_for_cat(callback, product_id, page)


# ── Листание: вкусы бренда в админке ──
@router.callback_query(F.data.startswith("aflpage:"))
async def admin_flavor_page_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts = callback.data.split(":")
    try:
        product_id = int(parts[1])
        brand_id   = int(parts[2])
        page       = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка"); return
    await callback.answer()
    await _render_brand_flavors(callback, product_id, brand_id, page)


# ── Листание: прямые вкусы (без брендов) в админке ──
@router.callback_query(F.data.startswith("adfpage:"))
async def admin_direct_flavor_page_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts = callback.data.split(":")
    try:
        product_id = int(parts[1])
        page       = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка"); return
    await callback.answer()
    await _show_direct_flavors(callback, product_id, page)


@router.callback_query(F.data == "brand_add")
async def brand_add_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text(
            "➕ <b>Новый бренд</b>\n\nВведите название бренда:\n<i>Пример: LOST MARY</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="manage_brands")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            "➕ <b>Новый бренд</b>\n\nВведите название бренда:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="manage_brands")]
            ]),
            parse_mode="HTML",
        )
    await state.set_state(AdminStates.waiting_for_brand_name)


@router.message(AdminStates.waiting_for_brand_name)
async def brand_add_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("❌ Пустое название. Попробуйте ещё раз:"); return

    await state.clear()
    session = Session()
    try:
        exists = session.query(Brand).filter(Brand.name.ilike(name)).first()
        if exists:
            await message.answer(
                f"❌ Бренд <b>{name}</b> уже есть!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ К брендам", callback_data="manage_brands")]
                ]),
                parse_mode="HTML",
            )
            return

        brand = Brand(name=name, emoji="🏷")
        session.add(brand)
        session.commit()
        brand_id = brand.id

        await message.answer(
            f"✅ <b>Бренд «{name}» создан!</b>\n\n"
            f"Выберите категорию чтобы сразу добавить вкусы:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🥤 Жидкости",       callback_data=f"abrand:1:{brand_id}")],
                [InlineKeyboardButton(text="🧪 СН*С/Пластинки", callback_data=f"abrand:2:{brand_id}")],
                [InlineKeyboardButton(text="💨 Одноразовые ОЭС", callback_data=f"abrand:3:{brand_id}")],
                [InlineKeyboardButton(text="🔥 Распродажа",      callback_data=f"abrand:7:{brand_id}")],
                [InlineKeyboardButton(text="🎲 Рандомный товар", callback_data=f"abrand:8:{brand_id}")],
                [InlineKeyboardButton(text="⬅️ К брендам",       callback_data="manage_brands")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка сохранения: {e}")
    finally:
        session.close()


@router.callback_query(F.data.startswith("brename:"))
async def brand_rename_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    brand_id = int(callback.data.split(":")[1])
    session  = Session()
    try:
        brand = session.query(Brand).filter_by(id=brand_id).first()
        if not brand:
            await callback.answer("❌ Бренд не найден", show_alert=True); return
        await state.clear()
        await state.update_data(rename_brand_id=brand_id)
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"✏️ <b>Переименовать бренд</b>\n\n"
                f"Текущее: <b>{brand.emoji} {brand.name}</b>\n\nВведите новое название:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="manage_brands")]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                f"✏️ <b>Переименовать бренд</b>\n\nТекущее: <b>{brand.name}</b>\n\nВведите новое название:",
                parse_mode="HTML",
            )
        await state.set_state(AdminStates.waiting_for_brand_rename)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_brand_rename)
async def brand_rename_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    new_name = message.text.strip() if message.text else ""
    if not new_name:
        await message.answer("❌ Пустое название. Введите ещё раз:"); return

    data = await state.get_data()
    await state.clear()
    brand_id = data.get("rename_brand_id")

    session = Session()
    try:
        brand = session.query(Brand).filter_by(id=brand_id).first()
        if not brand:
            await message.answer("❌ Бренд не найден"); return

        dup = session.query(Brand).filter(Brand.name.ilike(new_name), Brand.id != brand_id).first()
        if dup:
            await message.answer(
                f"❌ Бренд <b>{new_name}</b> уже существует!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ К брендам", callback_data="manage_brands")]
                ]),
                parse_mode="HTML",
            )
            return

        old_name   = brand.name
        brand.name = new_name
        session.commit()

        await message.answer(
            f"✅ <b>Бренд переименован!</b>\n\n<b>{old_name}</b> → <b>{new_name}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏷 К брендам", callback_data="manage_brands")],
                [InlineKeyboardButton(text="🏠 Меню",       callback_data="admin_menu")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


@router.callback_query(F.data.startswith("breemoji:"))
async def brand_emoji_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    brand_id = int(callback.data.split(":")[1])
    session  = Session()
    try:
        brand = session.query(Brand).filter_by(id=brand_id).first()
        if not brand:
            await callback.answer("❌ Бренд не найден", show_alert=True); return
        await state.clear()
        await state.update_data(emoji_brand_id=brand_id)
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"✏️ <b>Изменить эмодзи бренда</b>\n\n"
                f"Бренд: <b>{brand.emoji} {brand.name}</b>\n\n"
                f"Отправьте новый эмодзи (один символ):",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="manage_brands")]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                f"✏️ <b>Изменить эмодзи бренда</b>\n\nБренд: <b>{brand.name}</b>\n\nОтправьте новый эмодзи:",
                parse_mode="HTML",
            )
        await state.set_state(AdminStates.waiting_for_brand_emoji)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_brand_emoji)
async def brand_emoji_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    new_emoji = (message.text or "").strip()
    if not new_emoji:
        await message.answer("❌ Пустой ввод. Отправьте эмодзи:"); return

    data = await state.get_data()
    await state.clear()
    brand_id = data.get("emoji_brand_id")

    session = Session()
    try:
        brand = session.query(Brand).filter_by(id=brand_id).first()
        if not brand:
            await message.answer("❌ Бренд не найден"); return

        old_emoji  = brand.emoji
        brand.emoji = new_emoji
        session.commit()

        await message.answer(
            f"✅ <b>Эмодзи бренда обновлён!</b>\n\n"
            f"{old_emoji} <b>{brand.name}</b>  →  {new_emoji} <b>{brand.name}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏷 К брендам", callback_data="manage_brands")],
                [InlineKeyboardButton(text="🏠 Меню",       callback_data="admin_menu")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


@router.callback_query(F.data.startswith("bview:"))
async def brand_view_cats(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    brand_id = int(callback.data.split(":")[1])
    session  = Session()
    try:
        brand = session.query(Brand).filter_by(id=brand_id).first()
        if not brand:
            await callback.answer("❌ Бренд не найден", show_alert=True); return
        brand_label = f"{brand.emoji} {brand.name}"
    finally:
        session.close()

    await callback.answer()

    CAT = [(1,"🥤 Жидкости"), (2,"🧪 СН*С"), (3,"💨 Одноразовые"), (7,"🔥 Распродажа"), (8,"🎲 Рандомный")]
    keyboard = [[InlineKeyboardButton(text=n, callback_data=f"abrand:{c}:{brand_id}")] for c, n in CAT]
    keyboard.append([InlineKeyboardButton(text="⬅️ К брендам", callback_data="manage_brands")])

    try:
        await callback.message.edit_text(
            f"🍬 <b>{brand_label}</b>\n\nВыберите категорию:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode="HTML",
        )
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("bdel:"))
async def brand_del_ask(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    brand_id = int(callback.data.split(":")[1])
    session  = Session()
    try:
        brand = session.query(Brand).filter_by(id=brand_id).first()
        if not brand:
            await callback.answer("❌ Бренд не найден", show_alert=True); return
        dist    = get_admin_district(callback.from_user.id)
        cnt     = session.query(Flavor).filter_by(brand_id=brand_id, district=dist).count()
        cnt_all = session.query(Flavor).filter_by(brand_id=brand_id).count()
        bname   = brand.name
        bemoji  = brand.emoji
    finally:
        session.close()

    dist_label = DISTRICT_NAMES.get(dist, dist)
    warn_parts = []
    if cnt > 0:
        warn_parts.append(f"⚠️ В районе <b>{dist_label}</b> удалится <b>{cnt} вкус(ов)</b>!")
    if cnt_all - cnt > 0:
        warn_parts.append(f"ℹ️ Вкусы других районов (<b>{cnt_all - cnt} шт.</b>) НЕ затронуты.")
    warn = "\n" + "\n".join(warn_parts) if warn_parts else ""
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"🗑 Удалить бренд <b>{bemoji} {bname}</b>?{warn}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"bdelok:{brand_id}")],
                [InlineKeyboardButton(text="❌ Отмена",       callback_data="manage_brands")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("bdelok:"))
async def brand_del_ok(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    brand_id = int(callback.data.split(":")[1])
    dist     = get_admin_district(callback.from_user.id)
    session  = Session()
    try:
        session.query(Flavor).filter_by(brand_id=brand_id, district=dist).delete()
        remaining = session.query(Flavor).filter_by(brand_id=brand_id).count()
        brand = session.query(Brand).filter_by(id=brand_id).first()
        name  = brand.name if brand else "—"
        if brand and remaining == 0:
            session.delete(brand)
        session.commit()
        dist_label = DISTRICT_NAMES.get(dist, dist)
        msg_extra  = f"\n✅ Бренд сохранён (вкусы других районов: {remaining} шт.)" if remaining > 0 else ""
        await callback.answer(f"🗑 Вкусы «{name}» [{dist_label}] удалены{msg_extra}", show_alert=True)
        await _show_brands(callback.message, callback.from_user.id)
    except Exception as e:
        session.rollback()
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    finally:
        session.close()


# ══════════════════════════════════════════
# УПРАВЛЕНИЕ ВКУСАМИ — КАТЕГОРИИ
# ══════════════════════════════════════════

@router.callback_query(F.data == "manage_flavors")
async def manage_flavors_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    dist_label = admin_district_label(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"🍬 <b>Управление вкусами</b> · {dist_label}\n\nВыберите категорию:",
            reply_markup=flavors_category_kb(), parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            f"🍬 <b>Управление вкусами</b> · {dist_label}\n\nВыберите категорию:",
            reply_markup=flavors_category_kb(), parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("flav_cat_"))
async def flavors_for_category(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    product_id = int(callback.data[len("flav_cat_"):])
    await callback.answer()
    if product_id in BRAND_CATEGORY_IDS:
        await _show_brands_for_cat(callback, product_id)
    else:
        await _show_direct_flavors(callback, product_id)


async def _show_brands_for_cat(callback: CallbackQuery, product_id: int, page: int = 0):
    dist = get_admin_district(callback.from_user.id)
    session = Session()
    try:
        product   = session.query(Product).filter_by(id=product_id).first()
        brands    = session.query(Brand).order_by(Brand.id).all()
        prod_name = product.item_name if product else f"Категория {product_id}"
        counts    = {b.id: session.query(Flavor).filter_by(
                        product_id=product_id, brand_id=b.id, district=dist).count()
                     for b in brands}
    finally:
        session.close()

    dist_label = DISTRICT_NAMES.get(dist, dist)

    brands_with = [b for b in brands if counts[b.id] > 0]
    brands_without = [b for b in brands if counts[b.id] == 0]
    display_brands = brands_with if brands_with else brands_without

    total_pages = max(1, (len(display_brands) - 1) // ADMIN_BRANDS_PER_PAGE + 1)
    page = max(0, min(page, total_pages - 1))
    page_brands = display_brands[page * ADMIN_BRANDS_PER_PAGE:(page + 1) * ADMIN_BRANDS_PER_PAGE]

    text     = f"🍬 <b>{prod_name}</b> · {dist_label}\n\nВыберите бренд:\n"
    if total_pages > 1:
        text += f"<i>стр. {page + 1}/{total_pages}</i>\n"
    text += "\n"
    keyboard = []

    if brands_with:
        for b in page_brands:
            cnt = counts[b.id]
            text += f"{b.emoji} {b.name}  ·  {cnt} вкус(ов)\n"
            keyboard.append([InlineKeyboardButton(
                text=f"{b.emoji} {b.name}  ·  {cnt} шт.",
                callback_data=f"abrand:{product_id}:{b.id}",
            )])
    else:
        text += "<i>В этом районе пока нет вкусов. Выберите бренд для добавления:</i>\n\n"
        for b in page_brands:
            keyboard.append([InlineKeyboardButton(
                text=f"➕ {b.emoji} {b.name}",
                callback_data=f"abrand:{product_id}:{b.id}",
            )])

    # Навигация
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"abrandpage:{product_id}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"abrandpage:{product_id}:{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_flavors")])

    try:
        await callback.message.edit_text(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML",
        )
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("abrand:"))
async def show_brand_flavors(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    parts      = callback.data.split(":")
    product_id = int(parts[1])
    brand_id   = int(parts[2])
    await callback.answer()
    await _render_brand_flavors(callback, product_id, brand_id)


async def _render_brand_flavors(callback: CallbackQuery, product_id: int, brand_id: int, page: int = 0):
    dist = get_admin_district(callback.from_user.id)
    session = Session()
    try:
        product = session.query(Product).filter_by(id=product_id).first()
        brand   = session.query(Brand).filter_by(id=brand_id).first()
        flavors = (session.query(Flavor)
                   .filter_by(product_id=product_id, brand_id=brand_id, district=dist)
                   .order_by(Flavor.id).all())

        prod_name  = product.item_name if product else f"Категория {product_id}"
        brand_name = f"{brand.emoji} {brand.name}" if brand else f"Бренд {brand_id}"
        dist_label = DISTRICT_NAMES.get(dist, dist)

        total_pages = max(1, (len(flavors) - 1) // ADMIN_FLAVORS_PER_PAGE + 1) if flavors else 1
        page = max(0, min(page, total_pages - 1))
        page_flavors = flavors[page * ADMIN_FLAVORS_PER_PAGE:(page + 1) * ADMIN_FLAVORS_PER_PAGE]

        text     = f"<b>{brand_name}</b>  ·  <i>{prod_name}</i>  ·  {dist_label}\n"
        if total_pages > 1:
            text += f"<i>стр. {page + 1}/{total_pages}  ·  вкусов: {len(flavors)}</i>\n"
        text += "\n"
        keyboard = []

        if flavors:
            for fl in page_flavors:
                st   = "✅" if fl.is_available else "❌"
                pr   = f"{fl.price} ₽" if fl.price else "—"
                ph   = "📷" if fl.photo_file_id else "  "
                text += f"{st} {ph} {fl.name}  ·  {pr}\n"
                keyboard.append([
                    InlineKeyboardButton(text=f"✏️ {fl.name[:16]}", callback_data=f"fedit:{fl.id}"),
                    InlineKeyboardButton(text="💰",                  callback_data=f"fprice:{fl.id}"),
                    InlineKeyboardButton(text="📷",                  callback_data=f"fphoto:{fl.id}"),
                    InlineKeyboardButton(text=st,                    callback_data=f"ftoggle:{fl.id}"),
                    InlineKeyboardButton(text="🗑",                  callback_data=f"fdel:{fl.id}"),
                ])

            # Навигация
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"aflpage:{product_id}:{brand_id}:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"aflpage:{product_id}:{brand_id}:{page + 1}"))
            if nav_row:
                keyboard.append(nav_row)
        else:
            text += "<i>Вкусов нет. Добавьте первый!</i>\n"

        keyboard.append([InlineKeyboardButton(
            text="➕ Добавить вкус",
            callback_data=f"fadd:{product_id}:{brand_id}:{page}",
        )])
        back = f"flav_cat_{product_id}" if product_id in BRAND_CATEGORY_IDS else "manage_flavors"
        keyboard.append([InlineKeyboardButton(text="⬅️ К брендам", callback_data=back)])

        try:
            await callback.message.edit_text(
                text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML",
            )
        except Exception as e:
            await callback.answer(f"Ошибка отображения: {e}", show_alert=True)
    finally:
        session.close()


async def _show_direct_flavors(callback: CallbackQuery, product_id: int, page: int = 0):
    dist = get_admin_district(callback.from_user.id)
    session = Session()
    try:
        product    = session.query(Product).filter_by(id=product_id).first()
        flavors    = (session.query(Flavor)
                     .filter_by(product_id=product_id, brand_id=None, district=dist)
                     .order_by(Flavor.id).all())
        prod_name  = product.item_name if product else f"Товар {product_id}"
        dist_label = DISTRICT_NAMES.get(dist, dist)

        total_pages = max(1, (len(flavors) - 1) // ADMIN_FLAVORS_PER_PAGE + 1) if flavors else 1
        page = max(0, min(page, total_pages - 1))
        page_flavors = flavors[page * ADMIN_FLAVORS_PER_PAGE:(page + 1) * ADMIN_FLAVORS_PER_PAGE]

        text     = f"🍬 <b>{prod_name}</b>  ·  {dist_label}\n"
        if total_pages > 1:
            text += f"<i>стр. {page + 1}/{total_pages}  ·  позиций: {len(flavors)}</i>\n"
        text += "\n"
        keyboard = []

        if flavors:
            is_pod = (product_id == 5)
            for fl in page_flavors:
                st  = "✅" if fl.is_available else "❌"
                pr  = f"{fl.price} ₽" if fl.price else "—"
                ph  = "📷" if fl.photo_file_id else "  "
                kt  = ("📚" if fl.kit_info else "  ") if is_pod else ""
                text += f"{st} {ph}{kt} {fl.name}  ·  {pr}\n"
                row = [
                    InlineKeyboardButton(text=f"✏️ {fl.name[:16]}", callback_data=f"fedit:{fl.id}"),
                    InlineKeyboardButton(text="💰",                  callback_data=f"fprice:{fl.id}"),
                    InlineKeyboardButton(text="📷",                  callback_data=f"fphoto:{fl.id}"),
                ]
                if is_pod:
                    row.append(InlineKeyboardButton(text="📚", callback_data=f"fkit:{fl.id}"))
                row += [
                    InlineKeyboardButton(text=st,  callback_data=f"ftoggle:{fl.id}"),
                    InlineKeyboardButton(text="🗑", callback_data=f"fdel:{fl.id}"),
                ]
                keyboard.append(row)

            # Навигация
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"adfpage:{product_id}:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"adfpage:{product_id}:{page + 1}"))
            if nav_row:
                keyboard.append(nav_row)
        else:
            text += "<i>Позиций нет. Добавьте первую!</i>\n"

        keyboard.append([InlineKeyboardButton(
            text="➕ Добавить позицию",
            callback_data=f"fadd:{product_id}:0:{page}",
        )])
        keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_flavors")])

        try:
            await callback.message.edit_text(
                text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML",
            )
        except Exception as e:
            await callback.answer(f"Ошибка: {e}", show_alert=True)
    finally:
        session.close()


# ══════════════════════════════════════════
# ДОБАВЛЕНИЕ ВКУСА
# ══════════════════════════════════════════

@router.callback_query(F.data.startswith("fadd:"))
async def flavor_add_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    parts      = callback.data.split(":")
    product_id = int(parts[1])
    brand_id   = int(parts[2])
    try:
        from_page = int(parts[3])
    except (IndexError, ValueError):
        from_page = 0

    await state.clear()
    await state.update_data(fp=product_id, fb=brand_id, fpg=from_page)

    session = Session()
    try:
        brand     = session.query(Brand).filter_by(id=brand_id).first() if brand_id else None
        brand_str = f"  ·  {brand.emoji} {brand.name}" if brand else ""
    finally:
        session.close()

    back = _flavors_page_cb(product_id, brand_id, from_page)
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"➕ <b>Добавление вкуса{brand_str}</b>\n\n"
            "Введите <b>название</b>:\n<i>Пример: Манго-Маракуйя 3мг</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=back)]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(f"➕ <b>Добавление вкуса</b>\n\nВведите название:", parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_flavor_name)


@router.message(AdminStates.waiting_for_flavor_name)
async def flavor_add_got_name(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    await state.update_data(fn=message.text.strip())
    await message.answer(
        f"✅ Название: <b>{message.text.strip()}</b>\n\n"
        "Введите <b>цену</b> в рублях:\n<i>Например: 1200  или  0 — без цены</i>",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_for_flavor_price)


@router.message(AdminStates.waiting_for_flavor_price)
async def flavor_add_got_price(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    try:
        price_val = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Только цифры! Например: 1200 или 0"); return

    data       = await state.get_data()
    product_id = data.get("fp")
    brand_id   = data.get("fb", 0)
    name       = data.get("fn", "")
    price      = price_val if price_val > 0 else None
    dist       = get_admin_district(message.from_user.id)
    await state.clear()

    session = Session()
    try:
        session.add(Flavor(
            product_id=product_id,
            brand_id=brand_id if brand_id else None,
            name=name,
            price=price,
            district=dist,
        ))
        session.commit()

        brand     = session.query(Brand).filter_by(id=brand_id).first() if brand_id else None
        brand_str = f"  ·  {brand.name}" if brand else ""
        price_str = f"{price} ₽" if price else "без цены"
        dist_str  = DISTRICT_NAMES.get(dist, dist)
        # новый вкус встаёт в конец списка → «К списку» открывает последнюю страницу
        # (page больше числа страниц — рендер сам обрежет до последней)
        back      = _flavors_page_cb(product_id, brand_id, 9999)

        await message.answer(
            f"✅ <b>Вкус добавлен!</b>\n\n"
            f"📛 {name}{brand_str}\n💰 {price_str}\n📍 {dist_str}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Ещё вкус",   callback_data=f"fadd:{product_id}:{brand_id}:9999")],
                [InlineKeyboardButton(text="📋 К списку",   callback_data=back)],
                [InlineKeyboardButton(text="🏠 Меню",       callback_data="admin_menu")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка сохранения: {e}")
    finally:
        session.close()


# ══════════════════════════════════════════
# ФОТО ДЛЯ ВКУСА
# ══════════════════════════════════════════

def _flavors_page_cb(product_id: int, brand_id, page: int = 0) -> str:
    """callback_data, открывающий список вкусов сразу на нужной странице."""
    if brand_id:
        return f"aflpage:{product_id}:{brand_id}:{page}"
    return f"adfpage:{product_id}:{page}"


def _flavor_page(session, fl) -> int:
    """Номер страницы списка (0..), на которой стоит вкус fl — как в _render_brand_flavors."""
    ids = [r[0] for r in (
        session.query(Flavor.id)
        .filter_by(product_id=fl.product_id, brand_id=fl.brand_id, district=fl.district)
        .order_by(Flavor.id).all()
    )]
    idx = ids.index(fl.id) if fl.id in ids else 0
    return idx // ADMIN_FLAVORS_PER_PAGE


def _back_for_flavor(fl) -> str:
    """Кнопка «назад/к списку» — возвращает на ТУ ЖЕ страницу, где стоит вкус."""
    session = Session()
    try:
        page = _flavor_page(session, fl)
    except Exception:
        page = 0
    finally:
        session.close()
    return _flavors_page_cb(fl.product_id, fl.brand_id, page)


@router.callback_query(F.data.startswith("fphoto:"))
async def flavor_photo_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    flavor_id = int(callback.data.split(":")[1])
    session   = Session()
    try:
        fl = session.query(Flavor).filter_by(id=flavor_id).first()
        if not fl:
            await callback.answer("❌ Не найдено", show_alert=True); return
        await state.clear()
        await state.update_data(eid=flavor_id, fp=fl.product_id, fb=fl.brand_id or 0)
        back = _back_for_flavor(fl)

        has_photo  = bool(fl.photo_file_id)
        extra_btns = []
        if has_photo:
            extra_btns.append([InlineKeyboardButton(
                text="🗑 Удалить фото", callback_data=f"fphoto_del:{flavor_id}")])

        await callback.answer()
        try:
            await callback.message.edit_text(
                f"📷 <b>Фото для: {fl.name}</b>\n\n"
                f"{'✅ Фото прикреплено' if has_photo else '📭 Фото нет'}\n\n"
                f"Отправьте новое фото (или нажмите «Удалить»):",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=extra_btns + [
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=back)]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(f"📷 <b>Фото для: {fl.name}</b>\n\nОтправьте фото:", parse_mode="HTML")
        await state.set_state(AdminStates.waiting_for_flavor_photo)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_flavor_photo)
async def flavor_photo_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    if not message.photo:
        await message.answer("❌ Нужно отправить фото! Или нажмите «Отмена»."); return

    file_id = message.photo[-1].file_id
    data    = await state.get_data()
    await state.clear()

    session = Session()
    try:
        fl = session.query(Flavor).filter_by(id=data["eid"]).first()
        if fl:
            fl.photo_file_id = file_id
            session.commit()
            back = _back_for_flavor(fl)
            await message.answer(
                f"✅ <b>Фото добавлено!</b>\n📛 {fl.name}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 К списку", callback_data=back)],
                    [InlineKeyboardButton(text="🏠 Меню",     callback_data="admin_menu")],
                ]),
                parse_mode="HTML",
            )
        else:
            await message.answer("❌ Позиция не найдена")
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


@router.callback_query(F.data.startswith("fphoto_del:"))
async def flavor_photo_del(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    flavor_id = int(callback.data.split(":")[1])
    await state.clear()
    session = Session()
    try:
        fl = session.query(Flavor).filter_by(id=flavor_id).first()
        if not fl:
            await callback.answer("❌ Не найдено", show_alert=True); return
        fl.photo_file_id = None
        session.commit()
        await callback.answer("🗑 Фото удалено")
        page = _flavor_page(session, fl)
        if fl.brand_id:
            await _render_brand_flavors(callback, fl.product_id, fl.brand_id, page)
        else:
            await _show_direct_flavors(callback, fl.product_id, page)
    except Exception as e:
        session.rollback()
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    finally:
        session.close()


# ══════════════════════════════════════════
# КОМПЛЕКТАЦИЯ POD-УСТРОЙСТВА (только товар #5)
# ══════════════════════════════════════════

@router.callback_query(F.data.startswith("fkit:"))
async def flavor_kit_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    flavor_id = int(callback.data.split(":")[1])
    session   = Session()
    try:
        fl = session.query(Flavor).filter_by(id=flavor_id).first()
        if not fl:
            await callback.answer("❌ Не найдено", show_alert=True); return
        await state.clear()
        await state.update_data(eid=flavor_id, fp=fl.product_id, fb=fl.brand_id or 0)
        back = _back_for_flavor(fl)

        has_kit    = bool(fl.kit_info)
        extra_btns = []
        if has_kit:
            extra_btns.append([InlineKeyboardButton(
                text="🗑 Удалить комплектацию", callback_data=f"fkit_del:{flavor_id}")])

        current = f"\n\n<code>{fl.kit_info}</code>" if has_kit else ""
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"📚 <b>Комплектация для: {fl.name}</b>\n\n"
                f"{'✅ Текст указан:' if has_kit else '📭 Пока не указана'}{current}\n\n"
                f"Отправьте текст комплектации (или нажмите «Удалить»):",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=extra_btns + [
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=back)]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(f"📚 <b>Комплектация для: {fl.name}</b>\n\nОтправьте текст:", parse_mode="HTML")
        await state.set_state(AdminStates.waiting_for_flavor_kit)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_flavor_kit)
async def flavor_kit_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    if not message.text:
        await message.answer("❌ Нужно отправить текст! Или нажмите «Отмена»."); return

    kit_text = message.text.strip()
    data     = await state.get_data()
    await state.clear()

    session = Session()
    try:
        fl = session.query(Flavor).filter_by(id=data["eid"]).first()
        if fl:
            fl.kit_info = kit_text
            session.commit()
            back = _back_for_flavor(fl)
            await message.answer(
                f"✅ <b>Комплектация сохранена!</b>\n📛 {fl.name}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 К списку", callback_data=back)],
                    [InlineKeyboardButton(text="🏠 Меню",     callback_data="admin_menu")],
                ]),
                parse_mode="HTML",
            )
        else:
            await message.answer("❌ Позиция не найдена")
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


@router.callback_query(F.data.startswith("fkit_del:"))
async def flavor_kit_del(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    flavor_id = int(callback.data.split(":")[1])
    await state.clear()
    session = Session()
    try:
        fl = session.query(Flavor).filter_by(id=flavor_id).first()
        if not fl:
            await callback.answer("❌ Не найдено", show_alert=True); return
        fl.kit_info = None
        session.commit()
        await callback.answer("🗑 Комплектация удалена")
        page = _flavor_page(session, fl)
        if fl.brand_id:
            await _render_brand_flavors(callback, fl.product_id, fl.brand_id, page)
        else:
            await _show_direct_flavors(callback, fl.product_id, page)
    except Exception as e:
        session.rollback()
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    finally:
        session.close()


# ══════════════════════════════════════════
# РЕДАКТИРОВАНИЕ ВКУСА / ЦЕНЫ / TOGGLE / DEL
# ══════════════════════════════════════════

@router.callback_query(F.data.startswith("fedit:"))
async def flavor_edit_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    flavor_id = int(callback.data.split(":")[1])
    session   = Session()
    try:
        fl = session.query(Flavor).filter_by(id=flavor_id).first()
        if not fl:
            await callback.answer("❌ Не найдено", show_alert=True); return
        await state.clear()
        await state.update_data(eid=flavor_id, fp=fl.product_id, fb=fl.brand_id or 0)
        back = _back_for_flavor(fl)
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"✏️ <b>Переименовать вкус</b>\n\nТекущее: <b>{fl.name}</b>\n\nОтправьте новое название:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=back)]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(f"✏️ Текущее: <b>{fl.name}</b>\n\nОтправьте новое:", parse_mode="HTML")
        await state.set_state(AdminStates.waiting_for_flavor_edit)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_flavor_edit)
async def flavor_edit_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    data = await state.get_data()
    await state.clear()

    session = Session()
    try:
        fl = session.query(Flavor).filter_by(id=data["eid"]).first()
        if fl:
            old    = fl.name
            fl.name = message.text.strip()
            session.commit()
            back = _back_for_flavor(fl)
            await message.answer(
                f"✅ <b>{old}</b> → <b>{fl.name}</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 К списку", callback_data=back)]
                ]),
                parse_mode="HTML",
            )
        else:
            await message.answer("❌ Позиция не найдена")
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


@router.callback_query(F.data.startswith("fprice:"))
async def flavor_price_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    flavor_id = int(callback.data.split(":")[1])
    session   = Session()
    try:
        fl = session.query(Flavor).filter_by(id=flavor_id).first()
        if not fl:
            await callback.answer("❌ Не найдено", show_alert=True); return
        await state.clear()
        await state.update_data(eid=flavor_id, fp=fl.product_id, fb=fl.brand_id or 0)
        cur  = f"{fl.price} ₽" if fl.price else "не указана"
        back = _back_for_flavor(fl)
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"💰 <b>Изменить цену</b>\n\n{fl.name}\nТекущая: <b>{cur}</b>\n\nОтправьте новую цену (0 — убрать):",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=back)]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(f"💰 {fl.name}  ·  сейчас {cur}\n\nНовая цена:", parse_mode="HTML")
        await state.set_state(AdminStates.waiting_for_price_edit)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_price_edit)
async def flavor_price_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    try:
        price_val = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Только цифры!"); return

    data = await state.get_data()
    await state.clear()

    session = Session()
    try:
        fl = session.query(Flavor).filter_by(id=data["eid"]).first()
        if fl:
            fl.price = price_val if price_val > 0 else None
            session.commit()
            ps   = f"{fl.price} ₽" if fl.price else "убрана"
            back = _back_for_flavor(fl)
            await message.answer(
                f"✅ Цена: <b>{ps}</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 К списку", callback_data=back)]
                ]),
                parse_mode="HTML",
            )
        else:
            await message.answer("❌ Позиция не найдена")
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


@router.callback_query(F.data.startswith("ftoggle:"))
async def flavor_toggle(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    flavor_id = int(callback.data.split(":")[1])
    session   = Session()
    try:
        fl = session.query(Flavor).filter_by(id=flavor_id).first()
        if not fl:
            await callback.answer("❌ Не найдено", show_alert=True); return
        fl.is_available = not fl.is_available
        session.commit()
        await callback.answer("✅ Доступна" if fl.is_available else "❌ Скрыта")
        page = _flavor_page(session, fl)   # остаёмся на той же странице
        if fl.brand_id:
            await _render_brand_flavors(callback, fl.product_id, fl.brand_id, page)
        else:
            await _show_direct_flavors(callback, fl.product_id, page)
    finally:
        session.close()


@router.callback_query(F.data.startswith("fdel:"))
async def flavor_del(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    flavor_id = int(callback.data.split(":")[1])
    session   = Session()
    try:
        fl = session.query(Flavor).filter_by(id=flavor_id).first()
        if not fl:
            await callback.answer("❌ Не найдено", show_alert=True); return
        pid  = fl.product_id
        bid  = fl.brand_id
        name = fl.name
        page = _flavor_page(session, fl)   # считаем ДО удаления; если страница опустела — рендер сам вернёт на последнюю
        session.delete(fl)
        session.commit()
        await callback.answer(f"🗑 «{name}» удалён")
        if bid:
            await _render_brand_flavors(callback, pid, bid, page)
        else:
            await _show_direct_flavors(callback, pid, page)
    finally:
        session.close()


# ══════════════════════════════════════════
# ЗАКАЗЫ
# ══════════════════════════════════════════

@router.callback_query(F.data == "admin_orders")
async def admin_orders(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    dist       = get_admin_district(callback.from_user.id)
    dist_label = DISTRICT_NAMES.get(dist, dist)

    session = Session()
    try:
        pending = (session.query(Order)
                   .filter_by(status="pending", district=dist)
                   .order_by(Order.created_at.desc()).limit(20).all())
        cnt_p = session.query(Order).filter_by(status="pending",   district=dist).count()
        cnt_c = session.query(Order).filter_by(status="confirmed", district=dist).count()
    finally:
        session.close()

    text     = (f"📋 <b>Заказы · {dist_label}</b>\n\n"
                f"⏳ Новых: <b>{cnt_p}</b>  ·  ✅ Выполнено: <b>{cnt_c}</b>\n\n")
    keyboard = []
    for o in pending:
        keyboard.append([InlineKeyboardButton(
            text=f"#{o.id}  @{o.username or '—'}  {o.created_at.strftime('%d.%m %H:%M')}",
            callback_data=f"oview:{o.id}",
        )])
    if not pending:
        text += "✅ <i>Новых заказов нет</i>"
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])

    await callback.answer()
    try:
        await callback.message.edit_text(text,
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                                         parse_mode="HTML")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("oview:"))
async def order_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    order_id   = int(callback.data.split(":")[1])
    admin_dist = get_admin_district(callback.from_user.id)
    session    = Session()
    try:
        order = session.query(Order).filter_by(id=order_id).first()
        if not order:
            await callback.answer("❌ Не найден", show_alert=True); return
        # Защита от просмотра заказов чужого района
        if order_district(order.district) != admin_dist:
            await callback.answer(
                f"⛔ Этот заказ из другого района ({DISTRICT_NAMES.get(order.district, order.district)})",
                show_alert=True
            )
            return
        items = session.query(OrderItem).filter_by(order_id=order_id).all()
    finally:
        session.close()

    icons = {"pending":"⏳","confirmed":"✅","cancelled":"❌"}
    icon  = icons.get(order.status, "❓")
    total = 0
    rows  = ""
    for i in items:
        bs    = f" [{i.brand_name}]" if i.brand_name else ""
        ps    = f" — {i.price} ₽" if i.price else ""
        rows += f"  • {i.flavor_name}{bs} ×{i.quantity}{ps}\n"
        if i.price: total += i.price * i.quantity
    ts       = f"\n💵 Сумма: <b>{total} ₽</b>" if total else ""
    ds       = f"\n🎁 Скидка: {order.discount_pct:.1f}%" if order.discount_pct and order.discount_pct > 0 else ""
    promo_s  = f"\n🎟 Промокод: <b>{order.promo_code}</b> (−{order.promo_discount} ₽)" if order.promo_code else ""
    dist_str = f"\n📍 Район: <b>{DISTRICT_NAMES.get(order.district, order.district or '—')}</b>"

    text = (
        f"{icon} <b>Заказ #{order.id}</b>\n\n"
        f"👤 {esc(order.first_name) or '—'}  @{order.username or '—'}\n"
        f"🆔 <code>{order.user_id}</code>\n"
        f"📞 {esc(order.contact_info) or '—'}\n"
        f"📅 {order.created_at.strftime('%d.%m.%Y %H:%M')}"
        f"{dist_str}\n\n"
        f"📋 Состав:\n{rows}{ts}{ds}{promo_s}"
    )
    keyboard = []
    if order.status == "pending":
        keyboard.append([
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"oconfirm:{order.id}"),
            InlineKeyboardButton(text="❌ Отменить",    callback_data=f"ocancel:{order.id}"),
        ])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_orders")])

    await callback.answer()
    try:
        await callback.message.edit_text(text,
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                                         parse_mode="HTML")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("oconfirm:"))
async def order_confirm(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    order_id = int(callback.data.split(":")[1])
    session  = Session()
    try:
        o = session.query(Order).filter_by(id=order_id).first()
        if o and order_district(o.district) != get_admin_district(callback.from_user.id):
            await callback.answer("⛔ Этот заказ из другого района", show_alert=True); return
        if o and o.status == "pending":
            o.status       = "confirmed"
            o.confirmed_at = datetime.now()
            o.confirmed_by = callback.from_user.id
            session.commit()
            log_admin_action(
                callback.from_user.id,
                _admin_tag(callback.from_user),
                "order_confirm",
                f"Подтверждён заказ #{order_id} (пользователь {o.user_id})",
            )
            # FIX #7: уведомление пользователю при смене статуса
            try:
                await callback.bot.send_message(
                    o.user_id,
                    f"✅ <b>Заказ #{o.id} подтверждён!</b>\n\nСпасибо за покупку! 🎉\nМенеджер свяжется с вами.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        await callback.answer("✅ Подтверждён")
        callback.data = f"oview:{order_id}"
        await order_view(callback)
    finally:
        session.close()


@router.callback_query(F.data.startswith("ocancel:"))
async def order_cancel(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    order_id = int(callback.data.split(":")[1])
    session  = Session()
    try:
        o = session.query(Order).filter_by(id=order_id).first()
        if o and order_district(o.district) != get_admin_district(callback.from_user.id):
            await callback.answer("⛔ Этот заказ из другого района", show_alert=True); return
        if o and o.status == "pending":
            o.status = "cancelled"
            session.commit()
            log_admin_action(
                callback.from_user.id,
                _admin_tag(callback.from_user),
                "order_cancel",
                f"Отменён заказ #{order_id} (пользователь {o.user_id})",
            )
            # FIX #7: уведомление пользователю при отмене
            try:
                manager = (
                    config.config.SHOP_MANAGER_ZAOZERNOE
                    if getattr(o, "district", None) == "zaozernoe"
                    else config.config.SHOP_MANAGER
                )
                await callback.bot.send_message(
                    o.user_id,
                    f"❌ <b>Заказ #{o.id} отменён.</b>\n\nЕсли это ошибка — свяжитесь с менеджером @{manager}.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        await callback.answer("❌ Отменён")
        callback.data = f"oview:{order_id}"
        await order_view(callback)
    finally:
        session.close()


# ══════════════════════════════════════════
# ПРОМОКОДЫ (только /admin)
# ══════════════════════════════════════════

async def _show_promos(msg, user_id: int = 0):
    session = Session()
    try:
        promos = session.query(PromoCode).order_by(PromoCode.id.desc()).limit(30).all()
    finally:
        session.close()

    text     = "🎟 <b>Промокоды</b>\n\n"
    keyboard = []

    if promos:
        for p in promos:
            status  = "✅" if p.is_active else "❌"
            kind    = "♻️" if p.is_reusable else "1️⃣"
            uses    = f" (использ.: {p.used_count})" if p.used_count else ""
            text   += f"{status} {kind} <code>{p.code}</code> — <b>{p.discount_rub} ₽</b>{uses}\n"
            keyboard.append([
                InlineKeyboardButton(text=f"{status} {p.code}", callback_data=f"promo_toggle:{p.id}"),
                InlineKeyboardButton(text="🗑",                  callback_data=f"promo_del:{p.id}"),
            ])
    else:
        text += "<i>Промокодов пока нет.</i>\n"

    keyboard.append([InlineKeyboardButton(text="➕ Создать промокод", callback_data="promo_create")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад",            callback_data="admin_menu")])

    try:
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
    except Exception:
        await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")


@router.callback_query(F.data == "manage_promos")
async def manage_promos(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    if callback.from_user.id in config.config.ZAOZERNOE_ONLY_ADMINS:
        await callback.answer("⛔ Промокоды доступны только в /admin"); return
    await state.clear()
    await callback.answer()
    await _show_promos(callback.message, callback.from_user.id)


@router.callback_query(F.data == "promo_create")
async def promo_create_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text(
            "🎟 <b>Создание промокода</b>\n\n"
            "Введите <b>код</b> промокода (латинские буквы и цифры):\n"
            "<i>Пример: SALE200  или  VIP50</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="manage_promos")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer("🎟 Введите код промокода:")
    await state.set_state(AdminStates.waiting_for_promo_code)


@router.message(AdminStates.waiting_for_promo_code)
async def promo_got_code(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    code = message.text.strip().upper()
    if not code or len(code) > 50:
        await message.answer("❌ Код слишком длинный или пустой."); return

    session = Session()
    try:
        exists = session.query(PromoCode).filter_by(code=code).first()
        if exists:
            await message.answer(f"❌ Промокод <b>{code}</b> уже существует!", parse_mode="HTML")
            return
    finally:
        session.close()

    await state.update_data(new_promo_code=code)
    await message.answer(
        f"✅ Код: <b>{code}</b>\n\nВведите <b>размер скидки</b> в рублях:\n<i>Например: 200</i>",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_for_promo_discount)


@router.message(AdminStates.waiting_for_promo_discount)
async def promo_got_discount(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    try:
        discount = int(message.text.strip())
        if discount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число рублей."); return

    await state.update_data(new_promo_discount=discount)
    await message.answer(
        f"💰 Скидка: <b>{discount} ₽</b>\n\nТип промокода:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="1️⃣ Одноразовый",  callback_data="promo_type:single")],
            [InlineKeyboardButton(text="♻️ Многоразовый", callback_data="promo_type:multi")],
        ]),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_for_promo_type)


@router.callback_query(F.data.startswith("promo_type:"))
async def promo_got_type(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    ptype     = callback.data.split(":")[1]
    is_reuse  = (ptype == "multi")
    data      = await state.get_data()
    await state.clear()

    code     = data.get("new_promo_code")
    discount = data.get("new_promo_discount", 0)

    if not code or not discount:
        await callback.answer("❌ Ошибка: данные потеряны", show_alert=True); return

    session = Session()
    try:
        promo = PromoCode(
            code=code,
            discount_rub=discount,
            is_reusable=is_reuse,
            is_active=True,
        )
        session.add(promo)
        session.commit()

        kind_str = "♻️ Многоразовый" if is_reuse else "1️⃣ Одноразовый"
        await callback.answer()
        await callback.message.edit_text(
            f"✅ <b>Промокод создан!</b>\n\n"
            f"🎟 Код: <code>{code}</code>\n"
            f"💰 Скидка: <b>{discount} ₽</b>\n"
            f"Тип: {kind_str}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎟 К промокодам", callback_data="manage_promos")],
                [InlineKeyboardButton(text="🏠 Меню",          callback_data="admin_menu")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        session.rollback()
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    finally:
        session.close()


@router.callback_query(F.data.startswith("promo_toggle:"))
async def promo_toggle(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    promo_id = int(callback.data.split(":")[1])
    session  = Session()
    try:
        p = session.query(PromoCode).filter_by(id=promo_id).first()
        if not p:
            await callback.answer("❌ Не найден", show_alert=True); return
        p.is_active = not p.is_active
        session.commit()
        await callback.answer("✅ Активирован" if p.is_active else "❌ Деактивирован")
        await _show_promos(callback.message, callback.from_user.id)
    finally:
        session.close()


@router.callback_query(F.data.startswith("promo_del:"))
async def promo_del(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    promo_id = int(callback.data.split(":")[1])
    session  = Session()
    try:
        p = session.query(PromoCode).filter_by(id=promo_id).first()
        if not p:
            await callback.answer("❌ Не найден", show_alert=True); return
        session.delete(p)
        session.commit()
        await callback.answer("🗑 Удалён")
        await _show_promos(callback.message, callback.from_user.id)
    finally:
        session.close()


# ══════════════════════════════════════════
# СТАТИСТИКА
# ══════════════════════════════════════════

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    dist       = get_admin_district(callback.from_user.id)
    dist_label = DISTRICT_NAMES.get(dist, dist)

    session = Session()
    try:
        uc  = session.query(User).count()
        udc = session.query(User).filter_by(district=dist).count()
        oc  = session.query(Order).filter_by(district=dist).count()
        op  = session.query(Order).filter_by(status="pending",   district=dist).count()
        ocf = session.query(Order).filter_by(status="confirmed", district=dist).count()
        bc  = session.query(Brand).count()
        fc  = session.query(Flavor).filter_by(district=dist).count()
        rc  = session.query(Referral).count()
        rb  = session.query(Referral).filter_by(purchased=True).count()
        pc  = session.query(PromoCode).filter_by(is_active=True).count()
    finally:
        session.close()
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"📊 <b>СТАТИСТИКА · {dist_label}</b>\n\n"
            f"👥 Всего пользователей: <b>{uc}</b>  (в районе: <b>{udc}</b>)\n"
            f"🛒 Заказов в районе: <b>{oc}</b>  (⏳{op} / ✅{ocf})\n"
            f"🏷 Брендов: <b>{bc}</b>  ·  🍬 Вкусов в районе: <b>{fc}</b>\n\n"
            f"🔗 Рефералов: <b>{rc}</b>  (купили: <b>{rb}</b>)\n"
            f"🎟 Активных промокодов: <b>{pc}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
                [InlineKeyboardButton(text="⬅️ Назад",    callback_data="admin_menu")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


# ══════════════════════════════════════════
# РАССЫЛКА
# ══════════════════════════════════════════

def _broadcast_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")]
    ])


def _broadcast_skip_kb(skip_cb: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data=skip_cb)],
        [InlineKeyboardButton(text="❌ Отмена",     callback_data="broadcast_cancel")],
    ])


@router.callback_query(F.data == "broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text(
            "📢 <b>Рассылка — шаг 1/4</b>\n\n"
            "Введите <b>текст</b> сообщения или нажмите «Пропустить»:",
            reply_markup=_broadcast_skip_kb("broadcast_skip_text"),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            "📢 <b>Шаг 1/4</b> — Введите текст или пропустите:",
            reply_markup=_broadcast_skip_kb("broadcast_skip_text"),
            parse_mode="HTML",
        )
    await state.set_state(AdminStates.waiting_for_broadcast_text)


@router.message(AdminStates.waiting_for_broadcast_text)
async def broadcast_got_text(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    await state.update_data(bc_text=message.text or message.caption or "")
    await message.answer(
        "📢 <b>Рассылка — шаг 2/4</b>\n\n"
        "Отправьте <b>фото</b> или <b>видео</b> для рассылки, или нажмите «Пропустить»:",
        reply_markup=_broadcast_skip_kb("broadcast_skip_media"),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_for_broadcast_media)


@router.callback_query(F.data == "broadcast_skip_text")
async def broadcast_skip_text(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.update_data(bc_text="")
    await callback.answer()
    try:
        await callback.message.edit_text(
            "📢 <b>Рассылка — шаг 2/4</b>\n\n"
            "Отправьте <b>фото</b> или <b>видео</b> для рассылки, или нажмите «Пропустить»:",
            reply_markup=_broadcast_skip_kb("broadcast_skip_media"),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            "📢 <b>Шаг 2/4</b> — Отправьте фото/видео или пропустите:",
            reply_markup=_broadcast_skip_kb("broadcast_skip_media"),
            parse_mode="HTML",
        )
    await state.set_state(AdminStates.waiting_for_broadcast_media)


@router.message(AdminStates.waiting_for_broadcast_media)
async def broadcast_got_media(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    if message.photo:
        await state.update_data(bc_photo=message.photo[-1].file_id, bc_video=None)
        media_hint = "📷 Фото принято"
    elif message.video:
        await state.update_data(bc_photo=None, bc_video=message.video.file_id)
        media_hint = "🎥 Видео принято"
    else:
        await message.answer("❌ Нужно фото или видео! Или нажмите «Пропустить».",
                             reply_markup=_broadcast_skip_kb("broadcast_skip_media"))
        return
    await message.answer(
        f"✅ {media_hint}\n\n"
        "📢 <b>Рассылка — шаг 3/4</b>\n\n"
        "Отправьте <b>ссылку для кнопки</b> (например: https://t.me/...) или нажмите «Пропустить»:",
        reply_markup=_broadcast_skip_kb("broadcast_skip_url"),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_for_broadcast_url)


@router.callback_query(F.data == "broadcast_skip_media")
async def broadcast_skip_media(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.update_data(bc_photo=None, bc_video=None)
    await callback.answer()
    try:
        await callback.message.edit_text(
            "📢 <b>Рассылка — шаг 3/4</b>\n\n"
            "Отправьте <b>ссылку для кнопки</b> (например: https://t.me/...) или нажмите «Пропустить»:",
            reply_markup=_broadcast_skip_kb("broadcast_skip_url"),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            "📢 <b>Шаг 3/4</b> — Ссылка на кнопку или пропустите:",
            reply_markup=_broadcast_skip_kb("broadcast_skip_url"),
            parse_mode="HTML",
        )
    await state.set_state(AdminStates.waiting_for_broadcast_url)


@router.message(AdminStates.waiting_for_broadcast_url)
async def broadcast_got_url(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    url = (message.text or "").strip()
    if not url.startswith("http"):
        await message.answer("❌ Ссылка должна начинаться с http/https. Попробуйте ещё раз или нажмите «Пропустить».",
                             reply_markup=_broadcast_skip_kb("broadcast_skip_url"))
        return
    await state.update_data(bc_url=url)
    await message.answer(
        "📢 <b>Рассылка — шаг 4/4</b>\n\n"
        "Введите <b>текст кнопки</b> (например: 🛒 Перейти в магазин) "
        "или нажмите «Пропустить» — тогда будет надпись «Перейти»:",
        reply_markup=_broadcast_skip_kb("broadcast_skip_btn"),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.waiting_for_broadcast_btn)


@router.callback_query(F.data == "broadcast_skip_url")
async def broadcast_skip_url(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.update_data(bc_url=None, bc_btn=None)
    await callback.answer()
    # Если URL пропустили — пропускаем и кнопку, показываем превью
    await _broadcast_show_preview(callback.message, state)


@router.message(AdminStates.waiting_for_broadcast_btn)
async def broadcast_got_btn(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    await state.update_data(bc_btn=message.text.strip() if message.text else "Перейти")
    await _broadcast_show_preview(message, state)


@router.callback_query(F.data == "broadcast_skip_btn")
async def broadcast_skip_btn(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.update_data(bc_btn="Перейти")
    await callback.answer()
    await _broadcast_show_preview(callback.message, state)


async def _broadcast_show_preview(target, state: FSMContext):
    """Показывает превью рассылки и кнопку подтверждения."""
    data    = await state.get_data()
    text    = data.get("bc_text") or ""
    photo   = data.get("bc_photo")
    video   = data.get("bc_video")
    url     = data.get("bc_url")
    btn_lbl = data.get("bc_btn") or "Перейти"

    parts = []
    if text:   parts.append(f"📝 Текст: есть")
    if photo:  parts.append("📷 Фото: есть")
    if video:  parts.append("🎥 Видео: есть")
    if url:    parts.append(f"🔗 Кнопка: «{btn_lbl}» → {url}")

    if not text and not photo and not video:
        info = "⚠️ Ни текст, ни медиа не указаны — рассылка пустая!"
    else:
        info = "\n".join(parts)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить рассылку", callback_data="broadcast_confirm")],
        [InlineKeyboardButton(text="❌ Отмена",             callback_data="broadcast_cancel")],
    ])
    preview_text = f"📢 <b>Превью рассылки</b>\n\n{info}\n\nПодтвердить отправку?"
    try:
        if hasattr(target, "edit_text"):
            await target.edit_text(preview_text, reply_markup=kb, parse_mode="HTML")
        else:
            await target.answer(preview_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        if hasattr(target, "answer"):
            await target.answer(preview_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    data    = await state.get_data()
    await state.clear()
    await callback.answer()

    text    = data.get("bc_text") or ""
    photo   = data.get("bc_photo")
    video   = data.get("bc_video")
    url     = data.get("bc_url")
    btn_lbl = data.get("bc_btn") or "Перейти"

    # Inline-кнопка для рассылки
    reply_markup = None
    if url:
        reply_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=btn_lbl, url=url)]
        ])

    try:
        await callback.message.edit_text("⏳ Рассылка запущена...", parse_mode="HTML")
    except Exception:
        await callback.message.answer("⏳ Рассылка запущена...")

    session = Session()
    try:
        # Исключаем только тех, кого заблокировал админ вручную.
        # Тех, кто ранее заблокировал бота (left_bot), тоже пропускаем ради экономии
        # запросов к Telegram — но это не бан, при следующем обращении к боту
        # left_bot сбрасывается и они снова получают рассылки.
        uids = [
            u.user_id for u in
            session.query(User).filter_by(is_blocked=False, left_bot=False).all()
        ]
    finally:
        session.close()

    ok = fail = blocked_by_user = 0
    for uid in uids:
        try:
            if photo:
                try:
                    await callback.bot.send_photo(
                        uid, photo,
                        caption=text or None,
                        parse_mode="HTML" if text else None,
                        reply_markup=reply_markup,
                    )
                except TelegramForbiddenError:
                    raise
                except Exception:
                    # Текст рассылки мог содержать "<", ">" или "&", которые
                    # Telegram трактует как незакрытые HTML-теги и отклоняет
                    # ВСЁ сообщение ("can't parse entities") — без фолбэка
                    # это ломало бы рассылку сразу для всех получателей.
                    await callback.bot.send_photo(
                        uid, photo, caption=text or None, reply_markup=reply_markup,
                    )
            elif video:
                try:
                    await callback.bot.send_video(
                        uid, video,
                        caption=text or None,
                        parse_mode="HTML" if text else None,
                        reply_markup=reply_markup,
                    )
                except TelegramForbiddenError:
                    raise
                except Exception:
                    await callback.bot.send_video(
                        uid, video, caption=text or None, reply_markup=reply_markup,
                    )
            elif text:
                try:
                    await callback.bot.send_message(uid, text, parse_mode="HTML", reply_markup=reply_markup)
                except TelegramForbiddenError:
                    raise
                except Exception:
                    await callback.bot.send_message(uid, text, reply_markup=reply_markup)
            ok += 1
        except TelegramForbiddenError:
            # Настоящий Forbidden от Telegram: пользователь сам заблокировал бота
            # или удалил аккаунт. Это НЕ бан — только статистическая пометка,
            # доступ в бот не ограничивается. Бан выставляется только админом вручную.
            blocked_by_user += 1
            s2 = Session()
            try:
                u = s2.query(User).filter_by(user_id=uid).first()
                if u and not u.left_bot:
                    u.left_bot = True
                    s2.commit()
            except Exception:
                s2.rollback()
            finally:
                s2.close()
        except Exception as e:
            # Любая другая ошибка (сеть, таймаут, временный сбой Telegram) —
            # НЕ блокируем пользователя, просто считаем как неудачную отправку.
            fail += 1
            print(f"⚠️ Рассылка: не удалось отправить {uid}: {e}")
        await asyncio.sleep(0.05)

    await callback.message.answer(
        f"✅ Рассылка завершена!\n\nОтправлено: {ok}\n🚫 Заблокировали бота: {blocked_by_user}\n❌ Прочих ошибок: {fail}",
        reply_markup=admin_menu_kb(callback.from_user.id),
    )


@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer("Отменено")
    dist_label = admin_district_label(callback.from_user.id)
    try:
        await callback.message.edit_text(
            f"👑 <b>Админ-панель SUNTIME SHOP</b>\n📍 Район: <b>{dist_label}</b>\n\nВыберите действие:",
            reply_markup=admin_menu_kb(callback.from_user.id), parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            f"👑 <b>Админ-панель</b>\n\nВыберите действие:",
            reply_markup=admin_menu_kb(callback.from_user.id), parse_mode="HTML",
        )


# ══════════════════════════════════════════
# НАПИСАТЬ ПОЛЬЗОВАТЕЛЮ
# ══════════════════════════════════════════

@router.callback_query(F.data == "send_to_user")
async def send_to_user_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text(
            "✉️ Введите @username или Telegram ID:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_menu")]
            ]),
        )
    except Exception:
        await callback.message.answer("✉️ Введите @username или Telegram ID:")
    await state.set_state(AdminStates.waiting_for_username)


@router.message(AdminStates.waiting_for_username)
async def send_to_user_got_name(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    term    = message.text.strip().lstrip("@")
    session = Session()
    try:
        user = (session.query(User).filter_by(user_id=int(term)).first()
                if term.isdigit()
                else session.query(User).filter(User.username.ilike(f"%{term}%")).first())
        if not user:
            await message.answer("❌ Пользователь не найден"); await state.clear(); return
        await state.update_data(tuid=user.user_id)
        await message.answer(
            f"✅ @{user.username or user.user_id}\n\nОтправьте сообщение:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_menu")]
            ]),
        )
        await state.set_state(AdminStates.waiting_for_user_message)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_user_message)
async def send_to_user_msg(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    data = await state.get_data()
    # Поддерживаем оба источника: старый (tuid) и новый из карточки пользователя (msg_target_id)
    tuid = data.get("tuid") or data.get("msg_target_id") or data.get("target_user_id")
    await state.clear()
    if not tuid:
        await message.answer("❌ Цель не задана"); return
    try:
        tuid = int(tuid)
        if message.text:
            try:
                await message.bot.send_message(tuid,
                    f"📨 <b>Сообщение от администратора:</b>\n\n{message.text}", parse_mode="HTML")
            except Exception:
                # message.text может содержать "<", ">" или "&", которые Telegram
                # пытается распарсить как HTML-теги и отклоняет сообщение целиком
                # ("can't parse entities"). Если админ не использовал разметку
                # специально, шлём тем же текстом, но без HTML-парсинга —
                # чтобы сообщение пользователю точно дошло.
                await message.bot.send_message(tuid,
                    f"📨 Сообщение от администратора:\n\n{message.text}")
        else:
            await message.copy_to(tuid)
        log_admin_action(
            message.from_user.id,
            _admin_tag(message.from_user),
            "msg_user",
            f"Сообщение пользователю {tuid}: {(message.text or '')[:80]}",
        )
        await message.answer("✅ Отправлено!", reply_markup=admin_menu_kb(message.from_user.id))
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ══════════════════════════════════════════
# ВЫГРУЗКА БД
# ══════════════════════════════════════════

@router.callback_query(F.data == "get_db")
async def get_db(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    session = Session()
    try:
        users = session.query(User).all()
        if not users:
            await callback.answer("📭 Пусто"); return
        out = io.StringIO()
        w   = csv.writer(out)
        w.writerow(["ID","User ID","Username","Ник","Имя","Дата","Админ","Район","Скидка %"])
        for u in users:
            w.writerow([u.id, u.user_id, u.username or "", u.nickname or "",
                        u.first_name or "", u.joined_at,
                        "Да" if u.is_admin else "Нет",
                        u.district or "",
                        f"{u.referral_discount_pct or 0:.1f}"])
        out.seek(0)
        f      = io.BytesIO(out.getvalue().encode("utf-8-sig"))
        f.name = f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        await callback.bot.send_document(callback.from_user.id, f, caption=f"👥 Всего: {len(users)}")
        await callback.answer("✅")
    finally:
        session.close()


# ══════════════════════════════════════════
# СТАТУС БОТА
# ══════════════════════════════════════════

@router.callback_query(F.data == "restart_bot")
async def bot_status(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    session = Session()
    try:
        uc = session.query(User).count()
        oc = session.query(Order).count()
        op = session.query(Order).filter_by(status="pending").count()
    finally:
        session.close()
    bot_info = await callback.bot.get_me()
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"🤖 @{bot_info.username}\n✅ Активен\n\n"
            f"👥 {uc} пользователей  ·  🛒 {oc} заказов  (⏳{op} новых)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="♻️ Перезапустить", callback_data="restart_exec")],
                [InlineKeyboardButton(text="⬅️ Назад",          callback_data="admin_menu")],
            ]),
        )
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "restart_exec")
async def restart_exec(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await callback.answer("🔄 Перезапуск...")
    try:
        await callback.message.edit_text("🔄 Перезапуск...")
        with open("restart.trigger", "w") as f:
            f.write(str(datetime.now()))
        import sys; sys.exit(0)
    except Exception as e:
        await callback.message.edit_text(f"❌ {e}")


# ══════════════════════════════════════════
# АДМИНИСТРАТОРЫ
# ══════════════════════════════════════════

@router.callback_query(F.data == "manage_admins")
async def manage_admins(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await callback.answer()
    try:
        await callback.message.edit_text(
            "👑 <b>Администраторы</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить", callback_data="add_admin")],
                [InlineKeyboardButton(text="📋 Список",   callback_data="list_admins")],
                [InlineKeyboardButton(text="⬅️ Назад",    callback_data="admin_menu")],
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "list_admins")
async def list_admins(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    session = Session()
    try:
        db_admins = session.query(User).filter_by(is_admin=True).all()
    finally:
        session.close()
    text  = "👑 <b>Из config.py:</b>\n" + "\n".join(f"• {i}" for i in config.config.ADMINS)
    extra = "\n".join(f"• @{a.username or a.user_id}" for a in db_admins
                      if a.user_id not in config.config.ADMINS)
    text += f"\n\n<b>Добавленные:</b>\n{extra or '• Нет'}"
    await callback.answer()
    try:
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="manage_admins")]
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "add_admin")
async def add_admin_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text(
            "➕ Отправьте Telegram ID нового администратора:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="manage_admins")]
            ]),
        )
    except Exception:
        await callback.message.answer("➕ Отправьте Telegram ID:")
    await state.set_state(AdminStates.waiting_for_admin_id)


@router.message(AdminStates.waiting_for_admin_id)
async def add_admin_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    try:
        admin_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Только цифры!"); return
    await state.clear()
    session = Session()
    try:
        u = session.query(User).filter_by(user_id=admin_id).first()
        if not u:
            u = User(user_id=admin_id, is_admin=True); session.add(u)
        else:
            u.is_admin = True
        session.commit()
        await message.answer("✅ Администратор добавлен!", reply_markup=admin_menu_kb(message.from_user.id))
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


# ══════════════════════════════════════════
# ТЕКСТЫ (включая Расписание по районам)
# ══════════════════════════════════════════

@router.callback_query(F.data == "edit_texts")
async def edit_texts(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text("📝 <b>Тексты категорий</b>",
                                         reply_markup=edit_texts_kb(), parse_mode="HTML")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("edit_item_"))
async def edit_item_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    item_id = int(callback.data[len("edit_item_"):])
    session = Session()
    try:
        p = session.query(Product).filter_by(id=item_id).first()
        if not p:
            await callback.answer("❌ Не найдено"); return
        await state.clear()
        await state.update_data(item_id=item_id)
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"✏️ <b>{p.item_name}</b>\n\nТекущий текст:\n<code>{p.item_text or '—'}</code>\n\nОтправьте новый текст:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data="edit_texts")]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(f"✏️ {p.item_name}\n\nОтправьте новый текст:")
        await state.set_state(AdminStates.waiting_for_new_text)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_new_text)
async def edit_item_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    item_id = (await state.get_data()).get("item_id")
    await state.clear()
    session = Session()
    try:
        p = session.query(Product).filter_by(id=item_id).first()
        if p:
            p.item_text  = message.text
            p.updated_at = datetime.now()
            session.commit()
            await message.answer("✅ Текст сохранён!", reply_markup=edit_texts_kb())
        else:
            await message.answer("❌ Не найдено")
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


# ══════════════════════════════════════════
# ВЫХОД
# ══════════════════════════════════════════

@router.callback_query(F.data == "exit_admin")
async def exit_admin(callback: CallbackQuery, state: FSMContext):
    from handlers import main_menu_kb
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text("🏪 <b>Главное меню</b>", reply_markup=main_menu_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer("🏪 <b>Главное меню</b>", reply_markup=main_menu_kb(), parse_mode="HTML")


# ── ИСПРАВЛЕНИЕ 1: раздельное редактирование расписания по районам ────────────

@router.callback_query(F.data.in_({"edit_schedule_ryabkovo", "edit_schedule_zaozernoe"}))
async def edit_schedule_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return

    dist      = "ryabkovo" if callback.data == "edit_schedule_ryabkovo" else "zaozernoe"
    dist_name = "Рябково" if dist == "ryabkovo" else "Заозёрный"

    session = Session()
    try:
        p = session.query(Product).filter_by(id=9).first()
        if not p:
            await callback.answer("❌ Не найдено"); return
        current = (p.item_text_ryabkovo if dist == "ryabkovo" else p.item_text_zaozernoe) or "—"
        await state.clear()
        await state.update_data(schedule_district=dist)
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"✏️ <b>Расписание · {dist_name}</b>\n\n"
                f"Текущий текст:\n<code>{current}</code>\n\n"
                f"Отправьте новый текст расписания:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data="edit_texts")]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(f"✏️ Расписание {dist_name}\n\nОтправьте новый текст:")
        await state.set_state(AdminStates.waiting_for_schedule_text)
    finally:
        session.close()


@router.message(AdminStates.waiting_for_schedule_text)
async def edit_schedule_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return

    dist = (await state.get_data()).get("schedule_district")
    await state.clear()

    session = Session()
    try:
        p = session.query(Product).filter_by(id=9).first()
        if p:
            if dist == "ryabkovo":
                p.item_text_ryabkovo = message.text
            else:
                p.item_text_zaozernoe = message.text
            p.updated_at = datetime.now()
            session.commit()
            dist_name = "Рябково" if dist == "ryabkovo" else "Заозёрный"
            await message.answer(
                f"✅ Расписание <b>{dist_name}</b> сохранено!",
                reply_markup=edit_texts_kb(),
                parse_mode="HTML",
            )
        else:
            await message.answer("❌ Не найдено")
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        session.close()


# ══════════════════════════════════════════
# РЫНОК — МОНИТОРИНГ В АДМИНКЕ
# ══════════════════════════════════════════


MARKET_ADMIN_PER_PAGE = 5


def _market_status_label(status: str) -> str:
    return {"pending": "⏳ На проверке", "active": "✅ Активно", "rejected": "❌ Отклонено"}.get(status, status)


async def _show_admin_market(msg, page: int = 0, filter_status: str = "all"):
    """Показывает список объявлений в админке с фильтром по статусу."""
    session = Session()
    try:
        q = session.query(MarketListing).order_by(MarketListing.created_at.desc())
        if filter_status != "all":
            q = q.filter_by(status=filter_status)
        listings = q.all()
    finally:
        session.close()

    total_pages = max(1, (len(listings) - 1) // MARKET_ADMIN_PER_PAGE + 1) if listings else 1
    page = max(0, min(page, total_pages - 1))
    page_items = listings[page * MARKET_ADMIN_PER_PAGE:(page + 1) * MARKET_ADMIN_PER_PAGE]

    filter_labels = {"all": "Все", "active": "✅ Активные", "pending": "⏳ На проверке", "rejected": "❌ Отклонённые"}
    text = (
        f"🛍 <b>РЫНОК · Объявления</b>\n"
        f"Фильтр: <b>{filter_labels.get(filter_status, filter_status)}</b>  ·  всего: {len(listings)}\n"
        f"Стр. {page + 1}/{total_pages}\n\n"
    )
    keyboard = []

    # Фильтры
    keyboard.append([
        InlineKeyboardButton(text="Все",      callback_data="admkt_filter:all:0"),
        InlineKeyboardButton(text="✅ Актив.", callback_data="admkt_filter:active:0"),
        InlineKeyboardButton(text="⏳ Провер.",callback_data="admkt_filter:pending:0"),
        InlineKeyboardButton(text="❌ Откл.",  callback_data="admkt_filter:rejected:0"),
    ])

    if page_items:
        for lst in page_items:
            status_label = _market_status_label(lst.status)
            user = f"@{lst.username}" if lst.username else f"id:{lst.user_id}"
            text += f"<b>#{lst.id}</b> {esc(lst.title[:30])}\n   {status_label}  ·  {user}  ·  {lst.created_at.strftime('%d.%m.%Y')}\n\n"
            keyboard.append([InlineKeyboardButton(
                text=f"#{lst.id} {lst.title[:25]}",
                callback_data=f"admkt_view:{lst.id}:{page}:{filter_status}",
            )])
    else:
        text += "<i>Объявлений нет.</i>"

    # Навигация
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_market:{page - 1}:{filter_status}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_market:{page + 1}:{filter_status}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])

    try:
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")
    except Exception:
        await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_market:"))
async def admin_market_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts  = callback.data.split(":")
    page   = int(parts[1]) if len(parts) > 1 else 0
    filt   = parts[2] if len(parts) > 2 else "all"
    await callback.answer()
    await _show_admin_market(callback.message, page, filt)


@router.callback_query(F.data.startswith("admkt_filter:"))
async def admin_market_filter(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts = callback.data.split(":")
    filt  = parts[1] if len(parts) > 1 else "all"
    await callback.answer()
    await _show_admin_market(callback.message, 0, filt)


@router.callback_query(F.data.startswith("admkt_view:"))
async def admin_market_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts      = callback.data.split(":")
    listing_id = int(parts[1])
    back_page  = int(parts[2]) if len(parts) > 2 else 0
    back_filt  = parts[3] if len(parts) > 3 else "all"

    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if not lst:
            await callback.answer("❌ Не найдено", show_alert=True); return

        user_str = f"@{lst.username}" if lst.username else f"ID: {lst.user_id}"
        text = (
            f"🛍 <b>Объявление #{lst.id}</b>\n\n"
            f"📦 <b>{esc(lst.title)}</b>\n"
            f"📝 {esc(lst.description)}\n\n"
            f"💰 <b>Цена:</b> {esc(lst.price)}\n"
            f"📍 <b>Район:</b> {esc(lst.district)}\n"
            f"📞 <b>Контакт:</b> {esc(lst.contact)}\n\n"
            f"👤 Продавец: {user_str}\n"
            f"📅 Создано: {lst.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"Статус: {_market_status_label(lst.status)}"
        )

        kb_rows = []
        if lst.photo_file_id:
            kb_rows.append([InlineKeyboardButton(text="🖼 Посмотреть фото товара",
                                                  callback_data=f"admkt_photo:{lst.id}")])
        if lst.payment_photo:
            kb_rows.append([InlineKeyboardButton(text="💳 Посмотреть скрин оплаты",
                                                  callback_data=f"admkt_payph:{lst.id}")])

        if lst.status == "active":
            kb_rows.append([InlineKeyboardButton(
                text="🗑 Удалить объявление",
                callback_data=f"admkt_delete:{lst.id}:{back_page}:{back_filt}",
            )])
        elif lst.status == "pending":
            kb_rows.append([
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admkt_confirm:{lst.id}:{back_page}:{back_filt}"),
                InlineKeyboardButton(text="❌ Отклонить",   callback_data=f"admkt_reject:{lst.id}:{back_page}:{back_filt}"),
            ])
        elif lst.status == "rejected":
            kb_rows.append([InlineKeyboardButton(
                text="🗑 Удалить совсем",
                callback_data=f"admkt_delete:{lst.id}:{back_page}:{back_filt}",
            )])

        kb_rows.append([InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"admin_market:{back_page}:{back_filt}",
        )])

        await callback.answer()
        try:
            await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                                             parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                                          parse_mode="HTML")
    finally:
        session.close()


@router.callback_query(F.data.startswith("admkt_photo:"))
async def admin_market_photo(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    listing_id = int(callback.data.split(":")[1])
    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if lst and lst.photo_file_id:
            await callback.answer()
            await callback.message.answer_photo(lst.photo_file_id, caption=f"🖼 Фото товара — #{listing_id}")
        else:
            await callback.answer("❌ Фото нет", show_alert=True)
    finally:
        session.close()


@router.callback_query(F.data.startswith("admkt_payph:"))
async def admin_market_payph(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    listing_id = int(callback.data.split(":")[1])
    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if lst and lst.payment_photo:
            await callback.answer()
            await callback.message.answer_photo(lst.payment_photo, caption=f"💳 Скрин оплаты — #{listing_id}")
        else:
            await callback.answer("❌ Скрин не найден", show_alert=True)
    finally:
        session.close()


@router.callback_query(F.data.startswith("admkt_confirm:"))
async def admin_market_confirm(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts      = callback.data.split(":")
    listing_id = int(parts[1])
    back_page  = int(parts[2]) if len(parts) > 2 else 0
    back_filt  = parts[3] if len(parts) > 3 else "all"

    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if not lst:
            await callback.answer("❌ Не найдено", show_alert=True); return
        lst.status       = "active"
        lst.confirmed_at = datetime.now()
        lst.confirmed_by = callback.from_user.id
        session.commit()
        user_id = lst.user_id
    finally:
        session.close()

    await callback.answer("✅ Опубликовано!")
    try:
        await callback.bot.send_message(
            user_id,
            f"🎉 <b>Ваше объявление #{listing_id} опубликовано!</b>\n\nЕго видят все покупатели в разделе «🛍 Рынок».",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _show_admin_market(callback.message, back_page, back_filt)


@router.callback_query(F.data.startswith("admkt_reject:"))
async def admin_market_reject_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts      = callback.data.split(":")
    listing_id = int(parts[1])
    back_page  = int(parts[2]) if len(parts) > 2 else 0
    back_filt  = parts[3] if len(parts) > 3 else "all"

    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if not lst:
            await callback.answer("❌ Не найдено", show_alert=True); return
        lst.status = "rejected"
        session.commit()
        user_id = lst.user_id
    finally:
        session.close()

    await callback.answer("❌ Отклонено.")
    try:
        await callback.bot.send_message(
            user_id,
            f"😔 <b>Объявление #{listing_id} отклонено.</b>\n\nСвяжитесь с администратором.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _show_admin_market(callback.message, back_page, back_filt)


@router.callback_query(F.data.startswith("admkt_delete:"))
async def admin_market_delete(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts      = callback.data.split(":")
    listing_id = int(parts[1])
    back_page  = int(parts[2]) if len(parts) > 2 else 0
    back_filt  = parts[3] if len(parts) > 3 else "all"

    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if not lst:
            await callback.answer("❌ Не найдено", show_alert=True); return
        user_id = lst.user_id
        title   = lst.title
        session.delete(lst)
        session.commit()
    finally:
        session.close()

    await callback.answer(f"🗑 Объявление #{listing_id} удалено.")
    try:
        await callback.bot.send_message(
            user_id,
            f"⚠️ <b>Ваше объявление «{title}» было удалено администратором.</b>\n\nЕсли это ошибка, свяжитесь с поддержкой.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await _show_admin_market(callback.message, back_page, back_filt)


# ══════════════════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ ДЕЙСТВИЙ АДМИНИСТРАТОРОВ
# ══════════════════════════════════════════════════════════════════════════════

ADMIN_LOG_PER_PAGE = 15


def _admin_tag(user: object) -> str:
    """Возвращает @username или ID для подписи в логах."""
    if hasattr(user, "username") and user.username:
        return f"@{user.username}"
    return str(user.id if hasattr(user, "id") else user)


@router.callback_query(F.data.startswith("admin_log:"))
async def admin_log_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    page = int(callback.data.split(":")[1])
    await callback.answer()

    session = Session()
    try:
        total  = session.query(AdminLog).count()
        offset = page * ADMIN_LOG_PER_PAGE
        logs   = (session.query(AdminLog)
                  .order_by(AdminLog.created_at.desc())
                  .offset(offset).limit(ADMIN_LOG_PER_PAGE).all())
    finally:
        session.close()

    if not logs:
        text = "📜 <b>Лог действий</b>\n\nЗаписей пока нет."
    else:
        lines = []
        for log in logs:
            ts = log.created_at.strftime("%d.%m %H:%M") if log.created_at else "?"
            lines.append(f"<code>{ts}</code> {log.admin_name or log.admin_id} — {log.details or log.action}")
        text = f"📜 <b>Лог действий</b>  ({total} записей)\n\n" + "\n".join(lines)

    total_pages = max(1, (total - 1) // ADMIN_LOG_PER_PAGE + 1)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_log:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_log:{page+1}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
# УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ — ПОИСК, ПРОФИЛЬ, БЛОКИРОВКА
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "users_search")
async def users_search_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text(
            "🔍 <b>Поиск пользователя</b>\n\n"
            "Введите <b>Telegram ID</b>, <b>@username</b> или <b>ник</b>:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_menu")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            "🔍 Введите Telegram ID, @username или ник:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_menu")]
            ]),
        )
    await state.set_state(AdminStates.waiting_for_user_search)


@router.message(AdminStates.waiting_for_user_search)
async def users_search_process(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    query = message.text.strip().lstrip("@")
    await state.clear()

    session = Session()
    try:
        # Поиск по ID
        if query.isdigit():
            users = session.query(User).filter_by(user_id=int(query)).all()
        else:
            # Поиск по username или nickname (нечувствительно к регистру)
            users = (session.query(User)
                     .filter(
                         (User.username.ilike(f"%{query}%")) |
                         (User.nickname.ilike(f"%{query}%")) |
                         (User.first_name.ilike(f"%{query}%"))
                     ).limit(10).all())

        if not users:
            await message.answer(
                f"❌ Пользователь <b>{esc(query)}</b> не найден.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 Ещё поиск", callback_data="users_search")],
                    [InlineKeyboardButton(text="⬅️ Назад",     callback_data="admin_menu")],
                ]),
                parse_mode="HTML",
            )
            return

        if len(users) == 1:
            await _show_user_card(message, users[0].user_id, session)
        else:
            rows = []
            for u in users:
                label = f"{u.first_name or ''} @{u.username or '—'}  [{u.user_id}]"
                rows.append([InlineKeyboardButton(
                    text=label[:48], callback_data=f"user_card:{u.user_id}")])
            rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])
            await message.answer(
                f"🔍 Найдено <b>{len(users)}</b> пользователей:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                parse_mode="HTML",
            )
    finally:
        session.close()


async def _show_user_card(target, user_id: int, session=None):
    """Показывает карточку пользователя с его статистикой."""
    close_session = session is None
    if session is None:
        session = Session()
    try:
        u = session.query(User).filter_by(user_id=user_id).first()
        if not u:
            if hasattr(target, "answer"):
                await target.answer("❌ Пользователь не найден")
            return

        orders_all  = session.query(Order).filter_by(user_id=user_id).count()
        orders_done = session.query(Order).filter_by(user_id=user_id, status="confirmed").count()
        orders_pend = session.query(Order).filter_by(user_id=user_id, status="pending").count()

        # Общая сумма подтверждённых заказов
        total_sum = 0
        done_orders = session.query(Order).filter_by(user_id=user_id, status="confirmed").all()
        for o in done_orders:
            items = session.query(OrderItem).filter_by(order_id=o.id).all()
            total_sum += sum((i.price or 0) * (i.quantity or 1) for i in items)

        blocked_str = f"\n⛔ <b>ЗАБЛОКИРОВАН</b>: {esc(u.blocked_reason) or 'без причины'}" if u.is_blocked else ""
        left_bot_str = (
            "\n🚫 Ранее заблокировал бота (не бан, доступ не ограничен)"
            if getattr(u, "left_bot", False) and not u.is_blocked else ""
        )
        joined = u.joined_at.strftime("%d.%m.%Y") if u.joined_at else "—"
        district = DISTRICT_NAMES.get(u.district, "—") if u.district else "—"
        disc     = f"{u.referral_discount_pct:.1f}%" if u.referral_discount_pct else "0%"

        text = (
            f"👤 <b>Профиль пользователя</b>{blocked_str}{left_bot_str}\n\n"
            f"🆔 ID: <code>{u.user_id}</code>\n"
            f"👤 @{u.username or '—'}  ({esc(u.first_name) or '—'})\n"
            f"🏷 Ник: {esc(u.nickname) or '—'}\n"
            f"📍 Район: {district}\n"
            f"📅 В боте с: {joined}\n\n"
            f"📦 Заказов всего: {orders_all}  (✅ {orders_done} / ⏳ {orders_pend})\n"
            f"💰 Сумма покупок: {total_sum} ₽\n"
            f"🎁 Реф. скидка: {disc}\n"
        )

        block_btn = (
            InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"user_unblock:{user_id}")
            if u.is_blocked else
            InlineKeyboardButton(text="⛔ Заблокировать",  callback_data=f"user_block:{user_id}")
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [block_btn],
            [InlineKeyboardButton(text="📦 История заказов", callback_data=f"user_orders:{user_id}:0")],
            [InlineKeyboardButton(text="✉️ Написать",         callback_data=f"msg_user_id:{user_id}")],
            [InlineKeyboardButton(text="🔍 Ещё поиск",       callback_data="users_search")],
            [InlineKeyboardButton(text="⬅️ Назад",           callback_data="admin_menu")],
        ])

        if hasattr(target, "edit_text"):
            try:
                await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await target.answer(text, reply_markup=kb, parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=kb, parse_mode="HTML")
    finally:
        if close_session:
            session.close()


@router.callback_query(F.data.startswith("user_card:"))
async def user_card_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    user_id = int(callback.data.split(":")[1])
    await callback.answer()
    await _show_user_card(callback.message, user_id)


@router.callback_query(F.data.startswith("user_block:"))
async def user_block_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    user_id = int(callback.data.split(":")[1])
    await state.update_data(block_target=user_id)
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"⛔ Укажите причину блокировки пользователя <code>{user_id}</code>\n"
            f"(или отправьте <b>-</b> чтобы без причины):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"user_card:{user_id}")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(f"⛔ Причина блокировки для {user_id}:")
    await state.set_state(AdminStates.waiting_for_block_reason)


@router.message(AdminStates.waiting_for_block_reason)
async def user_block_save(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    data    = await state.get_data()
    uid     = data.get("block_target")
    reason  = message.text.strip()
    if reason == "-":
        reason = None
    await state.clear()

    session = Session()
    try:
        u = session.query(User).filter_by(user_id=uid).first()
        if u:
            u.is_blocked     = True
            u.blocked_reason = reason
            session.commit()
            from block_check import invalidate_block_cache
            invalidate_block_cache(uid)
            log_admin_action(
                message.from_user.id,
                _admin_tag(message.from_user),
                "user_block",
                f"Заблокирован {uid}" + (f" — причина: {reason}" if reason else ""),
            )
            await message.answer(
                f"⛔ Пользователь <code>{uid}</code> заблокирован.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Карточка", callback_data=f"user_card:{uid}")],
                    [InlineKeyboardButton(text="⬅️ Меню",    callback_data="admin_menu")],
                ]),
                parse_mode="HTML",
            )
        else:
            await message.answer("❌ Пользователь не найден")
    finally:
        session.close()


@router.callback_query(F.data.startswith("user_unblock:"))
async def user_unblock_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    user_id = int(callback.data.split(":")[1])

    session = Session()
    try:
        u = session.query(User).filter_by(user_id=user_id).first()
        if u:
            u.is_blocked     = False
            u.blocked_reason = None
            session.commit()
            from block_check import invalidate_block_cache
            invalidate_block_cache(user_id)
            log_admin_action(
                callback.from_user.id,
                _admin_tag(callback.from_user),
                "user_unblock",
                f"Разблокирован {user_id}",
            )
    finally:
        session.close()

    await callback.answer("✅ Пользователь разблокирован")
    await _show_user_card(callback.message, user_id)


# ══════════════════════════════════════════════════════════════════════════════
# СПИСОК ЗАБЛОКИРОВАННЫХ ПОЛЬЗОВАТЕЛЕЙ
# ══════════════════════════════════════════════════════════════════════════════

BLOCKED_USERS_PER_PAGE = 10


@router.callback_query(F.data.startswith("blocked_users:"))
async def blocked_users_menu_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    page = int(callback.data.split(":")[1])
    await callback.answer()

    session = Session()
    try:
        total = session.query(User).filter_by(is_blocked=True).count()
        offset = page * BLOCKED_USERS_PER_PAGE
        blocked = (
            session.query(User)
            .filter_by(is_blocked=True)
            .order_by(User.user_id.desc())
            .offset(offset).limit(BLOCKED_USERS_PER_PAGE).all()
        )
    finally:
        session.close()

    if total == 0:
        text = "⛔ <b>Заблокированные пользователи</b>\n\nСписок пуст."
        rows = []
    else:
        text = f"⛔ <b>Заблокированные пользователи</b>  ({total})\n\nНажмите на пользователя, чтобы открыть карточку:"
        rows = []
        for u in blocked:
            label = f"{u.first_name or ''} @{u.username or '—'} [{u.user_id}] — {u.blocked_reason or 'без причины'}"
            rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"user_card:{u.user_id}")])

    total_pages = max(1, (total - 1) // BLOCKED_USERS_PER_PAGE + 1)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"blocked_users:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"blocked_users:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="📤 Экспорт в Excel", callback_data="export_blocked_excel")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "export_blocked_excel")
async def export_blocked_excel(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await callback.answer("⏳ Генерирую Excel…")

    try:
        import pandas as pd

        session = Session()
        try:
            blocked = session.query(User).filter_by(is_blocked=True).order_by(User.user_id.desc()).all()
            rows = []
            for u in blocked:
                rows.append({
                    "User ID":       u.user_id,
                    "Username":      f"@{u.username}" if u.username else "",
                    "Имя":           u.first_name or "",
                    "Ник":           u.nickname or "",
                    "Район":         DISTRICT_NAMES.get(u.district, u.district or ""),
                    "Причина блока": u.blocked_reason or "",
                    "В боте с":      u.joined_at.strftime("%d.%m.%Y") if u.joined_at else "",
                })
        finally:
            session.close()

        df  = pd.DataFrame(rows)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Заблокированные")

            ws = writer.sheets["Заблокированные"]
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

        buf.seek(0)
        fname = f"blocked_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await callback.bot.send_document(
            callback.from_user.id,
            BufferedInputFile(buf.read(), filename=fname),
            caption=f"⛔ Заблокированные пользователи (всего {len(rows)}) · {datetime.now().strftime('%d.%m.%Y')}",
        )
        log_admin_action(
            callback.from_user.id,
            _admin_tag(callback.from_user),
            "export_blocked",
            f"Экспорт {len(rows)} заблокированных пользователей в Excel",
        )
    except ImportError:
        await callback.message.answer("❌ Установите pandas и openpyxl: pip install pandas openpyxl")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка генерации: {e}")


# История заказов конкретного пользователя
USER_ORDERS_PER_PAGE = 5

@router.callback_query(F.data.startswith("user_orders:"))
async def user_orders_history(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts   = callback.data.split(":")
    user_id = int(parts[1])
    page    = int(parts[2]) if len(parts) > 2 else 0
    await callback.answer()

    session = Session()
    try:
        total   = session.query(Order).filter_by(user_id=user_id).count()
        orders  = (session.query(Order)
                   .filter_by(user_id=user_id)
                   .order_by(Order.created_at.desc())
                   .offset(page * USER_ORDERS_PER_PAGE)
                   .limit(USER_ORDERS_PER_PAGE).all())

        if not orders:
            text = f"📦 <b>Заказы пользователя {user_id}</b>\n\nЗаказов нет."
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"user_card:{user_id}")]
            ])
        else:
            STATUS = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌", "completed": "🏁"}
            lines  = [f"📦 <b>Заказы пользователя {user_id}</b>  (всего {total})\n"]
            for o in orders:
                items    = session.query(OrderItem).filter_by(order_id=o.id).all()
                item_str = ", ".join(f"{i.flavor_name}×{i.quantity}" for i in items[:3])
                if len(items) > 3:
                    item_str += f" +{len(items)-3}"
                total_o  = sum((i.price or 0) * (i.quantity or 1) for i in items)
                ts       = o.created_at.strftime("%d.%m.%Y %H:%M") if o.created_at else "?"
                icon     = STATUS.get(o.status, "❓")
                lines.append(
                    f"{icon} <b>#{o.id}</b>  {ts}\n"
                    f"   {item_str}\n"
                    f"   💰 {total_o} ₽  📍 {DISTRICT_NAMES.get(o.district,'?')}\n"
                )
            text = "\n".join(lines)

            total_pages = max(1, (total - 1) // USER_ORDERS_PER_PAGE + 1)
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text="◀️", callback_data=f"user_orders:{user_id}:{page-1}"))
            nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(text="▶️", callback_data=f"user_orders:{user_id}:{page+1}"))

            kb = InlineKeyboardMarkup(inline_keyboard=[
                nav,
                [InlineKeyboardButton(text="⬅️ К профилю", callback_data=f"user_card:{user_id}")],
            ])
    finally:
        session.close()

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Написать пользователю по ID (из карточки пользователя) ──────────────────

@router.callback_query(F.data.startswith("msg_user_id:"))
async def msg_user_id_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    user_id = int(callback.data.split(":")[1])
    await state.update_data(msg_target_id=user_id)
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"✉️ Введите сообщение для пользователя <code>{user_id}</code>:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"user_card:{user_id}")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(f"✉️ Введите сообщение для {user_id}:")
    await state.set_state(AdminStates.waiting_for_user_message)


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОРИЯ ЗАКАЗОВ С ФИЛЬТРАМИ
# ══════════════════════════════════════════════════════════════════════════════

ORDERS_PER_PAGE = 8


def _filter_orders_by_district(q, district: str):
    """Фильтр запроса Order по району. Старые заказы без района считаются Рябково."""
    if district == "ryabkovo":
        return q.filter(or_(Order.district == "ryabkovo",
                            Order.district.is_(None),
                            Order.district == ""))
    return q.filter(Order.district == district)


def _order_visible_to(user_id: int, order) -> bool:
    """Курьер района видит только заказы своего района; суперадмин — все."""
    locked = locked_district(user_id)
    return locked is None or order_district(order.district) == locked


def _orders_menu_kb(user_id: int = 0):
    locked = locked_district(user_id) if user_id else None
    if locked:
        # Курьеру района — только заказы своего района, без переключателей района
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Все заказы",      callback_data=f"orders_list:0:all:{locked}"),
             InlineKeyboardButton(text="⏳ Новые",           callback_data=f"orders_list:0:pending:{locked}")],
            [InlineKeyboardButton(text="✅ Подтверждённые",  callback_data=f"orders_list:0:confirmed:{locked}"),
             InlineKeyboardButton(text="❌ Отменённые",      callback_data=f"orders_list:0:cancelled:{locked}")],
            [InlineKeyboardButton(text="🔍 Поиск по ID",     callback_data="order_search_start")],
            [InlineKeyboardButton(text="📤 Экспорт в Excel", callback_data="export_orders_excel")],
            [InlineKeyboardButton(text="⬅️ Назад",           callback_data="admin_menu")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Все заказы",      callback_data="orders_list:0:all:all"),
         InlineKeyboardButton(text="⏳ Новые",           callback_data="orders_list:0:pending:all")],
        [InlineKeyboardButton(text="✅ Подтверждённые",  callback_data="orders_list:0:confirmed:all"),
         InlineKeyboardButton(text="❌ Отменённые",      callback_data="orders_list:0:cancelled:all")],
        [InlineKeyboardButton(text="🏘 Рябково",         callback_data="orders_list:0:all:ryabkovo"),
         InlineKeyboardButton(text="🌊 Заозёрный",       callback_data="orders_list:0:all:zaozernoe")],
        [InlineKeyboardButton(text="🔍 Поиск по ID",     callback_data="order_search_start")],
        [InlineKeyboardButton(text="📤 Экспорт в Excel", callback_data="export_orders_excel")],
        [InlineKeyboardButton(text="⬅️ Назад",           callback_data="admin_menu")],
    ])


@router.callback_query(F.data == "orders_menu")
async def orders_menu_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await callback.answer()
    try:
        await callback.message.edit_text(
            "📦 <b>История заказов</b>\n\nВыберите фильтр:",
            reply_markup=_orders_menu_kb(callback.from_user.id), parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            "📦 <b>История заказов</b>\n\nВыберите фильтр:",
            reply_markup=_orders_menu_kb(callback.from_user.id), parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("orders_list:"))
async def orders_list_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    parts    = callback.data.split(":")
    page     = int(parts[1])
    status_f = parts[2]  # "all" | "pending" | "confirmed" | "cancelled"
    dist_f   = parts[3]  # "all" | "ryabkovo" | "zaozernoe"
    # Курьер района не может увидеть чужой район, даже подменив callback
    dist_f   = locked_district(callback.from_user.id) or dist_f
    await callback.answer()

    session = Session()
    try:
        q = session.query(Order).order_by(Order.created_at.desc())
        if status_f != "all":
            q = q.filter(Order.status == status_f)
        if dist_f != "all":
            q = _filter_orders_by_district(q, dist_f)

        total   = q.count()
        orders  = q.offset(page * ORDERS_PER_PAGE).limit(ORDERS_PER_PAGE).all()

        STATUS = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌", "completed": "🏁"}
        status_label = {"all": "Все", "pending": "⏳ Новые", "confirmed": "✅ Выполненные",
                        "cancelled": "❌ Отменённые"}.get(status_f, status_f)
        dist_label   = {"all": "все районы", "ryabkovo": "🏘 Рябково",
                        "zaozernoe": "🌊 Заозёрный"}.get(dist_f, dist_f)

        if not orders:
            text = f"📦 <b>Заказы</b> — {status_label} / {dist_label}\n\nПусто."
        else:
            lines = [f"📦 <b>Заказы</b> — {status_label} / {dist_label}  ({total} шт.)\n"]
            for o in orders:
                items    = session.query(OrderItem).filter_by(order_id=o.id).all()
                item_str = ", ".join(f"{i.flavor_name}×{i.quantity}" for i in items[:2])
                if len(items) > 2:
                    item_str += f" +{len(items)-2}"
                total_o = sum((i.price or 0) * (i.quantity or 1) for i in items)
                ts      = o.created_at.strftime("%d.%m %H:%M") if o.created_at else "?"
                icon    = STATUS.get(o.status, "❓")
                lines.append(
                    f"{icon} <b>#{o.id}</b> {ts}  @{o.username or o.user_id}\n"
                    f"   {item_str}  💰{total_o}₽"
                )
            text = "\n".join(lines)

        total_pages = max(1, (total - 1) // ORDERS_PER_PAGE + 1)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"orders_list:{page-1}:{status_f}:{dist_f}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"orders_list:{page+1}:{status_f}:{dist_f}"))

        kb = InlineKeyboardMarkup(inline_keyboard=[
            nav,
            [InlineKeyboardButton(text="🔙 Фильтры", callback_data="orders_menu")],
            [InlineKeyboardButton(text="⬅️ Меню",    callback_data="admin_menu")],
        ])
    finally:
        session.close()

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "order_search_start")
async def order_search_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await callback.answer()
    try:
        await callback.message.edit_text(
            "🔍 Введите <b>ID заказа</b> или <b>@username</b> клиента:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="orders_menu")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer("🔍 Введите ID заказа или @username:")
    await state.set_state(AdminStates.waiting_for_order_search)


@router.message(AdminStates.waiting_for_order_search)
async def order_search_process(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    query = message.text.strip().lstrip("#@")
    await state.clear()

    session = Session()
    try:
        if query.isdigit():
            orders = session.query(Order).filter(Order.id == int(query)).all()
            if not orders:
                # Может быть user_id
                orders = session.query(Order).filter(Order.user_id == int(query)).order_by(Order.created_at.desc()).limit(20).all()
        else:
            orders = (session.query(Order)
                      .filter(Order.username.ilike(f"%{query}%"))
                      .order_by(Order.created_at.desc()).limit(20).all())

        orders = [o for o in orders if _order_visible_to(message.from_user.id, o)]

        if not orders:
            await message.answer(
                f"❌ По запросу <b>{query}</b> ничего не найдено.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="orders_menu")]
                ]),
                parse_mode="HTML",
            )
            return

        STATUS = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌", "completed": "🏁"}
        lines = [f"🔍 <b>Результаты поиска:</b> <code>{query}</code>\n"]
        for o in orders:
            items   = session.query(OrderItem).filter_by(order_id=o.id).all()
            total_o = sum((i.price or 0) * (i.quantity or 1) for i in items)
            ts      = o.created_at.strftime("%d.%m.%Y %H:%M") if o.created_at else "?"
            icon    = STATUS.get(o.status, "❓")
            lines.append(f"{icon} <b>#{o.id}</b>  {ts}  @{o.username or o.user_id}  💰{total_o}₽")

        await message.answer(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="orders_menu")]
            ]),
            parse_mode="HTML",
        )
    finally:
        session.close()


# ══════════════════════════════════════════════════════════════════════════════
# ЭКСПОРТ ЗАКАЗОВ В EXCEL
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "export_orders_excel")
async def export_orders_excel(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await callback.answer("⏳ Генерирую Excel…")

    try:
        import pandas as pd

        session = Session()
        try:
            _q = session.query(Order).order_by(Order.created_at.desc())
            _locked = locked_district(callback.from_user.id)
            if _locked:
                _q = _filter_orders_by_district(_q, _locked)
            orders = _q.all()
            rows = []
            for o in orders:
                items    = session.query(OrderItem).filter_by(order_id=o.id).all()
                total_o  = sum((i.price or 0) * (i.quantity or 1) for i in items)
                item_str = "; ".join(f"{i.flavor_name}[{i.brand_name}]×{i.quantity}" for i in items)
                rows.append({
                    "ID заказа":   o.id,
                    "Дата":        o.created_at.strftime("%d.%m.%Y %H:%M") if o.created_at else "",
                    "Статус":      o.status,
                    "User ID":     o.user_id,
                    "Username":    f"@{o.username}" if o.username else "",
                    "Имя":         o.first_name or "",
                    "Контакт":     o.contact_info or "",
                    "Район":       DISTRICT_NAMES.get(o.district, o.district or ""),
                    "Состав":      item_str,
                    "Сумма (₽)":   total_o,
                    "Скидка %":    o.discount_pct or 0,
                    "Промокод":    o.promo_code or "",
                    "Скидка по коду (₽)": o.promo_discount or 0,
                })
        finally:
            session.close()

        df  = pd.DataFrame(rows)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Заказы")

            # Авторастяжка колонок
            ws = writer.sheets["Заказы"]
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

        buf.seek(0)
        fname = f"orders_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await callback.bot.send_document(
            callback.from_user.id,
            BufferedInputFile(buf.read(), filename=fname),
            caption=f"📤 Заказы (всего {len(rows)}) · {datetime.now().strftime('%d.%m.%Y')}",
        )
        log_admin_action(
            callback.from_user.id,
            _admin_tag(callback.from_user),
            "export_orders",
            f"Экспорт {len(rows)} заказов в Excel",
        )
    except ImportError:
        await callback.message.answer("❌ Установите pandas и openpyxl: pip install pandas openpyxl")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка генерации: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# РАСШИРЕННАЯ СТАТИСТИКА — ТОП ТОВАРОВ И ВЫРУЧКА ПО ДНЯМ
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "stats_top_items")
async def stats_top_items(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await callback.answer()

    session = Session()
    try:
        # Топ-10 вкусов по количеству заказов (только подтверждённые)
        top_dist = get_admin_district(callback.from_user.id)
        _base = _filter_orders_by_district(session.query(Order.id), top_dist)
        confirmed_ids = [o.id for o in _base.filter(Order.status == "confirmed").all()]
        if not confirmed_ids:
            confirmed_ids = [o.id for o in _base.all()]

        from sqlalchemy import func as f2
        top_flavors = (
            session.query(
                OrderItem.flavor_name,
                OrderItem.brand_name,
                f2.sum(OrderItem.quantity).label("qty"),
                f2.sum(OrderItem.price * OrderItem.quantity).label("revenue"),
            )
            .filter(OrderItem.order_id.in_(confirmed_ids))
            .group_by(OrderItem.flavor_name, OrderItem.brand_name)
            .order_by(f2.sum(OrderItem.quantity).desc())
            .limit(10).all()
        )

        # Выручка по последним 7 дням
        week_ago = datetime.utcnow() - timedelta(days=7)
        recent_orders = (
            _filter_orders_by_district(session.query(Order), top_dist)
            .filter(Order.status == "confirmed", Order.created_at >= week_ago)
            .all()
        )
        daily: dict[str, int] = {}
        for o in recent_orders:
            day = o.created_at.strftime("%d.%m") if o.created_at else "?"
            items = session.query(OrderItem).filter_by(order_id=o.id).all()
            daily[day] = daily.get(day, 0) + sum((i.price or 0) * (i.quantity or 1) for i in items)

    finally:
        session.close()

    lines = [f"🏆 <b>Топ-10 позиций (подтверждённые заказы)</b> · {DISTRICT_NAMES.get(top_dist, top_dist)}\n"]
    for i, row in enumerate(top_flavors, 1):
        brand_str = f" [{row.brand_name}]" if row.brand_name else ""
        rev_str   = f"  💰{int(row.revenue or 0)}₽" if row.revenue else ""
        lines.append(f"{i}. {row.flavor_name}{brand_str} — {row.qty} шт.{rev_str}")

    if daily:
        lines.append("\n📅 <b>Выручка за последние 7 дней:</b>\n")
        for day in sorted(daily):
            bar = "█" * min(20, daily[day] // 200)
            lines.append(f"<code>{day}</code>  {bar}  {daily[day]} ₽")

    text = "\n".join(lines) if len(lines) > 1 else "📊 Нет данных (нет подтверждённых заказов)"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="⬅️ Меню",            callback_data="admin_menu")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════════════════
# ПОСТ В КАНАЛ ИЗ АДМИНКИ
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "post_to_channel")
async def post_to_channel_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await state.clear()
    await callback.answer()
    channel = config.config.CHANNEL_USERNAME or str(config.config.CHANNEL_ID)
    try:
        await callback.message.edit_text(
            f"📣 <b>Пост в канал @{channel}</b>\n\n"
            "Отправьте текст поста (поддерживается HTML).\n"
            "Можно также прикрепить фото — отправьте фото с подписью.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_menu")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(f"📣 Введите текст поста в канал @{channel}:")
    await state.set_state(AdminStates.waiting_for_channel_post)


@router.message(AdminStates.waiting_for_channel_post)
async def post_to_channel_send(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await state.clear(); return
    await state.clear()

    channel_id = config.config.CHANNEL_ID
    channel    = config.config.CHANNEL_USERNAME or str(channel_id)
    try:
        if message.photo:
            # Фото с подписью
            await message.bot.send_photo(
                chat_id=channel_id,
                photo=message.photo[-1].file_id,
                caption=message.caption or "",
                parse_mode="HTML",
            )
        else:
            await message.bot.send_message(
                chat_id=channel_id,
                text=message.text or "",
                parse_mode="HTML",
            )
        log_admin_action(
            message.from_user.id,
            _admin_tag(message.from_user),
            "channel_post",
            f"Пост в @{channel}: {(message.text or message.caption or '')[:80]}",
        )
        await message.answer(
            f"✅ Пост опубликован в @{channel}!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📣 Ещё пост", callback_data="post_to_channel")],
                [InlineKeyboardButton(text="⬅️ Меню",     callback_data="admin_menu")],
            ]),
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось опубликовать пост: {e}\n\n"
            f"Убедитесь, что бот является администратором канала @{channel}.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Меню", callback_data="admin_menu")]
            ]),
        )


# ══════════════════════════════════════════════════════════════════════════════
# МАССОВОЕ УПРАВЛЕНИЕ НАЛИЧИЕМ ТОВАРОВ
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "bulk_availability")
async def bulk_availability_menu(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    await callback.answer()
    dist = get_admin_district(callback.from_user.id)
    dist_label = DISTRICT_NAMES.get(dist, dist)

    session = Session()
    try:
        brands = session.query(Brand).order_by(Brand.id).all()
        counts = {}
        for b in brands:
            total   = session.query(Flavor).filter_by(brand_id=b.id, district=dist).count()
            active  = session.query(Flavor).filter_by(brand_id=b.id, district=dist, is_available=True).count()
            counts[b.id] = (total, active)
    finally:
        session.close()

    rows = []
    for b in brands:
        total, active = counts[b.id]
        if total == 0:
            continue
        icon = "✅" if active > 0 else "❌"
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {b.emoji or ''} {b.name}  ({active}/{total})",
                callback_data=f"bulk_brand:{b.id}",
            )
        ])

    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="admin_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    try:
        await callback.message.edit_text(
            f"💡 <b>Управление наличием</b>  ·  {dist_label}\n\n"
            "Нажмите на бренд, чтобы вкл/выкл все его позиции сразу:",
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            f"💡 <b>Управление наличием</b>  ·  {dist_label}",
            reply_markup=kb, parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("bulk_brand:"))
async def bulk_brand_toggle(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа"); return
    brand_id = int(callback.data.split(":")[1])
    dist     = get_admin_district(callback.from_user.id)

    session = Session()
    try:
        brand   = session.query(Brand).filter_by(id=brand_id).first()
        flavors = session.query(Flavor).filter_by(brand_id=brand_id, district=dist).all()
        if not flavors:
            await callback.answer("Нет позиций в этом районе"); return

        active_count = sum(1 for f in flavors if f.is_available)
        # Если хоть одна активна — выключаем все; если все выключены — включаем все
        new_state = active_count == 0
        for flav in flavors:
            flav.is_available = new_state
        session.commit()

        action_str = "включено" if new_state else "выключено"
        log_admin_action(
            callback.from_user.id,
            _admin_tag(callback.from_user),
            "bulk_toggle",
            f"Бренд «{brand.name if brand else brand_id}» ({dist}): {action_str} {len(flavors)} позиций",
        )
        await callback.answer(
            f"{'✅' if new_state else '❌'} {brand.name if brand else ''}: {action_str} {len(flavors)} позиций"
        )
    finally:
        session.close()

    await bulk_availability_menu(callback)


# ══════════════════════════════════════════════════════════════════════════════
# АВТОБЭКАП — вызывается из reminder_loop в reminder.py
# ══════════════════════════════════════════════════════════════════════════════

async def send_daily_backup(bot):
    """Отправляет ежедневный бэкап БД всем суперадминам (не курьерам районов)."""
    try:
        import aiofiles
        db_path = "data/database.db"
        async with aiofiles.open(db_path, "rb") as f:
            data = await f.read()
        fname = f"backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db"
        for admin_id in config.config.ADMINS:
            if locked_district(admin_id):
                continue  # курьерам (любого района) полный бэкап БД не отправляем
            try:
                await bot.send_document(
                    admin_id,
                    BufferedInputFile(data, filename=fname),
                    caption=f"🗄 Автобэкап БД · {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                )
            except Exception as e:
                print(f"⚠️ Автобэкап не отправлен {admin_id}: {e}")
    except Exception as e:
        print(f"⚠️ Автобэкап ошибка: {e}")
