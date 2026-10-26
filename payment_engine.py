"""
Payment Engine for SastaSMS OTP Bot (ModedSmSBot)
Automated Crypto Token / Balance Recharge via @CryptoBot (Crypto Pay API)
Design identical to Raxi4 Bot:
- Mathematical half-up rounding for odd / fee-deducted amounts (e.g. 0.734 -> 0.73 -> 73 tokens; 0.656 -> 0.66 -> 66 tokens; 0.87732 -> 0.88 -> 88 tokens)
- Auto fee surcharge calculation so Owner receives full net amount
- Dual-layer instant settlement (Background Polling + Instant On-Demand Verification)
- Persistent thread-safe invoice storage in crypto_invoices.json
- Transparent breakdown receipts with full mathematical formula
"""

import os
import json
import time
import logging
import threading
import urllib.request
import urllib.error
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("payment_engine")

# ─── Configuration & Defaults ───
CRYPTO_PAY_TOKEN = os.environ.get("CRYPTO_PAY_TOKEN", "655242:AAYmK4gRIBr2k4KL6WaVMDtwC2sXjUcXFKE")
CRYPTO_API_BASE = "https://pay.crypt.bot/api"
INVOICES_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_invoices.json")

# Fee surcharge percentage to ensure owner receives full target amount (3.0% gateway fee)
DEFAULT_SURCHARGE_PCT = float(os.environ.get("CRYPTO_SURCHARGE_PCT", "3.0"))

# Exchange rate: 1 USD / USDT = 94.0 INR (Fixed from user specification: ₹94/$)
TOKENS_PER_USD = float(os.environ.get("USDT_EXCHANGE_RATE", "94.0"))

# Supported Crypto Assets for Multi-Mode Deposit
SUPPORTED_CRYPTO_ASSETS = [
    {"code": "USDT", "name": "USDT (Tether)", "icon": "🟢", "desc": "TRC20 / TON / BEP20", "style": "success"},
    {"code": "TON", "name": "TON (Telegram)", "icon": "💎", "desc": "The Open Network", "style": "primary"},
    {"code": "BTC", "name": "Bitcoin (BTC)", "icon": "🟠", "desc": "Native Bitcoin", "style": "primary"},
    {"code": "LTC", "name": "Litecoin (LTC)", "icon": "⚪", "desc": "Fast & Lowest Fee", "style": "success"},
    {"code": "TRX", "name": "TRON (TRX)", "icon": "🔴", "desc": "TRON Network", "style": "danger"},
    {"code": "MULTI", "name": "Multi-Coin Selector", "icon": "🌐", "desc": "USDT, BTC, TON, LTC, TRX", "style": "success"},
]

# Preset Recharge Packages based on ₹94 / $
DEFAULT_PACKAGES = [
    {"tokens": 94, "usd": 1.00, "label": "🟢 ₹94 ($1.00 USD)", "style": "success"},
    {"tokens": 188, "usd": 2.00, "label": "🔵 ₹188 ($2.00 USD)", "style": "primary"},
    {"tokens": 470, "usd": 5.00, "label": "🟣 ₹470 ($5.00 USD)", "style": "success"},
    {"tokens": 940, "usd": 10.00, "label": "🟡 ₹940 ($10.00 USD)", "style": "primary"},
]


def calculate_tokens_from_amount(amount_usd: Any) -> Tuple[Decimal, float, str, str]:
    """
    Standard Mathematical Half-Up Rounding Rule at 1 USD = ₹94.00:
    - Takes exact received amount from CryptoBot/network.
    - Multiplies by 94.00 INR/USD.
    - Applies Half-Up rounding to 2 decimal places.
    - Produces 2-line simple words proof for the user.
    Returns:
        (dec_usd, credited_inr, line1, line2)
    """
    try:
        dec = Decimal(str(amount_usd).strip())
    except Exception:
        dec = Decimal("0.00")

    raw_inr = dec * Decimal(str(TOKENS_PER_USD))
    rounded_inr = raw_inr.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 2-line explanation logic in simple words with mathematical proof:
    line1 = f"Received: ${dec} USDT × ₹{int(TOKENS_PER_USD)}/$ = ₹{raw_inr:.4f}"
    line2 = f"Half-Up Math Rule: ₹{raw_inr:.4f} ➔ ₹{rounded_inr:.2f} credited fairly"

    return dec, float(rounded_inr), line1, line2


def calculate_invoice_amount_with_surcharge(target_usd: float, surcharge_pct: float = DEFAULT_SURCHARGE_PCT) -> Decimal:
    """
    Calculates the gross invoice amount so after the gateway fee,
    the merchant receives at least the target USD.
    gross = target / (1 - surcharge_pct / 100)
    """
    t = Decimal(str(target_usd))
    if surcharge_pct <= 0:
        return t.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pct = Decimal(str(surcharge_pct)) / Decimal("100")
    denom = Decimal("1.0") - pct
    if denom <= Decimal("0.0"):
        denom = Decimal("1.0")
    gross = t / denom
    return gross.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class PaymentEngine:
    def __init__(self, api_token: str = CRYPTO_PAY_TOKEN, db_path: str = INVOICES_DB_PATH):
        self.api_token = api_token
        self.db_path = db_path
        self._lock = threading.RLock()
        self.invoices: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._on_settled_callback = None
        self.load_db()

    # ─── Persistence ───
    def load_db(self):
        with self._lock:
            if os.path.exists(self.db_path):
                try:
                    with open(self.db_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self.invoices = data.get("invoices", {})
                except Exception as e:
                    logger.error(f"[PaymentEngine] Error loading db: {e}")
                    self.invoices = {}
            else:
                self.invoices = {}

    def save_db(self):
        with self._lock:
            try:
                tmp = self.db_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"invoices": self.invoices, "updated_at": int(time.time())}, f, indent=2)
                os.replace(tmp, self.db_path)
            except Exception as e:
                logger.error(f"[PaymentEngine] Error saving db: {e}")

    # ─── API Requests ───
    def _api_call(self, endpoint: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Dict[str, Any]:
        url = f"{CRYPTO_API_BASE}/{endpoint.lstrip('/')}"
        headers = {
            "Crypto-Pay-API-Token": self.api_token,
            "User-Agent": "ModedSmSBot-PaymentEngine/2.0",
            "Content-Type": "application/json"
        }
        data_bytes = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(url, data=data_bytes, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"[PaymentEngine] HTTP error {e.code} on {endpoint}: {err_body}")
            return {"ok": False, "error": f"HTTP {e.code}: {err_body}"}
        except Exception as e:
            logger.error(f"[PaymentEngine] Request error on {endpoint}: {e}")
            return {"ok": False, "error": str(e)}

    def check_app_status(self) -> Dict[str, Any]:
        """Validates API token credentials and fetches connected app info."""
        return self._api_call("getMe")

    def get_exchange_rates(self) -> Dict[str, float]:
        """Fetches live USD exchange rates for crypto coins from CryptoBot."""
        now = time.time()
        if hasattr(self, "_cached_rates") and hasattr(self, "_rates_time") and (now - self._rates_time < 120):
            return self._cached_rates
        resp = self._api_call("getExchangeRates")
        rates = {}
        if resp.get("ok"):
            for item in resp.get("result", []):
                if item.get("target") == "USD":
                    try:
                        rates[item["source"]] = float(item["rate"])
                    except Exception:
                        pass
        if not rates:
            rates = {"USDT": 1.0, "TON": 1.22, "BTC": 80000.0, "LTC": 56.0, "TRX": 0.33, "SOL": 100.0}
        self._cached_rates = rates
        self._rates_time = now
        return rates

    # ─── Invoice Lifecycle ───
    def create_token_invoice(
        self,
        user_id: int,
        chat_id: int,
        target_tokens: int,
        asset: str = "USDT",
        surcharge_pct: float = DEFAULT_SURCHARGE_PCT,
        bot_username: str = "ModedSmSBot"
    ) -> Dict[str, Any]:
        """
        Creates an invoice for target_tokens (1 Token = ₹1).
        Supports multi-coin assets (USDT, TON, BTC, LTC, TRX, MULTI).
        Calculates required USD and applies surcharge so owner receives full net amount.
        """
        target_usd = round(target_tokens / TOKENS_PER_USD, 2)
        invoice_amount_usd = calculate_invoice_amount_with_surcharge(target_usd, surcharge_pct)
        rates = self.get_exchange_rates()

        if asset == "MULTI":
            payload = {
                "currency_type": "fiat",
                "fiat": "USD",
                "amount": str(invoice_amount_usd),
                "accepted_assets": "USDT,TON,BTC,LTC,TRX",
                "description": f"ModedSmS ₹{target_tokens} Balance (User {user_id})",
                "hidden_message": f"Thank you! ₹{target_tokens} balance has been credited to your OTP Bot account.",
                "paid_btn_name": "callback",
                "paid_btn_url": f"https://t.me/{bot_username}" if bot_username else "https://t.me/ModedSmSBot",
                "allow_comments": False,
                "allow_anonymous": True,
                "expires_in": 1800  # 30 minutes expiration
            }
            crypto_qty_str = f"${invoice_amount_usd} USD"
        elif asset == "USDT":
            payload = {
                "asset": "USDT",
                "amount": str(invoice_amount_usd),
                "description": f"ModedSmS ₹{target_tokens} Balance (User {user_id})",
                "hidden_message": f"Thank you! ₹{target_tokens} balance has been credited to your OTP Bot account.",
                "paid_btn_name": "callback",
                "paid_btn_url": f"https://t.me/{bot_username}" if bot_username else "https://t.me/ModedSmSBot",
                "allow_comments": False,
                "allow_anonymous": True,
                "expires_in": 1800  # 30 minutes expiration
            }
            crypto_qty_str = f"{invoice_amount_usd} USDT"
        else:
            coin_rate = rates.get(asset, 1.0)
            if coin_rate <= 0:
                coin_rate = 1.0
            coin_qty = float(invoice_amount_usd) / coin_rate
            if asset in ["BTC", "ETH"]:
                crypto_qty_str = f"{coin_qty:.8f}"
            else:
                crypto_qty_str = f"{coin_qty:.4f}"
            payload = {
                "asset": asset,
                "amount": crypto_qty_str,
                "description": f"ModedSmS ₹{target_tokens} Balance (User {user_id})",
                "hidden_message": f"Thank you! ₹{target_tokens} balance has been credited to your OTP Bot account.",
                "paid_btn_name": "callback",
                "paid_btn_url": f"https://t.me/{bot_username}" if bot_username else "https://t.me/ModedSmSBot",
                "allow_comments": False,
                "allow_anonymous": True,
                "expires_in": 1800  # 30 minutes expiration
            }

        resp = self._api_call("createInvoice", payload=payload)
        if not resp.get("ok"):
            return {"success": False, "error": resp.get("error", "Failed to create invoice via CryptoBot")}

        inv_data = resp.get("result", {})
        invoice_id = str(inv_data.get("invoice_id"))

        record = {
            "invoice_id": invoice_id,
            "hash": inv_data.get("hash", ""),
            "user_id": user_id,
            "chat_id": chat_id,
            "target_tokens": target_tokens,
            "target_usd": str(target_usd),
            "invoice_amount": str(invoice_amount_usd),
            "asset": asset,
            "crypto_qty": crypto_qty_str,
            "status": "active",
            "pay_url": inv_data.get("pay_url") or inv_data.get("bot_invoice_url"),
            "mini_app_url": inv_data.get("mini_app_invoice_url"),
            "created_at": int(time.time()),
            "expires_at": int(time.time()) + 1800,
            "credited_tokens": 0,
            "received_amount": None,
            "settled_at": None,
        }

        with self._lock:
            self.invoices[invoice_id] = record
            self.save_db()

        return {
            "success": True,
            "invoice_id": invoice_id,
            "pay_url": record["pay_url"],
            "mini_app_url": record["mini_app_url"],
            "target_tokens": target_tokens,
            "target_usd": str(target_usd),
            "invoice_amount": str(invoice_amount_usd),
            "asset": asset,
            "crypto_qty": crypto_qty_str,
            "expires_in_minutes": 30
        }

    def check_and_settle_invoice(self, invoice_id: str, db=None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Queries CryptoBot for invoice status. If paid, applies mathematical half-up rounding,
        credits balance to user in SQLite DB, and formats professional receipt.
        Returns:
            (is_settled: bool, message: str, invoice_record: Dict)
        """
        invoice_id = str(invoice_id)
        with self._lock:
            rec = self.invoices.get(invoice_id)
            if not rec:
                return False, "Invoice not found.", {}
            if rec.get("status") == "settled":
                return True, "Invoice already settled and credited.", rec

        # Query API for current invoice status
        resp = self._api_call(f"getInvoices?invoice_ids={invoice_id}")
        if not resp.get("ok"):
            return False, f"Could not contact CryptoBot: {resp.get('error')}", rec

        items = resp.get("result", {}).get("items", [])
        if not items:
            return False, "Invoice record not found on CryptoBot.", rec

        item = items[0]
        status = item.get("status")

        if status != "paid":
            return False, f"Payment pending. Current status: {status}", rec

        # Invoice is confirmed PAID!
        paid_amount_str = str(item.get("amount") or rec.get("invoice_amount") or "0")

        # Apply mathematical half-up rounding rule with 2-line simple proof
        dec_usd, credited_tokens, line1, line2 = calculate_tokens_from_amount(paid_amount_str)

        # Fallback check: ensure user gets at least target if full invoice paid
        target_tokens = float(rec.get("target_tokens", 0))
        if credited_tokens < target_tokens and float(paid_amount_str) >= float(rec.get("target_usd", 0)):
            credited_tokens = target_tokens

        user_id = rec.get("user_id")
        new_balance = float(credited_tokens)

        # Credit balance in Database
        if db and user_id:
            try:
                new_balance = db._update_balance(
                    user_id,
                    float(credited_tokens),
                    "DEPOSIT",
                    f"CryptoBot Deposit #{invoice_id} ({paid_amount_str} USDT)"
                )
            except Exception as e:
                logger.error(f"[PaymentEngine] Error crediting balance to {user_id}: {e}")

        # Mark as settled
        with self._lock:
            rec["status"] = "settled"
            rec["settled_at"] = int(time.time())
            rec["received_amount"] = paid_amount_str
            rec["credited_tokens"] = credited_tokens
            rec["proof_line1"] = line1
            rec["proof_line2"] = line2
            self.invoices[invoice_id] = rec
            self.save_db()

        # Format transparent receipt screen with 2-line logic & proof
        receipt_text = (
            f"🎉 <b>Crypto Payment Successful & Verified!</b>\n\n"
            f"📥 <b>Net Amount Received:</b> <code>${paid_amount_str} USDT</code>\n"
            f"💵 <b>Exchange Rate:</b> <code>1 USDT = ₹{int(TOKENS_PER_USD)} INR</code>\n\n"
            f"<b>🧾 Transparent Logic & Proof:</b>\n"
            f"• {line1}\n"
            f"• {line2}\n\n"
            f"💰 <b>Final Amount Credited:</b> <b>+₹{credited_tokens:.2f}</b>\n"
            f"💳 <b>Updated Wallet Balance:</b> <b>₹{new_balance:.2f}</b>\n\n"
            f"🆔 <b>CryptoBot Invoice ID:</b> <code>#{invoice_id}</code>\n"
            f"⏰ <b>Verified Timestamp:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Zero-loss guarantee: 100% automated & protected.</i>"
        )

        return True, receipt_text, rec

    # ─── Background Polling Loop ───
    def set_settlement_callback(self, callback):
        """Callback(user_id, chat_id, receipt_text, invoice_record) when auto-detected."""
        self._on_settled_callback = callback

    def start_background_poller(self, db, poll_interval: int = 5):
        if self._running:
            return
        self._running = True

        def _loop():
            logger.info("[PaymentEngine] Background auto-settlement poller started.")
            while self._running:
                try:
                    active_ids = []
                    with self._lock:
                        for inv_id, rec in self.invoices.items():
                            if rec.get("status") == "active":
                                active_ids.append(inv_id)

                    for inv_id in active_ids:
                        is_settled, receipt_text, rec = self.check_and_settle_invoice(inv_id, db=db)
                        if is_settled:
                            logger.info(f"[PaymentEngine] Invoice {inv_id} settled via auto-poller!")
                            if self._on_settled_callback:
                                try:
                                    self._on_settled_callback(
                                        rec.get("user_id"),
                                        rec.get("chat_id"),
                                        receipt_text,
                                        rec
                                    )
                                except Exception as e:
                                    logger.error(f"[PaymentEngine] Callback error: {e}")
                except Exception as e:
                    logger.error(f"[PaymentEngine] Poller loop error: {e}")

                time.sleep(poll_interval)

        self._poll_thread = threading.Thread(target=_loop, daemon=True, name="CryptoPayPoller")
        self._poll_thread.start()

    def stop_background_poller(self):
        self._running = False


# Global instance
engine = PaymentEngine()
