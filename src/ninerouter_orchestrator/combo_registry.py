from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


class ComboRegistry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.combos: list[str] = []
        self.refreshed_at: str | None = None

    def refresh(self) -> dict:
        with self.lock:
            previous = set(self.combos)
            try:
                combos, source = self._discover()
            except (OSError, ValueError, KeyError, TypeError):
                return {
                    "combos": list(self.combos),
                    "added": [],
                    "removed": [],
                    "source": "last successful refresh",
                    "refreshed_at": self.refreshed_at,
                    "error": "Cannot read 9router. Check the gateway and retry; previous routes are retained.",
                }
            current = set(combos)
            self.combos = sorted(current, key=str.casefold)
            self.refreshed_at = datetime.now(UTC).isoformat()
            return {
                "combos": self.combos,
                "added": sorted(current - previous, key=str.casefold),
                "removed": sorted(previous - current, key=str.casefold),
                "source": source,
                "refreshed_at": self.refreshed_at,
            }

    def _discover(self) -> tuple[list[str], str]:
        return self._from_gateway(), "9router gateway"

    @staticmethod
    def _from_gateway() -> list[str]:
        root = Path.home() / ".9router"
        machine = (root / "machine-id").read_text(encoding="utf-8").strip()
        secret = (root / "auth/cli-secret").read_text(encoding="utf-8").strip()
        token = hashlib.sha256(f"{machine}9r-cli-auth{secret}".encode()).hexdigest()[:16]
        request = urllib.request.Request(
            "http://127.0.0.1:20128/api/combos",
            headers={"x-9r-cli-token": token},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
        combos = payload["combos"]
        if not isinstance(combos, list):
            raise TypeError("Invalid registry")
        return [
            combo["name"]
            for combo in combos
            if combo.get("name") and combo.get("kind", "llm") in {None, "llm"}
        ]
