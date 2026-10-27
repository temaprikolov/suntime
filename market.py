#!/usr/bin/env python3
"""
Модуль «Рынок» — доска объявлений Б/У товаров.
"""

import config
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    Message,
)
from database import Session, MarketListing
from datetime import datetime
from typing import Optional
from textutils import esc

market_router = Router()

PAYMENT_CARD  = "2200 7021 8686 2863"
LISTING_PRICE = 70

print("✅ market.py загружен")


# ══════════════════════════════════════════
# FSM
# ══════════════════════════════════════════

class MarketStates(StatesGroup):
    waiting_photo       = State()
    waiting_title       = State()
    waiting_description = State()
    waiting_price       = State()
    waiting_district    = State()
    waiting_contact     = State()
    waiting_payment     = State()


# ══════════════════════════════════════════
# КЛАВИАТУРЫ
# ══════════════════════════════════════════

def market_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Смотреть объявления", callback_data="market_browse:0")],
        [InlineKeyboardButton(text="➕ Создать объявление",  callback_data="market_create")],
        [InlineKeyboardButton(text="📋 Мои объявления",     callback_data="market_my:0")],
        [InlineKeyboardButton(text="⬅️ Назад",              callback_data="back_to_menu")],
    ])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="market_main")],
    ])

def _listing_card_kb(
    page: int,
    total: int,
    seller_username: Optional[str],
    seller_id: int,
    viewer_id: int,
    listing_id: int,
    listing_user_id: int,
) -> InlineKeyboardMarkup:
    """Клавиатура под карточкой объявления."""
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"market_browse:{page - 1}"))
    if page < total - 1:
        nav_row.append(InlineKeyboardButton(text="▶️ След.", callback_data=f"market_browse:{page + 1}"))

    rows = []
    if nav_row:
        rows.append(nav_row)

    if viewer_id == listing_user_id:
        # Это объявление владельца — даём удалить, редактировать нельзя
        rows.append([InlineKeyboardButton(
            text="🗑 Удалить моё объявление",
            callback_data=f"market_browse_delete:{listing_id}:{page}",
        )])
    else:
        # Кнопка «Написать» — открывает диалог с продавцом
        if seller_username:
            rows.append([InlineKeyboardButton(
                text="✉️ Написать продавцу",
                url=f"https://t.me/{seller_username}",
            )])
        else:
            rows.append([InlineKeyboardButton(
                text="✉️ Написать продавцу",
                url=f"tg://user?id={seller_id}",
            )])

    rows.append([InlineKeyboardButton(text="⬅️ Рынок", callback_data="market_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ══════════════════════════════════════════
# ТОЧКА ВХОДА
# ══════════════════════════════════════════

@market_router.message(F.text == "🛍 Рынок")
async def market_entry(message: Message, state: FSMContext):
    await state.clear()
    session = Session()
    try:
        cnt = session.query(MarketListing).filter_by(status="active").count()
    finally:
        session.close()
    await message.answer(
        "🛍 <b>РЫНОК Б/У ТОВАРОВ</b>\n\n"
        f"Сейчас на рынке <b>{cnt}</b> объявлений.\n"
        f"Размещение — <b>{LISTING_PRICE} ₽</b>.",
        reply_markup=market_main_kb(),
        parse_mode="HTML",
    )


@market_router.callback_query(F.data == "market_main")
async def market_main_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    session = Session()
    try:
        cnt = session.query(MarketListing).filter_by(status="active").count()
    finally:
        session.close()
    text = (
        "🛍 <b>РЫНОК Б/У ТОВАРОВ</b>\n\n"
        f"Сейчас на рынке <b>{cnt}</b> объявлений.\n"
        f"Размещение — <b>{LISTING_PRICE} ₽</b>."
    )
    # Если предыдущее сообщение — фото, edit_text не работает → удаляем и шлём новое
    try:
        await callback.message.edit_text(text, reply_markup=market_main_kb(), parse_mode="HTML")
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=market_main_kb(), parse_mode="HTML")


# ══════════════════════════════════════════
# ЛИСТАЛКА ОБЪЯВЛЕНИЙ
# ══════════════════════════════════════════

@market_router.callback_query(F.data.startswith("market_browse:"))
async def market_browse(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    await callback.answer()
    await _render_browse(callback, page)


async def _render_browse(callback: CallbackQuery, page: int):
    session = Session()
    try:
        listings = (session.query(MarketListing)
                    .filter_by(status="active")
                    .order_by(MarketListing.confirmed_at.desc())
                    .all())

        if not listings:
            no_items_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать объявление", callback_data="market_create")],
                [InlineKeyboardButton(text="⬅️ Рынок",             callback_data="market_main")],
            ])
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(
                "📭 <b>Объявлений пока нет.</b>\n\nБудьте первым!",
                reply_markup=no_items_kb,
                parse_mode="HTML",
            )
            return

        total = len(listings)
        page  = max(0, min(page, total - 1))
        lst   = listings[page]

        caption = (
            f"📦 <b>{esc(lst.title)}</b>\n\n"
            f"📝 {esc(lst.description)}\n\n"
            f"💰 <b>Цена:</b> {esc(lst.price)}\n"
            f"📍 <b>Район:</b> {esc(lst.district)}\n"
            f"📞 <b>Контакт:</b> {esc(lst.contact)}\n\n"
            f"<i>Объявление {page + 1} из {total}</i>"
        )
        kb = _listing_card_kb(
            page, total,
            lst.username, lst.user_id,
            callback.from_user.id,
            lst.id, lst.user_id,
        )

        # ВСЕГДА удаляем старое сообщение и шлём новое — иначе фото не меняется при листании
        try:
            await callback.message.delete()
        except Exception:
            pass

        if lst.photo_file_id:
            await callback.message.answer_photo(
                photo=lst.photo_file_id,
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML",
            )
        else:
            await callback.message.answer(caption, reply_markup=kb, parse_mode="HTML")

    finally:
        session.close()


@market_router.callback_query(F.data.startswith("market_browse_delete:"))
async def market_browse_delete(callback: CallbackQuery):
    """Владелец удаляет своё объявление прямо из листалки рынка."""
    parts      = callback.data.split(":")
    listing_id = int(parts[1])
    cur_page   = int(parts[2]) if len(parts) > 2 else 0

    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if not lst or lst.user_id != callback.from_user.id:
            await callback.answer("❌ Объявление не найдено", show_alert=True)
            return
        title = lst.title
        session.delete(lst)
        session.commit()
    finally:
        session.close()

    await callback.answer(f"🗑 «{title[:30]}» удалено.", show_alert=True)
    await _render_browse(callback, cur_page)


# ══════════════════════════════════════════
# МОИ ОБЪЯВЛЕНИЯ — список + удаление
# ══════════════════════════════════════════

async def _render_my_listings(callback: CallbackQuery, page: int):
    """Отрисовывает список «Мои объявления» для указанной страницы."""
    PER_PAGE = 5
    STATUS_LABELS = {
        "pending":  "⏳ На проверке",
        "active":   "✅ Активно",
        "rejected": "❌ Отклонено",
    }

    session = Session()
    try:
        listings = (session.query(MarketListing)
                    .filter_by(user_id=callback.from_user.id)
                    .order_by(MarketListing.created_at.desc())
                    .all())

        if not listings:
            no_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать объявление", callback_data="market_create")],
                [InlineKeyboardButton(text="⬅️ Рынок",             callback_data="market_main")],
            ])
            try:
                await callback.message.edit_text("📭 <b>У вас нет объявлений.</b>",
                                                 reply_markup=no_kb, parse_mode="HTML")
            except Exception:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await callback.message.answer("📭 <b>У вас нет объявлений.</b>",
                                              reply_markup=no_kb, parse_mode="HTML")
            return

        total_pages = (len(listings) - 1) // PER_PAGE + 1
        page = max(0, min(page, total_pages - 1))
        page_items = listings[page * PER_PAGE:(page + 1) * PER_PAGE]

        text = f"📋 <b>МОИ ОБЪЯВЛЕНИЯ</b>  (стр. {page + 1}/{total_pages})\n\n"
        kb_rows = []

        for lst in page_items:
            status = STATUS_LABELS.get(lst.status, lst.status)
            text += (
                f"<b>{esc(lst.title)}</b>\n"
                f"   💰 {esc(lst.price)}  📍 {esc(lst.district)}\n"
                f"   {status}  ·  🕐 {lst.created_at.strftime('%d.%m.%Y')}\n\n"
            )
            kb_rows.append([InlineKeyboardButton(
                text=f"🗑 Удалить «{lst.title[:20]}»",
                callback_data=f"market_my_delete:{lst.id}:{page}",
            )])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"market_my:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"market_my:{page + 1}"))
        if nav_row:
            kb_rows.append(nav_row)

        kb_rows.append([InlineKeyboardButton(text="⬅️ Рынок", callback_data="market_main")])

        try:
            await callback.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                parse_mode="HTML",
            )
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                parse_mode="HTML",
            )
    finally:
        session.close()


@market_router.callback_query(F.data.startswith("market_my:"))
async def market_my(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    await callback.answer()
    await _render_my_listings(callback, page)


@market_router.callback_query(F.data.startswith("market_my_delete:"))
async def market_my_delete(callback: CallbackQuery):
    """Пользователь удаляет своё объявление (можно удалить на любом статусе, редактировать нельзя)."""
    parts      = callback.data.split(":")
    listing_id = int(parts[1])
    back_page  = int(parts[2]) if len(parts) > 2 else 0

    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()

        # Защита: удалять может только владелец объявления
        if not lst or lst.user_id != callback.from_user.id:
            await callback.answer("❌ Объявление не найдено", show_alert=True)
            return

        title = lst.title
        session.delete(lst)
        session.commit()
    finally:
        session.close()

    await callback.answer(f"🗑 Объявление «{title[:30]}» удалено.", show_alert=True)
    await _render_my_listings(callback, back_page)


# ══════════════════════════════════════════
# СОЗДАНИЕ ОБЪЯВЛЕНИЯ — FSM
# ══════════════════════════════════════════

@market_router.callback_query(F.data == "market_create")
async def market_create_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "📸 <b>Создание объявления</b>\n\n"
        "<b>Шаг 1 из 6</b> — отправьте фото товара.",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await state.set_state(MarketStates.waiting_photo)


@market_router.message(MarketStates.waiting_photo)
async def market_get_photo(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer(
            "❌ Нужно отправить <b>фото</b>! Попробуйте ещё раз.",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )
        return
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await message.answer(
        "✅ Фото получено!\n\n<b>Шаг 2 из 6</b> — введите <b>название товара</b>:",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await state.set_state(MarketStates.waiting_title)


@market_router.message(MarketStates.waiting_title)
async def market_get_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()[:200]
    if not title:
        await message.answer("❌ Название не может быть пустым.", reply_markup=cancel_kb())
        return
    await state.update_data(title=title)
    await message.answer(
        f"✅ Название: <b>{title}</b>\n\n<b>Шаг 3 из 6</b> — введите <b>описание</b>:",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await state.set_state(MarketStates.waiting_description)


@market_router.message(MarketStates.waiting_description)
async def market_get_description(message: Message, state: FSMContext):
    desc = (message.text or "").strip()
    if not desc:
        await message.answer("❌ Описание не может быть пустым.", reply_markup=cancel_kb())
        return
    await state.update_data(description=desc)
    await message.answer(
        "<b>Шаг 4 из 6</b> — укажите <b>цену</b> (например: 500 руб. / договорная):",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await state.set_state(MarketStates.waiting_price)


@market_router.message(MarketStates.waiting_price)
async def market_get_price(message: Message, state: FSMContext):
    price = (message.text or "").strip()[:100]
    if not price:
        await message.answer("❌ Укажите цену.", reply_markup=cancel_kb())
        return
    await state.update_data(price=price)
    await message.answer(
        "<b>Шаг 5 из 6</b> — укажите <b>район</b> (Рябково / Заозёрный / Центр и т.д.):",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await state.set_state(MarketStates.waiting_district)


@market_router.message(MarketStates.waiting_district)
async def market_get_district(message: Message, state: FSMContext):
    district = (message.text or "").strip()[:100]
    if not district:
        await message.answer("❌ Укажите район.", reply_markup=cancel_kb())
        return
    await state.update_data(district=district)
    await message.answer(
        "<b>Шаг 6 из 6</b> — укажите <b>контакт</b> для связи (телефон или @username):",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await state.set_state(MarketStates.waiting_contact)


@market_router.message(MarketStates.waiting_contact)
async def market_get_contact(message: Message, state: FSMContext):
    contact = (message.text or "").strip()[:300]
    if not contact:
        await message.answer("❌ Укажите контакт.", reply_markup=cancel_kb())
        return
    await state.update_data(contact=contact)
    data = await state.get_data()

    # Предпросмотр с фото
    preview = (
        f"📦 <b>{data['title']}</b>\n\n"
        f"📝 {data['description']}\n\n"
        f"💰 <b>Цена:</b> {data['price']}\n"
        f"📍 <b>Район:</b> {data['district']}\n"
        f"📞 <b>Контакт:</b> {data['contact']}"
    )
    await message.answer_photo(
        photo=data["photo_file_id"],
        caption=f"👀 <b>Предпросмотр объявления:</b>\n\n{preview}",
        parse_mode="HTML",
    )
    await message.answer(
        f"💳 <b>Оплата размещения</b>\n\n"
        f"Стоимость: <b>{LISTING_PRICE} ₽</b>\n\n"
        f"Переведите на карту:\n<code>{PAYMENT_CARD}</code>\n\n"
        f"Затем отправьте <b>скриншот оплаты</b> — "
        f"администратор проверит и опубликует объявление.",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await state.set_state(MarketStates.waiting_payment)


@market_router.message(MarketStates.waiting_payment)
async def market_get_payment(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer(
            "❌ Отправьте <b>скриншот оплаты</b> (фото).",
            reply_markup=cancel_kb(),
            parse_mode="HTML",
        )
        return

    payment_file_id = message.photo[-1].file_id
    data = await state.get_data()
    await state.clear()

    session = Session()
    try:
        listing = MarketListing(
            user_id       = message.from_user.id,
            username      = message.from_user.username,
            first_name    = message.from_user.first_name,
            contact       = data["contact"],
            title         = data["title"],
            description   = data["description"],
            price         = data["price"],
            district      = data["district"],
            photo_file_id = data.get("photo_file_id"),
            payment_photo = payment_file_id,
            status        = "pending",
        )
        session.add(listing)
        session.commit()
        listing_id = listing.id
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Ошибка сохранения: {e}")
        return
    finally:
        session.close()

    await message.answer(
        "✅ <b>Объявление отправлено на проверку!</b>\n\n"
        "После подтверждения оплаты администратором оно появится на рынке.",
        parse_mode="HTML",
    )

    # Уведомления всем админам
    user_link = f"@{message.from_user.username}" if message.from_user.username else f"id:{message.from_user.id}"
    admin_text = (
        f"🛍 <b>НОВОЕ ОБЪЯВЛЕНИЕ #{listing_id}</b>\n\n"
        f"👤 {user_link}  ·  ID: <code>{message.from_user.id}</code>\n"
        f"📞 Контакт: {data['contact']}\n\n"
        f"📦 <b>{data['title']}</b>\n"
        f"📝 {data['description']}\n"
        f"💰 {data['price']}\n"
        f"📍 {data['district']}"
    )
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"market_confirm:{listing_id}"),
        InlineKeyboardButton(text="❌ Отклонить",   callback_data=f"market_reject:{listing_id}"),
    ]])

    for admin_id in config.config.ADMINS:
        try:
            await message.bot.send_photo(
                admin_id,
                photo=data["photo_file_id"],
                caption=f"🖼 Фото товара — объявление #{listing_id}",
            )
            await message.bot.send_photo(
                admin_id,
                photo=payment_file_id,
                caption=f"💳 Скрин оплаты — объявление #{listing_id}",
            )
            await message.bot.send_message(
                admin_id,
                admin_text,
                reply_markup=confirm_kb,
                parse_mode="HTML",
            )
        except Exception as ex:
            print(f"market notify admin {admin_id}: {ex}")


# ══════════════════════════════════════════
# ПОДТВЕРЖДЕНИЕ / ОТКЛОНЕНИЕ (ADMIN)
# ══════════════════════════════════════════

@market_router.callback_query(F.data.startswith("market_confirm:"))
async def market_confirm(callback: CallbackQuery):
    listing_id = int(callback.data.split(":")[1])
    if callback.from_user.id not in config.config.ADMINS:
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if not lst:
            await callback.answer("❌ Не найдено", show_alert=True)
            return
        if lst.status != "pending":
            await callback.answer(f"Уже: {lst.status}", show_alert=True)
            return
        lst.status       = "active"
        lst.confirmed_at = datetime.now()
        lst.confirmed_by = callback.from_user.id
        session.commit()
        user_id = lst.user_id
    finally:
        session.close()

    await callback.answer("✅ Опубликовано!")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(f"✅ Объявление #{listing_id} опубликовано на рынке.")

    try:
        await callback.bot.send_message(
            user_id,
            f"🎉 <b>Ваше объявление #{listing_id} опубликовано!</b>\n\n"
            f"Его видят все покупатели в разделе «🛍 Рынок».",
            parse_mode="HTML",
        )
    except Exception:
        pass


@market_router.callback_query(F.data.startswith("market_reject:"))
async def market_reject(callback: CallbackQuery):
    listing_id = int(callback.data.split(":")[1])
    if callback.from_user.id not in config.config.ADMINS:
        await callback.answer("⛔ Нет прав", show_alert=True)
        return

    session = Session()
    try:
        lst = session.query(MarketListing).filter_by(id=listing_id).first()
        if not lst:
            await callback.answer("❌ Не найдено", show_alert=True)
            return
        if lst.status != "pending":
            await callback.answer(f"Уже: {lst.status}", show_alert=True)
            return
        lst.status = "rejected"
        session.commit()
        user_id = lst.user_id
    finally:
        session.close()

    await callback.answer("❌ Отклонено.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(f"❌ Объявление #{listing_id} отклонено.")

    try:
        await callback.bot.send_message(
            user_id,
            f"😔 <b>Объявление #{listing_id} отклонено.</b>\n\n"
            f"Свяжитесь с администратором для уточнения причины.",
            parse_mode="HTML",
        )
    except Exception:
        pass
