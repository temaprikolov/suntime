#!/usr/bin/env python3
"""
reload.py — перезапуск бота на Beget без потери данных.
Использование: python3 reload.py
"""

import subprocess
import sys
import time
import os
import signal

# ── Настройки ────────────────────────────────────────────────────────────────
BOT_SCRIPT  = "main.py"          # главный файл бота
PID_FILE    = "bot.pid"          # файл с PID процесса
LOG_FILE    = "bot.log"          # лог бота
PYTHON      = sys.executable     # тот же python3, которым запущен reload.py
WAIT_STOP   = 5                  # секунд ждём после SIGTERM перед SIGKILL
# ─────────────────────────────────────────────────────────────────────────────


def read_pid() -> int | None:
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def write_pid(pid: int):
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def stop_old():
    pid = read_pid()
    if pid is None:
        print("ℹ️  PID-файл не найден — старый процесс не запущен.")
        return

    if not is_running(pid):
        print(f"ℹ️  Процесс {pid} уже не работает.")
        return

    print(f"🛑 Останавливаем старый бот (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    for _ in range(WAIT_STOP * 10):
        time.sleep(0.1)
        if not is_running(pid):
            print(f"✅ Процесс {pid} остановлен.")
            return

    print(f"⚠️  Процесс не ответил — принудительное завершение (SIGKILL)...")
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def start_new():
    print(f"🚀 Запускаем {BOT_SCRIPT}...")
    log = open(LOG_FILE, "a")
    log.write(f"\n\n{'='*50}\n")
    log.write(f"▶ Запуск: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    log.write(f"{'='*50}\n")
    log.flush()

    proc = subprocess.Popen(
        [PYTHON, BOT_SCRIPT],
        stdout=log,
        stderr=log,
        # Отвязываем от текущего терминала — бот живёт сам по себе
        start_new_session=True,
    )
    write_pid(proc.pid)
    time.sleep(2)  # даём боту секунду на старт

    if is_running(proc.pid):
        print(f"✅ Бот запущен! PID: {proc.pid}")
        print(f"📄 Логи: {LOG_FILE}")
    else:
        print(f"❌ Бот не запустился. Смотри {LOG_FILE}")
        sys.exit(1)


if __name__ == "__main__":
    stop_old()
    start_new()
