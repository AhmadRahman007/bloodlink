import os
import time

import pymysql
from flask import g

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "bloodlink"),
    "password": os.getenv("DB_PASSWORD", "bloodlink"),
    "database": os.getenv("DB_NAME", "bloodlink"),
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": True,
    "charset": "utf8mb4",
    # show timestamps in Pakistan time regardless of the server/container timezone
    "init_command": "SET time_zone = '%s'" % os.getenv("DB_TIMEZONE", "+05:00"),
}

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        full_name VARCHAR(100) NOT NULL,
        email VARCHAR(120) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS donors (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        blood_group VARCHAR(3) NOT NULL,
        city VARCHAR(60) NOT NULL,
        phone VARCHAR(20) NOT NULL,
        last_donation DATE NULL,
        is_available TINYINT(1) DEFAULT 1,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blood_requests (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        patient_name VARCHAR(100) NOT NULL,
        blood_group VARCHAR(3) NOT NULL,
        units INT NOT NULL,
        hospital VARCHAR(120) NOT NULL,
        city VARCHAR(60) NOT NULL,
        contact VARCHAR(20) NOT NULL,
        urgency ENUM('normal','urgent','critical') DEFAULT 'normal',
        status ENUM('open','fulfilled') DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS responses (
        id INT AUTO_INCREMENT PRIMARY KEY,
        request_id INT NOT NULL,
        donor_id INT NOT NULL,
        message VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uniq_response (request_id, donor_id),
        FOREIGN KEY (request_id) REFERENCES blood_requests(id) ON DELETE CASCADE,
        FOREIGN KEY (donor_id) REFERENCES donors(id) ON DELETE CASCADE
    )
    """,
]


def connect():
    return pymysql.connect(**DB_CONFIG)


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=None, one=False):
    with get_db().cursor() as cur:
        cur.execute(sql, args or ())
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=None):
    with get_db().cursor() as cur:
        cur.execute(sql, args or ())
        return cur.lastrowid


def init_db(retries=30, delay=2):
    """Wait for MySQL to accept connections, then create tables."""
    for attempt in range(1, retries + 1):
        try:
            conn = connect()
            with conn.cursor() as cur:
                for stmt in SCHEMA:
                    cur.execute(stmt)
            conn.close()
            print("Database ready.", flush=True)
            return
        except pymysql.err.OperationalError as e:
            print(f"Waiting for database ({attempt}/{retries}): {e}", flush=True)
            time.sleep(delay)
    raise RuntimeError("Could not connect to the database.")
