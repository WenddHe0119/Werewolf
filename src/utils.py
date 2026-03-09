import hashlib
import random
from typing import Dict, List, Tuple

CANONICAL_ROLE_ORDER = ["werewolf", "seer", "witch", "villager"]


def parse_role_config(role_config_str: str) -> Dict[str, int]:
    role_config: Dict[str, int] = {}
    if not role_config_str:
        return role_config
    parts = [p.strip() for p in role_config_str.split(",") if p.strip()]
    for part in parts:
        if ":" not in part:
            raise ValueError("--role_config must be in role:count format.")
        role, count_str = part.split(":", 1)
        role = role.strip().lower()
        try:
            count = int(count_str)
        except ValueError:
            raise ValueError("--role_config must use integer counts.")
        if count < 0:
            raise ValueError("--role_config counts must be >= 0.")
        role_config[role] = role_config.get(role, 0) + count
    return role_config


def role_list_from_config(role_config: Dict[str, int]) -> List[str]:
    roles: List[str] = []
    for role in CANONICAL_ROLE_ORDER:
        roles.extend([role] * role_config.get(role, 0))
    for role, count in role_config.items():
        if role not in CANONICAL_ROLE_ORDER:
            roles.extend([role] * count)
    return roles


def derive_runtime_seed(global_seed: int, round_id: int) -> int:
    seed_input = f"{global_seed}:{round_id}".encode("utf-8")
    digest = hashlib.sha256(seed_input).digest()
    return int.from_bytes(digest[:8], "big")


def rotate_sequence(seq: List[int], shift: int) -> List[int]:
    if not seq:
        return seq
    shift = shift % len(seq)
    return seq[shift:] + seq[:shift]


def assign_roles_random(rng: random.Random, player_ids: List[int], roles: List[str]) -> Dict[int, str]:
    if len(player_ids) != len(roles):
        raise ValueError("Number of roles must equal number of players.")
    shuffled_roles = roles[:]
    rng.shuffle(shuffled_roles)
    return {pid: role for pid, role in zip(player_ids, shuffled_roles)}
