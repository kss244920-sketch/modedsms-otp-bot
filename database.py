import sqlite3
import time
import asyncio
import os
import logging
from typing import Optional, List, Dict, Any, Union
from config import (
    DB_PATH,
    DEFAULT_MARGIN_PERCENT,
    DEFAULT_MARGIN_FLAT,
    DEFAULT_UPI_ID,
    DEFAULT_UPI_NAME,
    TURSO_DATABASE_URL,
    TURSO_AUTH_TOKEN,
)
from turso_client import TursoConnection

logger = logging.getLogger("Database")

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.use_turso = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)
        if self.use_turso:
            logger.info("⚡ Database running on Turso Cloud SQLite (Zero-Data-Loss Persistent Cloud)!")
        else:
            logger.info(f"📁 Database running on local SQLite file: {self.db_path}")
        self._init_db()

    def _get_connection(self) -> Union[TursoConnection, sqlite3.Connection]:
        if self.use_turso:
            return TursoConnection(TURSO_DATABASE_URL, TURSO_AUTH_TOKEN)
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create tables if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    balance REAL DEFAULT 0.0,
                    total_spent REAL DEFAULT 0.0,
                    is_banned INTEGER DEFAULT 0,
                    created_at REAL
                )
            """)

            # Orders table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    order_id TEXT UNIQUE,
                    phone_number TEXT,
                    service_code TEXT,
                    service_name TEXT,
                    country_id TEXT,
                    country_name TEXT,
                    cost_price REAL,
                    selling_price REAL,
                    status TEXT DEFAULT 'PENDING',
                    otp_code TEXT,
                    full_sms TEXT,
                    created_at REAL,
                    expires_at REAL,
                    last_checked_at REAL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)

            # Transactions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount REAL,
                    type TEXT,
                    description TEXT,
                    created_at REAL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)

            # Recharge requests table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recharge_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount REAL,
                    utr TEXT UNIQUE,
                    status TEXT DEFAULT 'PENDING',
                    created_at REAL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)

            # Bot settings
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # Set default settings if not exists
            default_settings = [
                ("margin_percent", str(DEFAULT_MARGIN_PERCENT)),
                ("margin_flat", str(DEFAULT_MARGIN_FLAT)),
                ("upi_id", DEFAULT_UPI_ID),
                ("upi_name", DEFAULT_UPI_NAME),
            ]
            for key, val in default_settings:
                cursor.execute(
                    "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                    (key, val)
                )

            conn.commit()

    # ------------------ USERS ------------------

    def _get_or_create_user(self, user_id: int, username: Optional[str], first_name: Optional[str]) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                # Update info if changed
                cursor.execute(
                    "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                    (username, first_name, user_id)
                )
                conn.commit()
                return dict(row)
            
            # Create user
            now = time.time()
            cursor.execute(
                "INSERT INTO users (user_id, username, first_name, balance, total_spent, is_banned, created_at) VALUES (?, ?, ?, 0.0, 0.0, 0, ?)",
                (user_id, username, first_name, now)
            )
            conn.commit()
            return {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "balance": 0.0,
                "total_spent": 0.0,
                "is_banned": 0,
                "created_at": now
            }

    async def get_or_create_user(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_or_create_user, user_id, username, first_name)

    def _get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_user, user_id)

    def _update_balance(self, user_id: int, amount_change: float, tx_type: str, description: str) -> float:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance, total_spent FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"User {user_id} not found")
            
            current_bal = float(row["balance"])
            new_bal = round(current_bal + amount_change, 2)
            if new_bal < 0:
                raise ValueError("Insufficient balance")

            new_spent = float(row["total_spent"])
            if amount_change < 0 and tx_type == "PURCHASE":
                new_spent += abs(amount_change)
            elif amount_change > 0 and tx_type == "REFUND":
                new_spent = max(0.0, new_spent - amount_change)

            cursor.execute(
                "UPDATE users SET balance = ?, total_spent = ? WHERE user_id = ?",
                (new_bal, new_spent, user_id)
            )

            # Record transaction
            cursor.execute(
                "INSERT INTO transactions (user_id, amount, type, description, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, amount_change, tx_type, description, time.time())
            )
            conn.commit()
            return new_bal

    async def update_balance(self, user_id: int, amount_change: float, tx_type: str, description: str) -> float:
        return await asyncio.to_thread(self._update_balance, user_id, amount_change, tx_type, description)

    def _get_all_users(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    async def get_all_users(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_all_users)

    # ------------------ ORDERS ------------------

    def _create_order(
        self,
        user_id: int,
        order_id: str,
        phone_number: str,
        service_code: str,
        service_name: str,
        country_id: str,
        country_name: str,
        cost_price: float,
        selling_price: float,
        timeout_minutes: int
    ) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = time.time()
            expires_at = now + (timeout_minutes * 60)
            cursor.execute("""
                INSERT INTO orders (
                    user_id, order_id, phone_number, service_code, service_name,
                    country_id, country_name, cost_price, selling_price, status,
                    created_at, expires_at, last_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
            """, (
                user_id, order_id, phone_number, service_code, service_name,
                country_id, country_name, cost_price, selling_price,
                now, expires_at, now
            ))
            order_db_id = cursor.lastrowid
            conn.commit()

            cursor.execute("SELECT * FROM orders WHERE id = ?", (order_db_id,))
            return dict(cursor.fetchone())

    async def create_order(
        self,
        user_id: int,
        order_id: str,
        phone_number: str,
        service_code: str,
        service_name: str,
        country_id: str,
        country_name: str,
        cost_price: float,
        selling_price: float,
        timeout_minutes: int
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._create_order,
            user_id, order_id, phone_number, service_code, service_name,
            country_id, country_name, cost_price, selling_price, timeout_minutes
        )

    def _get_order_by_order_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    async def get_order_by_order_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_order_by_order_id, order_id)

    def _get_active_orders_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = time.time()
            cursor.execute(
                "SELECT * FROM orders WHERE user_id = ? AND status IN ('PENDING', 'WAITING_REOTP') AND expires_at > ? ORDER BY created_at DESC",
                (user_id, now)
            )
            return [dict(r) for r in cursor.fetchall()]

    async def get_active_orders_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_active_orders_for_user, user_id)

    def _get_all_pending_orders(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE status IN ('PENDING', 'WAITING_REOTP')")
            return [dict(r) for r in cursor.fetchall()]

    async def get_all_pending_orders(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_all_pending_orders)

    def _update_order_status(
        self,
        order_id: str,
        status: str,
        otp_code: Optional[str] = None,
        full_sms: Optional[str] = None
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = time.time()
            if otp_code is not None:
                cursor.execute(
                    "UPDATE orders SET status = ?, otp_code = ?, full_sms = ?, last_checked_at = ? WHERE order_id = ?",
                    (status, otp_code, full_sms, now, order_id)
                )
            else:
                cursor.execute(
                    "UPDATE orders SET status = ?, last_checked_at = ? WHERE order_id = ?",
                    (status, now, order_id)
                )
            conn.commit()

    async def update_order_status(
        self,
        order_id: str,
        status: str,
        otp_code: Optional[str] = None,
        full_sms: Optional[str] = None
    ):
        await asyncio.to_thread(self._update_order_status, order_id, status, otp_code, full_sms)

    def _get_user_order_history(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            )
            return [dict(r) for r in cursor.fetchall()]

    async def get_user_order_history(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_user_order_history, user_id, limit)

    # ------------------ RECHARGE REQUESTS ------------------

    def _create_recharge_request(self, user_id: int, amount: float, utr: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO recharge_requests (user_id, amount, utr, status, created_at) VALUES (?, ?, ?, 'PENDING', ?)",
                (user_id, amount, utr, time.time())
            )
            req_id = cursor.lastrowid
            conn.commit()
            return req_id

    async def create_recharge_request(self, user_id: int, amount: float, utr: str) -> int:
        return await asyncio.to_thread(self._create_recharge_request, user_id, amount, utr)

    def _get_recharge_request(self, req_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM recharge_requests WHERE id = ?", (req_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    async def get_recharge_request(self, req_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_recharge_request, req_id)

    def _process_recharge_request(self, req_id: int, approved: bool) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM recharge_requests WHERE id = ?", (req_id,))
            req = cursor.fetchone()
            if not req or req["status"] != "PENDING":
                return None
            
            new_status = "APPROVED" if approved else "REJECTED"
            cursor.execute("UPDATE recharge_requests SET status = ? WHERE id = ?", (new_status, req_id))
            conn.commit()
            return dict(req)

    async def process_recharge_request(self, req_id: int, approved: bool) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._process_recharge_request, req_id, approved)

    # ------------------ SETTINGS ------------------

    def _get_setting(self, key: str, default: str = "") -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    async def get_setting(self, key: str, default: str = "") -> str:
        return await asyncio.to_thread(self._get_setting, key, default)

    def _set_setting(self, key: str, value: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO bot_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value)
            )
            conn.commit()

    async def set_setting(self, key: str, value: str):
        await asyncio.to_thread(self._set_setting, key, value)

    # ------------------ STATS ------------------

    def _get_bot_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total_users FROM users")
            total_users = cursor.fetchone()["total_users"]

            cursor.execute("SELECT COUNT(*) as total_orders, SUM(selling_price) as total_volume, SUM(selling_price - cost_price) as total_profit FROM orders WHERE status = 'COMPLETED'")
            completed = cursor.fetchone()

            cursor.execute("SELECT COUNT(*) as active_orders FROM orders WHERE status IN ('PENDING', 'WAITING_REOTP')")
            active_orders = cursor.fetchone()["active_orders"]

            return {
                "total_users": total_users,
                "total_orders": completed["total_orders"] or 0,
                "total_volume": round(completed["total_volume"] or 0.0, 2),
                "total_profit": round(completed["total_profit"] or 0.0, 2),
                "active_orders": active_orders
            }

    async def get_bot_stats(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_bot_stats)
