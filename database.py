import os
import psycopg2
from psycopg2 import pool

DATABASE_URL = os.environ.get("DATABASE_URL")

connection_pool = None

def init_pool():
    global connection_pool
    if connection_pool is None:
        connection_pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)
    return connection_pool

def get_connection():
    if connection_pool is None:
        init_pool()
    return connection_pool.getconn()

def release_connection(conn):
    if connection_pool:
        connection_pool.putconn(conn)

def get_cursor(conn):
    return conn.cursor()