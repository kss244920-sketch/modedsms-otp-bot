import io
import time
import logging
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

import qrcode
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN,
    ADMIN_IDS,
    DEFAULT_MARGIN_PERCENT,
    DEFAULT_MARGIN_FLAT,
    COLOUR_TRADING_PRICE,
    MIN_RECHARGE,
    ORDER_TIMEOUT_MINUTES,
    POLL_INTERVAL_SECONDS,
    PORT,
)
from database import Database
from sastasms_api import SastaSMSClient, SastaSMSAPIError
from services_data import (
    COUNTRIES,
    SERVICES,
    COLOUR_TRADING_SITES,
    get_country_info,
    get_service_info,
    get_colour_site_info,
)
from payment_engine import (
    engine as crypto_engine,
    DEFAULT_PACKAGES as CRYPTO_PACKAGES,
)
from goagame_engine import goagame_gateway
from ccpayment_engine import ccpayment_gateway
from fampay_verifier import fampay_verifier

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize components
db = Database()
sms_client = SastaSMSClient()

# Global reference to Telegram application for background worker messages
TELEGRAM_APP: Optional[Application] = None

# Fallback default base prices if provider getPrices is not responding
DEFAULT_BASE_PRICES = {
    "tg": 15.0,  # Telegram
    "wa": 18.0,  # WhatsApp
    "go": 12.0,  # Google
    "ig": 12.0,  # Instagram
    "fb": 12.0,  # Facebook
    "am": 14.0,  # Amazon
    "nf": 15.0,  # Netflix
    "zm": 10.0,  # Zomato
    "sw": 10.0,  # Swiggy
    "pm": 12.0,  # Paytm
    "fk": 10.0,  # Flipkart
    "ot": 12.0,  # Other
}


# -------------------------------------------------------------
# Cloud Health Check Server for Render 24/7 Hosting
# -------------------------------------------------------------

class CloudHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = {
            "status": "online",
            "bot": "@ModedSmSBot",
            "service": "SastaSMS Professional OTP Bot",
            "timestamp": time.time()
        }
        import json
        self.wfile.write(json.dumps(resp).encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Quiet logs for health pings


def start_health_server(port: int = PORT):
    try:
        server = HTTPServer(("0.0.0.0", port), CloudHealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="RenderHealthServer")
        thread.start()
        logger.info(f"✅ Cloud health check server active on port {port}")
    except Exception as e:
        logger.warning(f"Could not start health server on port {port}: {e}")


# -------------------------------------------------------------
# CryptoBot Auto-Settlement Callback
# -------------------------------------------------------------

def on_crypto_invoice_settled(user_id: int, chat_id: int, receipt_text: str, invoice_record: dict):
    """Called automatically when background CryptoBot poller detects payment."""
    global TELEGRAM_APP
    if not TELEGRAM_APP or not chat_id:
        return

    async def _send():
        try:
            await TELEGRAM_APP.bot.send_message(
                chat_id=chat_id,
                text=receipt_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error sending crypto settlement receipt to {chat_id}: {e}")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_send(), loop)
        else:
            asyncio.run(_send())
    except Exception as e:
        logger.error(f"Failed to schedule settlement message: {e}")


# Wire callback to crypto payment engine
crypto_engine.set_settlement_callback(on_crypto_invoice_settled)


# -------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------

async def get_effective_price(country_id: str, service_code: str) -> tuple[float, float]:
    """
    Returns (cost_price, selling_price).
    Applies admin profit margin from settings.
    """
    cost = await sms_client.get_service_price(country_id, service_code)
    if cost is None or cost <= 0:
        cost = DEFAULT_BASE_PRICES.get(service_code.lower(), 12.0)

    try:
        margin_pct = float(await db.get_setting("margin_percent", str(DEFAULT_MARGIN_PERCENT)))
    except ValueError:
        margin_pct = DEFAULT_MARGIN_PERCENT

    try:
        margin_flat = float(await db.get_setting("margin_flat", str(DEFAULT_MARGIN_FLAT)))
    except ValueError:
        margin_flat = DEFAULT_MARGIN_FLAT

    selling = round(cost + (cost * (margin_pct / 100.0)) + margin_flat, 2)
    return round(cost, 2), selling


def generate_upi_qr(upi_id: str, name: str, amount: float) -> io.BytesIO:
    """Generate dynamic UPI QR Code."""
    upi_uri = f"upi://pay?pa={upi_id}&pn={name}&am={amount:.2f}&cu=INR"
    qr = qrcode.QRCode(version=1, box_size=8, border=3)
    qr.add_data(upi_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# -------------------------------------------------------------
# Main Menu & Start Command
# -------------------------------------------------------------

def build_main_menu(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        # Featured Colour Trading Offer Button
        [
            InlineKeyboardButton("💥 🎰 🟢 🔴 🟣 Colour Trading Sites OTP (₹20 Only) 💥", callback_data="colour_trading_menu")
        ],
        [
            InlineKeyboardButton("📱 🌐 Buy General Number", callback_data="buy_countries:0"),
            InlineKeyboardButton("⏳ ⚡ Active Orders", callback_data="active_orders"),
        ],
        [
            InlineKeyboardButton("💳 💰 My Wallet / Deposit", callback_data="wallet"),
            InlineKeyboardButton("📋 📦 Order History", callback_data="history"),
        ],
        [
            InlineKeyboardButton("ℹ️ 💬 Help & FAQ", callback_data="help"),
        ]
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("👑 ⚙️ Admin Control Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.get_or_create_user(user.id, user.username, user.first_name)
    user_data = await db.get_user(user.id)
    balance = user_data["balance"] if user_data else 0.0

    text = (
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        f"🤖 <b>ModedSmS Professional Virtual Number & OTP Bot</b>\n"
        f"High-Speed Instant Delivery • 100% Automated System\n\n"
        f"💥 <b>SPECIAL OFFER:</b> Colour Trading Sites OTP only at <b>₹20.00</b>!\n\n"
        f"💳 <b>Your Wallet Balance:</b> <b>₹{balance:.2f}</b>\n"
        f"🆔 <b>Your User ID:</b> <code>{user.id}</code>\n\n"
        f"👇 <i>Choose an option below to get started:</i>"
    )

    if update.message:
        await update.message.reply_text(
            text=text,
            reply_markup=build_main_menu(user.id),
            parse_mode="HTML"
        )
    elif update.callback_query:
        await update.callback_query.message.edit_text(
            text=text,
            reply_markup=build_main_menu(user.id),
            parse_mode="HTML"
        )


# -------------------------------------------------------------
# Colour Trading Sites Special Category (Fixed ₹20)
# -------------------------------------------------------------

async def show_colour_trading_menu(query):
    keyboard = []
    row = []
    for site in COLOUR_TRADING_SITES:
        row.append(
            InlineKeyboardButton(
                f"{site['icon']} {site['name']} (₹20)",
                callback_data=f"buy_colour_confirm:{site['code']}"
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 🏠 Main Menu", callback_data="menu_main")])

    text = (
        f"💥 <b>BIGGEST OFFER EVER</b> 💥\n\n"
        f"🎰 <b>Colour Trading Sites OTP — Only ₹{COLOUR_TRADING_PRICE:.2f}</b>\n\n"
        f"⚡ <b>Multi-OTP (3-4+ per number)</b>\n"
        f"🔄 <b>Instant REOTP</b> — reuse the same number anytime!\n"
        f"🚀 <b>FAST • AFFORDABLE • 100% REFUND GUARANTEE</b>\n\n"
        f"<i>Select your Colour Trading platform below:</i>"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def show_colour_trading_confirm(query, site_code: str, user_id: int):
    site_info = get_colour_site_info(site_code)
    user = await db.get_user(user_id)
    user_bal = user["balance"] if user else 0.0
    price = COLOUR_TRADING_PRICE

    keyboard = []
    if user_bal >= price:
        keyboard.append([
            InlineKeyboardButton(f"🟢 ✅ Buy Now (₹{price:.2f})", callback_data=f"buy_colour_execute:{site_code}")
        ])
    else:
        diff = int(price - user_bal + 5)
        keyboard.append([
            InlineKeyboardButton(f"💳 💰 Add Balance (Need ₹{price - user_bal:.2f})", callback_data="wallet")
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 🎰 Back to Sites", callback_data="colour_trading_menu"),
        InlineKeyboardButton("🏠 🔙 Main Menu", callback_data="menu_main")
    ])

    status_str = "✅ Balance Available" if user_bal >= price else "⚠️ Insufficient Balance"

    text = (
        f"📋 <b>Colour Trading Order Confirmation</b>\n\n"
        f"🎰 <b>Platform:</b> {site_info['icon']} <b>{site_info['name']}</b>\n"
        f"🇮🇳 <b>Country:</b> India (+91)\n"
        f"💵 <b>Price:</b> <b>₹{price:.2f}</b>\n"
        f"💳 <b>Your Balance:</b> ₹{user_bal:.2f} ({status_str})\n\n"
        f"✨ <b>Features:</b>\n"
        f"• Instant OTP auto-delivery\n"
        f"• REOTP enabled (Get multiple OTPs on same number)\n"
        f"• 20 Minutes Validity\n"
        f"• Auto-refund if no OTP received!"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def execute_colour_purchase(query, site_code: str, user_id: int):
    user = await db.get_user(user_id)
    user_bal = user["balance"] if user else 0.0
    price = COLOUR_TRADING_PRICE
    site_info = get_colour_site_info(site_code)

    if user_bal < price:
        await query.answer("❌ Insufficient balance! Please deposit to continue.", show_alert=True)
        return

    # Notify progress
    await query.edit_message_text(
        text=f"⏳ <b>Allocating Indian Virtual Number for {site_info['name']}...</b>\nPlease wait a few seconds...",
        parse_mode="HTML"
    )

    # 1. Deduct balance in DB
    try:
        await db.update_balance(
            user_id,
            -price,
            "PURCHASE",
            f"Buy Colour Trading: {site_info['name']}"
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Failed to process balance: {e}")
        return

    # 2. Call SastaSMS API: country 22 (India), service "ot" (other/custom)
    try:
        result = await sms_client.get_number(
            service=site_info["service"],
            country=site_info["country"],
            max_price=16.0  # Safe cap to maintain profit
        )
        order_id = result["id"]
        phone_num = result["number"]
        formatted_num = f"+{phone_num}" if not phone_num.startswith("+") else phone_num

    except SastaSMSAPIError as e:
        # Auto refund
        await db.update_balance(
            user_id,
            price,
            "REFUND",
            f"Auto-refund failed purchase: {site_info['name']}"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Try Again", callback_data=f"buy_colour_confirm:{site_code}")],
            [InlineKeyboardButton("🔙 Back to Sites", callback_data="colour_trading_menu")]
        ]
        await query.edit_message_text(
            text=f"❌ <b>Unable to allocate number:</b>\n{str(e)}\n\n💰 ₹{price:.2f} refunded back to your wallet.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return
    except Exception as e:
        await db.update_balance(user_id, price, "REFUND", f"Refund error: {site_info['name']}")
        await query.edit_message_text(
            text=f"❌ Temporary system error: {str(e)}\n💰 Refunded to wallet.",
            reply_markup=build_main_menu(user_id),
            parse_mode="HTML"
        )
        return

    # 3. Save order
    await db.create_order(
        user_id=user_id,
        order_id=order_id,
        phone_number=formatted_num,
        service_code=site_info["service"],
        service_name=f"Colour: {site_info['name']}",
        country_id=site_info["country"],
        country_name="India",
        cost_price=12.34,
        selling_price=price,
        timeout_minutes=ORDER_TIMEOUT_MINUTES
    )

    # 4. Display Active Order Screen with REOTP support
    keyboard = [
        [InlineKeyboardButton("🔄 ⚡ Check OTP", callback_data=f"order_status:{order_id}")],
        [
            InlineKeyboardButton("🔁 📲 Request REOTP", callback_data=f"order_reotp:{order_id}"),
            InlineKeyboardButton("🔴 ❌ Cancel & Refund", callback_data=f"order_cancel:{order_id}")
        ],
        [InlineKeyboardButton("🏠 🔙 Main Menu", callback_data="menu_main")]
    ]

    text = (
        f"🎉 <b>Virtual Number Allocated!</b>\n\n"
        f"🎰 <b>Service:</b> {site_info['icon']} <b>{site_info['name']}</b>\n"
        f"🇮🇳 <b>Country:</b> India\n"
        f"📞 <b>Phone Number:</b> <code>{formatted_num}</code> (Tap to copy)\n"
        f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n\n"
        f"⏳ <b>Status:</b> Waiting for SMS/OTP...\n"
        f"⏱️ <i>Validity: 20 minutes. As soon as the SMS arrives, the OTP will be displayed here automatically!</i>"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# -------------------------------------------------------------
# General Buy Number Flow
# -------------------------------------------------------------

async def show_countries(query, page: int = 0):
    page_size = 6
    total_countries = len(COUNTRIES)
    total_pages = (total_countries + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    current_list = COUNTRIES[start_idx : start_idx + page_size]

    keyboard = []
    row = []
    for c in current_list:
        row.append(
            InlineKeyboardButton(
                f"{c['flag']} {c['name']}",
                callback_data=f"buy_services:{c['id']}:0"
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"buy_countries:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"buy_countries:{page + 1}"))
    keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 🏠 Main Menu", callback_data="menu_main")])

    text = (
        "🌍 <b>Select Country:</b>\n\n"
        "Choose the country for which you need a virtual OTP number:"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def show_services(query, country_id: str, page: int = 0):
    country_info = get_country_info(country_id)
    page_size = 6
    total_services = len(SERVICES)
    total_pages = (total_services + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    current_list = SERVICES[start_idx : start_idx + page_size]

    keyboard = []
    row = []
    for s in current_list:
        row.append(
            InlineKeyboardButton(
                f"{s['icon']} {s['name']}",
                callback_data=f"buy_confirm:{country_id}:{s['code']}"
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"buy_services:{country_id}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"buy_services:{country_id}:{page + 1}"))
    keyboard.append(nav_row)

    keyboard.append([
        InlineKeyboardButton("🔙 Back to Countries", callback_data="buy_countries:0"),
        InlineKeyboardButton("🏠 Menu", callback_data="menu_main")
    ])

    text = (
        f"📱 <b>Select Service</b>\n"
        f"Country: <b>{country_info['flag']} {country_info['name']}</b>\n\n"
        f"Select the application or service you want to verify:"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def show_purchase_confirm(query, country_id: str, service_code: str, user_id: int):
    country_info = get_country_info(country_id)
    service_info = get_service_info(service_code)

    user = await db.get_user(user_id)
    user_bal = user["balance"] if user else 0.0

    cost_price, selling_price = await get_effective_price(country_id, service_code)

    keyboard = []
    if user_bal >= selling_price:
        keyboard.append([
            InlineKeyboardButton("🟢 ✅ Confirm & Buy Number", callback_data=f"buy_execute:{country_id}:{service_code}")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("💳 💰 Add Funds to Wallet", callback_data="wallet")
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 📱 Back to Services", callback_data=f"buy_services:{country_id}:0"),
        InlineKeyboardButton("🏠 🔙 Main Menu", callback_data="menu_main")
    ])

    status_indicator = "✅ Sufficient Funds" if user_bal >= selling_price else "⚠️ Insufficient Balance"

    text = (
        f"📋 <b>Order Confirmation</b>\n\n"
        f"🏳️ <b>Country:</b> {country_info['flag']} {country_info['name']} ({country_info['code']})\n"
        f"📱 <b>Service:</b> {service_info['icon']} {service_info['name']}\n"
        f"💵 <b>Price:</b> ₹{selling_price:.2f}\n"
        f"💳 <b>Your Balance:</b> ₹{user_bal:.2f} ({status_indicator})\n\n"
        f"⏱️ <i>Number will remain active for 20 minutes. If no OTP is received, the full amount is automatically refunded!</i>"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def execute_purchase(query, country_id: str, service_code: str, user_id: int):
    user = await db.get_user(user_id)
    user_bal = user["balance"] if user else 0.0

    cost_price, selling_price = await get_effective_price(country_id, service_code)
    country_info = get_country_info(country_id)
    service_info = get_service_info(service_code)

    if user_bal < selling_price:
        await query.answer("❌ Insufficient balance! Please recharge your wallet.", show_alert=True)
        return

    await query.edit_message_text(
        text="⏳ <b>Allocating your virtual number...</b>\nPlease wait a few seconds...",
        parse_mode="HTML"
    )

    try:
        await db.update_balance(
            user_id,
            -selling_price,
            "PURCHASE",
            f"Buy {service_info['name']} ({country_info['name']})"
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Failed to process balance: {e}")
        return

    try:
        result = await sms_client.get_number(
            service=service_code,
            country=country_id,
            max_price=cost_price * 1.5
        )
        order_id = result["id"]
        phone_number = result["number"]
        formatted_num = f"+{phone_number}" if not phone_number.startswith("+") else phone_number

    except SastaSMSAPIError as e:
        await db.update_balance(
            user_id,
            selling_price,
            "REFUND",
            f"Refund for failed purchase: {service_info['name']}"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Try Again", callback_data=f"buy_confirm:{country_id}:{service_code}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        await query.edit_message_text(
            text=f"❌ <b>Unable to get number:</b>\n{str(e)}\n\n💰 Your balance of ₹{selling_price:.2f} has been refunded.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return
    except Exception as e:
        await db.update_balance(user_id, selling_price, "REFUND", f"Refund error: {service_info['name']}")
        await query.edit_message_text(
            text=f"❌ An error occurred: {str(e)}\n\n💰 Your balance was refunded.",
            reply_markup=build_main_menu(user_id),
            parse_mode="HTML"
        )
        return

    await db.create_order(
        user_id=user_id,
        order_id=order_id,
        phone_number=formatted_num,
        service_code=service_code,
        service_name=service_info["name"],
        country_id=country_id,
        country_name=country_info["name"],
        cost_price=cost_price,
        selling_price=selling_price,
        timeout_minutes=ORDER_TIMEOUT_MINUTES
    )

    keyboard = [
        [InlineKeyboardButton("🔄 ⚡ Check Status / OTP", callback_data=f"order_status:{order_id}")],
        [
            InlineKeyboardButton("🔁 📲 Request REOTP", callback_data=f"order_reotp:{order_id}"),
            InlineKeyboardButton("🔴 ❌ Cancel & Refund", callback_data=f"order_cancel:{order_id}")
        ],
        [InlineKeyboardButton("🏠 🔙 Main Menu", callback_data="menu_main")]
    ]

    text = (
        f"🎉 <b>Virtual Number Allocated!</b>\n\n"
        f"📱 <b>Service:</b> {service_info['icon']} {service_info['name']}\n"
        f"🌍 <b>Country:</b> {country_info['flag']} {country_info['name']}\n"
        f"📞 <b>Phone Number:</b> <code>{formatted_num}</code> (Tap to copy)\n"
        f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n\n"
        f"⏳ <b>Status:</b> Waiting for SMS/OTP...\n"
        f"⏱️ <i>Validity: 20 minutes. As soon as the SMS arrives, the OTP will be sent to you here automatically!</i>"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# -------------------------------------------------------------
# Active Orders, Manual Check & REOTP
# -------------------------------------------------------------

async def show_active_orders(query, user_id: int):
    orders = await db.get_active_orders_for_user(user_id)
    if not orders:
        keyboard = [
            [InlineKeyboardButton("💥 🎰 Colour Trading OTP (₹20)", callback_data="colour_trading_menu")],
            [InlineKeyboardButton("📱 Buy General Number", callback_data="buy_countries:0")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")]
        ]
        await query.edit_message_text(
            text="📭 <b>No active orders found.</b>\nYou don't have any pending numbers waiting for OTP.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    keyboard = []
    text = "⏳ <b>Your Active Orders:</b>\n\n"
    now = time.time()
    for o in orders:
        time_left = max(0, int(o["expires_at"] - now))
        mins, secs = divmod(time_left, 60)
        text += (
            f"📱 <b>{o['service_name']}</b> ({o['country_name']})\n"
            f"📞 <code>{o['phone_number']}</code>\n"
            f"⏱️ Time Left: {mins}m {secs}s\n"
            f"─────────────────────\n"
        )
        keyboard.append([
            InlineKeyboardButton(f"🔍 Check {o['phone_number']}", callback_data=f"order_status:{o['order_id']}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"order_cancel:{o['order_id']}")
        ])

    keyboard.append([InlineKeyboardButton("🔙 🏠 Main Menu", callback_data="menu_main")])
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def handle_order_status(query, order_id: str):
    order = await db.get_order_by_order_id(order_id)
    if not order:
        await query.answer("Order not found.", show_alert=True)
        return

    if order["status"] == "COMPLETED":
        text = (
            f"✅ <b>Order Completed!</b>\n\n"
            f"📱 Service: {order['service_name']}\n"
            f"📞 Number: <code>{order['phone_number']}</code>\n"
            f"🔑 <b>OTP:</b> <code>{order['otp_code']}</code>\n"
        )
        keyboard = [
            [InlineKeyboardButton("🔁 Request Another OTP (REOTP)", callback_data=f"order_reotp:{order_id}")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    if order["status"] in ("CANCELLED", "EXPIRED"):
        await query.answer(f"This order is already {order['status'].lower()}.", show_alert=True)
        return

    try:
        status_type, code = await sms_client.get_status(order_id)
    except Exception as e:
        await query.answer(f"Error checking status: {e}", show_alert=True)
        return

    if status_type in ("OK", "WAIT_RETRY") and code:
        await db.update_order_status(order_id, "COMPLETED", otp_code=code)
        try:
            await sms_client.set_status(order_id, 6)
        except Exception:
            pass

        text = (
            f"🎉 <b>OTP RECEIVED!</b>\n\n"
            f"📱 <b>Service:</b> {order['service_name']}\n"
            f"📞 <b>Number:</b> <code>{order['phone_number']}</code>\n\n"
            f"🔑 <b>OTP Code:</b> <code>{code}</code> (Tap to copy)\n\n"
            f"✅ Order completed successfully!"
        )
        keyboard = [
            [InlineKeyboardButton("🔁 Request Another OTP (REOTP)", callback_data=f"order_reotp:{order_id}")],
            [InlineKeyboardButton("💥 Buy Another Number", callback_data="colour_trading_menu")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    elif status_type == "CANCEL":
        await db.update_order_status(order_id, "CANCELLED")
        await db.update_balance(order["user_id"], order["selling_price"], "REFUND", f"Refund for order #{order_id}")
        await query.answer("Order was cancelled by provider. Refunded to wallet.", show_alert=True)
        await show_active_orders(query, order["user_id"])
        return

    now = time.time()
    time_left = max(0, int(order["expires_at"] - now))
    mins, secs = divmod(time_left, 60)

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh Again", callback_data=f"order_status:{order_id}")],
        [
            InlineKeyboardButton("🔁 Request REOTP", callback_data=f"order_reotp:{order_id}"),
            InlineKeyboardButton("❌ Cancel & Refund", callback_data=f"order_cancel:{order_id}")
        ],
        [InlineKeyboardButton("🔙 Active Orders", callback_data="active_orders")]
    ]

    text = (
        f"⏳ <b>Waiting for OTP...</b>\n\n"
        f"📱 <b>Service:</b> {order['service_name']}\n"
        f"📞 <b>Number:</b> <code>{order['phone_number']}</code> (Tap to copy)\n"
        f"⏱️ <b>Time Remaining:</b> {mins}m {secs}s\n\n"
        f"💡 <i>Tip: The OTP will be automatically delivered here the moment it arrives!</i>"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    await query.answer("Checked! Still waiting for SMS...")


async def handle_order_reotp(query, order_id: str):
    """Request a secondary OTP on the same number (REOTP)."""
    order = await db.get_order_by_order_id(order_id)
    if not order:
        await query.answer("Order not found.", show_alert=True)
        return

    try:
        success = await sms_client.request_another_otp(order_id)
        if success:
            await query.answer("🔁 REOTP Requested! Waiting for the next code...", show_alert=True)
            await db.update_order_status(order_id, "PENDING")
            await handle_order_status(query, order_id)
        else:
            await query.answer("Could not request another OTP for this number.", show_alert=True)
    except Exception as e:
        await query.answer(f"REOTP error: {e}", show_alert=True)


async def handle_order_cancel(query, order_id: str):
    order = await db.get_order_by_order_id(order_id)
    if not order:
        await query.answer("Order not found.", show_alert=True)
        return

    if order["status"] not in ("PENDING", "COMPLETED"):
        await query.answer(f"Cannot cancel: order is already {order['status']}", show_alert=True)
        return

    try:
        await sms_client.cancel_number(order_id)
    except Exception as e:
        logger.warning(f"Failed to cancel on SastaSMS provider: {e}")

    await db.update_order_status(order_id, "CANCELLED")
    new_bal = await db.update_balance(
        order["user_id"],
        order["selling_price"],
        "REFUND",
        f"Refund for cancelled order #{order_id}"
    )

    await query.answer("✅ Order cancelled! Amount refunded to your wallet.", show_alert=True)

    text = (
        f"❌ <b>Order Cancelled & Refunded</b>\n\n"
        f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
        f"📞 <b>Number:</b> <code>{order['phone_number']}</code>\n"
        f"💰 <b>Refund Amount:</b> ₹{order['selling_price']:.2f}\n"
        f"💳 <b>Updated Balance:</b> ₹{new_bal:.2f}\n"
    )
    keyboard = [
        [InlineKeyboardButton("💥 🎰 Colour Trading OTP (₹20)", callback_data="colour_trading_menu")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# -------------------------------------------------------------
# Wallet & Payment System (CryptoBot + UPI QR)
# -------------------------------------------------------------

async def show_wallet(query, user_id: int):
    user = await db.get_user(user_id)
    bal = user["balance"] if user else 0.0
    spent = user["total_spent"] if user else 0.0

    keyboard = [
        [
            InlineKeyboardButton("🟢 🎮 GoaGames Instant UPI (₹100+)", callback_data="goagame_packages"),
        ],
        [
            InlineKeyboardButton("🔵 📲 Direct UPI QR (₹10 - ₹50 Auto-Approve)", callback_data="upi_packages"),
        ],
        [
            InlineKeyboardButton("🟣 ⚡ Crypto (USDT / CryptoBot @ ₹94)", callback_data="crypto_packages"),
        ],
        [
            InlineKeyboardButton("🔙 🏠 Main Menu", callback_data="menu_main")
        ]
    ]

    text = (
        f"💳 <b>My Wallet & Deposits</b>\n\n"
        f"💰 <b>Current Balance:</b> <b>₹{bal:.2f}</b>\n"
        f"📊 <b>Total Spent:</b> ₹{spent:.2f}\n\n"
        f"Choose your preferred instant deposit method:\n"
        f"• <b>GoaGames Instant UPI:</b> Automated dynamic UPI checkout (₹100+)\n"
        f"• <b>Direct UPI QR:</b> Scan & Pay for small amounts (₹10, ₹20, ₹50)\n"
        f"• <b>Crypto (USDT / CryptoBot):</b> 100% automated instant crypto recharge"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def show_goagame_packages(query, user_id: int):
    keyboard = [
        [
            InlineKeyboardButton("🟢 ➕ Add ₹100", callback_data="goagame_order:100"),
            InlineKeyboardButton("🔵 ➕ Add ₹200", callback_data="goagame_order:200"),
        ],
        [
            InlineKeyboardButton("🟣 ➕ Add ₹500", callback_data="goagame_order:500"),
            InlineKeyboardButton("🟡 ➕ Add ₹1,000", callback_data="goagame_order:1000"),
        ],
        [
            InlineKeyboardButton("🔙 💼 Back to Wallet", callback_data="wallet")
        ]
    ]

    text = (
        f"🎮 <b>GoaGames Instant UPI Gateway</b>\n\n"
        f"• <b>Payment Mode:</b> Dynamic UPI-QR (ArbPay / GPay / PhonePe / Paytm)\n"
        f"• <b>Instant Auto-Credit:</b> System checks payment status and credits balance automatically!\n"
        f"• <b>Platform Limit:</b> Minimum deposit is ₹100.00\n\n"
        f"<i>Select deposit amount to proceed:</i>"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def create_goagame_deposit(query, context: ContextTypes.DEFAULT_TYPE, amount: float):
    user_id = query.from_user.id
    await query.edit_message_text("⏳ <i>Connecting to GoaGames UPI gateway, generating payment link...</i>", parse_mode="HTML")

    ok, data, err = await goagame_gateway.create_deposit_order(amount)
    if not ok:
        keyboard = [[InlineKeyboardButton("🔙 Back to Wallet", callback_data="wallet")]]
        await query.edit_message_text(f"❌ <b>Gateway Error:</b> {err}\n\nPlease try again or use direct UPI QR.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    submit_url = data["submit_url"]
    rec_num = data["recharge_number"]

    keyboard = [
        [InlineKeyboardButton("🟢 📲 Pay via UPI Page", url=submit_url)],
        [InlineKeyboardButton("🔄 ⚡ Check Payment Status", callback_data=f"goagame_check:{rec_num}:{amount}")],
        [InlineKeyboardButton("🔙 💼 Back to Wallet", callback_data="wallet")]
    ]

    text = (
        f"🧾 <b>GoaGames UPI Order Generated!</b>\n\n"
        f"💰 <b>Deposit Amount:</b> <b>₹{amount:.2f}</b>\n"
        f"🆔 <b>Recharge ID:</b> <code>{rec_num}</code>\n"
        f"⚡ <b>Gateway:</b> ArbPay UPI-QR\n\n"
        f"<b>Instructions:</b>\n"
        f"1. Tap <b>'📲 Pay via UPI Page'</b> to open UPI app / scan QR.\n"
        f"2. Complete the payment of exact ₹{amount:.2f}.\n"
        f"3. Return here and tap <b>'🔄 Check Payment Status'</b> (or wait, auto-detects in a few seconds)!"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def handle_goagame_check(query, rec_num: str, amount: float, context: ContextTypes.DEFAULT_TYPE):
    user_id = query.from_user.id
    status, paid_amount = await goagame_gateway.check_order_status(rec_num)

    if status == "SUCCESS":
        credit_amt = paid_amount if paid_amount > 0 else amount
        new_bal = await db.update_balance(
            user_id,
            credit_amt,
            "DEPOSIT_GOAGAME",
            f"GoaGames UPI Deposit #{rec_num}"
        )
        keyboard = [
            [InlineKeyboardButton("💥 🎰 Buy Colour Trading OTP (₹20)", callback_data="colour_trading_menu")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        text = (
            f"🎉 <b>Deposit Successful!</b>\n\n"
            f"💰 <b>Amount Credited:</b> <b>₹{credit_amt:.2f}</b>\n"
            f"💼 <b>New Wallet Balance:</b> <b>₹{new_bal:.2f}</b>\n"
            f"🆔 <b>Order Reference:</b> <code>{rec_num}</code>\n\n"
            f"<i>Your balance has been updated instantly. Enjoy our OTP services!</i>"
        )
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif status == "FAILED":
        await query.answer("❌ This order was cancelled or timed out. Please generate a new deposit order.", show_alert=True)
    else:
        await query.answer("⏳ Payment is still pending. If you just paid, please wait a few seconds and tap again!", show_alert=True)


async def show_crypto_packages(query, user_id: int):
    keyboard = []
    row = []
    for pkg in CRYPTO_PACKAGES:
        row.append(
            InlineKeyboardButton(
                pkg["label"],
                callback_data=f"crypto_create:{pkg['tokens']}"
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("✏️ 💬 Custom Amount (INR / Tokens)", callback_data="crypto_custom")
    ])
    keyboard.append([
        InlineKeyboardButton("🌐 🔗 CCPayment USDT Deposit Address", callback_data="ccpayment_address")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 💼 Back to Wallet", callback_data="wallet")
    ])

    text = (
        f"⚡ <b>Automated CryptoBot & CCPayment Deposit</b>\n\n"
        f"• <b>Exchange Rate:</b> 1 USD / USDT = ₹94 INR\n"
        f"• <b>Instant Settlement:</b> Verified automatically within 5 seconds!\n"
        f"• <b>Mathematical Half-Up rounding:</b> Exact ₹94 rate calculation.\n\n"
        f"<i>Select a top-up package or generate direct CCPayment address:</i>"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def handle_ccpayment_address(query, user_id: int):
    await query.edit_message_text("⏳ <i>Generating CCPayment deposit address...</i>", parse_mode="HTML")
    ok, data, err = await ccpayment_gateway.create_deposit_address(user_id, chain="TRC20", crypto="USDT")
    keyboard = [[InlineKeyboardButton("🔙 Back to Packages", callback_data="crypto_packages")]]
    if not ok:
        await query.edit_message_text(
            f"ℹ️ <b>CCPayment Notice:</b>\n{err}\n\n"
            f"<i>💡 Tip: You can use @CryptoBot (USDT) above which is 100% active and working right now!</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    addr = data.get("address", "")
    chain = data.get("chain", "TRC20")
    text = (
        f"🌐 <b>CCPayment USDT Deposit Address</b>\n\n"
        f"• <b>Network:</b> <code>{chain}</code>\n"
        f"• <b>Deposit Address:</b>\n<code>{addr}</code>\n\n"
        f"⚠️ <i>Please send only USDT ({chain}) to this address. Funds will be credited after blockchain confirmation.</i>"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def create_crypto_invoice(query, context: ContextTypes.DEFAULT_TYPE, target_tokens: int):
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    # Call CryptoBot API
    res = crypto_engine.create_token_invoice(
        user_id=user_id,
        chat_id=chat_id,
        target_tokens=target_tokens,
        bot_username="ModedSmSBot"
    )

    if not res.get("success"):
        await query.answer(f"Failed to generate invoice: {res.get('error')}", show_alert=True)
        return

    invoice_id = res["invoice_id"]
    pay_url = res["pay_url"]
    inv_amt = res["invoice_amount"]
    target_usd = res["target_usd"]

    keyboard = [
        [InlineKeyboardButton("🟢 💳 Pay via CryptoBot", url=pay_url)],
        [InlineKeyboardButton("🔄 ⚡ Check Payment Status", callback_data=f"crypto_check:{invoice_id}")],
        [InlineKeyboardButton("🔙 💼 Back to Packages", callback_data="crypto_packages")]
    ]

    text = (
        f"🧾 <b>CryptoBot Invoice Generated!</b>\n\n"
        f"💰 <b>Credited Balance:</b> <b>₹{target_tokens:.2f}</b>\n"
        f"💵 <b>Exchange Rate:</b> <code>1 USDT = ₹94 INR</code>\n"
        f"💵 <b>Target Value:</b> <code>${target_usd} USDT</code>\n"
        f"💳 <b>Invoice Total (incl. 3% gateway fee):</b> <b>${inv_amt} USDT</b>\n"
        f"🆔 <b>Invoice ID:</b> <code>#{invoice_id}</code>\n\n"
        f"<b>Instructions:</b>\n"
        f"1. Click <b>'💳 Pay via CryptoBot'</b> below to pay.\n"
        f"2. After payment, click <b>'🔄 Check Payment Status'</b> (or wait, bot auto-detects in 5s)!\n"
        f"3. Balance will be credited instantly."
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def handle_crypto_check(query, invoice_id: str):
    is_settled, msg, rec = crypto_engine.check_and_settle_invoice(invoice_id, db=db)
    if is_settled:
        keyboard = [
            [InlineKeyboardButton("💥 🎰 Buy Colour Trading OTP (₹20)", callback_data="colour_trading_menu")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ]
        await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await query.answer("Payment still pending. If you just paid, please wait a few seconds and tap again!", show_alert=True)


async def show_upi_packages(query):
    keyboard = [
        [
            InlineKeyboardButton("🟢 ➕ Add ₹10", callback_data="recharge_amount:10"),
            InlineKeyboardButton("🔵 ➕ Add ₹20", callback_data="recharge_amount:20"),
        ],
        [
            InlineKeyboardButton("🟣 ➕ Add ₹50", callback_data="recharge_amount:50"),
            InlineKeyboardButton("🟡 ➕ Add ₹100", callback_data="recharge_amount:100"),
        ],
        [
            InlineKeyboardButton("✏️ 💬 Custom UPI Amount", callback_data="recharge_custom"),
        ],
        [
            InlineKeyboardButton("🔙 💼 Back to Wallet", callback_data="wallet")
        ]
    ]

    text = (
        f"📲 <b>UPI Deposit (GPay / PhonePe / Paytm / BHIM)</b>\n\n"
        f"Select an amount to generate a dynamic UPI QR Code:"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def initiate_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float):
    query = update.callback_query
    user_id = update.effective_user.id

    if amount < MIN_RECHARGE:
        if query:
            await query.answer(f"Minimum recharge amount is ₹{MIN_RECHARGE:.2f}", show_alert=True)
        return

    upi_id = await db.get_setting("upi_id", "your-upi@oksbi")
    upi_name = await db.get_setting("upi_name", "OTP Service")

    qr_bytes = generate_upi_qr(upi_id, upi_name, amount)

    caption = (
        f"📲 <b>Deposit ₹{amount:.2f} via UPI</b>\n\n"
        f"🔹 <b>UPI ID:</b> <code>{upi_id}</code> (Tap to copy)\n"
        f"🔹 <b>Payee Name:</b> {upi_name}\n"
        f"🔹 <b>Amount:</b> ₹{amount:.2f}\n\n"
        f"<b>Instructions:</b>\n"
        f"1. Scan the QR code or pay to the UPI ID.\n"
        f"2. After payment, click <b>'📝 Submit UTR'</b> below.\n"
        f"3. Send your 12-digit UPI Reference / UTR Number.\n"
        f"<i>Your balance will be credited instantly upon approval!</i>"
    )

    keyboard = [
        [InlineKeyboardButton("🟢 📝 Submit UTR / Ref Number", callback_data=f"submit_utr_btn:{amount}")],
        [InlineKeyboardButton("🔙 💼 Back to Wallet", callback_data="wallet")]
    ]

    context.user_data["pending_recharge_amount"] = amount

    chat_id = update.effective_chat.id
    if query:
        await query.message.delete()
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=qr_bytes,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def ask_utr(query, context: ContextTypes.DEFAULT_TYPE, amount: float):
    context.user_data["waiting_for"] = "utr"
    context.user_data["recharge_amount"] = amount

    text = (
        f"📝 <b>Submit UTR Number for ₹{amount:.2f}</b>\n\n"
        f"Please send the <b>12-digit UPI Reference ID / UTR Number</b> as a text message here.\n"
        f"Example: <code>429182736451</code>"
    )
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="wallet")]]
    await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    waiting_for = context.user_data.get("waiting_for")

    if not waiting_for:
        return

    if waiting_for == "crypto_custom_amount":
        try:
            tokens = int(float(text))
            if tokens < 10:
                await update.message.reply_text("❌ Minimum deposit is ₹10. Please enter a valid amount:")
                return
            context.user_data["waiting_for"] = None
            res = crypto_engine.create_token_invoice(
                user_id=user_id,
                chat_id=update.effective_chat.id,
                target_tokens=tokens,
                bot_username="ModedSmSBot"
            )
            if not res.get("success"):
                await update.message.reply_text(f"❌ Error generating invoice: {res.get('error')}")
                return

            inv_id = res["invoice_id"]
            keyboard = [
                [InlineKeyboardButton("💳 Pay via CryptoBot", url=res["pay_url"])],
                [InlineKeyboardButton("🔄 Check Payment Status", callback_data=f"crypto_check:{inv_id}")],
                [InlineKeyboardButton("🔙 Back to Wallet", callback_data="wallet")]
            ]
            await update.message.reply_text(
                f"🧾 <b>CryptoBot Invoice Generated!</b>\n\n"
                f"💰 <b>Credited Balance:</b> <b>₹{tokens:.2f}</b>\n"
                f"💵 <b>Target:</b> <code>${res['target_usd']} USDT</code>\n"
                f"💳 <b>Invoice Total:</b> <b>${res['invoice_amount']} USDT</b>\n\n"
                f"Tap button below to pay via CryptoBot:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number (e.g. 100):")
        return

    elif waiting_for == "custom_recharge_amount":
        try:
            amount = float(text)
            if amount < MIN_RECHARGE:
                await update.message.reply_text(f"❌ Minimum recharge is ₹{MIN_RECHARGE:.2f}. Please enter a valid amount:")
                return
            context.user_data["waiting_for"] = None
            await initiate_recharge(update, context, amount)
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number (e.g. 50 or 150):")
        return

    elif waiting_for == "utr":
        amount = context.user_data.get("recharge_amount", 0.0)
        utr = text.strip()
        if len(utr) < 6:
            await update.message.reply_text("❌ Please enter a valid UTR / Transaction ID (at least 6-12 characters):")
            return

        context.user_data["waiting_for"] = None

        verifying_msg = await update.message.reply_text(
            "🔍 <i>Checking FamPay transaction alert in Gmail... Please wait a few seconds...</i>",
            parse_mode="HTML"
        )

        # Attempt Automated Verification via FamPay Gmail Alerts
        verify_res = await fampay_verifier.verify_transaction(expected_amount=amount, utr=utr)

        if verify_res.get("verified"):
            try:
                req_id = await db.create_recharge_request(user_id, amount, utr)
                await db.process_recharge_request(req_id, True)
            except Exception:
                pass

            new_bal = await db.update_balance(
                user_id,
                amount,
                "DEPOSIT_FAMPAY",
                f"Auto-verified FamPay UPI: {utr}"
            )
            success_text = (
                f"🎉 <b>Payment Auto-Verified & Credited!</b>\n\n"
                f"💰 <b>Amount Credited:</b> <b>+₹{amount:.2f}</b>\n"
                f"💳 <b>Updated Wallet Balance:</b> <b>₹{new_bal:.2f}</b>\n"
                f"🔢 <b>Verified UTR:</b> <code>{utr}</code>\n"
                f"⚡ <b>Method:</b> FamPay Gmail Alert (Instant Auto-Credit)\n\n"
                f"<i>Thank you! You can now purchase your OTPs instantly.</i>"
            )
            await verifying_msg.edit_text(success_text, parse_mode="HTML")

            # Notify admins of automated approval
            user_name = update.effective_user.username or update.effective_user.first_name
            admin_text = (
                f"⚡ <b>FamPay UPI Auto-Verified & Credited!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>User:</b> @{user_name} (<code>{user_id}</code>)\n"
                f"💵 <b>Amount:</b> +₹{amount:.2f}\n"
                f"🔢 <b>UTR:</b> <code>{utr}</code>\n"
                f"💳 <b>Updated Balance:</b> ₹{new_bal:.2f}\n"
                f"📧 <b>Alert Subject:</b> <i>{verify_res.get('subject', 'FamPay Credit')}</i>"
            )
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="HTML")
                except Exception:
                    pass
            return

        # Fallback to manual admin approval if alert delayed or not found
        try:
            req_id = await db.create_recharge_request(user_id, amount, utr)
        except Exception:
            await verifying_msg.edit_text("⚠️ This UTR has already been submitted or is being processed.")
            return

        await verifying_msg.edit_text(
            f"✅ <b>Deposit Request Submitted!</b>\n\n"
            f"💰 Amount: ₹{amount:.2f}\n"
            f"🔢 UTR: <code>{utr}</code>\n\n"
            f"ℹ️ <i>FamPay alert not detected in Gmail yet (may take 30-60s). Request forwarded to Admin for 1-click verification!</i>",
            parse_mode="HTML"
        )

        user_name = update.effective_user.username or update.effective_user.first_name
        admin_keyboard = [
            [
                InlineKeyboardButton(f"🟢 ✅ Approve ₹{amount:.2f}", callback_data=f"appr_rech:{req_id}"),
                InlineKeyboardButton("🔴 ❌ Reject", callback_data=f"rej_rech:{req_id}")
            ]
        ]
        admin_text = (
            f"🔔 <b>New Recharge Request #{req_id}</b>\n\n"
            f"👤 User: @{user_name} (ID: <code>{user_id}</code>)\n"
            f"💵 Amount: <b>₹{amount:.2f}</b>\n"
            f"🔢 UTR: <code>{utr}</code>\n\n"
            f"Verify payment and click Approve/Reject below:"
        )
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=InlineKeyboardMarkup(admin_keyboard),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
                logger.error(f"Failed to notify admin {admin_id}: {e}")

    elif waiting_for == "broadcast_text":
        if not is_admin(user_id):
            return
        context.user_data["waiting_for"] = None
        users = await db.get_all_users()
        sent = 0
        failed = 0
        status_msg = await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u["user_id"],
                    text=f"📢 <b>Announcement:</b>\n\n{text}",
                    parse_mode="HTML"
                )
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)

        await status_msg.edit_text(f"✅ Broadcast Complete!\nSent: {sent}\nFailed/Blocked: {failed}")


# -------------------------------------------------------------
# Admin Actions & Approvals
# -------------------------------------------------------------

async def handle_recharge_decision(query, req_id: int, approve: bool, context: ContextTypes.DEFAULT_TYPE):
    req = await db.get_recharge_request(req_id)
    if not req or req["status"] != "PENDING":
        await query.answer("This request has already been processed.", show_alert=True)
        return

    processed = await db.process_recharge_request(req_id, approve)
    if not processed:
        await query.answer("Request processing error.", show_alert=True)
        return

    user_id = req["user_id"]
    amount = req["amount"]
    utr = req["utr"]

    if approve:
        new_bal = await db.update_balance(user_id, amount, "DEPOSIT", f"UPI Deposit UTR: {utr}")
        await query.edit_message_text(
            f"✅ <b>Approved Request #{req_id}</b>\nCredited ₹{amount:.2f} to user <code>{user_id}</code>.",
            parse_mode="HTML"
        )
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 <b>Recharge Successful!</b>\n\n"
                    f"💰 Amount: <b>₹{amount:.2f}</b>\n"
                    f"🔢 UTR: <code>{utr}</code>\n"
                    f"💳 Current Balance: <b>₹{new_bal:.2f}</b>\n\n"
                    f"You can now purchase OTP numbers!"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await query.edit_message_text(
            f"❌ <b>Rejected Request #{req_id}</b> for user <code>{user_id}</code>.",
            parse_mode="HTML"
        )
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Your recharge request for ₹{amount:.2f} (UTR: <code>{utr}</code>) was rejected. If you believe this was an error, please contact support.",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def show_admin_panel(query):
    stats = await db.get_bot_stats()
    margin_pct = await db.get_setting("margin_percent", str(DEFAULT_MARGIN_PERCENT))
    margin_flat = await db.get_setting("margin_flat", str(DEFAULT_MARGIN_FLAT))
    upi_id = await db.get_setting("upi_id", "Not set")

    try:
        prov_bal = await sms_client.get_balance()
        prov_bal_str = f"₹{prov_bal:.2f}"
    except Exception as e:
        prov_bal_str = f"Error: {e}"

    keyboard = [
        [
            InlineKeyboardButton("🔄 ⚡ Refresh Stats", callback_data="admin_panel"),
            InlineKeyboardButton("📢 💬 Broadcast Message", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("🔙 🏠 Main Menu", callback_data="menu_main")
        ]
    ]

    text = (
        f"👑 <b>Admin Control Panel (@ModedSmSBot)</b>\n\n"
        f"🌐 <b>SastaSMS Provider Balance:</b> {prov_bal_str}\n"
        f"👥 <b>Total Users:</b> {stats['total_users']}\n"
        f"📦 <b>Total Orders:</b> {stats['total_orders']}\n"
        f"⏳ <b>Active Orders:</b> {stats['active_orders']}\n"
        f"💰 <b>Total Sales Volume:</b> ₹{stats['total_volume']:.2f}\n"
        f"📈 <b>Estimated Profit:</b> ₹{stats['total_profit']:.2f}\n\n"
        f"⚙️ <b>Settings:</b>\n"
        f"• Colour Trading Price: <b>₹{COLOUR_TRADING_PRICE:.2f}</b>\n"
        f"• Margin %: <code>{margin_pct}%</code>\n"
        f"• Flat Margin: <code>₹{margin_flat}</code>\n"
        f"• Current UPI ID: <code>{upi_id}</code>\n\n"
        f"<b>Admin Commands:</b>\n"
        f"• <code>/addbal &lt;user_id&gt; &lt;amount&gt;</code> - Add balance\n"
        f"• <code>/deductbal &lt;user_id&gt; &lt;amount&gt;</code> - Deduct balance\n"
        f"• <code>/setmargin &lt;percent&gt;</code> - Set markup %\n"
        f"• <code>/setflat &lt;amount&gt;</code> - Set flat markup ₹\n"
        f"• <code>/setupi &lt;upi_id&gt; &lt;name&gt;</code> - Set UPI ID\n"
        f"• <code>/broadcast &lt;message&gt;</code> - Send message to all"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# -------------------------------------------------------------
# Admin Commands (/addbal, /deductbal, /setmargin, /setupi, /broadcast)
# -------------------------------------------------------------

async def admin_add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: <code>/addbal &lt;user_id&gt; &lt;amount&gt;</code>", parse_mode="HTML")
        return
    try:
        target_uid = int(args[0])
        amount = float(args[1])
        new_bal = await db.update_balance(target_uid, amount, "ADMIN_ADJUST", f"Admin added ₹{amount}")
        await update.message.reply_text(f"✅ Added ₹{amount:.2f} to user {target_uid}. New balance: ₹{new_bal:.2f}")
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=f"🎁 <b>Balance Added!</b>\n₹{amount:.2f} was added to your wallet by Admin.\nNew Balance: <b>₹{new_bal:.2f}</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def admin_deduct_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: <code>/deductbal &lt;user_id&gt; &lt;amount&gt;</code>", parse_mode="HTML")
        return
    try:
        target_uid = int(args[0])
        amount = float(args[1])
        new_bal = await db.update_balance(target_uid, -amount, "ADMIN_ADJUST", f"Admin deducted ₹{amount}")
        await update.message.reply_text(f"✅ Deducted ₹{amount:.2f} from user {target_uid}. New balance: ₹{new_bal:.2f}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def admin_set_margin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: <code>/setmargin &lt;percent&gt;</code> (e.g. /setmargin 25)", parse_mode="HTML")
        return
    try:
        pct = float(args[0])
        await db.set_setting("margin_percent", str(pct))
        await update.message.reply_text(f"✅ Profit margin set to <b>{pct}%</b>", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number.")


async def admin_set_flat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: <code>/setflat &lt;amount&gt;</code> (e.g. /setflat 2)", parse_mode="HTML")
        return
    try:
        flat = float(args[0])
        await db.set_setting("margin_flat", str(flat))
        await update.message.reply_text(f"✅ Flat margin set to <b>₹{flat:.2f}</b>", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number.")


async def admin_set_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: <code>/setupi &lt;upi_id&gt; [Payee Name]</code>", parse_mode="HTML")
        return
    upi_id = args[0]
    name = " ".join(args[1:]) if len(args) > 1 else "OTP Service"
    await db.set_setting("upi_id", upi_id)
    await db.set_setting("upi_name", name)
    await update.message.reply_text(f"✅ UPI Details updated!\nUPI ID: <code>{upi_id}</code>\nName: {name}", parse_mode="HTML")


async def admin_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    msg_text = update.message.text.split(maxsplit=1)
    if len(msg_text) < 2:
        await update.message.reply_text("Usage: <code>/broadcast &lt;your message&gt;</code>", parse_mode="HTML")
        return

    content = msg_text[1]
    users = await db.get_all_users()
    sent = 0
    failed = 0
    prog = await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u["user_id"],
                text=f"📢 <b>Announcement:</b>\n\n{content}",
                parse_mode="HTML"
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await prog.edit_text(f"✅ Broadcast finished!\nDelivered: {sent}\nFailed: {failed}")


# -------------------------------------------------------------
# Order History & Help
# -------------------------------------------------------------

async def show_history(query, user_id: int):
    orders = await db.get_user_order_history(user_id, limit=8)
    if not orders:
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")]]
        await query.edit_message_text("📋 <b>No past orders found.</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    text = "📋 <b>Your Recent Orders:</b>\n\n"
    for o in orders:
        status_emoji = "✅" if o["status"] == "COMPLETED" else "❌" if o["status"] == "CANCELLED" else "⏳"
        otp_str = f"Code: <code>{o['otp_code']}</code>" if o["otp_code"] else "No OTP received"
        text += (
            f"{status_emoji} <b>{o['service_name']}</b> ({o['country_name']})\n"
            f"📞 <code>{o['phone_number']}</code>\n"
            f"💵 ₹{o['selling_price']:.2f} | Status: <b>{o['status']}</b>\n"
            f"🔑 {otp_str}\n"
            f"─────────────────────\n"
        )

    keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")]]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def show_help(query):
    text = (
        "ℹ️ <b>Help & FAQ</b>\n\n"
        "<b>1. How to buy Colour Trading OTPs?</b>\n"
        "• Click <b>💥 Colour Trading Sites OTP (₹20)</b>\n"
        "• Select Jai Club, Jalwa Game, Raja Games, IN999, 51Games, etc.\n"
        "• Copy the allocated number and paste it in the game app.\n"
        "• As soon as the SMS is sent, the OTP will appear here automatically!\n\n"
        "<b>2. Multi-OTP & REOTP Support:</b>\n"
        "• Need multiple OTPs on the same number? Click <b>'🔁 Request REOTP'</b> anytime!\n\n"
        "<b>3. What if OTP does not arrive?</b>\n"
        "• Click <b>❌ Cancel & Refund</b> anytime.\n"
        "• If 20 minutes pass with no OTP, 100% of the money is auto-refunded to your wallet!\n\n"
        "<b>4. How to add balance?</b>\n"
        "• Click <b>💳 My Wallet / Deposit</b>\n"
        "• Top up instantly using <b>CryptoBot (@CryptoBot)</b> or scan the <b>UPI QR Code</b>."
    )
    keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")]]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# -------------------------------------------------------------
# Callback Query Router
# -------------------------------------------------------------

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "noop":
        return

    if data == "menu_main":
        await start_command(update, context)

    # Colour Trading Flows
    elif data == "colour_trading_menu":
        await show_colour_trading_menu(query)

    elif data.startswith("buy_colour_confirm:"):
        site_code = data.split(":", 1)[1]
        await show_colour_trading_confirm(query, site_code, user_id)

    elif data.startswith("buy_colour_execute:"):
        site_code = data.split(":", 1)[1]
        await execute_colour_purchase(query, site_code, user_id)

    # General Buy Flows
    elif data.startswith("buy_countries:"):
        page = int(data.split(":")[1])
        await show_countries(query, page)

    elif data.startswith("buy_services:"):
        parts = data.split(":")
        country_id = parts[1]
        page = int(parts[2])
        await show_services(query, country_id, page)

    elif data.startswith("buy_confirm:"):
        _, country_id, service_code = data.split(":")
        await show_purchase_confirm(query, country_id, service_code, user_id)

    elif data.startswith("buy_execute:"):
        _, country_id, service_code = data.split(":")
        await execute_purchase(query, country_id, service_code, user_id)

    # Order management
    elif data == "active_orders":
        await show_active_orders(query, user_id)

    elif data.startswith("order_status:"):
        order_id = data.split(":", 1)[1]
        await handle_order_status(query, order_id)

    elif data.startswith("order_reotp:"):
        order_id = data.split(":", 1)[1]
        await handle_order_reotp(query, order_id)

    elif data.startswith("order_cancel:"):
        order_id = data.split(":", 1)[1]
        await handle_order_cancel(query, order_id)

    # Wallet & CryptoBot Flows
    elif data == "wallet":
        await show_wallet(query, user_id)

    # GoaGames Instant UPI Gateway Flows
    elif data == "goagame_packages":
        await show_goagame_packages(query, user_id)

    elif data.startswith("goagame_order:"):
        amount = float(data.split(":")[1])
        await create_goagame_deposit(query, context, amount)

    elif data.startswith("goagame_check:"):
        parts = data.split(":")
        rec_num = parts[1]
        amount = float(parts[2]) if len(parts) > 2 else 100.0
        await handle_goagame_check(query, rec_num, amount, context)

    # Crypto Flows
    elif data == "crypto_packages":
        await show_crypto_packages(query, user_id)

    elif data == "ccpayment_address":
        await handle_ccpayment_address(query, user_id)

    elif data.startswith("crypto_create:"):
        tokens = int(data.split(":")[1])
        await create_crypto_invoice(query, context, tokens)

    elif data.startswith("crypto_check:"):
        inv_id = data.split(":", 1)[1]
        await handle_crypto_check(query, inv_id)

    elif data == "crypto_custom":
        context.user_data["waiting_for"] = "crypto_custom_amount"
        keyboard = [[InlineKeyboardButton("🔙 Back to Packages", callback_data="crypto_packages")]]
        await query.edit_message_text(
            "✏️ <b>Enter Custom Amount (INR / Tokens):</b>\nMinimum deposit is ₹10.\nPlease reply with the amount:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    # UPI Flows
    elif data == "upi_packages":
        await show_upi_packages(query)

    elif data.startswith("recharge_amount:"):
        amount = float(data.split(":")[1])
        await initiate_recharge(update, context, amount)

    elif data == "recharge_custom":
        context.user_data["waiting_for"] = "custom_recharge_amount"
        keyboard = [[InlineKeyboardButton("🔙 Back to Wallet", callback_data="wallet")]]
        await query.edit_message_text(
            f"✏️ <b>Enter Custom Amount (INR):</b>\nMinimum deposit is ₹{MIN_RECHARGE:.2f}.\nPlease reply with the amount:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data.startswith("submit_utr_btn:"):
        amount = float(data.split(":")[1])
        await ask_utr(query, context, amount)

    elif data == "history":
        await show_history(query, user_id)

    elif data == "help":
        await show_help(query)

    elif data == "admin_panel":
        if is_admin(user_id):
            await show_admin_panel(query)

    elif data == "admin_broadcast":
        if is_admin(user_id):
            context.user_data["waiting_for"] = "broadcast_text"
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]]
            await query.edit_message_text(
                "📢 <b>Broadcast Message:</b>\n\nPlease send the text/announcement you want to broadcast to all bot users:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

    elif data.startswith("appr_rech:"):
        if is_admin(user_id):
            req_id = int(data.split(":")[1])
            await handle_recharge_decision(query, req_id, True, context)

    elif data.startswith("rej_rech:"):
        if is_admin(user_id):
            req_id = int(data.split(":")[1])
            await handle_recharge_decision(query, req_id, False, context)


# -------------------------------------------------------------
# Background OTP Polling Worker
# -------------------------------------------------------------

async def background_otp_worker(app: Application):
    """
    Checks active orders continuously.
    Sends automatic notifications when OTP arrives.
    Expires and refunds orders older than 20 minutes.
    """
    logger.info("Background OTP Polling Worker started.")
    while True:
        try:
            pending_orders = await db.get_all_pending_orders()
            now = time.time()

            for order in pending_orders:
                order_id = order["order_id"]
                user_id = order["user_id"]
                expires_at = order["expires_at"]
                selling_price = order["selling_price"]
                phone_num = order["phone_number"]

                # 1. Check expiration
                if now >= expires_at:
                    try:
                        await sms_client.cancel_number(order_id)
                    except Exception:
                        pass
                    await db.update_order_status(order_id, "EXPIRED")
                    new_bal = await db.update_balance(
                        user_id,
                        selling_price,
                        "REFUND",
                        f"Auto-refund for expired order #{order_id}"
                    )
                    try:
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=(
                                f"⏰ <b>Order Expired & Refunded</b>\n\n"
                                f"Order: <code>{order_id}</code> ({phone_num})\n"
                                f"No SMS was received within 20 minutes.\n"
                                f"💰 <b>₹{selling_price:.2f}</b> has been automatically credited back to your wallet!\n"
                                f"💳 New Balance: <b>₹{new_bal:.2f}</b>"
                            ),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"Could not notify user {user_id} of expiry: {e}")
                    continue

                # 2. Check live status from SastaSMS
                try:
                    status_type, code = await sms_client.get_status(order_id)
                except Exception as e:
                    logger.debug(f"Status check error for order {order_id}: {e}")
                    continue

                if status_type in ("OK", "WAIT_RETRY") and code:
                    await db.update_order_status(order_id, "COMPLETED", otp_code=code)
                    try:
                        await sms_client.set_status(order_id, 6)
                    except Exception:
                        pass

                    keyboard = [
                        [InlineKeyboardButton("🔁 Request Another OTP (REOTP)", callback_data=f"order_reotp:{order_id}")],
                        [InlineKeyboardButton("💥 Buy Another Number", callback_data="colour_trading_menu")]
                    ]

                    try:
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=(
                                f"🎉 <b>NEW OTP RECEIVED!</b>\n\n"
                                f"📱 <b>Service:</b> {order['service_name']}\n"
                                f"📞 <b>Number:</b> <code>{phone_num}</code>\n\n"
                                f"🔑 <b>OTP Code:</b> <code>{code}</code> (Tap to copy)\n\n"
                                f"✅ Order completed successfully!"
                            ),
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to deliver OTP to user {user_id}: {e}")

                elif status_type == "CANCEL":
                    await db.update_order_status(order_id, "CANCELLED")
                    new_bal = await db.update_balance(
                        user_id,
                        selling_price,
                        "REFUND",
                        f"Auto-refund for cancelled order #{order_id}"
                    )
                    try:
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=(
                                f"❌ <b>Order Cancelled & Refunded</b>\n\n"
                                f"Order <code>{order_id}</code> was cancelled by the provider.\n"
                                f"💰 <b>₹{selling_price:.2f}</b> refunded to your wallet.\n"
                                f"💳 Current Balance: <b>₹{new_bal:.2f}</b>"
                            ),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Error in background OTP loop: {e}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def post_init(application: Application):
    """Start background tasks after bot initialization."""
    global TELEGRAM_APP
    TELEGRAM_APP = application
    # Start Cloud Health Server
    start_health_server(PORT)
    # Start CryptoBot background payment poller
    crypto_engine.start_background_poller(db=db, poll_interval=5)
    # Start background OTP polling worker
    asyncio.create_task(background_otp_worker(application))


# -------------------------------------------------------------
# Application Setup & Runner
# -------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        print("\n" + "=" * 60)
        print("❌ CONFIGURATION REQUIRED: Please set BOT_TOKEN in .env")
        print("=" * 60 + "\n")
        return

    print("🚀 Initializing SastaSMS OTP Bot (@ModedSmSBot)...")
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", lambda u, c: show_help(u.message)))
    app.add_handler(CommandHandler("admin", lambda u, c: show_admin_panel(u.message) if is_admin(u.effective_user.id) else None))
    app.add_handler(CommandHandler("addbal", admin_add_balance))
    app.add_handler(CommandHandler("deductbal", admin_deduct_balance))
    app.add_handler(CommandHandler("setmargin", admin_set_margin))
    app.add_handler(CommandHandler("setflat", admin_set_flat))
    app.add_handler(CommandHandler("setupi", admin_set_upi))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_cmd))

    # Callback Query Router
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Text Input (UTR submission, custom amounts, broadcast)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    print("✅ Bot is online & listening for updates!")
    app.run_polling()


if __name__ == "__main__":
    main()
