"""Push-Nachrichten über ntfy (https://ntfy.sh)."""
from __future__ import annotations

import json
import urllib.request


def send(topic: str | None, title: str, message: str, priority: int = 3, tags: list[str] | None = None,
         click: str | None = None, actions: list[dict] | None = None, server: str = "https://ntfy.sh",
         log=print) -> bool:
    payload = {"topic": topic, "title": title, "message": message, "priority": priority,
               "tags": tags or []}
    if click:
        payload["click"] = click
    if actions:
        payload["actions"] = [{"action": "view", "clear": False, **a} for a in actions[:3]]
    if not topic:
        log(f"[ntfy, Testmodus ohne Topic] {title}\n{message}")
        return False
    req = urllib.request.Request(server, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200 <= r.status < 300
    except Exception as e:  # Push darf den Lauf nie abbrechen
        log(f"[ntfy] Fehler: {e}")
        return False
