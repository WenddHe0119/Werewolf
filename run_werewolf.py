#!/usr/bin/env python3
import argparse
import json
import os
import random
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, List, Optional

from src.agent import Agent, ROLE_TEAMS
from src.config_loader import load_config
from src.env import load_env
from src.game import WerewolfGame
from src.metrics import aggregate_metrics
from src.models import GameConfig, Player
from src.templates import SpeechTemplateEngine, VerbosePrinter, load_yaml
from src.llm import build_llm_client, resolve_model_spec
from src.phase_prompts import PhasePromptEngine
from src.utils import (
    assign_roles_random,
    derive_runtime_seed,
    parse_role_config,
    role_list_from_config,
    rotate_sequence,
)


def validate_args(args, role_config):
    if args.num_players <= 0:
        raise ValueError("--num_players must be > 0.")
    if args.num_rounds <= 0:
        raise ValueError("--num_rounds must be > 0.")
    if args.fairness_test == "werewolf_window" and (args.num_rounds % args.num_players != 0):
        raise ValueError("--num_rounds must be a multiple of --num_players when --fairness_test is werewolf_window.")
    if sum(role_config.values()) != args.num_players:
        raise ValueError("the sum of roles in --role_config must equal --num_players.")


def build_agents(
    config: GameConfig,
    role_assignment: Dict[int, str],
    rng: random.Random,
    speech_engine=None,
    player_models: Optional[Dict[int, Dict[str, str]]] = None,
    allow_claim_roles: Optional[set] = None,
    prompt_engine: Optional[PhasePromptEngine] = None,
    role_settings: Optional[Dict[str, Dict]] = None,
):
    players: Dict[int, Player] = {}
    agents: Dict[int, Agent] = {}

    for pid, role in role_assignment.items():
        player = Player(id=pid, name=f"P{pid}", role=role, team=ROLE_TEAMS.get(role, "good_team"))
        players[pid] = player

    for pid, player in players.items():
        entry = player_models.get(pid) if player_models else None
        model_spec = None
        api_key_env = None
        api_base_env = None
        if entry:
            model_spec = f"{entry['provider']}:{entry['model']}"
            api_key_env = entry.get("api_key_env")
            api_base_env = entry.get("api_base_env")
        _, model_name = resolve_model_spec(model_spec)
        client = build_llm_client(model_spec, api_key_env=api_key_env, api_base_env=api_base_env)

        agent_config = SimpleNamespace(
            num_players=config.num_players,
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=config.max_tokens,
            deterministic_action=config.deterministic_action,
            model_name=model_name,
        )

        agent = Agent(
            player=player,
            config=agent_config,
            llm_client=client,
            rng=rng,
            speech_engine=speech_engine,
            allow_claim_roles=allow_claim_roles,
            prompt_engine=prompt_engine,
            role_settings=role_settings,
        )
        if player.role == "werewolf":
            teammates = [wid for wid, r in role_assignment.items() if r == "werewolf" and wid != pid]
            agent.set_teammates(teammates)
            agent.private_memory["wolf_night_discussions"] = []
        if player.role == "seer":
            agent.private_memory["checked_results"] = []
        if player.role == "witch":
            agent.private_memory["antidote_remaining"] = 1
            agent.private_memory["poison_remaining"] = 1
            agent.private_memory["antidote_used"] = False
            agent.private_memory["poison_used"] = False
            agent.private_memory["observed_kill_targets"] = []
        if player.role == "villager":
            agent.private_memory["self_notes"] = []
        agents[pid] = agent

    return players, agents


def assign_roles_for_round(
    round_id: int,
    player_ids: list,
    role_list: list,
    fairness_test: str,
    base_sequence: list,
    window_shift_per_round: int,
    runtime_seed: int,
):
    if fairness_test == "werewolf_window":
        shift = (round_id * window_shift_per_round) % len(base_sequence)
        rotated = rotate_sequence(base_sequence, shift)
        return {pid: role for pid, role in zip(rotated, role_list)}
    rng = random.Random(runtime_seed)
    return assign_roles_random(rng, player_ids, role_list)


def main():
    parser = argparse.ArgumentParser(description="Werewolf MAS simulation")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--num_players", type=int, default=None)
    parser.add_argument("--role_config", type=str, default=None)
    parser.add_argument("--global_seed", type=int, default=None)
    parser.add_argument("--num_rounds", type=int, default=None)
    parser.add_argument("--fairness_test", type=str, default=None, choices=["none", "werewolf_window"])
    parser.add_argument("--window_shift_per_round", type=int, default=None)
    parser.add_argument("--max_days", type=int, default=None)
    parser.add_argument("--benign_method", type=str, default=None, choices=["base", "claim"])
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top_p", type=float, default=None)
    parser.add_argument("--max_tokens", type=int, default=None)
    parser.add_argument("--deterministic_action", type=lambda x: str(x).lower() == "true", default=None)
    parser.add_argument("--use_public_memory", type=lambda x: str(x).lower() == "true", default=None)
    parser.add_argument("--memory_max_rounds", type=int, default=None)
    parser.add_argument("--log_dir", type=str, default=None)
    parser.add_argument("--save_transcript", type=lambda x: str(x).lower() == "true", default=None)
    parser.add_argument("--save_metrics", type=lambda x: str(x).lower() == "true", default=None)
    parser.add_argument("--verbose", type=lambda x: str(x).lower() == "true", default=None)
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    setting_dir = os.path.join(base_dir, "setting")
    env_path = os.path.join(setting_dir, ".env")
    load_env(env_path)

    config_path = args.config or os.path.join(setting_dir, "config.yaml")
    cfg = load_config(config_path)
    game_cfg = cfg.get("game", {})
    role_settings = cfg.get("roles", {})
    agents_cfg = cfg.get("agents", [])

    args.num_players = args.num_players or int(game_cfg.get("num_players", 8))
    args.global_seed = args.global_seed or int(game_cfg.get("global_seed", 42))
    args.num_rounds = args.num_rounds or int(game_cfg.get("num_rounds", 8))
    args.fairness_test = args.fairness_test or game_cfg.get("fairness_test", "none")
    args.window_shift_per_round = args.window_shift_per_round or int(game_cfg.get("window_shift_per_round", 1))
    args.max_days = args.max_days or int(game_cfg.get("max_days", 20))
    args.benign_method = args.benign_method or game_cfg.get("benign_method", "base")
    args.temperature = args.temperature if args.temperature is not None else float(game_cfg.get("temperature", 0.7))
    args.top_p = args.top_p if args.top_p is not None else float(game_cfg.get("top_p", 1.0))
    args.max_tokens = args.max_tokens if args.max_tokens is not None else int(game_cfg.get("max_tokens", 512))
    args.deterministic_action = (
        args.deterministic_action if args.deterministic_action is not None else bool(game_cfg.get("deterministic_action", False))
    )
    args.use_public_memory = (
        args.use_public_memory if args.use_public_memory is not None else bool(game_cfg.get("use_public_memory", True))
    )
    args.memory_max_rounds = args.memory_max_rounds or int(game_cfg.get("memory_max_rounds", 50))
    args.log_dir = args.log_dir or game_cfg.get("log_dir", "./logs")
    args.save_transcript = (
        args.save_transcript if args.save_transcript is not None else bool(game_cfg.get("save_transcript", True))
    )
    args.save_metrics = args.save_metrics if args.save_metrics is not None else bool(game_cfg.get("save_metrics", True))
    args.verbose = args.verbose if args.verbose is not None else bool(game_cfg.get("verbose", True))

    speech_template_file = "speech_temple.yaml" if args.benign_method == "base" else "speech_temple_claim.yaml"
    speech_templates = load_yaml(os.path.join(setting_dir, speech_template_file))
    phase_prompt = load_yaml(os.path.join(setting_dir, "phase_prompt.yaml"))
    phase_instruction = load_yaml(os.path.join(setting_dir, "phase_instruction.yaml"))
    verbose_templates = load_yaml(os.path.join(setting_dir, "verbose.yaml"))
    speech_engine = SpeechTemplateEngine(speech_templates, mode=args.benign_method)
    prompt_engine = PhasePromptEngine(phase_prompt)
    verbose_printer = VerbosePrinter(verbose_templates, enabled=args.verbose)

    if args.role_config:
        role_config = parse_role_config(args.role_config)
    else:
        role_config = {r: int(cfg.get("count", 0)) for r, cfg in role_settings.items() if isinstance(cfg, dict)}
    validate_args(args, role_config)

    player_ids = list(range(1, args.num_players + 1))
    role_list = role_list_from_config(role_config)

    base_rng = random.Random(args.global_seed)
    base_sequence = player_ids[:]
    base_rng.shuffle(base_sequence)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log_dir = os.path.join(args.log_dir, f"{timestamp}_{args.global_seed}")

    player_models: Dict[int, Dict[str, str]] = {}
    for entry in agents_cfg:
        raw_id = entry.get("id", "")
        if not isinstance(raw_id, str) or not raw_id.startswith("P"):
            raise ValueError(f"Invalid agent id in config agents: {raw_id}")
        try:
            pid = int(raw_id[1:])
        except ValueError as exc:
            raise ValueError(f"Invalid agent id in config agents: {raw_id}") from exc
        player_models[pid] = entry
    if len(player_models) != args.num_players:
        raise ValueError(
            f"config agents must contain exactly {args.num_players} unique ids; "
            f"found {len(player_models)}."
        )
    if set(player_models.keys()) != set(player_ids):
        raise ValueError(f"config agent ids must cover all players P1..P{args.num_players}.")

    config = GameConfig(
        num_players=args.num_players,
        role_config=role_config,
        global_seed=args.global_seed,
        num_rounds=args.num_rounds,
        fairness_test=args.fairness_test,
        window_shift_per_round=args.window_shift_per_round,
        max_days=args.max_days,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        deterministic_action=args.deterministic_action,
        use_public_memory=args.use_public_memory,
        memory_max_rounds=args.memory_max_rounds,
        log_dir=run_log_dir,
        save_transcript=args.save_transcript,
        save_metrics=args.save_metrics,
        verbose=args.verbose,
    )

    results = []
    verbose_printer.print_block(
        "experiment_start",
        {
            "global_seed": args.global_seed,
            "num_rounds": args.num_rounds,
            "num_players": args.num_players,
            "role_config": args.role_config or role_config,
        },
    )
    for round_id in range(1, config.num_rounds + 1):
        runtime_seed = derive_runtime_seed(config.global_seed, round_id)
        rng = random.Random(runtime_seed)
        role_assignment = assign_roles_for_round(
            round_id - 1,
            player_ids,
            role_list,
            config.fairness_test,
            base_sequence,
            config.window_shift_per_round,
            runtime_seed,
        )
        allow_claim_roles = {"seer", "witch"} if args.benign_method == "claim" else set()
        players, agents = build_agents(
            config,
            role_assignment,
            rng,
            speech_engine=speech_engine,
            player_models=player_models,
            allow_claim_roles=allow_claim_roles,
            prompt_engine=prompt_engine,
            role_settings=role_settings,
        )
        verbose_printer.print_block(
            "round_start",
            {
                "round_id": round_id,
                "runtime_seed": runtime_seed,
                "alive_players": list(range(1, config.num_players + 1)),
                "dead_players": [],
            },
        )
        game = WerewolfGame(
            round_id=round_id,
            config=config,
            players=players,
            agents=agents,
            runtime_seed=runtime_seed,
            verbose_printer=verbose_printer,
            player_models=player_models,
            phase_instruction=phase_instruction,
        )
        result = game.run()
        results.append(result)
        if config.save_transcript:
            game.save_transcript(config.log_dir)

    if config.save_metrics:
        metrics = aggregate_metrics(results, config.num_players)
        os.makedirs(config.log_dir, exist_ok=True)
        path = os.path.join(config.log_dir, f"metrics_seed_{config.global_seed}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
