import os
import asyncio
import logging
import io
import sqlite3
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN env variable is required")

CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN", "")
CRYPTO_PAY_API = "https://pay.crypt.bot/api"
VPN_API_URL = os.environ.get("VPN_API_URL", "http://127.0.0.1:5000")
VPN_API_KEY = os.environ.get("VPN_API_KEY", "change_me")
DB_PATH = os.environ.get("DB_PATH", "/etc/amnezia/vpn.db")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/home/vpn_clients")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище для отслеживания платежей: invoice_id -> (chat_id, client_name, months)
pending_invoices = {}

def get_user_subscription(telegram_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT client_name, paid_until FROM clients WHERE telegram_id=? AND active=1",
        (str(telegram_id),)
    ).fetchone()
    conn.close()
    return row

def save_telegram_id(client_name, telegram_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE clients SET telegram_id=? WHERE client_name=?",
        (str(telegram_id), client_name)
    )
    conn.commit()
    conn.close()

async def call_api_create(client_name, days):
    async with aiohttp.ClientSession() as session:
        headers = {"X-Api-Key": VPN_API_KEY}
        payload = {"client_name": client_name, "days": days}
        async with session.post(
            f"{VPN_API_URL}/create", json=payload, headers=headers
        ) as resp:
            return await resp.json()

async def create_crypto_invoice(amount_usd, description, payload):
    """Создаёт инвойс через Crypto Pay API. Возвращает (invoice_id, bot_invoice_url)"""
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    data = {
        "amount": str(amount_usd),
        "currency_type": "fiat",
        "fiat": "USD",
        "description": description,
        "payload": payload,
        "expires_in": 3600  # 1 час
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{CRYPTO_PAY_API}/createInvoice", headers=headers, json=data) as resp:
            result = await resp.json()
            if not result.get("ok"):
                raise Exception(f"Crypto Pay error: {result.get('error')}")
            inv = result["result"]
            return inv["invoice_id"], inv["bot_invoice_url"]

async def check_invoice_status(invoice_id):
    """Проверяет статус инвойса, возвращает 'active', 'paid' или 'expired'"""
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    params = {"invoice_ids": str(invoice_id)}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{CRYPTO_PAY_API}/getInvoices", headers=headers, params=params) as resp:
            result = await resp.json()
            if result.get("ok") and result["result"]["items"]:
                return result["result"]["items"][0]["status"]
            return "unknown"

async def payment_poller(invoice_id, chat_id, client_name, months):
    """Фоновая задача: опрашивает статус, при оплате активирует подписку"""
    for _ in range(120):  # максимум 10 минут (120 * 5 сек)
        await asyncio.sleep(5)
        status = await check_invoice_status(invoice_id)
        if status == "paid":
            result = await call_api_create(client_name, months * 30)
            if "error" in result:
                await bot.send_message(chat_id, f"❌ Ошибка активации: {result['error']}")
            else:
                config = result["config"]
                file = io.BytesIO(config.encode())
                file.name = f"{client_name}.conf"
                await bot.send_document(
                    chat_id,
                    types.BufferedInputFile(file.read(), filename=file.name),
                    caption=f"✅ Подписка активирована до {result['paid_until']}\nИмпортируйте этот файл в AmneziaVPN/WireGuard."
                )
            del pending_invoices[invoice_id]
            return
        elif status == "expired":
            await bot.send_message(chat_id, "⏰ Время оплаты истекло. Попробуйте снова /buy")
            del pending_invoices[invoice_id]
            return
    # Таймаут
    await bot.send_message(chat_id, "⌛️ Превышено время ожидания оплаты.")
    del pending_invoices[invoice_id]

@dp.message(Command("start"))
async def start(message: types.Message):
    sub = get_user_subscription(message.from_user.id)
    if sub:
        client_name, paid_until = sub
        await message.answer(
            f"✅ У вас активна подписка.\nКлиент: {client_name}\nДействует до: {paid_until}\n\n"
            "Используйте /config для получения конфигурации."
        )
    else:
        await message.answer(
            "Добро пожаловать! Здесь можно купить доступ к AmneziaWG VPN.\n"
            "Для начала придумайте имя клиента (логин) и введите /buy <имя>"
        )

@dp.message(Command("buy"))
async def buy(message: types.Message):
    if not CRYPTO_BOT_TOKEN:
        await message.reply("💳 Платёжная система не настроена.")
        return
    args = message.text.split()
    if len(args) != 2:
        await message.reply("Используйте: /buy <имя_клиента>")
        return
    client_name = args[1]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="1 месяц - 10$", callback_data=f"tariff:1:{client_name}"),
        InlineKeyboardButton(text="3 месяца - 25$", callback_data=f"tariff:3:{client_name}"),
        InlineKeyboardButton(text="6 месяцев - 45$", callback_data=f"tariff:6:{client_name}"),
    )
    await message.answer(
        f"Выберите срок подписки для клиента {client_name}:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("tariff:"))
async def process_tariff(callback: types.CallbackQuery):
    if not CRYPTO_BOT_TOKEN:
        await callback.answer("Платёжная система не настроена", show_alert=True)
        return
    _, months, client_name = callback.data.split(":")
    months = int(months)
    prices = {1: 10, 3: 25, 6: 45}
    price = prices[months]

    try:
        invoice_id, pay_url = await create_crypto_invoice(
            amount_usd=price,
            description=f"AmneziaWG VPN: {months} мес.",
            payload=f"sub:{client_name}:{months}"
        )
    except Exception as e:
        await callback.answer(f"Ошибка создания счёта: {e}", show_alert=True)
        return

    # Запоминаем платёж
    pending_invoices[invoice_id] = (callback.from_user.id, client_name, months)

    # Кнопка оплаты
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Оплатить", url=pay_url))
    await callback.message.answer(
        f"Счёт на {price} USD создан. Нажмите «Оплатить» для перехода в @CryptoBot.",
        reply_markup=builder.as_markup()
    )

    # Запускаем фоновую проверку
    asyncio.create_task(payment_poller(invoice_id, callback.from_user.id, client_name, months))
    await callback.answer()

@dp.message(Command("config"))
async def config(message: types.Message):
    sub = get_user_subscription(message.from_user.id)
    if not sub:
        await message.reply("У вас нет активной подписки.")
        return
    client_name, _ = sub
    config_path = os.path.join(CONFIG_DIR, f"{client_name}.conf")
    try:
        with open(config_path) as f:
            config_text = f.read()
        file = io.BytesIO(config_text.encode())
        file.name = f"{client_name}.conf"
        await message.answer_document(
            types.BufferedInputFile(file.read(), filename=file.name),
            caption="Ваш конфигурационный файл."
        )
    except FileNotFoundError:
        await message.reply("Файл конфигурации не найден. Обратитесь в поддержку.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())