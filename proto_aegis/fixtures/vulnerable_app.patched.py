"""Patched: parameterized queries and secrets read from the environment."""
import os
import sqlite3

DB_PASSWORD = os.environ["DB_PASSWORD"]
STRIPE_API_KEY = os.environ["STRIPE_API_KEY"]
AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]

conn = sqlite3.connect("app.db")


def get_user_fstring(name):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = ?", (name,))
    return cur.fetchall()


def get_user_concat(name):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = ?", (name,))
    return cur.fetchall()


def get_user_percent(user_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cur.fetchall()


def get_user_format(email):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (email,))
    return cur.fetchall()
