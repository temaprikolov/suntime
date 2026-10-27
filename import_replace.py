import pandas as pd
import sqlite3
from datetime import datetime
import os

def import_replace(excel_file='user.xlsx'):
    """Импорт с заменой существующих пользователей"""
    
    print("🔄 ИМПОРТ С ЗАМЕНОЙ СУЩЕСТВУЮЩИХ ДАННЫХ")
    print("=" * 50)
    
    # Проверяем файл
    if not os.path.exists(excel_file):
        print(f"❌ Файл {excel_file} не найден!")
        print("\n🔍 Ищу файлы...")
        
        files = [f for f in os.listdir('.') if f.endswith(('.xlsx', '.xls'))]
        if files:
            print(f"📋 Найдены файлы: {files}")
            excel_file = files[0]
            print(f"✅ Использую: {excel_file}")
        else:
            print("❌ Excel файлы не найдены!")
            return
    
    print(f"📁 Файл: {excel_file}")
    
    # Проверяем базу
    db_path = 'data/database.db'
    if not os.path.exists(db_path):
        print("❌ База данных не найдена! Сначала запустите бота.")
        return
    
    # Читаем Excel
    try:
        df = pd.read_excel(excel_file)
        print(f"✅ Файл прочитан. Строк: {len(df)}")
        print(f"📋 Колонки: {list(df.columns)}")
        
        # Показываем пример данных
        print("\n🔍 Пример данных (первые 3 строки):")
        print(df.head(3))
        
    except Exception as e:
        print(f"❌ Ошибка чтения файла: {e}")
        return
    
    # Определяем колонки автоматически
    print("\n🔑 Определяю колонки...")
    
    col_map = {}
    
    # Пробуем найти колонку с user_id
    user_id_candidates = ['user_id', 'User ID', 'userid', 'ID', 'id', 'telegram_id', 'Telegram ID']
    for col in df.columns:
        col_lower = str(col).lower().replace(' ', '_')
        for candidate in user_id_candidates:
            if candidate.lower() in col_lower:
                col_map['user_id'] = col
                break
        if 'user_id' in col_map:
            break
    
    if 'user_id' not in col_map:
        print("❌ Не могу найти колонку с user_id!")
        print("Доступные колонки:", list(df.columns))
        
        col_user = input("Введите точное название колонки с user_id: ").strip()
        if col_user in df.columns:
            col_map['user_id'] = col_user
        else:
            print("❌ Колонка не найдена!")
            return
    
    # Пробуем найти другие колонки
    for col in df.columns:
        col_lower = str(col).lower()
        
        if 'username' in col_lower or 'user' in col_lower or 'nick' in col_lower:
            col_map['username'] = col
        elif 'first' in col_lower or 'имя' in col_lower or 'name' in col_lower:
            col_map['first_name'] = col
        elif 'last' in col_lower or 'фамилия' in col_lower or 'surname' in col_lower:
            col_map['last_name'] = col
    
    print(f"📋 Назначенные колонки:")
    for key, value in col_map.items():
        print(f"  {key}: {value}")
    
    # Подтверждение
    confirm = input("\n✅ Начать импорт? (y/n): ").lower()
    if confirm != 'y':
        print("❌ Импорт отменен")
        return
    
    # Импортируем
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    imported = 0
    updated = 0
    errors = 0
    
    print("\n🔄 Импортирую...")
    
    for index, row in df.iterrows():
        try:
            user_id = int(row[col_map['user_id']])
            
            # Получаем данные
            username = str(row[col_map.get('username', '')]) if 'username' in col_map and pd.notna(row[col_map['username']]) else ''
            first_name = str(row[col_map.get('first_name', '')]) if 'first_name' in col_map and pd.notna(row[col_map['first_name']]) else ''
            last_name = str(row[col_map.get('last_name', '')]) if 'last_name' in col_map and pd.notna(row[col_map['last_name']]) else ''
            
            # Проверяем, есть ли уже пользователь
            cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
            existing = cursor.fetchone()
            
            if existing:
                # ОБНОВЛЯЕМ существующего пользователя
                cursor.execute('''
                UPDATE users 
                SET username = ?, first_name = ?, last_name = ?, joined_at = ?
                WHERE user_id = ?
                ''', (username, first_name, last_name, datetime.now(), user_id))
                updated += 1
            else:
                # Добавляем нового пользователя
                cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, joined_at, is_admin)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (user_id, username, first_name, last_name, datetime.now(), 0))
                imported += 1
            
            # Показываем прогресс
            total_processed = imported + updated
            if total_processed % 50 == 0:
                print(f"✅ Обработано: {total_processed}")
                
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"⚠️ Ошибка в строке {index}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n🎉 ИМПОРТ ЗАВЕРШЕН!")
    print(f"=" * 30)
    print(f"📊 Всего строк в Excel: {len(df)}")
    print(f"✅ Новых пользователей: {imported}")
    print(f"🔄 Обновлено пользователей: {updated}")
    print(f"📈 Всего в базе теперь: {imported + updated}")
    print(f"❌ Ошибок: {errors}")

if __name__ == "__main__":
    import_replace()
