# ==========================================
# 🧱 БЛОК 0: ФУНДАМЕНТ (Импорты и Настройки)
# ==========================================
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.client.session.aiohttp import AiohttpSession # Нужен для локального сервера

# Подключаем библиотеку Google Gemini
import google.generativeai as genai

# Подключаем "пинг-систему" (чтобы Render не засыпал)
from keep_alive import keep_alive

# Читаем секретные ключи из настроек сервера
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Получаем ID админа (превращаем текст в число)
admin_env = os.getenv("ADMIN_ID")
ADMIN_ID = int(admin_env) if admin_env else 0

# Настройка папок и файлов
ACCESS_FILE = "allowed_users.txt"
TEMP_FOLDER = "temp_data"

# Создаем временную папку, если её нет
if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

# Включаем логирование (чтобы видеть ошибки в консоли)
logging.basicConfig(level=logging.INFO)


# ==========================================
# 🧠 БЛОК 1: МОЗГ (Настройка Gemini и Каскад)
# ==========================================
import time # Необходим для пауз между попытками

if not GOOGLE_API_KEY:
    print("❌ ОШИБКА: Не найден GOOGLE_API_KEY!")
else:
    genai.configure(api_key=GOOGLE_API_KEY)

# КАСКАД МОДЕЛЕЙ (Выбраны самые стабильные из вашего списка)
MODEL_CASCADE = [
    "gemini-2.5-pro",          # Топ качество
    "gemini-2.5-flash",        # Баланс скорости и ума
    "gemini-2.0-flash",        # Стабильность
    "gemini-2.0-flash-lite",   # Экономия квот
    "gemini-flash-latest"      # Надежный резерв
]

# Промпт (Инструкция для ИИ)
SYSTEM_PROMPT = (
    "Сделай полную и точную транскрибацию этого аудио. "
    "Раздели текст на абзацы. Обозначай спикеров (Спикер 1, Спикер 2). "
    "Пиши чистым текстом без markdown."
)

# ==========================================
# 🛡️ БЛОК 2: ФЕЙСКОНТРОЛЬ (Безопасность)
# ==========================================

def get_allowed_users():
    """Читает файл с разрешенными ID."""
    if not os.path.exists(ACCESS_FILE):
        return set()
    with open(ACCESS_FILE, "r") as f:
        users = set()
        for line in f:
            if line.strip().isdigit():
                users.add(int(line.strip()))
        return users

def add_user_to_list(user_id):
    """Добавляет нового пользователя в белый список."""
    users = get_allowed_users()
    if user_id not in users:
        with open(ACCESS_FILE, "a") as f:
            f.write(f"{user_id}\n")
        return True
    return False

def is_authorized(user_id):
    """Главная проверка: пускать или нет."""
    return user_id == ADMIN_ID or user_id in get_allowed_users()


# ==========================================
# 🔌 БЛОК 3: СВЯЗЬ (Инициализация Бота)
# ==========================================

# Проверяем, есть ли адрес Локального Сервера (для больших файлов)
LOCAL_SERVER_URL = os.getenv("TELEGRAM_LOCAL_SERVER_URL")

if not TELEGRAM_TOKEN:
    print("❌ ОШИБКА: Не найден TELEGRAM_TOKEN!")
    exit(1)

# Логика переключения: Облако или Локальный сервер
if LOCAL_SERVER_URL:
    print(f"🔌 РЕЖИМ: Локальный сервер ({LOCAL_SERVER_URL})")
    session = AiohttpSession(base_url=LOCAL_SERVER_URL)
    bot = Bot(token=TELEGRAM_TOKEN, session=session)
else:
    print("☁️ РЕЖИМ: Облачный сервер Telegram (стандартный)")
    bot = Bot(token=TELEGRAM_TOKEN)

dp = Dispatcher()


# ==========================================
# 🎮 БЛОК 4: ПУЛЬТ УПРАВЛЕНИЯ (Команды)
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if is_authorized(message.from_user.id):
        await message.answer(
            f"🎙 <b>Бот готов к работе!</b>\n"
            f"Я использую каскад моделей Gemini.\n"
            "Просто перешли мне голосовое или аудиофайл.", 
            parse_mode="HTML"
        )
    else:
        await message.answer(f"⛔ Нет доступа. Ваш ID: {message.from_user.id}")
        # Стучим админу
        if ADMIN_ID:
            try:
                await bot.send_message(ADMIN_ID, f"🔔 Кто-то ломится в бота! ID: {message.from_user.id}")
            except: pass

@dp.message(Command("add"))
async def cmd_add(message: Message):
    """Команда только для админа: /add 12345678"""
    if message.from_user.id != ADMIN_ID: return
    try:
        new_id = int(message.text.split()[1])
        add_user_to_list(new_id)
        await message.answer(f"✅ Пользователь {new_id} добавлен в базу.")
    except:
        await message.answer("Ошибка. Пиши так: /add 12345678")


# ==========================================
# 🏭 БЛОК 5: КОНВЕЙЕР (Обработка Аудио)
# ==========================================

@dp.message(F.voice | F.audio)
async def handle_audio(message: Message):
    user_id = message.from_user.id
    
    # 1. Проверка доступа
    if not is_authorized(user_id):
        return

    status_msg = await message.answer("⏳ Принял. Начинаю магию...")

    # 2. Определяем тип файла
    if message.voice:
        file_id = message.voice.file_id
        ext = ".ogg"
    else:
        file_id = message.audio.file_id
        ext = ".mp3"

    # Пути к файлам
    input_path = os.path.join(TEMP_FOLDER, f"in_{user_id}_{file_id}{ext}")
    output_filename = f"transcription_{user_id}_{message.message_id}.txt"
    output_path = os.path.join(TEMP_FOLDER, output_filename)

    try:
        # 3. Скачиваем файл (С локального сервера это мгновенно)
        bot_file = await bot.get_file(file_id)
        await bot.download_file(bot_file.file_path, input_path)

        await status_msg.edit_text("☁️ Загружаю аудио в мозг Gemini...")
        
        # 4. Загрузка в Google
        uploaded_file = genai.upload_file(input_path)

        final_text = None
        used_model = None
        errors_log = []

       #5 КАСКАДНЫЙ ПЕРЕБОР
        for model_name in MODEL_CASCADE:
            # ---> НОВАЯ СТРОЧКА: Пауза 2 секунды перед каждой попыткой
            time.sleep(2) 
            
            try:
                await status_msg.edit_text(f"🎧 Слушаю моделью: <b>{model_name}</b>...", parse_mode="HTML")
                # ... (дальше код как был)
                model = genai.GenerativeModel(model_name)
                
                # Генерация ответа
                response = model.generate_content([SYSTEM_PROMPT, uploaded_file])
                
                if response.text:
                    final_text = response.text
                    used_model = model_name
                    break # Успех! Выходим из цикла
            except Exception as e:
                print(f"Сбой модели {model_name}: {e}")
                errors_log.append(f"{model_name}: error")
                continue # Пробуем следующую
        # 6. Обработка результата
        if final_text:
            # Сохраняем в файл
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"ТРАНСКРИБАЦИЯ (Модель: {used_model})\n")
                f.write("="*30 + "\n\n")
                f.write(final_text)

            # Отправляем пользователю
            await status_msg.edit_text("📤 Отправляю готовый документ...")
            doc_to_send = FSInputFile(output_path)
            caption_text = f"✅ <b>Готово!</b>\nМодель: {used_model}\nСлов: {len(final_text.split())}"
            await message.answer_document(doc_to_send, caption=caption_text, parse_mode="HTML")
            await status_msg.delete()
        else:
            await status_msg.edit_text(f"❌ Все модели дали сбой. Лог: {errors_log}")

    except Exception as e:
        await status_msg.edit_text(f"❌ Критическая ошибка: {e}")
        logging.error(e)

    finally:
        # 7. Уборка мусора
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)
        try:
            if 'uploaded_file' in locals():
                genai.delete_file(uploaded_file.name)
        except: pass


# ==========================================
# 🚀 БЛОК 6: ЗАЖИГАНИЕ (Запуск)
# ==========================================
async def main():
    print("🚀 SMART BOT запускается...")
    # Удаляем старые вебхуки, чтобы не конфликтовать
    await bot.delete_webhook(drop_pending_updates=True)
    # Поехали!
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Запускаем фоновый сервер для Render (keep_alive)
    keep_alive()
    # Запускаем основного бота
    asyncio.run(main())



