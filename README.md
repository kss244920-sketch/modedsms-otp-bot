# 📱 SastaSMS Professional Telegram OTP Bot

Professional, fully-automated Telegram OTP Reselling Bot powered by the **SastaSMS (`sastasms.pro`) API**.

---

## 🌟 Features

### 👤 For Users
- **📱 Instant Virtual Numbers:** Buy temporary phone numbers for 20+ services (WhatsApp, Telegram, Google, Instagram, Netflix, Zomato, etc.) across 20+ countries (India 🇮🇳, USA 🇺🇸, Indonesia 🇮🇩, UK 🇬🇧, etc.).
- **⚡ Instant Auto-OTP Delivery:** Background worker checks for incoming SMS every 4 seconds. The moment an OTP arrives, the bot delivers it instantly with one-tap copy formatting (`123456`).
- **🛡️ 100% Automatic Refund Guarantee:**
  - If no SMS is received within 20 minutes, the order expires and money is **automatically credited back** to the user's wallet!
  - Users can also click **"❌ Cancel & Refund"** at any time to immediately cancel and get their balance back.
- **💳 Dynamic UPI QR Code Top-up:** Users can recharge their wallet (₹20, ₹50, ₹100, custom) by scanning an auto-generated QR code and submitting their 12-digit UTR reference ID.
- **📋 Order & Wallet History:** Users can inspect past purchases, OTP history, and remaining balances.

### 👑 For Bot Admins
- **📈 Profit Margin / Markup Engine:** Automatically calculates selling prices with configurable profit margins (e.g. +20% + ₹1 flat) so you earn profit on every number sold.
- **🌐 SastaSMS Live Balance:** Check provider credit directly inside Telegram with `/admin`.
- **💰 Manual Balance Management:** Add or deduct user credits (`/addbal <user_id> <amount>`).
- **🔔 Instant Deposit Approval:** Get notified with one-click `[Approve]` and `[Reject]` buttons when a user submits a deposit UTR.
- **📢 Mass Broadcast:** Broadcast announcements to all bot users with `/broadcast <text>`.

---

## 📁 Project Structure

```
sastasms-otp-bot/
├── bot.py             # Main Telegram bot & background polling worker
├── sastasms_api.py    # SastaSMS API async integration client
├── database.py        # SQLite persistent database manager
├── services_data.py   # Mappings for services (WA, TG, etc.) and countries
├── config.py          # Configuration manager
├── requirements.txt   # Dependencies
├── start.sh           # One-click startup script
└── .env               # Your secrets (BOT_TOKEN, API keys, UPI details)
```

---

## 🚀 Setup & Installation

### 1. Configuration (`.env`)
Open `.env` and fill in your details:

```ini
# 1. Telegram Bot Token from @BotFather
BOT_TOKEN=1234567890:ABCdefGhIJKlmNoPQRstuVWxYz

# 2. SastaSMS API Key from https://sastasms.pro dashboard
SASTASMS_API_KEY=your_sastasms_api_key_here

# 3. Your Telegram User ID (for admin permissions)
ADMIN_IDS=123456789

# 4. Profit Margin Configuration
# Markup in % (20 = 20% profit on provider cost)
DEFAULT_MARGIN_PERCENT=20.0
# Flat extra in ₹ (1.0 = +₹1 per number)
DEFAULT_MARGIN_FLAT=1.0

# 5. Your UPI ID for receiving payments
DEFAULT_UPI_ID=yourname@okaxis
DEFAULT_UPI_NAME=OTP Service
MIN_RECHARGE=10.0
```

### 2. Run the Bot

```bash
cd /home/piyush/sastasms-otp-bot
./start.sh
```
Or directly:
```bash
python3 bot.py
```

---

## 🛠️ Admin Commands

Send these commands to the bot from your Telegram account (matching `ADMIN_IDS`):

| Command | Description | Example |
| :--- | :--- | :--- |
| `/admin` | Open Admin Control Panel & view SastaSMS balance | `/admin` |
| `/addbal <uid> <amount>` | Add balance to any user | `/addbal 987654321 100` |
| `/deductbal <uid> <amount>` | Deduct balance from any user | `/deductbal 987654321 20` |
| `/setmargin <percent>` | Change profit margin percentage | `/setmargin 25` (25% profit) |
| `/setflat <amount>` | Change flat extra charge | `/setflat 2` (+₹2 extra) |
| `/setupi <upi_id> [name]` | Update UPI address for QR | `/setupi mypay@upi FlashPay` |
| `/broadcast <msg>` | Broadcast message to all users | `/broadcast Hello users!` |

---

## 🔄 Run 24/7 with Systemd (Optional)

To keep the bot running in the background 24/7 on your Linux machine:

1. Create a service file:
```bash
sudo nano /etc/systemd/system/sastasms-bot.service
```

2. Paste this configuration:
```ini
[Unit]
Description=SastaSMS Telegram OTP Bot
After=network.target

[Service]
Type=simple
User=piyush
WorkingDirectory=/home/piyush/sastasms-otp-bot
ExecStart=/usr/bin/python3 /home/piyush/sastasms-otp-bot/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

3. Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable sastasms-bot
sudo systemctl start sastasms-bot
```
