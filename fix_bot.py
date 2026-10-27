#!/usr/bin/env python3
"""
fix_bot.py — одним запуском лечит "Conflict: terminated by other getUpdates".

Положи файл в папку бота (рядом с main.py) и запусти:
    python3 fix_bot.py

Что делает:
  1. Находит ВСЕ процессы main.py, которые запущены из этой папки
     ИЛИ используют тот же BOT_TOKEN (например, старая копия из другой папки).
  2. Останавливает их (SIGTERM, потом SIGKILL). Чужие боты с другим токеном не трогает.
  3. Проверяет связь сервера с Telegram (IPv4, IPv6, getMe). Если IPv6 не работает
     (из-за него aiogram виснет на "Request timeout error") — сам правит main.py:
     только IPv4 + таймаут 60с (копия старого main.py.bak). Прокси можно задать
     строкой TELEGRAM_PROXY=... в .env.
  4. Выбирает рабочий python (где есть aiogram и sqlalchemy).
  5. Запускает ОДНУ копию отвязанно от screen (переживёт закрытие терминала).
  6. Проверяет лог: если Conflict остался — значит копия живёт где-то ещё
     (другой сервер/аккаунт) и нужно сделать /revoke токена в @BotFather.

Только стандартная библиотека. Нужен Linux (читает /proc).
"""
import os
import re
import signal
import subprocess
import sys
import time

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT = "main.py"
LOG_FILE = os.path.join(BOT_DIR, "bot.log")
PID_FILE = os.path.join(BOT_DIR, "bot.pid")
MAX_LOG_BYTES = 5 * 1024 * 1024  # больше — архивируем в bot.log.old
CHECK_SECONDS = 12               # сколько ждём после старта, прежде чем смотреть лог

ME = os.getpid()


def say(msg=""):
    print(msg, flush=True)


# ── Токен ────────────────────────────────────────────────────────────────────
def read_token(folder):
    """BOT_TOKEN из .env, а если его там нет — из значения по умолчанию в config.py."""
    for name, pattern in (
        (".env", r"^\s*BOT_TOKEN\s*=\s*['\"]?([^'\"\s#]+)"),
        ("config.py", r"(\d{6,}:[A-Za-z0-9_\-]{30,})"),
    ):
        try:
            with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        m = re.search(pattern, text, re.M)
        if m and ":" in m.group(1):
            return m.group(1)
    return None


def _env_has(key):
    try:
        with open(os.path.join(BOT_DIR, ".env"), encoding="utf-8", errors="ignore") as f:
            return re.search(rf"^\s*{key}\s*=\s*\S+", f.read(), re.M) is not None
    except OSError:
        return False


# ── Поиск процессов ──────────────────────────────────────────────────────────
def proc_info(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = [a.decode(errors="ignore") for a in f.read().split(b"\0") if a]
        cwd = os.path.realpath(os.readlink(f"/proc/{pid}/cwd"))
    except (OSError, ProcessLookupError):
        return None
    return args, cwd


def find_bot_processes(my_token):
    """Возвращает список (pid, cwd, причина) копий бота."""
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == ME:
            continue
        info = proc_info(pid)
        if not info:
            continue
        args, cwd = info
        # интересуют только "python ... main.py"
        if not args or "python" not in os.path.basename(args[0]).lower():
            continue
        script = next((a for a in args[1:] if a.endswith(".py")), None)
        if not script or os.path.basename(script) != BOT_SCRIPT:
            continue
        script_dir = os.path.dirname(script) if os.path.isabs(script) else cwd
        script_dir = os.path.realpath(script_dir)

        if script_dir == BOT_DIR:
            found.append((pid, script_dir, "запущен из этой папки"))
        elif my_token and read_token(script_dir) == my_token:
            found.append((pid, script_dir, "другая папка, но ТОТ ЖЕ токен"))
    return found


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # зомби считаем мёртвым
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def kill_all(procs):
    for pid, _, _ in procs:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 6
    while time.time() < deadline and any(alive(p) for p, _, _ in procs):
        time.sleep(0.2)
    for pid, _, _ in procs:
        if alive(pid):
            say(f"   ⚠️  {pid} не ответил — SIGKILL")
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    time.sleep(0.5)


# ── Выбор python ─────────────────────────────────────────────────────────────
def pick_python():
    candidates = [
        os.path.join(BOT_DIR, "venv", "bin", "python"),
        os.path.join(BOT_DIR, "venv", "bin", "python3"),
        os.path.join(os.path.dirname(BOT_DIR), "masconv", "venv", "bin", "python"),
        sys.executable,
        "python3",
    ]
    for py in candidates:
        if os.path.sep in py and not os.path.exists(py):
            continue
        try:
            r = subprocess.run(
                [py, "-c", "import aiogram, sqlalchemy, dotenv"],
                capture_output=True, timeout=30, cwd=BOT_DIR,
            )
        except Exception:
            continue
        if r.returncode == 0:
            return py
    return None


# ── Сеть до Telegram ─────────────────────────────────────────────────────────
TG_HOST = "api.telegram.org"


def tg_probe(family, token):
    """Пробует достучаться до Telegram по IPv4/IPv6. Возвращает (ok, подробности)."""
    import socket
    import ssl

    t0 = time.time()
    try:
        addr = socket.getaddrinfo(TG_HOST, 443, family, socket.SOCK_STREAM)[0][4]
    except Exception as e:
        return False, f"DNS не отдал адрес ({e.__class__.__name__})"
    try:
        raw = socket.create_connection(addr[:2], timeout=10)
    except Exception as e:
        return False, f"TCP {addr[0]}:443 не подключился за 10с ({e.__class__.__name__})"
    try:
        ctx = ssl.create_default_context()
        s = ctx.wrap_socket(raw, server_hostname=TG_HOST)
        s.settimeout(15)
        path = f"/bot{token}/getMe" if token else "/"
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {TG_HOST}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while len(data) < 2000:
            chunk = s.recv(2000)
            if not chunk:
                break
            data += chunk
        s.close()
    except Exception as e:
        return False, f"{addr[0]} подключился, но ответа нет ({e.__class__.__name__})"
    dt = time.time() - t0
    head = data.split(b"\r\n", 1)[0].decode(errors="ignore")
    if b'"ok":true' in data:
        return True, f"{addr[0]} getMe OK за {dt:.1f}с"
    if b"401" in data[:20]:
        return True, f"{addr[0]} сеть ок, но Telegram отвечает 401 — токен неверный/отозван ({dt:.1f}с)"
    return True, f"{addr[0]} ответил «{head}» за {dt:.1f}с"


def check_network(token):
    """Возвращает (ipv4_ok, ipv6_ok)."""
    import socket

    say("\n🌐 Проверяю связь с Telegram (api.telegram.org)...")
    ok4, d4 = tg_probe(socket.AF_INET, token)
    say(f"   IPv4: {'✅' if ok4 else '❌'} {d4}")
    ok6, d6 = tg_probe(socket.AF_INET6, token)
    say(f"   IPv6: {'✅' if ok6 else '⚠️ '} {d6}")
    return ok4, ok6


# ── Правка main.py: только IPv4 + увеличенный таймаут (+ прокси из .env) ─────
PATCH_MARK = "# [fix_bot.py] IPv4-only session"
PATCH_BLOCK = (
    "    {mark}\n"
    "    import socket as _socket\n"
    "    from aiogram.client.session.aiohttp import AiohttpSession\n"
    "    _session = AiohttpSession(proxy=os.getenv(\"TELEGRAM_PROXY\") or None, timeout=60)\n"
    "    _session._connector_init[\"family\"] = _socket.AF_INET  # без IPv6: он на хостинге часто виснет\n"
    "    bot = Bot(token=config.config.BOT_TOKEN, session=_session)\n"
)


def patch_main():
    path = os.path.join(BOT_DIR, BOT_SCRIPT)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if PATCH_MARK in src:
        say("   ℹ️  main.py уже исправлен ранее")
        return True
    new, n = re.subn(
        r"^[ \t]*bot\s*=\s*Bot\(\s*token\s*=\s*config\.config\.BOT_TOKEN\s*\)[ \t]*\n",
        PATCH_BLOCK.format(mark=PATCH_MARK),
        src,
        count=1,
        flags=re.M,
    )
    if n != 1:
        say("   ⚠️  не нашёл строку  bot = Bot(token=config.config.BOT_TOKEN)  в main.py — правку пропускаю")
        return False
    compile(new, path, "exec")  # убедились, что синтаксис не сломан
    backup = path + ".bak"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as f:
            f.write(src)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    say("   ✅ main.py исправлен: бот ходит в Telegram только по IPv4, таймаут 60с (копия: main.py.bak)")
    return True


# ── Запуск ───────────────────────────────────────────────────────────────────
def start_bot(python):
    try:
        if os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".old")
            say("   🗂  большой bot.log перенесён в bot.log.old")
    except OSError:
        pass

    log = open(LOG_FILE, "a", encoding="utf-8")
    log.write(f"\n\n{'=' * 50}\n▶ Запуск (fix_bot.py): {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'=' * 50}\n")
    log.flush()
    start_offset = log.tell()

    proc = subprocess.Popen(
        [python, "-u", BOT_SCRIPT],
        cwd=BOT_DIR,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    return proc, start_offset


def check_result(proc, offset):
    time.sleep(CHECK_SECONDS)
    if proc.poll() is not None:
        say(f"❌ Бот сразу завершился (код {proc.returncode}). Последние строки лога:")
        show_tail(offset)
        return False

    with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
        f.seek(offset)
        new = f.read()

    if "Conflict" in new:
        say("❌ Telegram всё ещё говорит Conflict — где-то работает ЕЩЁ одна копия,")
        say("   которую я не вижу (другой сервер, другой аккаунт Beget, старый хостинг).")
        say("   Лечится одним действием: @BotFather → /revoke → выбрать бота →")
        say("   новый токен вписать в .env и запустить  python3 fix_bot.py  ещё раз.")
        return False
    if "Traceback" in new or "ModuleNotFoundError" in new:
        say("❌ В логе ошибка при старте:")
        show_tail(offset)
        return False
    if "Бот готов к работе" in new:
        say("✅ Бот запущен и подключился к Telegram, конфликтов нет.")
    else:
        say("🟡 Процесс жив, конфликтов нет, но строка «Бот готов к работе» ещё не появилась.")
        say("   Проверь через минуту:  tail -n 20 bot.log")
    return True


def show_tail(offset, n=15):
    try:
        with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
            f.seek(offset)
            for line in f.read().splitlines()[-n:]:
                say("   | " + line[:200])
    except OSError:
        pass


def main():
    say(f"📁 Папка бота: {BOT_DIR}")
    if not os.path.exists(os.path.join(BOT_DIR, BOT_SCRIPT)):
        say(f"❌ Не найден {BOT_SCRIPT}. Положи fix_bot.py в папку бота (рядом с main.py).")
        sys.exit(1)
    if not os.path.isdir("/proc"):
        say("❌ Нужен Linux (/proc). Запускай на сервере Beget.")
        sys.exit(1)

    token = read_token(BOT_DIR)
    say("🔑 Токен найден" if token else "⚠️  BOT_TOKEN не найден в .env — ищу копии только по папке")

    # 1. старые копии
    procs = find_bot_processes(token)
    if procs:
        say(f"\n🔎 Найдено копий бота: {len(procs)}")
        for pid, d, why in procs:
            say(f"   PID {pid:<8} {d}   ({why})")
        say("🛑 Останавливаю...")
        kill_all(procs)
        left = [p for p, _, _ in procs if alive(p)]
        if left:
            say(f"❌ Не удалось остановить: {left}. Попробуй: kill -9 {' '.join(map(str, left))}")
            sys.exit(1)
        say("✅ Все старые копии остановлены")
    else:
        say("\n🔎 Запущенных копий не найдено (значит, процесса нет или он на другом сервере)")

    # 2. сеть до Telegram + правка main.py
    ok4, ok6 = check_network(token)
    if ok4:
        if not ok6:
            say("   → IPv6 до Telegram не работает, а aiogram пробует его первым и виснет. Чиню.")
        patch_main()
    else:
        say("\n❌ С этого сервера нет связи с Telegram даже по IPv4 — бот не сможет работать, пока это не решить.")
        say("   Варианты:")
        say("   • написать в поддержку Beget: «нет доступа к api.telegram.org с сервера»;")
        say("   • прокси: добавь в .env строку  TELEGRAM_PROXY=http://логин:пароль@хост:порт")
        say("     (для socks5 нужен  pip install aiohttp-socks)  и запусти fix_bot.py снова.")
        if os.getenv("TELEGRAM_PROXY") or _env_has("TELEGRAM_PROXY"):
            say("   Прокси в .env найден — пробую запустить через него.")
            patch_main()
        else:
            sys.exit(3)

    # 3. python
    python = pick_python()
    if not python:
        say("❌ Не нашёл python с aiogram/sqlalchemy/python-dotenv. Установи зависимости:")
        say(f"   pip install -r {os.path.join(BOT_DIR, 'requirements.txt')}")
        sys.exit(1)
    say(f"\n🐍 Python: {python}")

    # 4. старт одной копии
    say("🚀 Запускаю бота...")
    proc, offset = start_bot(python)
    say(f"   PID {proc.pid}, лог: {LOG_FILE}")

    ok = check_result(proc, offset)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
