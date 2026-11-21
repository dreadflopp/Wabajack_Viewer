"""Atomic, portable JSON snapshots of parsed mod lists and verified source choices."""
import gzip
import json
from pathlib import Path
import tempfile


def write_snapshot(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            with gzip.GzipFile(fileobj=stream, mode='wb', compresslevel=1, mtime=0) as compressed:
                # Streaming encoding keeps full package manifests out of a second giant string.
                encoder = json.JSONEncoder(ensure_ascii=False, separators=(',', ':'))
                buffer = []
                for chunk in encoder.iterencode(payload):
                    buffer.append(chunk)
                    if len(buffer) >= 4096:
                        compressed.write(''.join(buffer).encode('utf-8'))
                        buffer.clear()
                compressed.write(''.join(buffer).encode('utf-8'))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
