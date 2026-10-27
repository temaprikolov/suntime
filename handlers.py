#!/usr/bin/env python3
import config
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)
from database import (
    Session, User, Product, Flavor, CartItem, Order, OrderItem,
    ProductView, Brand, Referral, PromoCode, BRAND_CATEGORY_IDS, DISTRICT_NAMES,
    get_or_create_referral_code, set_channel_shown, is_channel_shown,
)
from reminder import clear_reminder
from antispam import AntiSpamFilter, AntiSpamMiddleware
from textutils import esc
from district_access import order_recipients

router = Router()
router.message.middleware(AntiSpamMiddleware())
print("✅ handlers.py загружен")


# ══════════════════════════════════════════
# СОСТОЯНИЯ
# ══════════════════════════════════════════

class OrderStates(StatesGroup):
    waiting_for_contact = State()
    waiting_for_promo   = State()

class ProfileStates(StatesGroup):
    editing_nickname = State()
    editing_avatar   = State()

class SearchStates(StatesGroup):
    waiting_for_query = State()


# ══════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════

async def get_user_district(user_id: int):
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=user_id).first()
        return user.district if user else None
    finally:
        session.close()


def menu_text(district: str | None) -> str:
    dist_label = DISTRICT_NAMES.get(district, "") if district else ""
    suffix = f"  ·  🏘 <b>{dist_label}</b>" if dist_label else ""
    return f"🏪 <b>НАЛИЧИЕ ТОВАРА SUNTIME{suffix}</b>\n\n⬇️ Выберите категорию:"


async def safe_show_menu(target, district: str | None):
    """Надёжно показывает главное меню: сначала пытается edit, при любой ошибке — удаляет и присылает новое."""
    text = menu_text(district)
    kb   = main_menu_kb()
    try:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            # Содержимое то же — меню уже правильное, ничего не делаем
            return
        # Любая другая ошибка edit — удаляем и шлём новое
    except Exception:
        pass
    try:
        await target.delete()
    except Exception:
        pass
    try:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


def district_selection_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏘 Рябково",    callback_data="district:ryabkovo")],
        [InlineKeyboardButton(text="🌊 Заозёрный",  callback_data="district:zaozernoe")],
    ])


async def ask_for_district(target):
    text = (
        "🗺 <b>Выберите ваш район:</b>\n\n"
        "Ваш район определяет доступные товары.\n"
        "<i>Выбор можно будет изменить в профиле.</i>"
    )
    try:
        await target.edit_text(text, reply_markup=district_selection_kb(), parse_mode="HTML")
    except Exception:
        await target.answer(text, reply_markup=district_selection_kb(), parse_mode="HTML")


# ══════════════════════════════════════════
# КЛАВИАТУРЫ
# ══════════════════════════════════════════

def main_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="🛍 Рынок")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие...",
    )

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 РАСПРОДАЖА",                callback_data="item_7")],
        [InlineKeyboardButton(text="🎲 РАНДОМНЫЙ ТОВАР",           callback_data="item_8")],
        [InlineKeyboardButton(text="🥤 НАЛИЧИЕ ЖИДКОСТЕЙ",         callback_data="item_1")],
        [InlineKeyboardButton(text="🧪 НАЛИЧИЕ СН*СА И ПЛАСТИНОК", callback_data="item_2")],
        [InlineKeyboardButton(text="💨 НАЛИЧИЕ ОДНОРАЗОВЫХ ОЭС",   callback_data="item_3")],
        [InlineKeyboardButton(text="🔧 НАЛИЧИЕ РАСХОДНИКОВ",       callback_data="item_4")],
        [InlineKeyboardButton(text="📱 НАЛИЧИЕ POD-УСТРОЙСТВ",     callback_data="item_5")],
        [InlineKeyboardButton(text="📦 ИНФОРМАЦИЯ О ЗАВОЗЕ",       callback_data="item_6")],
        [InlineKeyboardButton(text="🔍 ПОИСК ВКУСА",               callback_data="search_flavor")],
        [InlineKeyboardButton(text="📅 РАСПИСАНИЕ",                callback_data="item_9")],
        [InlineKeyboardButton(text="🛒 МОЯ КОРЗИНА",               callback_data="view_cart")],
        [InlineKeyboardButton(text="❓ КАК ЗАКАЗАТЬ",              callback_data="how_to_order")],
    ])

def cart_keyboard(has_discount: bool = False, has_promo: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if has_discount:
        rows.append([InlineKeyboardButton(text="✅ ЗАКАЗАТЬ + СКИДКА 🎁", callback_data="place_order_discount")])
        rows.append([InlineKeyboardButton(text="✅ ЗАКАЗАТЬ БЕЗ СКИДКИ",  callback_data="place_order_no_discount")])
    else:
        rows.append([InlineKeyboardButton(text="✅ ЗАКАЗАТЬ", callback_data="place_order_no_discount")])
    if not has_promo:
        rows.append([InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="enter_promo")])
    rows.append([InlineKeyboardButton(text="🗑 ОЧИСТИТЬ",  callback_data="clear_cart")])
    rows.append([InlineKeyboardButton(text="⬅️ НАЗАД",    callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ══════════════════════════════════════════
# РЕГИСТРАЦИЯ
# ══════════════════════════════════════════

async def register_user(user_id, username, first_name, last_name):
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=user_id).first()
        if not user:
            session.add(User(user_id=user_id, username=username,
                             first_name=first_name, last_name=last_name))
        else:
            user.username   = username
            user.first_name = first_name
            user.last_name  = last_name
            if getattr(user, "left_bot", False):
                user.left_bot = False  # снова пишет боту → снимаем пометку "заблокировал бота"
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"register_user error: {e}")
    finally:
        session.close()


async def handle_referral(new_user_id: int, ref_code: str):
    session = Session()
    try:
        referrer = session.query(User).filter_by(referral_code=ref_code).first()
        if not referrer or referrer.user_id == new_user_id:
            return
        existing = session.query(Referral).filter_by(referred_user_id=new_user_id).first()
        if not existing:
            session.add(Referral(referrer_user_id=referrer.user_id, referred_user_id=new_user_id))
            session.commit()
    except Exception as e:
        session.rollback()
        print(f"handle_referral error: {e}")
    finally:
        session.close()


# ══════════════════════════════════════════
# /start
# ══════════════════════════════════════════

@router.message(Command("start"))
async def cmd_start(message: Message):
    u = message.from_user
    args = message.text.split(" ", 1)
    start_param = args[1].strip() if len(args) > 1 else ""

    await register_user(u.id, u.username, u.first_name, u.last_name)

    if start_param.startswith("ref_"):
        await handle_referral(u.id, start_param[4:])

    await message.answer("👋 Добро пожаловать! Кнопка профиля — внизу 👇",
                         reply_markup=main_reply_kb())

    district = await get_user_district(u.id)
    if not district:
        await message.answer(
            "🗺 <b>Выберите ваш район:</b>\n\n"
            "Ваш район определяет доступные товары.\n"
            "<i>Выбор можно изменить в профиле.</i>",
            reply_markup=district_selection_kb(),
            parse_mode="HTML",
        )
        return

    await message.answer(
        menu_text(district),
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "go_to_menu")
async def go_to_menu_cb(callback: CallbackQuery):
    """Старая кнопка 'В меню' из прежнего приветствия (подписку проверяет SubscriptionMiddleware)."""
    await callback.answer()
    await callback.message.delete()

    await callback.message.answer(
        "👋 Добро пожаловать! Кнопка профиля — внизу 👇",
        reply_markup=main_reply_kb()
    )

    district = await get_user_district(callback.from_user.id)
    if not district:
        await callback.message.answer(
            "🗺 <b>Выберите ваш район:</b>\n\nВаш район определяет доступные товары.\n"
            "<i>Выбор можно изменить в профиле.</i>",
            reply_markup=district_selection_kb(),
            parse_mode="HTML",
        )
    else:
        await safe_show_menu(callback.message, district)


@router.callback_query(F.data == "check_subscription")
async def check_subscription_cb(callback: CallbackQuery):
    await callback.answer()

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "👋 Добро пожаловать! Кнопка профиля — внизу 👇",
        reply_markup=main_reply_kb(),
    )

    district = await get_user_district(callback.from_user.id)
    if not district:
        await callback.message.answer(
            "🗺 <b>Выберите ваш район:</b>\n\nВаш район определяет доступные товары.\n"
            "<i>Выбор можно изменить в профиле.</i>",
            reply_markup=district_selection_kb(),
            parse_mode="HTML",
        )
    else:
        await callback.message.answer(
            menu_text(district), reply_markup=main_menu_kb(), parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("district:"))
async def select_district(callback: CallbackQuery):
    district = callback.data.split(":")[1]
    if district not in ("ryabkovo", "zaozernoe"):
        await callback.answer("❌ Неизвестный район"); return

    district_name = DISTRICT_NAMES.get(district, district)

    session = Session()
    try:
        user = session.query(User).filter_by(user_id=callback.from_user.id).first()
        if user:
            old = user.district
            user.district = district
            session.commit()
            if old and old != district:
                session.query(CartItem).filter_by(user_id=callback.from_user.id).delete()
                session.commit()
    except Exception as e:
        session.rollback()
        print(f"select_district error: {e}")
    finally:
        session.close()

    await callback.answer(f"✅ Район: {district_name}")
    await callback.message.edit_text(
        f"✅ <b>Ваш район: {district_name}</b>\n\n"
        f"Теперь вам доступен весь ассортимент!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏪 В меню", callback_data="back_to_menu")],
        ]),
        parse_mode="HTML",
    )


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"🆔 <b>Ваш ID:</b>\n<code>{message.from_user.id}</code>", parse_mode="HTML")


@router.message(Command("order"))
async def cmd_order(message: Message, state: FSMContext):
    """
    /order — быстрый вход в корзину.
    Если корзина пуста — показываем меню, иначе — корзину.
    """
    await register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name,
    )

    district = await get_user_district(message.from_user.id)
    if not district:
        await message.answer(
            "🗺 <b>Сначала выберите район:</b>",
            reply_markup=district_selection_kb(),
            parse_mode="HTML",
        )
        return

    session = Session()
    try:
        items = session.query(CartItem).filter_by(user_id=message.from_user.id).all()

        if not items:
            await message.answer(
                "🛒 <b>Ваша корзина пуста.</b>\n\n"
                "Добавьте товары из меню и вернитесь для оформления заказа.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🏪 Перейти в меню", callback_data="back_to_menu")],
                ]),
                parse_mode="HTML",
            )
            return

        fsm_data   = await state.get_data()
        promo_code = fsm_data.get("promo_code")
        promo_disc = fsm_data.get("promo_discount", 0)
        has_promo  = bool(promo_code)

        user         = session.query(User).filter_by(user_id=message.from_user.id).first()
        discount_pct = (user.referral_discount_pct or 0.0) if user else 0.0
        has_discount = discount_pct > 0

        total = 0
        text  = "🛒 <b>ВАША КОРЗИНА</b>\n\n"
        for item in items:
            brand_str  = f" · <i>{item.brand_name}</i>" if item.brand_name else ""
            price_unit = f"  💰 {item.price} ₽/шт." if item.price else ""
            item_total = (item.price * item.quantity) if item.price else None
            total_str  = f"  = <b>{item_total} ₽</b>" if item_total else ""
            text += f"📦 {item.product_name}{brand_str}\n"
            text += f"   🍬 {item.flavor_name}{price_unit}\n"
            text += f"   🔢 {item.quantity} шт.{total_str}   /del_{item.id}\n\n"
            if item_total:
                total += item_total

        if total > 0:
            text += "━━━━━━━━━━━━━━━━━━\n"
            text += f"💵 <b>Итого: {total} ₽</b>\n"
            if has_discount:
                save = total * discount_pct / 100
                text += f"🎁 Скидка {discount_pct:.1f}%: <b>−{save:.0f} ₽</b>\n"
            if has_promo:
                final = max(0, total - promo_disc)
                text += f"🎟 Промокод <b>{promo_code}</b>: −{promo_disc} ₽\n"
                text += f"✅ С промокодом: <b>{final} ₽</b>\n"
        text += "\nНажмите <b>ЗАКАЗАТЬ</b> для оформления."

        await message.answer(text, reply_markup=cart_keyboard(has_discount, has_promo), parse_mode="HTML")

    except Exception as e:
        print(f"cmd_order error: {e}")
        await message.answer("❌ Ошибка при загрузке корзины. Попробуйте /start")
    finally:
        session.close()


@router.message(Command("myorders"))
async def cmd_myorders(message: Message):
    session = Session()
    try:
        orders = (session.query(Order)
                  .filter_by(user_id=message.from_user.id)
                  .order_by(Order.created_at.desc())
                  .limit(10).all())
        if not orders:
            await message.answer("📭 У вас пока нет заказов")
            return
        icons = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌"}
        text = "📜 <b>Ваши заказы:</b>\n\n"
        for order in orders:
            icon = icons.get(order.status, "❓")
            dist_str = f" · {DISTRICT_NAMES.get(order.district, '')}" if order.district else ""
            text += f"{icon} <b>Заказ #{order.id}</b>{dist_str} — {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            for item in session.query(OrderItem).filter_by(order_id=order.id).all():
                brand_str = f" [{item.brand_name}]" if item.brand_name else ""
                price_str = f" — {item.price} ₽" if item.price else ""
                text += f"   • {item.flavor_name}{brand_str} x{item.quantity}{price_str}\n"
            if order.discount_pct and order.discount_pct > 0:
                text += f"   🎁 Скидка: {order.discount_pct:.1f}%\n"
            if order.promo_code:
                text += f"   🎟 Промокод: {order.promo_code} (−{order.promo_discount} ₽)\n"
            text += "\n"
        await message.answer(text, parse_mode="HTML")
    finally:
        session.close()


# ══════════════════════════════════════════
# ПРОФИЛЬ
# ══════════════════════════════════════════

@router.message(F.text == "👤 Мой профиль")
async def show_profile_reply(message: Message):
    await _send_profile(message)


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    await _send_profile(message)


@router.callback_query(F.data == "open_profile")
async def open_profile_cb(callback: CallbackQuery):
    await callback.answer()
    await _send_profile(callback.message)


async def _send_profile(message: Message):
    uid = message.chat.id if hasattr(message, "chat") else message.from_user.id
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=uid).first()
        if not user:
            await message.answer("❌ Профиль не найден. Напишите /start")
            return

        confirmed_orders = session.query(Order).filter_by(user_id=uid, status="confirmed").all()
        total_confirmed  = len(confirmed_orders)

        cat_counts: dict = {}
        for o in confirmed_orders:
            for item in session.query(OrderItem).filter_by(order_id=o.id).all():
                cat_counts[item.product_name] = cat_counts.get(item.product_name, 0) + item.quantity

        refs         = session.query(Referral).filter_by(referrer_user_id=uid).all()
        refs_total   = len(refs)
        refs_bought  = sum(1 for r in refs if r.purchased)
        discount_pct = user.referral_discount_pct or 0.0

        display_name = user.nickname or user.first_name or f"ID{uid}"
        username_str = f"@{user.username}" if user.username else "не указан"
        district_str = DISTRICT_NAMES.get(user.district, "❌ Не выбран")

        text = (
            f"╔══════════════════╗\n"
            f"        👤 <b>МОЙ ПРОФИЛЬ</b>\n"
            f"╚══════════════════╝\n\n"
            f"📛 <b>Ник:</b> {esc(display_name)}\n"
            f"🆔 <b>Username:</b> {username_str}\n"
            f"📍 <b>Район:</b> {district_str}\n"
            f"📅 <b>С нами с:</b> {user.joined_at.strftime('%d.%m.%Y')}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>МОЯ СТАТИСТИКА</b>\n\n"
        )

        if cat_counts:
            CAT_ICONS = {
                "НАЛИЧИЕ ЖИДКОСТЕЙ":          "🥤",
                "НАЛИЧИЕ СН*СА И ПЛАСТИНОК":  "🧪",
                "НАЛИЧИЕ ОДНОРАЗОВЫХ ОЭС":    "💨",
                "НАЛИЧИЕ РАСХОДНИКОВ":         "🔧",
                "НАЛИЧИЕ POD-УСТРОЙСТВ":       "📱",
                "🔥 РАСПРОДАЖА":               "🔥",
                "🎲 РАНДОМНЫЙ ТОВАР":          "🎲",
            }
            for cat, qty in cat_counts.items():
                icon = CAT_ICONS.get(cat, "📦")
                short = cat.replace("НАЛИЧИЕ ", "").replace("🔥 ", "").replace("🎲 ", "")
                text += f"{icon} <b>{short}:</b> {qty} шт.\n"
        else:
            text += "📭 Пока нет покупок\n"

        text += (
            f"\n🛒 <b>Подтверждённых заказов:</b> {total_confirmed}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎁 <b>РЕФЕРАЛЬНАЯ ПРОГРАММА</b>\n\n"
            f"👥 Приглашено друзей: <b>{refs_total}</b>\n"
            f"✅ Совершили покупку: <b>{refs_bought}</b>\n"
            f"💰 Накоплено скидки: <b>{discount_pct:.1f}%</b>\n"
        )
        if discount_pct > 0:
            text += f"\n<i>✨ Скидку можно применить к следующему заказу!</i>\n"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить ник",       callback_data="profile_edit_nick")],
            [InlineKeyboardButton(text="🖼 Изменить аватар",    callback_data="profile_edit_avatar")],
            [InlineKeyboardButton(text="📍 Изменить район",     callback_data="profile_change_district")],
            [InlineKeyboardButton(text="🔗 Реферальная ссылка", callback_data="profile_referral")],
            [InlineKeyboardButton(text="🏪 В меню",             callback_data="back_to_menu")],
        ])

        if user.avatar_file_id:
            try:
                await message.answer_photo(photo=user.avatar_file_id, caption=text,
                                           reply_markup=kb, parse_mode="HTML")
                return
            except Exception:
                pass

        await message.answer(text, reply_markup=kb, parse_mode="HTML")

    finally:
        session.close()


@router.callback_query(F.data == "profile_change_district")
async def profile_change_district(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🗺 <b>Выберите новый район:</b>\n\n"
        "⚠️ При смене района ваша корзина будет очищена.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏘 Рябково",   callback_data="district:ryabkovo")],
            [InlineKeyboardButton(text="🌊 Заозёрный", callback_data="district:zaozernoe")],
            [InlineKeyboardButton(text="❌ Отмена",    callback_data="open_profile")],
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "profile_edit_nick")
async def profile_edit_nick(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "✏️ <b>Смена никнейма</b>\n\nОтправьте новый ник (до 32 символов):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_state")]
        ]),
        parse_mode="HTML",
    )
    await state.set_state(ProfileStates.editing_nickname)


@router.callback_query(F.data == "profile_edit_avatar")
async def profile_edit_avatar(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "🖼 <b>Смена аватара</b>\n\nОтправьте фото для вашего профиля:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_state")]
        ]),
        parse_mode="HTML",
    )
    await state.set_state(ProfileStates.editing_avatar)


@router.callback_query(F.data == "cancel_state")
async def cancel_state(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено ✅")
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.message(ProfileStates.editing_nickname)
async def save_nickname(message: Message, state: FSMContext):
    nick = message.text.strip()[:32]
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=message.from_user.id).first()
        if user:
            user.nickname = nick
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()
    await state.clear()
    await message.answer(
        f"✅ Ник изменён на <b>{esc(nick)}</b>!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Открыть профиль", callback_data="open_profile")],
            [InlineKeyboardButton(text="🏪 В меню",          callback_data="back_to_menu")],
        ]),
        parse_mode="HTML",
    )


@router.message(ProfileStates.editing_avatar)
async def save_avatar(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Нужно отправить фото!")
        return
    file_id = message.photo[-1].file_id
    session = Session()
    try:
        user = session.query(User).filter_by(user_id=message.from_user.id).first()
        if user:
            user.avatar_file_id = file_id
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()
    await state.clear()
    await message.answer(
        "✅ <b>Аватар обновлён!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Открыть профиль", callback_data="open_profile")],
            [InlineKeyboardButton(text="🏪 В меню",          callback_data="back_to_menu")],
        ]),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "profile_referral")
async def profile_referral(callback: CallbackQuery):
    await callback.answer()
    uid  = callback.from_user.id
    code = get_or_create_referral_code(uid)
    bot_info = await callback.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{code}"

    session = Session()
    try:
        refs         = session.query(Referral).filter_by(referrer_user_id=uid).all()
        refs_total   = len(refs)
        refs_bought  = sum(1 for r in refs if r.purchased)
        user         = session.query(User).filter_by(user_id=uid).first()
        discount_pct = (user.referral_discount_pct or 0.0) if user else 0.0
    finally:
        session.close()

    text = (
        f"🔗 <b>РЕФЕРАЛЬНАЯ СИСТЕМА</b>\n\n"
        f"Приглашай друзей — получай скидку!\n\n"
        f"<b>Как это работает:</b>\n"
        f"1️⃣ Поделись своей ссылкой с другом\n"
        f"2️⃣ Друг регистрируется по ссылке\n"
        f"3️⃣ Друг совершает покупку → ты получаешь +0.5% скидки\n"
        f"4️⃣ Накапливай скидку и применяй к одному заказу\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Твоя статистика:</b>\n"
        f"👥 Приглашено: <b>{refs_total}</b>\n"
        f"✅ Купили: <b>{refs_bought}</b>\n"
        f"💰 Накоплено: <b>{discount_pct:.1f}%</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <b>Твоя реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"<i>👆 Нажми чтобы скопировать и отправь другу!</i>"
    )

    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в профиль", callback_data="open_profile")],
        ]),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════
# ПОИСК ВКУСА
# ══════════════════════════════════════════

@router.callback_query(F.data == "search_flavor")
async def search_flavor_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    district = await get_user_district(callback.from_user.id)
    if not district:
        await callback.answer("Сначала выберите район!", show_alert=True)
        return
    await state.update_data(search_district=district)
    try:
        await callback.message.edit_text(
            "🔍 <b>Поиск вкуса</b>\n\nВведите название вкуса или бренда:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_menu")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            "🔍 <b>Поиск вкуса</b>\n\nВведите название вкуса или бренда:",
            parse_mode="HTML",
        )
    await state.set_state(SearchStates.waiting_for_query)


@router.message(SearchStates.waiting_for_query)
async def search_flavor_query(message: Message, state: FSMContext):
    data     = await state.get_data()
    district = data.get("search_district")
    await state.clear()

    query = message.text.strip().lower()
    if not query:
        await message.answer("❌ Запрос пустой.")
        return

    session = Session()
    try:
        all_flavors = (session.query(Flavor)
                       .filter_by(is_available=True, district=district)
                       .all())
        results = []
        for fl in all_flavors:
            brand = session.query(Brand).filter_by(id=fl.brand_id).first() if fl.brand_id else None
            brand_name = brand.name.lower() if brand else ""
            if query in fl.name.lower() or query in brand_name:
                results.append((fl, brand))

        if not results:
            await message.answer(
                f"🔍 По запросу «<b>{esc(message.text.strip())}</b>» ничего не найдено.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 Искать ещё", callback_data="search_flavor")],
                    [InlineKeyboardButton(text="🏪 В меню",     callback_data="back_to_menu")],
                ]),
                parse_mode="HTML",
            )
            return

        text = f"🔍 <b>Результаты поиска: «{esc(message.text.strip())}»</b>\n\n"
        keyboard = []
        for fl, brand in results[:20]:
            price_str = f" — {fl.price} ₽" if fl.price else ""
            brand_str = f" [{brand.name}]" if brand else ""
            text += f"• {fl.name}{brand_str}{price_str}\n"
            btn_label = f"➕ {fl.name}" + (f" | {fl.price} ₽" if fl.price else "")
            keyboard.append([InlineKeyboardButton(text=btn_label, callback_data=f"add_to_cart_{fl.id}")])

        if len(results) > 20:
            text += f"\n<i>Показаны первые 20 из {len(results)} результатов.</i>"

        keyboard.append([InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_flavor")])
        keyboard.append([InlineKeyboardButton(text="🏪 В меню",       callback_data="back_to_menu")])

        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")

    finally:
        session.close()


# ══════════════════════════════════════════
# РЕНДЕР ТОВАРОВ
# ══════════════════════════════════════════

BRANDS_PER_PAGE  = 8   # брендов на страницу (список брендов)
FLAVORS_PER_PAGE = 10  # вкусов на страницу (список вкусов внутри бренда)


async def render_product_page(msg, product_id: int, district: str, page: int = 0):
    if product_id in BRAND_CATEGORY_IDS:
        await render_brand_list(msg, product_id, district, page)
    else:
        await render_direct_flavors(msg, product_id, district, page)


async def render_brand_list(msg, product_id: int, district: str, page: int = 0):
    session = Session()
    try:
        product = session.query(Product).filter_by(id=product_id).first()
        if not product:
            await msg.edit_text("❌ Товар не найден",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="⬅️ НАЗАД", callback_data="back_to_menu")]
                                ]))
            return

        flavors  = (session.query(Flavor)
                    .filter_by(product_id=product_id, is_available=True, district=district)
                    .filter(Flavor.brand_id != None).all())
        seen_ids = list(dict.fromkeys(f.brand_id for f in flavors))
        brands   = [session.query(Brand).filter_by(id=bid).first() for bid in seen_ids]
        brands   = [b for b in brands if b]

        text = product.item_text or f"<b>{product.item_name}</b>"
        keyboard = []

        if brands:
            total_pages = (len(brands) - 1) // BRANDS_PER_PAGE + 1
            page = max(0, min(page, total_pages - 1))
            page_brands = brands[page * BRANDS_PER_PAGE:(page + 1) * BRANDS_PER_PAGE]

            text += f"\n\n🏷 <b>Выберите бренд:</b>"
            if total_pages > 1:
                text += f"  <i>стр. {page + 1}/{total_pages}</i>"

            for brand in page_brands:
                cnt = sum(1 for f in flavors if f.brand_id == brand.id)
                keyboard.append([InlineKeyboardButton(
                    text=f"{brand.emoji} {brand.name}  ·  {cnt} вкус(ов)",
                    callback_data=f"brand_{product_id}_{brand.id}",
                )])

            # Навигация по страницам
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"bpage_{product_id}_{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"bpage_{product_id}_{page + 1}"))
            if nav_row:
                keyboard.append(nav_row)
        else:
            manager = (
                config.config.SHOP_MANAGER_ZAOZERNOE
                if district == "zaozernoe"
                else config.config.SHOP_MANAGER
            )
            text += (f"\n\n📭 <b>Пока нет позиций</b>\n\n"
                     f"📞 По наличию: @{manager}")

        keyboard.append([InlineKeyboardButton(text="🛒 МОЯ КОРЗИНА", callback_data="view_cart")])
        keyboard.append([InlineKeyboardButton(text="⬅️ НАЗАД",        callback_data="back_to_menu")])

        await msg.edit_text(text,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                            parse_mode="HTML")
    finally:
        session.close()


async def render_brand_flavors(msg, product_id: int, brand_id: int, district: str, page: int = 0):
    session = Session()
    try:
        product = session.query(Product).filter_by(id=product_id).first()
        brand   = session.query(Brand).filter_by(id=brand_id).first()
        flavors = (session.query(Flavor)
                   .filter_by(product_id=product_id, brand_id=brand_id,
                              is_available=True, district=district)
                   .all())

        if not brand:
            await msg.edit_text("❌ Бренд не найден",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="⬅️ НАЗАД", callback_data=f"item_{product_id}")]
                                ]))
            return

        cat_name = product.item_name if product else ""
        text     = (f"{brand.emoji} <b>{brand.name}</b>\n"
                    f"<i>{cat_name}</i>\n\n")
        keyboard = []

        if flavors:
            total_pages = (len(flavors) - 1) // FLAVORS_PER_PAGE + 1
            page = max(0, min(page, total_pages - 1))
            page_flavors = flavors[page * FLAVORS_PER_PAGE:(page + 1) * FLAVORS_PER_PAGE]

            text += "🍬 <b>Доступные вкусы:</b>\n"
            if total_pages > 1:
                text += f"<i>стр. {page + 1}/{total_pages}</i>\n"
            text += "\n"

            for fl in page_flavors:
                price_str = f"  —  <b>{fl.price} ₽</b>" if fl.price else ""
                photo_mark = " 📷" if fl.photo_file_id else ""
                text += f"• {fl.name}{price_str}{photo_mark}\n"
                btn_label = f"➕  {fl.name}" + (f"  |  {fl.price} ₽" if fl.price else "")
                row = [InlineKeyboardButton(text=btn_label, callback_data=f"add_to_cart_{fl.id}")]
                if fl.photo_file_id:
                    row.append(InlineKeyboardButton(text="📷", callback_data=f"showphoto:{fl.id}"))
                keyboard.append(row)

            # Навигация по страницам вкусов
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"flpage_{product_id}_{brand_id}_{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"flpage_{product_id}_{brand_id}_{page + 1}"))
            if nav_row:
                keyboard.append(nav_row)

            keyboard.append([InlineKeyboardButton(text="🛒 МОЯ КОРЗИНА", callback_data="view_cart")])
        else:
            manager = (
                config.config.SHOP_MANAGER_ZAOZERNOE
                if district == "zaozernoe"
                else config.config.SHOP_MANAGER
            )
            text += f"📭 Нет в наличии\n📞 @{manager}"

        keyboard.append([InlineKeyboardButton(text=f"⬅️ К брендам", callback_data=f"item_{product_id}")])
        keyboard.append([InlineKeyboardButton(text="🏪 В меню",      callback_data="back_to_menu")])

        await msg.edit_text(text,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                            parse_mode="HTML")
    finally:
        session.close()


async def render_direct_flavors(msg, product_id: int, district: str, page: int = 0):
    session = Session()
    try:
        product = session.query(Product).filter_by(id=product_id).first()
        if not product:
            await msg.edit_text("❌ Товар не найден",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="⬅️ НАЗАД", callback_data="back_to_menu")]
                                ]))
            return

        # ── ИСПРАВЛЕНИЕ 1: расписание показываем по реальному району пользователя ──
        if product_id == 9:
            if district == "zaozernoe":
                text = (product.item_text_zaozernoe
                        or "📅 <b>Расписание Заозёрный</b>\n\n<i>Информация пока не добавлена.</i>")
            else:
                text = (product.item_text_ryabkovo
                        or "📅 <b>Расписание Рябково</b>\n\n<i>Информация пока не добавлена.</i>")
            await msg.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ НАЗАД", callback_data="back_to_menu")]
                ]),
                parse_mode="HTML",
            )
            return

        flavors  = (session.query(Flavor)
                    .filter_by(product_id=product_id, is_available=True, district=district)
                    .filter(Flavor.brand_id == None).all())
        text     = product.item_text or "Описание отсутствует"
        keyboard = []

        if flavors:
            total_pages = (len(flavors) - 1) // FLAVORS_PER_PAGE + 1
            page = max(0, min(page, total_pages - 1))
            page_flavors = flavors[page * FLAVORS_PER_PAGE:(page + 1) * FLAVORS_PER_PAGE]

            text += "\n\n🍬 <b>Доступные позиции:</b>\n"
            if total_pages > 1:
                text += f"<i>стр. {page + 1}/{total_pages}</i>\n"
            text += "\n"

            for fl in page_flavors:
                price_str = f"  —  <b>{fl.price} ₽</b>" if fl.price else ""
                photo_mark = " 📷" if fl.photo_file_id else ""
                kit_mark   = " 📚" if (product_id == 5 and fl.kit_info) else ""
                text += f"• {fl.name}{price_str}{photo_mark}{kit_mark}\n"
                btn_label = f"➕  {fl.name}" + (f"  |  {fl.price} ₽" if fl.price else "")
                row = [InlineKeyboardButton(text=btn_label, callback_data=f"add_to_cart_{fl.id}")]
                if fl.photo_file_id:
                    row.append(InlineKeyboardButton(text="📷", callback_data=f"showphoto:{fl.id}"))
                if product_id == 5 and fl.kit_info:
                    row.append(InlineKeyboardButton(text="📚Комплектация", callback_data=f"showkit:{fl.id}"))
                keyboard.append(row)

            # Навигация по страницам
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"dfpage_{product_id}_{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"dfpage_{product_id}_{page + 1}"))
            if nav_row:
                keyboard.append(nav_row)

            keyboard.append([InlineKeyboardButton(text="🛒 МОЯ КОРЗИНА", callback_data="view_cart")])
        else:
            manager = (
                config.config.SHOP_MANAGER_ZAOZERNOE
                if district == "zaozernoe"
                else config.config.SHOP_MANAGER
            )
            text += f"\n\n📞 Для заказа: @{manager}"

        keyboard.append([InlineKeyboardButton(text="⬅️ НАЗАД", callback_data="back_to_menu")])

        await msg.edit_text(text,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                            parse_mode="HTML")
    finally:
        session.close()


# ══════════════════════════════════════════
# ПОКАЗАТЬ ФОТО ПОЗИЦИИ
# ══════════════════════════════════════════

@router.callback_query(F.data.startswith("showphoto:"))
async def show_flavor_photo(callback: CallbackQuery):
    fl_id = int(callback.data.split(":")[1])
    session = Session()
    try:
        fl = session.query(Flavor).filter_by(id=fl_id).first()
        if fl and fl.photo_file_id:
            await callback.answer()
            price_str = f" — {fl.price} ₽" if fl.price else ""
            await callback.message.answer_photo(
                photo=fl.photo_file_id,
                caption=f"📷 <b>{fl.name}</b>{price_str}",
                parse_mode="HTML",
            )
        else:
            await callback.answer("📷 Фото нет", show_alert=True)
    finally:
        session.close()


# ══════════════════════════════════════════
# ПОКАЗАТЬ КОМПЛЕКТАЦИЮ POD-УСТРОЙСТВА
# ══════════════════════════════════════════

@router.callback_query(F.data.startswith("showkit:"))
async def show_flavor_kit(callback: CallbackQuery):
    fl_id = int(callback.data.split(":")[1])
    session = Session()
    try:
        fl = session.query(Flavor).filter_by(id=fl_id).first()
        if fl and fl.kit_info:
            await callback.answer()
            await callback.message.answer(
                f"📚 <b>Комплектация: {fl.name}</b>\n\n{fl.kit_info}",
                parse_mode="HTML",
            )
        else:
            await callback.answer("📚 Комплектация не указана", show_alert=True)
    finally:
        session.close()


# ══════════════════════════════════════════
# CALLBACK-ОБРАБОТЧИКИ ТОВАРОВ
# ══════════════════════════════════════════

async def _require_district(callback: CallbackQuery) -> str | None:
    district = await get_user_district(callback.from_user.id)
    if not district:
        await callback.answer("Сначала выберите район!", show_alert=True)
        try:
            await callback.message.edit_text(
                "🗺 <b>Выберите ваш район:</b>\n\nВаш район определяет доступные товары.",
                reply_markup=district_selection_kb(),
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                "🗺 <b>Выберите ваш район:</b>",
                reply_markup=district_selection_kb(),
                parse_mode="HTML",
            )
        return None
    return district


@router.callback_query(F.data.startswith("item_"))
async def show_item(callback: CallbackQuery):
    try:
        product_id = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка")
        return

    # ── ИСПРАВЛЕНИЕ 1: для расписания тоже получаем реальный район ──
    district = await _require_district(callback)
    if not district:
        return

    session = Session()
    try:
        session.add(ProductView(product_id=product_id, user_id=callback.from_user.id))
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()

    await callback.answer()
    await render_product_page(callback.message, product_id, district)


@router.callback_query(F.data.regexp(r"^brand_\d+_\d+$"))
async def show_brand_flavors_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        product_id = int(parts[1])
        brand_id   = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка")
        return

    district = await _require_district(callback)
    if not district:
        return

    await callback.answer()
    await render_brand_flavors(callback.message, product_id, brand_id, district)


# ── Листание: список брендов (пользовательская сторона) ──
@router.callback_query(F.data.startswith("bpage_"))
async def brand_page_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        product_id = int(parts[1])
        page       = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка"); return

    district = await _require_district(callback)
    if not district:
        return
    await callback.answer()
    await render_brand_list(callback.message, product_id, district, page)


# ── Листание: вкусы внутри бренда ──
@router.callback_query(F.data.startswith("flpage_"))
async def flavor_page_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        product_id = int(parts[1])
        brand_id   = int(parts[2])
        page       = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка"); return

    district = await _require_district(callback)
    if not district:
        return
    await callback.answer()
    await render_brand_flavors(callback.message, product_id, brand_id, district, page)


# ── Листание: прямые вкусы (без брендов) ──
@router.callback_query(F.data.startswith("dfpage_"))
async def direct_flavor_page_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    try:
        product_id = int(parts[1])
        page       = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка"); return

    district = await _require_district(callback)
    if not district:
        return
    await callback.answer()
    await render_direct_flavors(callback.message, product_id, district, page)


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.answer()
    district = await get_user_district(callback.from_user.id)
    await safe_show_menu(callback.message, district)


@router.callback_query(F.data == "how_to_order")
async def how_to_order(callback: CallbackQuery):
    await callback.answer()
    district = await get_user_district(callback.from_user.id)
    manager = (
        config.config.SHOP_MANAGER_ZAOZERNOE
        if district == "zaozernoe"
        else config.config.SHOP_MANAGER
    )
    text = (
        "❓ <b>КАК СДЕЛАТЬ ЗАКАЗ</b>\n\n"
        "1️⃣ Выберите <b>категорию</b> в меню\n\n"
        "2️⃣ Выберите <b>бренд</b> (для жидкостей, одноразок, сн*са)\n\n"
        "3️⃣ Нажмите <b>➕ на нужный вкус</b> — он добавится в корзину\n\n"
        "4️⃣ Откройте <b>🛒 МОЯ КОРЗИНА</b> — там все выбранные товары с ценами\n\n"
        "5️⃣ Нажмите <b>✅ ЗАКАЗАТЬ</b> и отправьте контакт (телефон или @username)\n\n"
        "6️⃣ Ожидайте — менеджер свяжется с вами!\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎁 <b>Скидка:</b> Приглашай друзей через реферальную систему\n"
        f"📞 <b>Поддержка:</b> @{manager}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ НАЗАД", callback_data="back_to_menu")],
        ]),
        parse_mode="HTML",
    )


# ══════════════════════════════════════════
# КОРЗИНА
# ══════════════════════════════════════════

@router.callback_query(F.data.startswith("add_to_cart_"))
async def add_to_cart(callback: CallbackQuery):
    try:
        flavor_id = int(callback.data.split("_")[3])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка")
        return

    district = await _require_district(callback)
    if not district:
        return

    session = Session()
    try:
        flavor = session.query(Flavor).filter_by(id=flavor_id).first()
        if not flavor:
            await callback.answer("❌ Позиция не найдена", show_alert=True)
            return

        # Проверяем что позиция принадлежит району пользователя
        if flavor.district and flavor.district != district:
            await callback.answer("❌ Эта позиция недоступна в вашем районе", show_alert=True)
            return

        product = session.query(Product).filter_by(id=flavor.product_id).first()
        if not product:
            await callback.answer("❌ Товар не найден", show_alert=True)
            return

        brand      = session.query(Brand).filter_by(id=flavor.brand_id).first() if flavor.brand_id else None
        brand_name = brand.name if brand else None
        product_id = product.id
        brand_id   = flavor.brand_id

        existing = session.query(CartItem).filter_by(
            user_id=callback.from_user.id, flavor_id=flavor_id).first()

        if existing:
            existing.quantity += 1
        else:
            session.add(CartItem(
                user_id=callback.from_user.id,
                flavor_id=flavor_id,
                product_id=product.id,
                product_name=product.item_name,
                brand_name=brand_name,
                flavor_name=flavor.name,
                price=flavor.price,
                quantity=1,
            ))
        session.commit()

    except Exception as e:
        session.rollback()
        print(f"add_to_cart error: {e}")
        await callback.answer("❌ Ошибка при добавлении", show_alert=True)
        return
    finally:
        session.close()

    price_str = f" ({flavor.price} ₽)" if flavor.price else ""
    await callback.answer(f"✅ Добавлено: {flavor.name}{price_str}", show_alert=True)

    if brand_id:
        await render_brand_flavors(callback.message, product_id, brand_id, district)
    else:
        await render_direct_flavors(callback.message, product_id, district)


@router.callback_query(F.data == "view_cart")
async def view_cart(callback: CallbackQuery, state: FSMContext):
    district = await _require_district(callback)
    if not district:
        return

    session = Session()
    try:
        items = session.query(CartItem).filter_by(user_id=callback.from_user.id).all()

        if not items:
            await callback.answer("🛒 Корзина пуста", show_alert=True)
            district = await get_user_district(callback.from_user.id)
            await safe_show_menu(callback.message, district)
            return

        total = 0
        text  = "🛒 <b>ВАША КОРЗИНА</b>\n\n"
        for item in items:
            brand_str    = f" · <i>{item.brand_name}</i>" if item.brand_name else ""
            price_unit   = f"  💰 {item.price} ₽/шт." if item.price else ""
            item_total   = (item.price * item.quantity) if item.price else None
            total_str    = f"  = <b>{item_total} ₽</b>" if item_total else ""
            text += f"📦 {item.product_name}{brand_str}\n"
            text += f"   🍬 {item.flavor_name}{price_unit}\n"
            text += f"   🔢 {item.quantity} шт.{total_str}   /del_{item.id}\n\n"
            if item_total:
                total += item_total

        user         = session.query(User).filter_by(user_id=callback.from_user.id).first()
        discount_pct = (user.referral_discount_pct or 0.0) if user else 0.0
        has_discount = discount_pct > 0

        fsm_data   = await state.get_data()
        promo_code = fsm_data.get("promo_code")
        promo_disc = fsm_data.get("promo_discount", 0)
        has_promo  = bool(promo_code)

        if total > 0:
            text += f"━━━━━━━━━━━━━━━━━━\n"
            text += f"💵 <b>Итого: {total} ₽</b>\n"
            if has_discount:
                save       = total * discount_pct / 100
                after_disc = total - save
                text += f"🎁 Скидка {discount_pct:.1f}%: <b>−{save:.0f} ₽</b>\n"
                text += f"✅ К оплате со скидкой: <b>{after_disc:.0f} ₽</b>\n"
            if has_promo:
                final = max(0, total - promo_disc)
                text += f"🎟 Промокод <b>{promo_code}</b>: −{promo_disc} ₽\n"
                text += f"✅ С промокодом: <b>{final} ₽</b>\n"
        text += "\nНажмите <b>ЗАКАЗАТЬ</b> для оформления."

        await callback.answer()
        await callback.message.edit_text(text, reply_markup=cart_keyboard(has_discount, has_promo), parse_mode="HTML")

    except Exception as e:
        print(f"view_cart error: {e}")
    finally:
        session.close()


@router.callback_query(F.data == "clear_cart")
async def clear_cart(callback: CallbackQuery, state: FSMContext):
    session = Session()
    try:
        session.query(CartItem).filter_by(user_id=callback.from_user.id).delete()
        session.commit()
        clear_reminder(callback.from_user.id)
        await state.update_data(promo_code=None, promo_discount=0)
        await callback.answer("🗑 Корзина очищена", show_alert=True)
        district = await get_user_district(callback.from_user.id)
        await safe_show_menu(callback.message, district)
    except Exception as e:
        session.rollback()
        print(f"clear_cart error: {e}")
    finally:
        session.close()


# ══════════════════════════════════════════
# ПРОМОКОД
# ══════════════════════════════════════════

@router.callback_query(F.data == "enter_promo")
async def enter_promo_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        await callback.message.edit_text(
            "🎟 <b>Введите промокод:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="view_cart")]
            ]),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer("🎟 Введите промокод:")
    await state.set_state(OrderStates.waiting_for_promo)


@router.message(OrderStates.waiting_for_promo)
async def process_promo(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    session = Session()
    try:
        promo = session.query(PromoCode).filter_by(code=code, is_active=True).first()
        if not promo:
            await message.answer(
                "❌ Промокод не найден или недействителен.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🛒 В корзину", callback_data="view_cart")]
                ]),
            )
            await state.clear()
            return

        if not promo.is_reusable:
            promo.is_active = False
            session.commit()

        await state.update_data(promo_code=promo.code, promo_discount=promo.discount_rub)
        await state.set_state(None)

        await message.answer(
            f"✅ <b>Промокод применён!</b>\n\n"
            f"🎟 <b>{promo.code}</b> — скидка <b>{promo.discount_rub} ₽</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛒 Перейти в корзину", callback_data="view_cart")]
            ]),
            parse_mode="HTML",
        )
    finally:
        session.close()


# ══════════════════════════════════════════
# ОФОРМЛЕНИЕ ЗАКАЗА
# ══════════════════════════════════════════

@router.callback_query(F.data.in_({"place_order_no_discount", "place_order_discount"}))
async def place_order_btn(callback: CallbackQuery, state: FSMContext):
    use_discount = (callback.data == "place_order_discount")
    fsm_data     = await state.get_data()
    promo_code   = fsm_data.get("promo_code")
    promo_disc   = fsm_data.get("promo_discount", 0)

    session = Session()
    try:
        items = session.query(CartItem).filter_by(user_id=callback.from_user.id).all()
        if not items:
            await callback.answer("🛒 Корзина пуста", show_alert=True)
            return

        user         = session.query(User).filter_by(user_id=callback.from_user.id).first()
        discount_pct = (user.referral_discount_pct or 0.0) if (use_discount and user) else 0.0

        total = 0
        text  = "📝 <b>ОФОРМЛЕНИЕ ЗАКАЗА</b>\n\n📋 <b>Состав:</b>\n"
        for item in items:
            brand_str = f" [{item.brand_name}]" if item.brand_name else ""
            price_str = f" — {item.price} ₽" if item.price else ""
            text += f"• {item.flavor_name}{brand_str} × {item.quantity}{price_str}\n"
            if item.price:
                total += item.price * item.quantity

        # ── ИСПРАВЛЕНИЕ 3: убираем двойной «итог» при одновременной скидке и промокоде ──
        if total > 0:
            text += f"\n💵 <b>Сумма: {total} ₽</b>"
            save = 0
            if discount_pct > 0:
                save = total * discount_pct / 100
                text += f"\n🎁 Скидка {discount_pct:.1f}%: −{save:.0f} ₽"
            if promo_code:
                text += f"\n🎟 Промокод <b>{promo_code}</b>: −{promo_disc} ₽"
            final = max(0, total - int(save) - promo_disc)
            text += f"\n✅ К оплате: <b>{final} ₽</b>"

        text += "\n\n📞 <b>Отправьте контакт (телефон или @username):</b>"

    finally:
        session.close()

    await state.update_data(use_discount=use_discount)
    await callback.answer()
    await callback.message.answer(text, parse_mode="HTML")
    await state.set_state(OrderStates.waiting_for_contact)


@router.message(OrderStates.waiting_for_contact)
async def process_order(message: Message, state: FSMContext):
    data         = await state.get_data()
    use_discount = data.get("use_discount", False)
    promo_code   = data.get("promo_code")
    promo_disc   = data.get("promo_discount", 0)
    await state.clear()
    contact = message.text.strip() if message.text else "—"

    session = Session()
    try:
        items = session.query(CartItem).filter_by(user_id=message.from_user.id).all()
        if not items:
            await message.answer("❌ Корзина пуста")
            return

        user = session.query(User).filter_by(user_id=message.from_user.id).first()

        # Страховка: заказ уходит курьеру района пользователя, поэтому позиции
        # другого района (остались в корзине от старой версии) в него попасть не должны.
        if user and user.district:
            foreign = []
            for it in items:
                fl = session.query(Flavor).filter_by(id=it.flavor_id).first()
                if fl and fl.district and fl.district != user.district:
                    foreign.append(it)
            if foreign:
                names = ", ".join(esc(f.flavor_name) for f in foreign)
                for it in foreign:
                    session.delete(it)
                session.commit()
                items = [i for i in items if i not in foreign]
                await message.answer(
                    f"⚠️ Из корзины убраны позиции другого района: {names}",
                    parse_mode="HTML",
                )
                if not items:
                    await message.answer("❌ Корзина пуста")
                    return

        discount_pct = 0.0
        if use_discount and user and user.referral_discount_pct:
            discount_pct = user.referral_discount_pct
            user.referral_discount_pct = 0.0

        district = user.district if user else None
        if not district:
            await message.answer(
                "❌ Ваш район не выбран. Пожалуйста, выберите район в профиле.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📍 Выбрать район", callback_data="profile_change_district")],
                ]),
                parse_mode="HTML",
            )
            return

        order = Order(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            contact_info=contact,
            status="pending",
            discount_pct=discount_pct,
            promo_code=promo_code,
            promo_discount=promo_disc if promo_code else 0,
            district=district,
        )
        session.add(order)
        session.flush()

        total       = 0
        admin_items = ""
        for item in items:
            session.add(OrderItem(
                order_id=order.id,
                product_name=item.product_name,
                brand_name=item.brand_name,
                flavor_name=item.flavor_name,
                price=item.price,
                quantity=item.quantity,
            ))
            if item.price:
                total += item.price * item.quantity
            brand_str  = f" [{item.brand_name}]" if item.brand_name else ""
            price_str  = f" — {item.price} ₽" if item.price else ""
            admin_items += f"  • {item.flavor_name}{brand_str} × {item.quantity}{price_str}\n"

        session.query(CartItem).filter_by(user_id=message.from_user.id).delete()
        session.commit()
        clear_reminder(message.from_user.id)
        order_id = order.id

        # Уведомление реферера
        ref = session.query(Referral).filter_by(
            referred_user_id=message.from_user.id, purchased=False).first()
        if ref:
            ref.purchased = True
            referrer = session.query(User).filter_by(user_id=ref.referrer_user_id).first()
            if referrer:
                referrer.referral_discount_pct = (referrer.referral_discount_pct or 0.0) + 0.5
                try:
                    await message.bot.send_message(
                        referrer.user_id,
                        f"🎉 <b>Реферальный бонус!</b>\n\n"
                        f"Твой друг совершил первую покупку!\n"
                        f"<b>+0.5%</b> к твоей скидке 🎁\n"
                        f"Итого накоплено: <b>{referrer.referral_discount_pct:.1f}%</b>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            session.commit()

        # Уведомление администраторам
        district_str = f"\n📍 Район: <b>{DISTRICT_NAMES.get(district, district or '—')}</b>" if district else ""
        total_str    = f"\n💵 Сумма: {total} ₽" if total else ""
        disc_str     = f"\n🎁 Скидка: {discount_pct:.1f}%" if discount_pct > 0 else ""
        promo_str    = f"\n🎟 Промокод: <b>{promo_code}</b> (−{promo_disc} ₽)" if promo_code else ""

        # Курьеры получают ТОЛЬКО заказы своего района; суперадмины — все.
        for admin_id in order_recipients(district):
            admin_cmd = "/admin1" if district == "zaozernoe" else "/admin"
            try:
                await message.bot.send_message(
                    admin_id,
                    f"🆕 <b>НОВЫЙ ЗАКАЗ #{order_id}</b>\n\n"
                    f"👤 @{message.from_user.username or message.from_user.id}\n"
                    f"📞 {esc(contact)}"
                    f"{district_str}\n"
                    f"📋 Состав:\n{admin_items}"
                    f"{total_str}{disc_str}{promo_str}\n\n"
                    f"📦 {admin_cmd} для управления",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        disc_msg  = f"\n🎁 Скидка {discount_pct:.1f}% применена!" if discount_pct > 0 else ""
        promo_msg = f"\n🎟 Промокод применён: −{promo_disc} ₽" if promo_code else ""
        await message.answer(
            f"✅ <b>Заказ #{order_id} оформлен!</b>{disc_msg}{promo_msg}\n\nМенеджер свяжется с вами.\nСтатус: /myorders",
            parse_mode="HTML",
        )
        await message.answer(
            menu_text(district),
            reply_markup=main_menu_kb(), parse_mode="HTML",
        )

    except Exception as e:
        session.rollback()
        print(f"process_order error: {e}")
        await message.answer(f"❌ Ошибка при оформлении: {e}")
    finally:
        session.close()


# ══════════════════════════════════════════
# УДАЛЕНИЕ ИЗ КОРЗИНЫ
# ══════════════════════════════════════════

@router.message(lambda m: m.text and m.text.startswith("/del_"))
async def remove_from_cart(message: Message):
    try:
        item_id = int(message.text.split("_")[1])
    except (IndexError, ValueError):
        await message.answer("❌ Используйте: /del_123")
        return

    session = Session()
    try:
        item = session.query(CartItem).filter_by(id=item_id, user_id=message.from_user.id).first()
        if not item:
            await message.answer("❌ Позиция не найдена")
            return
        session.delete(item)
        session.commit()

        remaining = session.query(CartItem).filter_by(user_id=message.from_user.id).all()
        if not remaining:
            await message.answer(
                "✅ Удалено. Корзина пуста.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ В МЕНЮ", callback_data="back_to_menu")],
                ]),
            )
        else:
            total = 0
            text  = "✅ Удалено.\n\n🛒 <b>ВАША КОРЗИНА</b>\n\n"
            for i in remaining:
                brand_str  = f" · <i>{i.brand_name}</i>" if i.brand_name else ""
                price_unit = f"  💰 {i.price} ₽/шт." if i.price else ""
                item_tot   = (i.price * i.quantity) if i.price else None
                tot_str    = f"  = <b>{item_tot} ₽</b>" if item_tot else ""
                text += f"📦 {i.product_name}{brand_str}\n"
                text += f"   🍬 {i.flavor_name}{price_unit}\n"
                text += f"   🔢 {i.quantity} шт.{tot_str}   /del_{i.id}\n\n"
                if item_tot:
                    total += item_tot

            user         = session.query(User).filter_by(user_id=message.from_user.id).first()
            discount_pct = (user.referral_discount_pct or 0.0) if user else 0.0
            has_discount = discount_pct > 0

            if total > 0:
                text += f"━━━━━━━━━━━━━━━━━━\n"
                text += f"💵 <b>Итого: {total} ₽</b>\n"
                if has_discount:
                    save = total * discount_pct / 100
                    text += f"🎁 Скидка {discount_pct:.1f}%: −{save:.0f} ₽\n"
            text += "Нажмите <b>ЗАКАЗАТЬ</b> для оформления."

            await message.answer(text, parse_mode="HTML", reply_markup=cart_keyboard(has_discount))
    except Exception as e:
        session.rollback()
        print(f"remove_from_cart error: {e}")
        await message.answer("❌ Ошибка при удалении")
    finally:
        session.close()
