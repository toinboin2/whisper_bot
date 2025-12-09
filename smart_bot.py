import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile

# --- БИБЛИОТЕКА GOOGLE GEMINI ---
import google.generativeai as genai

# Подключаем нашу "пинг-систему" (файл keep_alive.py должен лежать рядом)
from keep_alive import keep_alive

# ==========================================
# ⚙️ НАСТРОЙКИ (ТЕПЕРЬ БЕЗОПАСНЫЕ)
# ==========================================

# Бот ищет эти переменные в настройках сервера (Render)
# Если ты запускаешь это локально на ПК, тебе нужно либо создать файл .env,
# либо временно вписать ключи сюда обратно (но не заливай это на GitHub!)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Получаем ID админа и превращаем его в число (int)
# Если переменной нет, ставим 0 (чтобы бот не упал с ошибкой)
admin_env = os.getenv("ADMIN_ID")
ADMIN_ID = int(admin_env) if admin_env else 0

# Список моделей (Твой каскад)
MODEL_CASCADE = [
    "gemini-2.0-pro-exp-02-05", 
    "gemini-2.5-flash",
    "gemini-2.0-flash", 
    "gemini-flash-latest", 
    "gemini-1.5-pro"
]

ACCESS_FILE = "allowed_users.txt"
TEMP_FOLDER = "temp_data"

# ==========================================
# 🚀 ИНИЦИАЛИЗАЦИЯ
# ==========================================

if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

# Важно: проверяем, что ключ вообще есть
if not GOOGLE_API_KEY:
    print("❌ ОШИБКА: Не найден GOOGLE_API_KEY в переменных окружения!")
else:
    genai.configure(api_key=GOOGLE_API_KEY)

logging.basicConfig(level=logging.INFO)

# Проверка токена
if not TELEGRAM_TOKEN:
    print("❌ ОШИБКА: Не найден TELEGRAM_TOKEN!")
    exit(1) # Останавливаем скрипт, если нет токена

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ==========================================
# 🛡️ СИСТЕМА БЕЗОПАСНОСТИ
# ==========================================

def get_allowed_users():
    if not os.path.exists(ACCESS_FILE):
        return set()
    with open(ACCESS_FILE, "r") as f:
        users = set()
        for line in f:
            if line.strip().isdigit():
                users.add(int(line.strip()))
        return users

def add_user_to_list(user_id):
    users = get_allowed_users()
    if user_id not in users:
        with open(ACCESS_FILE, "a") as f:
            f.write(f"{user_id}\n")
        return True
    return False

def is_authorized(user_id):
    # Сравниваем ID. ADMIN_ID берется из настроек сервера
    return user_id == ADMIN_ID or user_id in get_allowed_users()

# ==========================================
# 🎮 ОБРАБОТКА КОМАНД
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if is_authorized(message.from_user.id):
        await message.answer(
            f"🎙 <b>Система Gemini (Cloud Version) готова.</b>\n"
            f"Я работаю на сервере.\n"
            "Отправь аудио, я пришлю расшифровку файлом.", 
            parse_mode="HTML"
        )
    else:
        await message.answer(f"⛔ Доступ закрыт. ID: {message.from_user.id}")
        # Пытаемся уведомить админа, если он задан
        if ADMIN_ID:
            try:
                await bot.send_message(ADMIN_ID, f"🔔 Стук в дверь! ID: {message.from_user.id}")
            except:
                pass

@dp.message(Command("add"))
async def cmd_add(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        new_id = int(message.text.split()[1])
        add_user_to_list(new_id)
        await message.answer(f"✅ Пользователь {new_id} добавлен.")
    except:
        await message.answer("Формат: /add 12345678")

# ==========================================
# 🔄 ОБРАБОТКА АУДИО
# ==========================================

@dp.message(F.voice | F.audio)
async def handle_audio(message: Message):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        return

    status_msg = await message.answer("⏳ Скачиваю файл...")

    if message.voice:
        file_id = message.voice.file_id
        ext = ".ogg"
    else:
        file_id = message.audio.file_id
        ext = ".mp3"

    input_path = os.path.join(TEMP_FOLDER, f"in_{user_id}_{file_id}{ext}")
    output_filename = f"transcription_{user_id}_{message.message_id}.txt"
    output_path = os.path.join(TEMP_FOLDER, output_filename)

    try:
        bot_file = await bot.get_file(file_id)
        await bot.download_file(bot_file.file_path, input_path)

        await status_msg.edit_text("☁️ Загружаю в Gemini...")
        uploaded_file = genai.upload_file(input_path)

        prompt = (
            "Сделай полную и точную транскрибацию этого аудио. "
            "Раздели текст на абзацы. Обозначай спикеров. "
            "Пиши чистым текстом без markdown."
        )
        
        final_text = None
        used_model = None
        errors_log = []

        for model_name in MODEL_CASCADE:
            try:
                await status_msg.edit_text(f"🎧 Слушаю: <b>{model_name}</b>...", parse_mode="HTML")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content([prompt, uploaded_file])
                
                if response.text:
                    final_text = response.text
                    used_model = model_name
                    break
            except Exception as e:
                print(f"Ошибка {model_name}: {e}")
                errors_log.append(f"{model_name}: error")
                continue

        if final_text:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"ТРАНСКРИБАЦИЯ (Модель: {used_model})\n")
                f.write("="*30 + "\n\n")
                f.write(final_text)

            await status_msg.edit_text("📤 Отправляю файл...")
            doc_to_send = FSInputFile(output_path)
            caption_text = f"✅ <b>Готово!</b>\nМодель: {used_model}\nСлов: {len(final_text.split())}"
            await message.answer_document(doc_to_send, caption=caption_text, parse_mode="HTML")
            await status_msg.delete()
        else:
            await status_msg.edit_text(f"❌ Не удалось расшифровать. Лог: {errors_log}")

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        logging.error(e)

    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)
        try:
            if 'uploaded_file' in locals():
                genai.delete_file(uploaded_file.name)
        except: pass

# ==========================================
# ▶️ ЗАПУСК
# ==========================================
async def main():
    print("🚀 SMART BOT запущен на сервере!")
    # Удаляем вебхуки (на случай если были)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Сначала запускаем фоновый веб-сервер (чтобы Render дал нам ссылку)
    keep_alive()
    # Потом запускаем самого бота
    asyncio.run(main())