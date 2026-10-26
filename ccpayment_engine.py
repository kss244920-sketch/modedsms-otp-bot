"""
CCPayment Engine for ModedSMS OTP Bot
Integrates with CCPayment (ccpayment.com) v2 API for automated crypto payments.
Credentials:
- App ID: D81uhnJkY2pG7inw
- App Secret: 1b8b6726e2183406fcb64449394a7caa
"""

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Dict, Any, Optional, Tuple
import httpx

logger = logging.getLogger("ccpayment_engine")

CCPAYMENT_BASE_URL = os.getenv("CCPAYMENT_BASE_URL", "https://ccpayment.com/ccpayment/v2")
CCPAYMENT_APP_ID = os.getenv("CCPAYMENT_APP_ID", "D81uhnJkY2pG7inw")
CCPAYMENT_APP_SECRET = os.getenv("CCPAYMENT_APP_SECRET", "1b8b6726e2183406fcb64449394a7caa")


class CCPaymentGateway:
    def __init__(
        self,
        app_id: str = CCPAYMENT_APP_ID,
        app_secret: str = CCPAYMENT_APP_SECRET,
        base_url: str = CCPAYMENT_BASE_URL,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")

    def _generate_signature(self, payload_str: str) -> str:
        """Generate HMAC-SHA256 signature using App Secret."""
        return hmac.new(
            self.app_secret.encode("utf-8"),
            payload_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def create_deposit_address(
        self,
        user_id: int,
        chain: str = "TRC20",
        crypto: str = "USDT",
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Generate or fetch user deposit address.
        Returns: (success, data, error_msg)
        """
        endpoint = f"{self.base_url}/getOrCreateAppDepositAddress"
        ts = int(time.time())
        payload = {
            "userId": str(user_id),
            "chain": chain,
            "crypto": crypto,
        }
        payload_str = json.dumps(payload, separators=(",", ":"))
        signature = self._generate_signature(payload_str)

        headers = {
            "Content-Type": "application/json",
            "Appid": self.app_id,
            "Sign": signature,
            "Timestamp": str(ts),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(endpoint, content=payload_str, headers=headers)
                data = res.json()
                if data.get("code") == 10000 and data.get("data"):
                    return True, data["data"], "Success"
                elif data.get("code") == 12002:
                    return False, {}, "IP not whitelisted in CCPayment dashboard. Please enable 'Developer Test Mode' on your CCPayment portal."
                else:
                    return False, {}, data.get("msg", "Unknown error from CCPayment")
        except Exception as e:
            logger.error("CCPayment API request error: %s", e)
            return False, {}, str(e)


# Global singleton instance
ccpayment_gateway = CCPaymentGateway()
