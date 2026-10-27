import pandas as pd
import sqlite3
from datetime import datetime
import os

def smart_import(excel_file='user.xlsx'):
    """Умный импорт с автодетектом структуры файла"""
    
    print("🔍 Анализ структуры файла...")
    
    try:
        df = pd.read_excel(excel_file)
        
        print("\n📋 Обнаруженные колонки:")
        for i, col in enumerate(df.columns, 1):
            print(f"{i}. {col}: {df[col].dtype}, пример: {df[col].iloc[0] if len(df) > 0 else 'нет данных'}")
        
        # Автоматическое сопоставление колонок
        column_map = {}
        
        for col in df.columns:
            col_lower = str(col).lower()
            
            if any(key in col_lower for key in ['user_id', 'id', 'telegram', 'tg']):
                column_map['user_id'] = col
            elif any(key in col_lower for key in ['user', 'nick', 'username']):
                column_map['username'] = col
            elif any(key in col_lower for key in ['first', 'имя', 'name']):
                column_map['first_name'] = col
            elif any(key in col_lower for key in ['last', 'фамилия', 'surname']):
                column_map['last_name'] = col
            elif any(key in col_lower for key in ['date', 'время', 'joined']):
                column_map['joined_at'] = col
        
        print(f"\n🗺️ Автоматическое сопоставление колонок:")
        for key, value in column_map.items():
            print(f"  {key} → {value}")
        
        # Подтверждение от пользователя
        confirm = input("\n✅ Все правильно? (y/n): ").lower()
        
        if confirm != 'y':
            print("\n✏️ Введите правильные названия колонок:")
            column_map['user_id'] = input("Колонка для user_id: ") or column_map.get('user_id', '')
            column_map['username'] = input("Колонка для username: ") or column_map.get('username', '')
            column_map['first_name'] = input("Колонка для first_name: ") or column_map.get('first_name', '')
            column_map['last_name'] = input("Колонка для last_name: ") or column_map.get('last_name', '')
        
        # Проверка обязательной колонки user_id
        if not column_map.get('user_id'):
            print("❌ Не найдена колонка с user_id!")
            return
        
        # Подключаемся к базе данных
        conn = sqlite3.connect('data/database.db')
        cursor = conn.cursor()
        
        total = 0
        imported = 0
        
        for index, row in df.iterrows():
            total += 1
            
            try:
                user_id = int(row[column_map['user_id']])
                
                username = str(row[column_map['username']]) if column_map.get('username') and pd.notna(row[column_map['username']]) else ''
                first_name = str(row[column_map['first_name']]) if column_map.get('first_name') and pd.notna(row[column_map['first_name']]) else ''
                last_name = str(row[column_map['last_name']]) if column_map.get('last_name') and pd.notna(row[column_map['last_name']]) else ''
                
                # Проверяем существование
                cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
                if not cursor.fetchone():
                    cursor.execute('''
                    INSERT INTO users (user_id, username, first_name, last_name, joined_at, is_admin)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''', (user_id, username, first_name, last_name, datetime.now(), 0))
                    imported += 1
                    print(f"✅ {imported}/{total}: Импортирован {user_id}")
                
            except Exception as e:
                print(f"⚠️ Ошибка в строке {index}: {e}")
        
        conn.commit()
        conn.close()
        
        print(f"\n🎉 Импорт завершен!")
        print(f"📊 Всего строк в файле: {total}")
        print(f"✅ Импортировано новых пользователей: {imported}")
        print(f"⚠️ Уже существовало в базе: {total - imported}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    smart_import()
