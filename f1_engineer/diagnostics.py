from __future__ import annotations

import traceback


def exception_trace(exception):
    chain = []
    seen = set()
    current = exception
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append({"type": type(current).__name__, "frames": [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(current.__traceback__)]})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return chain
