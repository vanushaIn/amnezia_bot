import os
import asyncio
import logging
import io
import sqlite3
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Обязательные переменные
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN env variable is required")

CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN", "")
VPN_API_URL = os.environ.get("VPN_API_URL", "http://127.0.0.1:5000")
VPN_API_KEY = os.environ.get("VPN_API_KEY", "change_me")
DB_PATH = os.environ.get("DB_PATH", "/etc/amnezia/vpn.db")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/home/vpn_clients")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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
    _, months, client_name = callback.data.split(":")
    months = int(months)
    prices = {1: 10, 3: 25, 6: 45}
    price_usd = prices[months]

    if CRYPTO_BOT_TOKEN:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title="AmneziaWG VPN",
            description=f"Подписка на {months} мес.",
            payload=f"sub:{client_name}:{months}",
            provider_token=CRYPTO_BOT_TOKEN,
            currency="USD",
            prices=[LabeledPrice(label="Подписка", amount=price_usd * 100)],
        )
    else:
        await callback.answer("Платёжная система не настроена", show_alert=True)
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    _, client_name, months = payload.split(":")
    months = int(months)

    save_telegram_id(client_name, message.from_user.id)
    result = await call_api_create(client_name, months * 30)
    if "error" in result:
        await message.answer(f"❌ Ошибка: {result['error']}")
        return

    config = result["config"]
    paid_until = result["paid_until"]
    file = io.BytesIO(config.encode())
    file.name = f"{client_name}.conf"
    await message.answer_document(
        types.BufferedInputFile(file.read(), filename=file.name),
        caption=f"✅ Подписка активирована до {paid_until}\nИмпортируйте этот файл в AmneziaVPN/WireGuard."
    )

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