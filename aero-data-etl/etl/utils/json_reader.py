"""
Streaming JSONL reader for large files.
Yields one JSON object per line, never loads entire file into memory.
"""
import json

def stream_jsonl(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception as e:
                yield {'_jsonl_error': str(e), '_raw_line': line}
