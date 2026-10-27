#!/usr/bin/env python3
import asyncio
import sys
import os

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

import config
from database import init_db, init_market_db
from reminder import reminder_loop


async def _retry(coro_factory, what: str, attempts: int = 5, delay: float = 3.0):
    """Повторяет сетевой вызов к Telegram, чтобы временный таймаут не убивал бота."""
    for i in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            print(f"⚠️  {what}: попытка {i}/{attempts} не удалась: {e}")
            if i == attempts:
                raise
            await asyncio.sleep(delay * i)


async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="start",    description="🏠 Главное меню"),
        BotCommand(command="myid",     description="🆔 Мой Telegram ID"),
        BotCommand(command="myorders", description="📜 Мои заказы"),
        BotCommand(command="order",    description="🛒 Оформить заказ"),
        BotCommand(command="profile",  description="👤 Мой профиль"),
    ]
    await bot.set_my_commands(commands)


async def main():
    print("🚀 Запуск бота SUNTIME SHOP...")

    if not config.config.BOT_TOKEN:
        print("❌ BOT_TOKEN не задан! Укажите его в файле .env (см. .env.example / README).")
        sys.exit(1)

    os.makedirs("data", exist_ok=True)
    init_db()
    init_market_db()
    print("✅ База данных готова")

    from aiogram.client.session.aiohttp import AiohttpSession
    bot = Bot(token=config.config.BOT_TOKEN, session=AiohttpSession(timeout=90))

    # Персистентное FSM-хранилище — стейты НЕ слетают при перезапуске
    from fsm_storage import SimpleSqliteStorage
    storage = SimpleSqliteStorage("data/fsm_states.db")
    print("✅ FSM: SqliteStorage (персистентный)")

    dp = Dispatcher(storage=storage)

    # Закрытый доступ: все, кроме ALLOWED_USER_IDS, молча игнорируются.
    # Регистрируется первой, чтобы сработать раньше любых других middleware и хендлеров.
    from access_guard import AccessGuardMiddleware, ALLOWED_USER_IDS
    dp.update.outer_middleware(AccessGuardMiddleware())
    print(f"🔒 Доступ только для ID: {sorted(ALLOWED_USER_IDS)}")

    from block_check import BlockCheckMiddleware
    dp.message.middleware(BlockCheckMiddleware())
    dp.callback_query.middleware(BlockCheckMiddleware())

    from handlers import router as handlers_router
    from market   import market_router
    from admin    import router as admin_router

    dp.include_router(admin_router)
    dp.include_router(market_router)
    dp.include_router(handlers_router)

    # Меню команд — не критично: если Telegram не ответил, бот всё равно стартует
    try:
        await _retry(lambda: set_commands(bot), "set_my_commands", attempts=3)
    except Exception as e:
        print(f"⚠️  Не удалось установить команды меню, продолжаю без этого: {e}")

    me = await _retry(lambda: bot.get_me(), "get_me")
    print(f"✅ Бот готов к работе! @{me.username}")
    print(f"⚠️  Админ-команды /admin и /admin1 скрыты из меню, но доступны при вводе вручную")

    asyncio.create_task(reminder_loop(bot))

    try:
        await dp.start_polling(bot)
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        raise
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
        sys.exit(0)
