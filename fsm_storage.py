"""
Простое персистентное FSM-хранилище на базе SQLite.
Не требует внешних зависимостей — только стандартная библиотека
(sqlite3 + threading). aiosqlite НЕ используется (раньше в докстринге
было написано обратное — это было неверно и вводило в заблуждение).

Стейты сохраняются между перезапусками бота.

ВАЖНО: sqlite3 из стандартной библиотеки — синхронный/блокирующий.
aiogram дёргает get_state()/get_data() на КАЖДОЕ входящее сообщение
и callback, поэтому все обращения к файлу БД выполняются в отдельном
потоке через asyncio.to_thread — иначе каждое открытие соединения
с диском блокировало бы event loop бота целиком (и на время такого
обращения бот не мог бы обрабатывать других пользователей одновременно).
"""
import asyncio
import json
import sqlite3
import threading
from typing import Any, Dict, Optional

from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType


class SimpleSqliteStorage(BaseStorage):
    """
    FSM-хранилище на SQLite. Блокирующие операции с БД выполняются в
    отдельном потоке (asyncio.to_thread), поэтому event loop не подвисает.
    threading.Lock защищает от гонок между потоками при одновременных
    обращениях к файлу БД.
    Работает надёжно при одном процессе (что и есть на Beget/screen).
    """

    def __init__(self, db_path: str = "data/fsm_states.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            con = sqlite3.connect(self._db_path)
            con.execute("""
                CREATE TABLE IF NOT EXISTS fsm (
                    key     TEXT PRIMARY KEY,
                    state   TEXT,
                    data    TEXT NOT NULL DEFAULT '{}'
                )
            """)
            con.commit()
            con.close()

    def _key(self, key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}"

    def _get_row(self, k: str) -> Optional[Dict]:
        con = sqlite3.connect(self._db_path)
        try:
            row = con.execute("SELECT state, data FROM fsm WHERE key=?", (k,)).fetchone()
        finally:
            con.close()
        if row:
            return {"state": row[0], "data": json.loads(row[1])}
        return None

    def _upsert(self, k: str, state: Optional[str], data: Dict):
        con = sqlite3.connect(self._db_path)
        try:
            con.execute("""
                INSERT INTO fsm(key, state, data) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET state=excluded.state, data=excluded.data
            """, (k, state, json.dumps(data)))
            con.commit()
        finally:
            con.close()

    # ── Синхронные версии, вызываются из потока через asyncio.to_thread ──

    def _set_state_sync(self, key: StorageKey, state: StateType):
        with self._lock:
            k = self._key(key)
            row = self._get_row(k)
            data = row["data"] if row else {}
            st = state.state if hasattr(state, "state") else state
            self._upsert(k, st, data)

    def _get_state_sync(self, key: StorageKey) -> Optional[str]:
        with self._lock:
            row = self._get_row(self._key(key))
            return row["state"] if row else None

    def _set_data_sync(self, key: StorageKey, data: Dict[str, Any]):
        with self._lock:
            k = self._key(key)
            row = self._get_row(k)
            st = row["state"] if row else None
            self._upsert(k, st, data)

    def _get_data_sync(self, key: StorageKey) -> Dict[str, Any]:
        with self._lock:
            row = self._get_row(self._key(key))
            return row["data"] if row else {}

    # ── Асинхронный интерфейс BaseStorage ──

    async def set_state(self, key: StorageKey, state: StateType = None):
        await asyncio.to_thread(self._set_state_sync, key, state)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return await asyncio.to_thread(self._get_state_sync, key)

    async def set_data(self, key: StorageKey, data: Dict[str, Any]):
        await asyncio.to_thread(self._set_data_sync, key, data)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_data_sync, key)

    async def close(self):
        pass
