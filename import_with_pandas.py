import pandas as pd
import sqlite3
from datetime import datetime
import os

def import_with_pandas(excel_file='user.xlsx'):
    """Импорт пользователей из Excel файла с помощью pandas"""
    
    db_path = 'data/database.db'
    
    if not os.path.exists(db_path):
        print("❌ Сначала запустите бота для создания базы данных!")
        return
    
    if not os.path.exists(excel_file):
        print(f"❌ Файл {excel_file} не найден!")
        return
    
    # Читаем Excel файл
    try:
        df = pd.read_excel(excel_file)
        print(f"📁 Загружен файл: {excel_file}")
        print(f"📊 Количество строк: {len(df)}")
        print(f"📋 Колонки: {list(df.columns)}")
        
        # Показываем первые строки для проверки
        print("\n🔍 Первые 5 строк файла:")
        print(df.head())
        
    except Exception as e:
        print(f"❌ Ошибка чтения Excel файла: {e}")
        return
    
    # Подключаемся к базе данных
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    users_imported = 0
    users_updated = 0
    
    for index, row in df.iterrows():
        try:
            # Определяем названия колонок в вашем файле
            # Возможные варианты (настройте под ваш файл):
            user_id = None
            
            # Пробуем разные варианты названий колонок
            for col_name in ['user_id', 'User ID', 'userid', 'ID', 'id', 'telegram_id']:
                if col_name in df.columns:
                    user_id = int(row[col_name])
                    break
            
            if user_id is None:
                print(f"⚠️ Строка {index}: не найден user_id")
                continue
            
            # Получаем остальные данные
            username = ''
            for col_name in ['username', 'Username', 'user', 'nickname']:
                if col_name in df.columns:
                    username = str(row[col_name]) if pd.notna(row[col_name]) else ''
                    break
            
            first_name = ''
            for col_name in ['first_name', 'First Name', 'firstname', 'Имя', 'name']:
                if col_name in df.columns:
                    first_name = str(row[col_name]) if pd.notna(row[col_name]) else ''
                    break
            
            last_name = ''
            for col_name in ['last_name', 'Last Name', 'lastname', 'Фамилия', 'surname']:
                if col_name in df.columns:
                    last_name = str(row[col_name]) if pd.notna(row[col_name]) else ''
                    break
            
            # Проверяем, есть ли уже пользователь
            cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
            existing = cursor.fetchone()
            
            if existing:
                # Обновляем существующего пользователя
                cursor.execute('''
                UPDATE users 
                SET username = ?, first_name = ?, last_name = ?
                WHERE user_id = ?
                ''', (username, first_name, last_name, user_id))
                users_updated += 1
                print(f"🔄 Обновлен пользователь {user_id}")
            else:
                # Добавляем нового пользователя
                cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, joined_at, is_admin)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (user_id, username, first_name, last_name, datetime.now(), 0))
                users_imported += 1
                print(f"✅ Импортирован пользователь {user_id}")
                
        except Exception as e:
            print(f"❌ Ошибка в строке {index}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n📊 Импорт завершен!")
    print(f"✅ Новых пользователей: {users_imported}")
    print(f"🔄 Обновлено пользователей: {users_updated}")

if __name__ == "__main__":
    import_with_pandas()
