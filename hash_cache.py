"""Disposable file-hash cache; failures always fall back to reading file bytes."""
import json
import os
from pathlib import Path
import sqlite3


def default_cache_path():
    base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    return base / 'WabbajackViewer' / 'xxhash64-v1.sqlite'


class HashCache:
    def __init__(self, path):
        self.connection = None
        self.pending = 0
        if path is None:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path, timeout=.2)
            self.connection.execute('CREATE TABLE IF NOT EXISTS hashes(path TEXT PRIMARY KEY, signature TEXT, digest TEXT)')
        except (OSError, sqlite3.Error):
            self.close()

    @staticmethod
    def signature(info):
        return json.dumps([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns])

    def get(self, path, info):
        if self.connection:
            try:
                row = self.connection.execute('SELECT signature,digest FROM hashes WHERE path=?', (str(path.absolute()),)).fetchone()
                if row and row[0] == self.signature(info):
                    return row[1]
            except sqlite3.Error:
                self.close()
        return None

    def put(self, path, info, digest):
        if self.connection:
            try:
                self.connection.execute('INSERT OR REPLACE INTO hashes VALUES(?,?,?)', (str(path.absolute()), self.signature(info), digest))
                self.pending += 1
                if self.pending >= 512:
                    self.connection.commit()
                    self.pending = 0
            except sqlite3.Error:
                self.close()

    def close(self):
        connection, self.connection = self.connection, None
        if connection:
            try:
                connection.commit()
            except sqlite3.Error:
                pass
            finally:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
