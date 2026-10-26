import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token (ModedSmSBot)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8997356612:AAEtzn1-g67geNaZ50qSal6bFRiU3H42DKg")

# SastaSMS API Key (https://sastasms.pro dashboard)
SASTASMS_API_KEY = os.getenv("SASTASMS_API_KEY", "stp_c2d6b6181ea95c27a5d73a32df4e5682608109a898ce3659")

# CryptoBot Pay Token (@CryptoBot API)
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "655242:AAYmK4gRIBr2k4KL6WaVMDtwC2sXjUcXFKE")

# SastaSMS Base Endpoint
BASE_API_URL = os.getenv("BASE_API_URL", "https://sastasms.pro/stubs/handler_api.php")

# Admin Telegram IDs (Comma separated integers)
ADMIN_IDS = [
    int(x.strip()) 
    for x in os.getenv("ADMIN_IDS", "").split(",") 
    if x.strip().isdigit()
]

# Colour Trading Fixed Selling Price (From user specification)
COLOUR_TRADING_PRICE = float(os.getenv("COLOUR_TRADING_PRICE", "20.0"))

# Profit Margin Settings for other general services
DEFAULT_MARGIN_PERCENT = float(os.getenv("DEFAULT_MARGIN_PERCENT", "20.0"))
DEFAULT_MARGIN_FLAT = float(os.getenv("DEFAULT_MARGIN_FLAT", "1.0"))

# UPI Payment Details for Deposits
DEFAULT_UPI_ID = os.getenv("DEFAULT_UPI_ID", "your-upi@oksbi")
DEFAULT_UPI_NAME = os.getenv("DEFAULT_UPI_NAME", "OTP Service")
MIN_RECHARGE = float(os.getenv("MIN_RECHARGE", "10.0"))

# Active order timeout in minutes
ORDER_TIMEOUT_MINUTES = int(os.getenv("ORDER_TIMEOUT_MINUTES", "20"))

# Background polling interval for active OTPs in seconds
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "4"))

# Database path
DB_PATH = os.getenv("DB_PATH", "bot_database.db")

# Port for cloud health check (Render / 24/7 Hosting)
PORT = int(os.getenv("PORT", "10000"))
