import logging
from typing import Dict, Any, Optional, Tuple
import httpx
from config import BASE_API_URL, SASTASMS_API_KEY

logger = logging.getLogger(__name__)


class SastaSMSAPIError(Exception):
    """Custom exception for SastaSMS API errors."""
    pass


class SastaSMSClient:
    def __init__(self, api_key: Optional[str] = None, base_url: str = BASE_API_URL):
        self.api_key = api_key or SASTASMS_API_KEY
        self.base_url = base_url
        self.timeout = httpx.Timeout(20.0, connect=10.0)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "TelegramOTPBot/2.0 (Compatible; Python-Httpx)",
            "Accept": "application/json, text/plain, */*",
        }

    async def _request(self, params: Dict[str, Any]) -> str:
        """Make an async GET request to the SastaSMS handler."""
        if not self.api_key:
            raise SastaSMSAPIError("SastaSMS API Key is not configured. Please set SASTASMS_API_KEY in .env")

        req_params = {"api_key": self.api_key, **params}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    self.base_url,
                    params=req_params,
                    headers=self._get_headers()
                )
                text = response.text.strip()
                logger.debug(f"SastaSMS API call [{params.get('action')}] -> {text}")
                return text
            except httpx.RequestError as e:
                logger.error(f"Network error communicating with SastaSMS: {e}")
                raise SastaSMSAPIError(f"Network error with SMS provider: {str(e)}")

    async def get_balance(self) -> float:
        """Fetch account balance in INR."""
        raw = await self._request({"action": "getBalance"})
        
        # Responses: "ACCESS_BALANCE:150.50" or JSON
        if raw.startswith("ACCESS_BALANCE:"):
            try:
                return float(raw.split(":", 1)[1])
            except ValueError:
                raise SastaSMSAPIError(f"Unexpected balance response: {raw}")
        elif "BAD_KEY" in raw:
            raise SastaSMSAPIError("Invalid SastaSMS API Key (BAD_KEY).")
        elif "ERROR_SQL" in raw:
            raise SastaSMSAPIError("Provider database error (ERROR_SQL).")
        
        # Try JSON parsing if returned as json
        try:
            import json
            data = json.loads(raw)
            if "balance" in data:
                return float(data["balance"])
        except Exception:
            pass

        raise SastaSMSAPIError(f"Failed to fetch balance: {raw}")

    async def get_prices(self, country: Optional[str] = None, service: Optional[str] = None) -> Dict[str, Any]:
        """Fetch real-time prices for services and countries."""
        params = {"action": "getPrices", "format": "json"}
        if country is not None:
            params["country"] = str(country)
        if service is not None:
            params["service"] = str(service)

        try:
            raw = await self._request(params)
            import json
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Failed to fetch getPrices: {e}")
            return {}

    async def get_service_price(self, country: str, service: str) -> Optional[float]:
        """
        Attempts to find the cost price for a service in a given country.
        Returns float price or None if not listed.
        """
        data = await self.get_prices(country=country, service=service)
        if "data" in data and isinstance(data["data"], dict):
            data = data["data"]

        c_str = str(country)
        s_str = str(service).lower()

        service_data = None
        if c_str in data and s_str in data[c_str]:
            service_data = data[c_str][s_str]
        elif s_str in data:
            service_data = data[s_str]

        if isinstance(service_data, dict):
            if "cost" in service_data:
                return float(service_data["cost"])
            # Keys might be price strings like "12.50": count
            prices = []
            for k, val in service_data.items():
                if k == "cost" and isinstance(val, (int, float)):
                    return float(val)
                try:
                    prices.append(float(k))
                except ValueError:
                    if isinstance(val, (int, float)):
                        prices.append(float(val))
            if prices:
                return min(prices)
        elif isinstance(service_data, (int, float)):
            return float(service_data)

        return None

    async def get_number(
        self,
        service: str,
        country: str,
        max_price: Optional[float] = None
    ) -> Dict[str, str]:
        """
        Request / Buy a virtual phone number.
        Returns: {"id": "activation_id", "number": "phone_number"}
        """
        params = {
            "action": "getNumber",
            "service": service,
            "country": str(country),
        }
        if max_price is not None:
            params["maxPrice"] = str(max_price)

        raw = await self._request(params)

        # Standard response format: ACCESS_NUMBER:ID:NUMBER
        if raw.startswith("ACCESS_NUMBER:"):
            parts = raw.split(":")
            if len(parts) >= 3:
                return {"id": parts[1], "number": parts[2]}
            raise SastaSMSAPIError(f"Malformed ACCESS_NUMBER response: {raw}")

        # Human friendly errors
        error_map = {
            "NO_NUMBERS": "No numbers available right now for this service & country. Please try again later or try another country.",
            "NO_BALANCE": "Provider account balance is low. Please notify admin to refill provider balance.",
            "BAD_KEY": "API Key configuration error (BAD_KEY).",
            "BAD_ACTION": "Invalid API action.",
            "BAD_SERVICE": "Invalid or unsupported service code.",
            "WRONG_SERVICE": "Service is temporarily unavailable.",
            "MAX_PRICE_EXCEEDED": "Price of available number exceeds max price setting.",
            "ERROR_SQL": "Provider server temporary error. Please try again in a few moments.",
        }

        msg = error_map.get(raw, f"Provider error: {raw}")
        raise SastaSMSAPIError(msg)

    async def get_status(self, activation_id: str) -> Tuple[str, Optional[str]]:
        """
        Check the status of an activation / order.
        Returns tuple: (STATUS_TYPE, CODE_OR_DETAIL)
        STATUS_TYPE can be:
          - "WAIT_CODE": Still waiting for SMS
          - "OK": SMS received, code is returned in detail
          - "WAIT_RETRY": Previous code received, waiting for new one
          - "CANCEL": Order was cancelled
          - "ERROR": An error occurred
        """
        params = {"action": "getStatus", "id": str(activation_id)}
        raw = await self._request(params)

        if raw == "STATUS_WAIT_CODE":
            return ("WAIT_CODE", None)
        elif raw.startswith("STATUS_OK:"):
            code = raw.split(":", 1)[1]
            return ("OK", code)
        elif raw.startswith("STATUS_WAIT_RETRY:"):
            code = raw.split(":", 1)[1]
            return ("WAIT_RETRY", code)
        elif raw == "STATUS_WAIT_RESEND":
            return ("WAIT_CODE", None)
        elif raw == "STATUS_CANCEL":
            return ("CANCEL", None)
        elif "BAD_KEY" in raw:
            return ("ERROR", "Invalid API key")
        elif "NO_ACTIVATION" in raw:
            return ("CANCEL", "Activation expired or not found")
        else:
            return ("ERROR", raw)

    async def set_status(self, activation_id: str, status: int) -> str:
        """
        Change status of an activation.
        status:
          1 - SMS sent (notification)
          3 - Request another SMS
          6 - Complete order (activation successful)
          8 - Cancel order (triggers refund on provider side)
        """
        params = {
            "action": "setStatus",
            "id": str(activation_id),
            "status": str(status)
        }
        raw = await self._request(params)
        logger.info(f"setStatus(id={activation_id}, status={status}) -> {raw}")
        return raw

    async def cancel_number(self, activation_id: str) -> bool:
        """Cancel an order and request provider refund."""
        res = await self.set_status(activation_id, 8)
        return "ACCESS_CANCEL" in res or "STATUS_CANCEL" in res

    async def request_another_otp(self, activation_id: str) -> bool:
        """Request another SMS / REOTP on the same number (status=3)."""
        res = await self.set_status(activation_id, 3)
        return "ACCESS_RETRY_GET" in res or "STATUS_WAIT_RESEND" in res or "ACCESS_READY" in res
