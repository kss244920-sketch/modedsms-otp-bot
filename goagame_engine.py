"""
Goa Games Automated UPI Deposit Gateway Integration
Reverse-engineered from AR-SAAS platform (api.goa7777.com)
Features:
- Automated authentication with MD5 signature hashing
- Dynamic UPI Order creation via CreateRechargeOrder
- Polling / verification via GetRechargeRecord
- Automatic balance crediting upon confirmation
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Dict, Any, Optional, Tuple
import httpx

logger = logging.getLogger("goagame_engine")


def _mz() -> str:
    """Generate 32-char UUID string without hyphens."""
    return uuid.uuid4().hex


def sign_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    AR-SAAS Frontend signature algorithm.
    Sorts payload keys alphabetically excluding signature/track/xosoBettingData,
    concatenates into compact JSON, and computes uppercase MD5 hash.
    """
    t = dict(body)
    t.pop("signature", None)
    t.pop("timestamp", None)
    t["language"] = 0
    t["random"] = _mz()

    gue = {"signature", "track", "xosoBettingData"}
    n = {}
    for k in sorted(t.keys()):
        v = t[k]
        if v is not None and v != "" and k not in gue:
            n[k] = 0 if v == 0 else v

    json_str = json.dumps(n, separators=(",", ":"))
    sig = hashlib.md5(json_str.encode()).hexdigest().upper()
    t["signature"] = sig
    t["timestamp"] = int(time.time())
    return t


class GoaGameGateway:
    def __init__(
        self,
        phone: str = "7435843877",
        password: str = "Tjsx3662",
        api_base: str = "https://api.goa7777.com",
    ):
        self.phone = phone
        self.password = password
        self.api_base = api_base.rstrip("/")
        self.token: Optional[str] = None
        self.token_expiry: float = 0.0
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Ar-Origin": self.api_base,
            "Origin": self.api_base,
            "Referer": f"{self.api_base}/",
        }

    async def login(self) -> bool:
        """Authenticate with Goa Games backend and obtain JWT token."""
        url = f"{self.api_base}/api/webapi/Login"
        payload = sign_body({
            "username": f"91{self.phone}",
            "pwd": self.password,
            "loginType": "mobile",
            "phonetype": 1,
        })
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload, headers=self.headers)
                data = res.json()
                if data.get("code") == 0 and data.get("data"):
                    self.token = data["data"].get("token")
                    self.token_expiry = time.time() + 1800  # 30 mins validity
                    logger.info("Successfully authenticated with Goa Games backend.")
                    return True
                else:
                    logger.error("Goa Game login failed: %s", data.get("msg"))
                    return False
        except Exception as e:
            logger.error("Goa Game login error: %s", e)
            return False

    async def _ensure_token(self) -> bool:
        if not self.token or time.time() >= self.token_expiry:
            return await self.login()
        return True

    async def create_deposit_order(self, amount: float) -> Tuple[bool, Dict[str, Any], str]:
        """
        Create a dynamic UPI recharge order.
        Minimum allowed on Goa Games is ₹100.
        Returns: (success, order_data, error_msg)
        """
        if amount < 100.0:
            return False, {}, "Minimum deposit via Goa Games UPI gateway is ₹100.00."

        if not await self._ensure_token():
            return False, {}, "Failed to authenticate with payment provider."

        # Fetch available UPI channel
        url_types = f"{self.api_base}/api/webapi/GetRechargeTypes"
        req_headers = dict(self.headers)
        req_headers["Authorization"] = f"Bearer {self.token}"

        channel_id = 26001  # Default UPI-QR (payID: 26)
        pay_id = 26

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r_types = await client.post(url_types, json=sign_body({"payid": 1}), headers=req_headers)
                t_data = r_types.json()
                if t_data.get("code") == 0 and t_data.get("data"):
                    r_list = t_data["data"].get("rechargetypelist", [])
                    if r_list:
                        channel_id = r_list[0].get("payTypeID", 26001)
                        pay_id = r_list[0].get("payID", 26)

                # Now create the order
                url_order = f"{self.api_base}/api/webapi/CreateRechargeOrder"
                order_payload = sign_body({
                    "amount": int(amount),
                    "type": channel_id,
                    "payTypeID": channel_id,
                    "payID": pay_id,
                    "ReturnUrl": "http://www.goabete.com/#/wallet",
                })

                r_order = await client.post(url_order, json=order_payload, headers=req_headers)
                res_json = r_order.json()

                if res_json.get("code") == 0 and res_json.get("data"):
                    d = res_json["data"]
                    submit_url = d.get("submitUrl")
                    
                    # Fetch latest record to get the recharge number
                    await asyncio.sleep(0.5)
                    rec_info = await self.get_latest_order_info()

                    return True, {
                        "submit_url": submit_url,
                        "recharge_number": rec_info.get("rechargeNumber", ""),
                        "amount": amount,
                        "channel": "GoaGames UPI-QR",
                        "created_at": time.time(),
                    }, "Success"
                else:
                    msg = res_json.get("msg", "Failed to generate recharge order")
                    logger.warning("CreateRechargeOrder rejected: %s", msg)
                    return False, {}, msg
        except Exception as e:
            logger.error("Error creating Goa Game deposit order: %s", e)
            return False, {}, str(e)

    async def get_latest_order_info(self) -> Dict[str, Any]:
        """Fetch the most recent recharge record."""
        if not await self._ensure_token():
            return {}

        url = f"{self.api_base}/api/webapi/GetRechargeRecord"
        req_headers = dict(self.headers)
        req_headers["Authorization"] = f"Bearer {self.token}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=sign_body({"pageNo": 1, "pageSize": 5}), headers=req_headers)
                data = res.json()
                if data.get("code") == 0 and data.get("data"):
                    records = data["data"].get("list", [])
                    if records:
                        return records[0]
        except Exception as e:
            logger.error("Failed to fetch recharge record: %s", e)
        return {}

    async def check_order_status(self, recharge_number: str) -> Tuple[str, float]:
        """
        Check status of a recharge order by rechargeNumber.
        Returns: (status: 'PENDING' | 'SUCCESS' | 'FAILED', paid_amount)
        In Goa Games AR-SAAS:
        state == 0: Pending / Processing
        state == 1: Success / Paid
        state == 2 / 3: Cancelled / Failed
        """
        if not await self._ensure_token():
            return "PENDING", 0.0

        url = f"{self.api_base}/api/webapi/GetRechargeRecord"
        req_headers = dict(self.headers)
        req_headers["Authorization"] = f"Bearer {self.token}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=sign_body({"pageNo": 1, "pageSize": 10}), headers=req_headers)
                data = res.json()
                if data.get("code") == 0 and data.get("data"):
                    for rec in data["data"].get("list", []):
                        if rec.get("rechargeNumber") == recharge_number:
                            state = rec.get("state")
                            price = float(rec.get("price", rec.get("orderAmount", 0.0)))
                            if state == 1:
                                return "SUCCESS", price
                            elif state in (2, 3):
                                return "FAILED", price
                            return "PENDING", price
        except Exception as e:
            logger.error("Check order status failed: %s", e)

        return "PENDING", 0.0


# Global singleton instance
goagame_gateway = GoaGameGateway()
