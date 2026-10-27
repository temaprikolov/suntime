import sqlite3
import os

def check_database():
    """Проверка содержимого базы данных"""
    
    db_path = 'data/database.db'
    
    if not os.path.exists(db_path):
        print("❌ База данных не найдена!")
        print("Сначала запустите бота: python main.py")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Проверяем таблицу users
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cursor.fetchone():
        print("❌ Таблица users не существует!")
        conn.close()
        return
    
    # Считаем пользователей
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    print(f"📊 Пользователей в базе: {count}")
    
    # Показываем первых 10 пользователей
    if count > 0:
        print("\n👥 Первые 10 пользователей:")
        cursor.execute("SELECT user_id, username, first_name, last_name FROM users LIMIT 10")
        users = cursor.fetchall()
        
        for i, user in enumerate(users, 1):
            user_id, username, first_name, last_name = user
            print(f"{i}. ID: {user_id}, Username: @{username}, Имя: {first_name} {last_name}")
    
    conn.close()

if __name__ == "__main__":
    check_database()
