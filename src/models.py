from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Player:
    id: int
    name: str
    role: str
    team: str
    alive: bool = True


@dataclass
class AgentConfig:
    llm_backend: Optional[str] = None
    model_name: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 512
    top_p: float = 1.0
    reasoning_style: str = "balanced"
    strategy_profile: str = "balanced"
    deterministic_action: bool = False
    memory_limits: Dict[str, int] = field(default_factory=lambda: {
        "max_public_rounds": 50,
        "max_private_entries": 200,
    })


@dataclass
class PublicMemory:
    day: int = 1
    phase: str = "night"
    alive_players: List[int] = field(default_factory=list)
    dead_players: List[int] = field(default_factory=list)
    announcements: List[Dict[str, str]] = field(default_factory=list)
    speech_history: List[Dict[str, str]] = field(default_factory=list)
    vote_history: List[Dict[str, int]] = field(default_factory=list)


@dataclass
class GameConfig:
    num_players: int
    role_config: Dict[str, int]
    global_seed: int
    num_rounds: int = 8
    fairness_test: str = "none"
    window_shift_per_round: int = 1
    max_days: int = 20
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 512
    deterministic_action: bool = False
    use_public_memory: bool = True
    memory_max_rounds: int = 50
    log_dir: str = "./logs"
    save_transcript: bool = True
    save_metrics: bool = True
    verbose: bool = False


@dataclass
class RoundResult:
    round_id: int
    winner: str
    day_count: int
    runtime_seed: int
    role_assignment: Dict[int, str]
    survival_rounds: Dict[int, int]
    seer_checks: Dict[int, Dict[str, int]]
    witch_usage: Dict[int, Dict[str, int]]
    vote_history: List[Dict[str, int]]
    speech_lengths: Dict[int, int]
    player_models: Dict[int, Dict[str, str]] = field(default_factory=dict)
    belief_history: List[Dict[str, object]] = field(default_factory=list)
