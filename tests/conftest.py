from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"", payload: Any = None) -> None:
        self.status_code = status_code
        self.content = content if payload is None else json.dumps(payload).encode()
        self._payload = payload

    def json(self) -> Any:
        return self._payload if self._payload is not None else json.loads(self.content)


class FakeSession:
    """Returns queued responses in order and records every call."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def make_zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def no_sleep():
    return lambda _seconds: None
