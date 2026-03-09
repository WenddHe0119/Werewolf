from __future__ import annotations

from typing import Any, Dict, List

import yaml


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def normalize_config(data: Dict[str, Any]) -> Dict[str, Any]:
    # Legacy flat keys support
    game = data.get("game", {})
    roles = data.get("roles", {})
    agents = data.get("agents", []) or []

    if "num_wolves" in data or "num_seers" in data or "num_witches" in data:
        roles = roles or {}
        roles.setdefault("werewolf", {})
        roles.setdefault("seer", {})
        roles.setdefault("witch", {})
        if "num_wolves" in data:
            roles["werewolf"]["count"] = int(data.get("num_wolves", 0))
        if "num_seers" in data:
            roles["seer"]["count"] = int(data.get("num_seers", 0))
        if "num_witches" in data:
            roles["witch"]["count"] = int(data.get("num_witches", 0))

    if "setting" in data:
        setting = data.get("setting", {}) or {}
        game = {**setting, **game}

    # Ensure role counts
    for role, cfg in roles.items():
        if isinstance(cfg, dict):
            cfg.setdefault("count", 0)

    # derive num_players if missing
    num_players = game.get("num_players")
    if not num_players:
        total_roles = sum(int(cfg.get("count", 0)) for cfg in roles.values() if isinstance(cfg, dict))
        num_players = total_roles or len(agents) or 8
        game["num_players"] = num_players

    # ensure villagers count
    if "villager" not in roles:
        roles["villager"] = {"count": 0}
    if roles["villager"].get("count", 0) == 0:
        total_non_villagers = sum(
            int(cfg.get("count", 0)) for r, cfg in roles.items() if r != "villager" and isinstance(cfg, dict)
        )
        roles["villager"]["count"] = max(0, int(num_players) - total_non_villagers)

    return {"game": game, "roles": roles, "agents": agents}


def load_config(path: str) -> Dict[str, Any]:
    data = load_yaml(path)
    return normalize_config(data)
