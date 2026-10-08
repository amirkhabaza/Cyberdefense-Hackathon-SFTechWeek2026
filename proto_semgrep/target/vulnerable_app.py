"""Deliberately vulnerable demo code. The credentials below are FAKE."""
import os
import sqlite3

DB_PASSWORD = "SuperSecret123!"
STRIPE_API_KEY = "sk_test_FAKE_not_a_real_key_1234"
AWS_ACCESS_KEY_ID = "AKIAFAKEFAKEFAKE1234"

conn = sqlite3.connect("app.db")


def get_user_fstring(name):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cur.fetchall()


def get_user_concat(name):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = '" + name + "'")
    return cur.fetchall()


def get_user_percent(user_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s" % user_id)
    return cur.fetchall()


def get_user_format(email):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = '{}'".format(email))
    return cur.fetchall()
