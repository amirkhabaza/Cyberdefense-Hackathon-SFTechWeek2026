"""Control file: should produce no findings."""
import os
import sqlite3

DB_PASSWORD = os.environ["DB_PASSWORD"]
conn = sqlite3.connect("app.db")


def get_user(name):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = ?", (name,))
    return cur.fetchall()
