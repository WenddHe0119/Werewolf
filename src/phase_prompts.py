from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .templates import SafeFormatDict


def _fmt_player(pid: int) -> str:
    return f"P{pid}"


def _fmt_players(pids: List[int]) -> List[str]:
    return [f"P{pid}" for pid in pids]


def _fmt_belief(belief: Dict[int, float]) -> str:
    payload = {f"P{pid}": round(float(val), 4) for pid, val in belief.items()}
    return json.dumps(payload, ensure_ascii=False)


def _fmt_role_map(role_map: Dict[int, str]) -> Dict[str, str]:
    return {f"P{pid}": str(role) for pid, role in role_map.items()}


def _fmt_speeches(speeches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted = []
    for s in speeches:
        formatted.append(
            {
                "day": s.get("day"),
                "speaker": _fmt_player(s.get("speaker")) if s.get("speaker") else None,
                "text": s.get("text"),
                "target": _fmt_player(s.get("target")) if s.get("target") else None,
                "reason": s.get("reason"),
            }
        )
    return formatted


def format_context(context: Dict[str, Any]) -> Dict[str, Any]:
    formatted: Dict[str, Any] = {}
    for key, value in context.items():
        if key in {"alive_player", "partner"} and isinstance(value, list):
            formatted[key] = _fmt_players(value)
        elif key == "belief" and isinstance(value, dict):
            formatted[key] = _fmt_belief(value)
        elif key == "known_player_role" and isinstance(value, dict):
            formatted[key] = _fmt_role_map(value)
        elif key in {"all_player_speech", "known_partner_speech"} and isinstance(value, list):
            formatted[key] = _fmt_speeches(value)
        else:
            formatted[key] = value
    return formatted


class PhasePromptEngine:
    def __init__(self, data: Dict[str, Any]):
        self.prompts = data.get("prompts", {}) if data else {}

    def render(
        self,
        role: str,
        phase: str,
        variant: Optional[str],
        context: Dict[str, Any],
    ) -> Optional[str]:
        role_prompts = self.prompts.get(role, {})
        if not role_prompts:
            return None
        phase_node = role_prompts.get(phase)
        if not phase_node:
            return None
        if isinstance(phase_node, dict) and "prompt" in phase_node:
            template = phase_node.get("prompt")
        elif variant and isinstance(phase_node, dict):
            template = phase_node.get(variant, {}).get("prompt")
        else:
            template = None
        if not template:
            return None
        formatted = format_context(context)
        return template.format_map(SafeFormatDict(formatted))

    def meta_output_rule(self) -> List[str]:
        meta = self.prompts.get("meta", {})
        return meta.get("output_rule", []) if isinstance(meta, dict) else []

    @staticmethod
    def parse_json(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        # Strip code fences if present
        stripped = text.strip()
        if stripped.startswith("'```"):
            parts = stripped.split("'```")
            if len(parts) >= 2:
                stripped = parts[1].strip()
        else:
            stripped = text
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        snippet = stripped[start : end + 1]
        try:
            return json.loads(snippet)
        except Exception:
            pass
        try:
            import ast

            parsed = ast.literal_eval(snippet)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
        return None
