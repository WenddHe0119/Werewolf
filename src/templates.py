from __future__ import annotations

import os
from typing import Dict, List, Optional

import yaml


def load_yaml(path: str) -> Dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _fmt_player(pid: Optional[int]) -> str:
    if not pid:
        return "none"
    return f"P{pid}"


def _fmt_players(pids: Optional[List[int]]) -> str:
    if not pids:
        return "none"
    return ", ".join(f"P{pid}" for pid in pids)


def _fmt_vote_map(vote_map: Optional[Dict[int, int]]) -> str:
    if not vote_map:
        return "none"
    items = []
    for voter, target in vote_map.items():
        items.append(f"P{voter}->P{target}")
    return ", ".join(items)


def _fmt_value(val):
    if isinstance(val, list):
        return _fmt_players(val)
    if isinstance(val, dict):
        return _fmt_vote_map(val)
    if val is None:
        return "none"
    return str(val)


class SafeFormatDict(dict):
    def __missing__(self, key):
        return "none"


class VerbosePrinter:
    def __init__(self, templates: Dict, enabled: bool = False):
        self.enabled = enabled
        self.templates = templates.get("cli_print_templates", {}) if templates else {}

    def print_block(self, name: str, context: Dict[str, object]) -> None:
        if not self.enabled:
            return
        lines = self.templates.get(name)
        if not lines:
            return
        formatted_context = {k: _fmt_value(v) for k, v in context.items()}
        formatter = SafeFormatDict(formatted_context)
        for line in lines:
            print(line.format_map(formatter))


class SpeechTemplateEngine:
    def __init__(self, templates: Dict, mode: str = "base"):
        self.templates = templates.get("speech_templates", {}) if templates else {}
        self.mode = mode

    def _role_templates(self, role: str) -> Dict:
        return self.templates.get(role, {}).get("templates", {})

    def get_role_templates(self, role: str) -> Dict:
        return self._role_templates(role)

    def _select_category(self, role: str, day: int) -> Optional[str]:
        if self.mode == "claim" and role in {"seer", "witch"}:
            return "claim"
        if role == "villager":
            return "opening" if day <= 1 else "analytical"
        if role == "seer":
            return "reasoning"
        if role == "witch":
            return "neutral"
        if role == "werewolf":
            return "disguise_villager"
        return None

    def compose(
        self,
        role: str,
        day: int,
        target: Optional[int],
        vote_target: Optional[int],
        reason: str,
        result: Optional[str] = None,
        side: Optional[str] = None,
        action: Optional[str] = None,
        player: Optional[int] = None,
    ) -> str:
        role_templates = self._role_templates(role)
        category = self._select_category(role, day)
        lines = None
        if category:
            lines = role_templates.get(category)
        if not lines:
            for _, candidate in role_templates.items():
                if candidate:
                    lines = candidate
                    break
        if not lines:
            return ""

        mapping = {
            "target": _fmt_player(target),
            "vote_target": _fmt_player(vote_target),
            "reason": reason or "insufficient information",
            "result": result or "unknown",
            "side": side or "unknown",
            "action": action or "observed",
            "player": _fmt_player(player),
        }
        rendered = [line.format_map(SafeFormatDict(mapping)) for line in lines]
        return " ".join(rendered).strip()
