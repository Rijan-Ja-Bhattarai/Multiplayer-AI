"""Atomic local history storage, shared by the relay and desktop UI."""
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


class HistoryStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "history.sqlite3"
        with self.connection() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("CREATE TABLE IF NOT EXISTS records (namespace TEXT, key TEXT, value TEXT NOT NULL, PRIMARY KEY (namespace, key))")

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as database:
            with database:
                yield database

    def load(self, namespace):
        with self.connection() as database:
            return {key: json.loads(value) for key, value in database.execute(
                "SELECT key, value FROM records WHERE namespace = ?", (namespace,))}

    def save(self, namespace, key, value):
        encoded = json.dumps(value, ensure_ascii=False)
        with self.connection() as database:
            database.execute("INSERT INTO records VALUES (?, ?, ?) ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                             (namespace, key, encoded))

    def clear(self):
        with self.connection() as database:
            database.execute("DELETE FROM records")
        with closing(sqlite3.connect(self.path)) as database:
            database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            database.execute("VACUUM")


def model_context(messages, limit=100, byte_limit=190000):
    """Keep the saved archive intact while fitting the provider transport."""
    history = [{"role": message["role"], "content": message["content"]}
               for message in messages if message["role"] in ("user", "assistant")][-limit:]
    while len(history) > 1 and len(json.dumps(history).encode()) > byte_limit:
        history.pop(0)
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history
