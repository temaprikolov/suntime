import importlib.util
import sys

# Проверяем, загружается ли admin.py
spec = importlib.util.spec_from_file_location("admin", "admin.py")
admin_module = importlib.util.module_from_spec(spec)
sys.modules["admin"] = admin_module
spec.loader.exec_module(admin_module)

print("✅ admin.py загружен успешно")

# Проверяем функции
if hasattr(admin_module, 'admin_menu_kb'):
    print("✅ Функция admin_menu_kb найдена")
    kb = admin_module.admin_menu_kb()
    print(f"✅ Клавиатура создана: {kb}")
else:
    print("❌ Функция admin_menu_kb НЕ найдена")
