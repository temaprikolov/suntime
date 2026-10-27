#!/usr/bin/env python3
"""
Одноразовый скрипт: импортирует пользователей из users.csv в базу данных.
Запускать из папки проекта (там где main.py):

    python3 migrate_users_now.py

Безопасен при повторном запуске — дубликаты пропускаются (INSERT OR IGNORE).
"""

import csv
import sqlite3
from datetime import datetime

CSV_FILE = "users.csv"
DB_PATH  = "data/database.db"


def main():
    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Убеждаемся, что все нужные колонки есть (на случай старой схемы)
    for col_def in [
        "nickname VARCHAR(100)",
        "avatar_file_id VARCHAR(300)",
        "referral_code VARCHAR(20)",
        "referral_discount_pct FLOAT DEFAULT 0.0",
    ]:
        col_name = col_def.split()[0]
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # колонка уже есть

    inserted = skipped = errors = 0

    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Поддерживаем русские заголовки Telegram-экспорта
                raw_id = (
                    row.get("user_id") or row.get("User ID") or row.get("id") or
                    row.get("ID пользователя") or ""
                ).strip()

                if not raw_id or not raw_id.isdigit():
                    skipped += 1
                    continue

                user_id    = int(raw_id)
                username   = (row.get("username") or row.get("Username") or
                              row.get("Короткое имя пользователя") or "").strip() or None
                first_name = (row.get("first_name") or row.get("First Name") or
                              row.get("Имя пользователя") or "").strip() or None
                last_name  = (row.get("last_name") or row.get("Last Name") or
                              row.get("Фамилия пользователя") or "").strip() or None

                cursor.execute(
                    """
                    INSERT OR IGNORE INTO users
                        (user_id, username, first_name, last_name, joined_at, is_admin)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (user_id, username, first_name, last_name, datetime.now()),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    skipped += 1  # уже был в базе

            except Exception as e:
                print(f"⚠️  Ошибка в строке {row}: {e}")
                errors += 1

    conn.commit()
    conn.close()

    total = cursor.lastrowid  # просто для отчёта
    print(f"\n✅ Готово!")
    print(f"   Добавлено новых: {inserted}")
    print(f"   Пропущено (уже были): {skipped}")
    print(f"   Ошибок: {errors}")

    # Итоговый счётчик в БД
    conn2 = sqlite3.connect(DB_PATH)
    count = conn2.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn2.close()
    print(f"   Всего пользователей в БД теперь: {count}")


if __name__ == "__main__":
    main()
