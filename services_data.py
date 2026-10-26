"""
Pre-mapped services and countries for SastaSMS API.
SastaSMS uses standard SMS-Activate service codes and country IDs.
"""

COLOUR_TRADING_PRICE = 20.0  # Fixed 20 Rs price for Colour Trading Sites

COLOUR_TRADING_SITES = [
    {"code": "ct_jaiclub", "name": "Jai Club", "icon": "🟢 🎰", "service": "ot", "country": "22", "style": "success"},
    {"code": "ct_jalwa", "name": "JALWA GAME", "icon": "🔴 🎲", "service": "ot", "country": "22", "style": "danger"},
    {"code": "ct_raja", "name": "RAJA GAMES", "icon": "🟣 👑", "service": "ot", "country": "22", "style": "primary"},
    {"code": "ct_in999", "name": "IN999", "icon": "🟢 🎯", "service": "ot", "country": "22", "style": "success"},
    {"code": "ct_51games", "name": "51GAMES", "icon": "🔴 🎮", "service": "ot", "country": "22", "style": "danger"},
    {"code": "ct_okwin", "name": "OKWIN", "icon": "🟣 🏆", "service": "ot", "country": "22", "style": "primary"},
    {"code": "ct_tiranga", "name": "Tiranga Games", "icon": "🟢 🌈", "service": "ot", "country": "22", "style": "success"},
    {"code": "ct_daman", "name": "Daman Games", "icon": "🔴 💎", "service": "ot", "country": "22", "style": "danger"},
    {"code": "ct_91club", "name": "91Club", "icon": "🟣 🍀", "service": "ot", "country": "22", "style": "primary"},
    {"code": "ct_other", "name": "Other Colour Sites", "icon": "🔥 🌈", "service": "ot", "country": "22", "style": "success"},
]

COLOUR_TRADING_MAP = {s["code"]: s for s in COLOUR_TRADING_SITES}

# Country ID mapping (SastaSMS standard)
COUNTRIES = [
    {"id": "22", "name": "India", "flag": "🇮🇳", "code": "+91"},
    {"id": "6", "name": "Indonesia", "flag": "🇮🇩", "code": "+62"},
    {"id": "187", "name": "USA", "flag": "🇺🇸", "code": "+1"},
    {"id": "16", "name": "United Kingdom", "flag": "🇬🇧", "code": "+44"},
    {"id": "0", "name": "Russia", "flag": "🇷🇺", "code": "+7"},
    {"id": "4", "name": "Philippines", "flag": "🇵🇭", "code": "+63"},
    {"id": "51", "name": "Vietnam", "flag": "🇻🇳", "code": "+84"},
    {"id": "73", "name": "Brazil", "flag": "🇧🇷", "code": "+55"},
    {"id": "32", "name": "South Africa", "flag": "🇿🇦", "code": "+27"},
    {"id": "15", "name": "Poland", "flag": "🇵🇱", "code": "+48"},
    {"id": "11", "name": "Ukraine", "flag": "🇺🇦", "code": "+380"},
    {"id": "86", "name": "Italy", "flag": "🇮🇹", "code": "+39"},
    {"id": "43", "name": "Germany", "flag": "🇩🇪", "code": "+49"},
    {"id": "78", "name": "France", "flag": "🇫🇷", "code": "+33"},
    {"id": "40", "name": "Spain", "flag": "🇪🇸", "code": "+34"},
    {"id": "56", "name": "Bangladesh", "flag": "🇧🇩", "code": "+880"},
    {"id": "10", "name": "Pakistan", "flag": "🇵🇰", "code": "+92"},
    {"id": "60", "name": "Malaysia", "flag": "🇲🇾", "code": "+60"},
    {"id": "62", "name": "Thailand", "flag": "🇹🇭", "code": "+66"},
    {"id": "13", "name": "Israel", "flag": "🇮🇱", "code": "+972"},
]

COUNTRY_MAP = {c["id"]: c for c in COUNTRIES}

# Popular Services mapping (SastaSMS service codes)
SERVICES = [
    {"code": "tg", "name": "Telegram", "icon": "✈️"},
    {"code": "wa", "name": "WhatsApp", "icon": "💬"},
    {"code": "go", "name": "Google / Gmail / YouTube", "icon": "🌐"},
    {"code": "ig", "name": "Instagram", "icon": "📸"},
    {"code": "fb", "name": "Facebook", "icon": "👥"},
    {"code": "tw", "name": "Twitter / X", "icon": "🐦"},
    {"code": "am", "name": "Amazon", "icon": "🛒"},
    {"code": "nf", "name": "Netflix", "icon": "🎬"},
    {"code": "ds", "name": "Discord", "icon": "🎮"},
    {"code": "oi", "name": "Tinder", "icon": "🔥"},
    {"code": "ub", "name": "Uber", "icon": "🚗"},
    {"code": "zm", "name": "Zomato", "icon": "🍔"},
    {"code": "sw", "name": "Swiggy", "icon": "🛵"},
    {"code": "pm", "name": "Paytm", "icon": "💳"},
    {"code": "fk", "name": "Flipkart", "icon": "🛍️"},
    {"code": "sn", "name": "Snapchat", "icon": "👻"},
    {"code": "tt", "name": "TikTok", "icon": "🎵"},
    {"code": "lf", "name": "TikTok / Douyin", "icon": "🎶"},
    {"code": "me", "name": "Line messenger", "icon": "🟢"},
    {"code": "vi", "name": "Viber", "icon": "🟣"},
    {"code": "wb", "name": "WeChat", "icon": "💬"},
    {"code": "ot", "name": "Any Other Service", "icon": "📱"},
]

SERVICE_MAP = {s["code"]: s for s in SERVICES}


def get_country_info(country_id: str) -> dict:
    country_id = str(country_id)
    return COUNTRY_MAP.get(
        country_id,
        {"id": country_id, "name": f"Country #{country_id}", "flag": "🌍", "code": ""}
    )


def get_service_info(service_code: str) -> dict:
    service_code = str(service_code).lower()
    return SERVICE_MAP.get(
        service_code,
        {"code": service_code, "name": service_code.upper(), "icon": "📱"}
    )


def get_colour_site_info(code: str) -> dict:
    return COLOUR_TRADING_MAP.get(
        code,
        {"code": code, "name": "Colour Trading", "icon": "🎰", "service": "ot", "country": "22"}
    )

