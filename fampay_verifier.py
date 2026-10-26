"""
FamPay Automated UPI Verification via Gmail Alerts
Reads credit alert emails from FamApp / FamX in Gmail (imap.gmail.com:993).
Features:
- Amount and 12-digit UTR matching
- Auto-crediting wallet balance without manual admin approval
- Fallback to admin review if email alert is delayed
"""

import asyncio
import email
from email.header import decode_header
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger("fampay_verifier")

GMAIL_USER = os.getenv("GMAIL_USER", "bruhgamer786@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "okgphebsqpjlgipa").replace(" ", "")


class FamPayVerifier:
    def __init__(self, username: str = "", password: str = ""):
        self.username = username or GMAIL_USER
        self.password = (password or GMAIL_APP_PASSWORD).replace(" ", "")

    def is_configured(self) -> bool:
        return bool(self.username and self.password and len(self.password) >= 16)

    def _decode_header_str(self, header_raw: Optional[str]) -> str:
        if not header_raw:
            return ""
        decoded_parts = decode_header(header_raw)
        result = []
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                try:
                    result.append(part.decode(encoding or "utf-8", errors="ignore"))
                except Exception:
                    result.append(part.decode("utf-8", errors="ignore"))
            else:
                result.append(str(part))
        return "".join(result)

    def _extract_body_text(self, msg: email.message.Message) -> str:
        text_parts = []
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type in ["text/plain", "text/html"] and "attachment" not in content_disposition:
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            text_parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="ignore"))
                    except Exception:
                        pass
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    text_parts.append(payload.decode(msg.get_content_charset() or "utf-8", errors="ignore"))
            except Exception:
                pass

        full_text = " ".join(text_parts)
        clean_text = re.sub(r"<[^>]+>", " ", full_text)
        return re.sub(r"\s+", " ", clean_text).strip()

    def _check_amount_matches(self, text: str, expected_amount: float) -> bool:
        amt_patterns = [f"{expected_amount:.2f}"]
        if expected_amount.is_integer():
            int_val = int(expected_amount)
            amt_patterns.extend([str(int_val), f"{int_val:,}", f"{expected_amount:.1f}"])
        else:
            amt_patterns.append(f"{expected_amount:g}")

        options = "|".join(re.escape(p) for p in set(amt_patterns))
        pattern = r"(?<!\d)(?:₹|rs\.?|inr)?\s*(?:" + options + r")(?!\d|\.\d)"
        return bool(re.search(pattern, text, re.IGNORECASE))

    def _sync_verify(
        self,
        expected_amount: float,
        utr: Optional[str] = None,
        max_emails_to_check: int = 40,
    ) -> Dict[str, Any]:
        """Synchronously check Gmail IMAP for FamPay transaction credit alert."""
        if not self.is_configured():
            return {
                "verified": False,
                "reason": "not_configured",
                "message": "Gmail credentials are not configured or invalid in .env",
            }

        clean_utr = utr.strip() if utr else None
        if not clean_utr:
            return {
                "verified": False,
                "reason": "missing_utr",
                "message": "UTR reference ID is required for verification.",
            }

        mail = None
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=12)
            mail.login(self.username, self.password)
            mail.select("INBOX", readonly=True)

            # Strategy 1: Direct ultra-fast search by UTR text in Gmail
            direct_status, direct_msgs = mail.search(None, f'(TEXT "{clean_utr}")')
            candidate_ids = []
            if direct_status == "OK" and direct_msgs[0]:
                candidate_ids = direct_msgs[0].split()
                candidate_ids.reverse()

            # Strategy 2: If direct search returns nothing, fall back to recent emails
            if not candidate_ids:
                months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                dt = datetime.now() - timedelta(days=2)
                since_date = f"{dt.day:02d}-{months[dt.month - 1]}-{dt.year}"
                status, messages = mail.search(None, f'(SINCE "{since_date}")')

                if status != "OK" or not messages[0]:
                    status, messages = mail.search(None, "ALL")

                if status != "OK" or not messages[0]:
                    return {
                        "verified": False,
                        "reason": "no_emails",
                        "message": "No recent transaction emails found in Gmail.",
                    }

                msg_ids = messages[0].split()
                latest_ids = msg_ids[-max_emails_to_check:]
                latest_ids.reverse()
                candidate_ids = latest_ids

            for msg_id in candidate_ids:
                res, data = mail.fetch(msg_id, "(RFC822)")
                if res != "OK" or not data or not data[0]:
                    continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = self._decode_header_str(msg.get("Subject", ""))
                sender = self._decode_header_str(msg.get("From", ""))
                date_str = msg.get("Date", "")
                body_text = self._extract_body_text(msg)

                combined_content = f"{subject} {sender} {body_text}"

                # Match amount
                if not self._check_amount_matches(combined_content, expected_amount):
                    continue

                # Match UTR in content
                if clean_utr in combined_content or (len(clean_utr) >= 8 and clean_utr[-8:] in combined_content):
                    logger.info(f"Verified FamPay payment! UTR: {clean_utr}, Amount: {expected_amount}")
                    return {
                        "verified": True,
                        "matched_by": "utr",
                        "utr": clean_utr,
                        "amount": expected_amount,
                        "subject": subject,
                        "sender": sender,
                        "date": date_str,
                        "message": "Payment verified automatically via FamPay alert in Gmail!",
                    }

            return {
                "verified": False,
                "reason": "not_found",
                "message": "FamPay alert not found yet in Gmail.",
            }

        except imaplib.IMAP4.error as e:
            logger.warning(f"IMAP login error: {e}")
            return {
                "verified": False,
                "reason": "auth_error",
                "message": f"IMAP authentication failed: {e}",
            }
        except Exception as e:
            logger.error(f"Unexpected error in FamPay verification: {e}")
            return {
                "verified": False,
                "reason": "error",
                "message": f"Verification error: {e}",
            }
        finally:
            if mail:
                try:
                    mail.close()
                except Exception:
                    pass
                try:
                    mail.logout()
                except Exception:
                    pass

    async def verify_transaction(self, expected_amount: float, utr: str) -> Dict[str, Any]:
        """Asynchronously verify transaction in background thread."""
        return await asyncio.to_thread(self._sync_verify, expected_amount=expected_amount, utr=utr)


# Global singleton instance
fampay_verifier = FamPayVerifier()
