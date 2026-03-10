import json
import os
import random
from typing import Dict, List, Optional, Tuple

from .agent import Agent, ROLE_TEAMS
from .models import PublicMemory, RoundResult


def count_teams(players: Dict[int, object]) -> Tuple[int, int]:
    wolves = 0
    good = 0
    for p in players.values():
        if not p.alive:
            continue
        if p.team == "wolf_team":
            wolves += 1
        else:
            good += 1
    return wolves, good


class WerewolfGame:
    def __init__(
        self,
        round_id: int,
        config,
        players: Dict[int, object],
        agents: Dict[int, Agent],
        runtime_seed: int,
        verbose_printer=None,
        player_models: Optional[Dict[int, str]] = None,
        phase_instruction: Optional[Dict[str, object]] = None,
    ):
        self.round_id = round_id
        self.config = config
        self.players = players
        self.agents = agents
        self.runtime_seed = runtime_seed
        self.speaker_rng = random.SystemRandom()
        self.verbose_printer = verbose_printer
        self.player_models = player_models or {}
        self.phase_instruction = phase_instruction or {}
        self.winner: Optional[str] = None
        self.final_day: Optional[int] = None
        self.public_memory = PublicMemory(
            day=1,
            phase="night",
            alive_players=sorted([pid for pid, p in players.items() if p.alive]),
            dead_players=[],
        )
        self.phase_state: Dict[str, object] = {
            "player_speech_role": {},
            "known_player_role": {},
            "actioned_player": {"saved_player": None, "poisoned_player": None},
            "player_speech": [],
            "death_target": None,
        }
        self.events: List[Dict] = []
        self.survival_rounds: Dict[int, int] = {pid: 0 for pid in players}
        self.seer_checks: Dict[int, Dict[str, int]] = {}
        self.witch_usage: Dict[int, Dict[str, int]] = {}
        self.vote_history: List[Dict[str, int]] = []
        self.belief_history: List[Dict[str, object]] = []
        self.speech_lengths: Dict[int, int] = {pid: 0 for pid in players}
        self._belief_full_by_player: Dict[int, Dict[int, float]] = {}

    def log_event(self, event_type: str, payload: Dict) -> None:
        payload = {"type": event_type, **payload}
        self.events.append(payload)

    @staticmethod
    def serialize_belief(belief: Dict[int, float]) -> Dict[str, float]:
        return {str(pid): float(val) for pid, val in belief.items()}

    def _update_full_belief(self, pid: int, belief: Dict[int, float]) -> Dict[int, float]:
        full = self._belief_full_by_player.get(pid, {}).copy()
        for other, val in belief.items():
            full[int(other)] = float(val)
        for other in self.players:
            if other == pid:
                continue
            full.setdefault(other, 0.0)
        self._belief_full_by_player[pid] = full
        return full

    def update_public_alive_dead(self) -> None:
        self.public_memory.alive_players = sorted([pid for pid, p in self.players.items() if p.alive])
        self.public_memory.dead_players = sorted([pid for pid, p in self.players.items() if not p.alive])

    def public_memory_for_agents(self) -> Dict:
        if self.config.use_public_memory:
            base = dict(self.public_memory.__dict__)
        else:
            base = {
            "day": self.public_memory.day,
            "phase": self.public_memory.phase,
            "alive_players": self.public_memory.alive_players,
            "dead_players": self.public_memory.dead_players,
            "announcements": [],
            "speech_history": [],
            "vote_history": [],
        }
        base["player_speech_role"] = self.phase_state.get("player_speech_role", {})
        base["known_player_role"] = self.phase_state.get("known_player_role", {})
        base["actioned_player"] = self.phase_state.get("actioned_player", {})
        base["all_player_speech"] = self.phase_state.get("player_speech", [])
        return base

    def trim_public_memory(self) -> None:
        if not self.config.use_public_memory:
            return
        if self.config.memory_max_rounds <= 0:
            return
        min_day = self.public_memory.day - self.config.memory_max_rounds + 1
        if min_day <= 1:
            return
        self.public_memory.announcements = [a for a in self.public_memory.announcements if a.get("day", 0) >= min_day]
        self.public_memory.speech_history = [s for s in self.public_memory.speech_history if s.get("day", 0) >= min_day]
        self.public_memory.vote_history = [v for v in self.public_memory.vote_history if v.get("day", 0) >= min_day]

    def check_win_condition(self) -> Optional[str]:
        wolves, good = count_teams(self.players)
        if wolves == 0:
            return "good_team"
        if wolves >= good:
            return "wolf_team"
        return None

    def night_phase(self) -> Dict[str, object]:
        if self.verbose_printer:
            self.verbose_printer.print_block("night_start", {"round_id": self.public_memory.day})
        self.phase_state["actioned_player"] = {"saved_player": None, "poisoned_player": None}

        alive_werewolves = [pid for pid, p in self.players.items() if p.alive and p.role == "werewolf"]
        candidate_targets = [pid for pid, p in self.players.items() if p.alive and p.role != "werewolf"]
        if self.verbose_printer:
            self.verbose_printer.print_block(
                "werewolf_discussion_start",
                {
                    "round_id": self.public_memory.day,
                    "alive_werewolves": alive_werewolves,
                    "candidate_targets": candidate_targets,
                },
            )
        wolf_targets: List[int] = []
        for pid, agent in self.agents.items():
            if not self.players[pid].alive:
                continue
            if self.players[pid].role != "werewolf":
                continue
            if not agent.update_belief_from_prompt("night_action", self.public_memory_for_agents()):
                agent.update_belief(agent.observe(self.public_memory_for_agents(), agent.private_memory))
            action = agent.night_action(self.public_memory_for_agents(), None)
            target = action.get("kill_target")
            if target:
                wolf_targets.append(target)
            reason = action.get("reason", "no reason")
            self.log_event("werewolf_discussion", {"speaker": pid, "target": target, "reason": reason})
            if self.verbose_printer:
                self.verbose_printer.print_block(
                    "werewolf_discussion_message",
                    {
                        "round_id": self.public_memory.day,
                        "speaker": pid,
                        "target": target,
                        "reason": reason,
                    },
                )
        kill_target = None
        if wolf_targets:
            counts: Dict[int, int] = {}
            for t in wolf_targets:
                counts[t] = counts.get(t, 0) + 1
            max_count = max(counts.values())
            tied = [t for t, c in counts.items() if c == max_count]
            kill_target = tied[0] if len(tied) == 1 else self.agents[wolf_targets[0]].rng.choice(tied)
        self.phase_state["death_target"] = kill_target
        if self.verbose_printer:
            self.verbose_printer.print_block(
                "werewolf_discussion_end",
                {"round_id": self.public_memory.day, "target": kill_target},
            )
        self.log_event("night_wolf", {"kill_target": kill_target})

        seer_result = None
        for pid, agent in self.agents.items():
            if not self.players[pid].alive:
                continue
            if self.players[pid].role != "seer":
                continue
            action = agent.night_action(self.public_memory_for_agents(), kill_target)
            check_target = action.get("check_target")
            if check_target:
                alignment = self.players[check_target].team
                seer_result = {
                    "seer": pid,
                    "target": check_target,
                    "alignment": alignment,
                    "reason": action.get("reason"),
                }
                agent.private_memory.setdefault("checked_results", []).append(
                    {"target": check_target, "alignment": alignment}
                )
                self.phase_state.setdefault("known_player_role", {})[check_target] = (
                    "werewolf" if alignment == "wolf_team" else "good"
                )
                if self.verbose_printer:
                    self.verbose_printer.print_block(
                        "seer_view",
                        {
                            "round_id": self.public_memory.day,
                            "target": check_target,
                            "result": "werewolf" if alignment == "wolf_team" else "good",
                            "reason": action.get("reason", "no reason"),
                        },
                    )
            self.seer_checks.setdefault(pid, {"checks": 0, "hits": 0})
            if check_target:
                self.seer_checks[pid]["checks"] += 1
                if alignment == "wolf_team":
                    self.seer_checks[pid]["hits"] += 1
            if not agent.update_belief_from_prompt("night_action", self.public_memory_for_agents()):
                agent.update_belief(agent.observe(self.public_memory_for_agents(), agent.private_memory))
        if seer_result:
            self.log_event("night_seer", seer_result)

        witch_action = None
        for pid, agent in self.agents.items():
            if not self.players[pid].alive:
                continue
            if self.players[pid].role != "witch":
                continue
            if self.verbose_printer:
                self.verbose_printer.print_block(
                    "witch_view_start",
                    {
                        "round_id": self.public_memory.day,
                        "killed_target": kill_target,
                        "antidote_available": bool(agent.private_memory.get("antidote_remaining", 1)),
                        "poison_available": bool(agent.private_memory.get("poison_remaining", 1)),
                    },
                )
            action = agent.night_action(self.public_memory_for_agents(), kill_target)
            witch_action = {"witch": pid, **action}
            antidote = int(agent.private_memory.get("antidote_remaining", 1))
            poison = int(agent.private_memory.get("poison_remaining", 1))
            agent.private_memory.setdefault("observed_kill_targets", []).append(kill_target)
            if action.get("use_antidote") and antidote > 0:
                agent.private_memory["antidote_remaining"] = antidote - 1
                self.witch_usage.setdefault(pid, {"antidote": 0, "poison": 0})
                self.witch_usage[pid]["antidote"] += 1
            if action.get("use_poison") and poison > 0:
                agent.private_memory["poison_remaining"] = poison - 1
                self.witch_usage.setdefault(pid, {"antidote": 0, "poison": 0})
                self.witch_usage[pid]["poison"] += 1
            self.phase_state["actioned_player"] = {
                "saved_player": kill_target if action.get("use_antidote") else None,
                "poisoned_player": action.get("poison_target") if action.get("use_poison") else None,
            }
            if not agent.update_belief_from_prompt("night_action", self.public_memory_for_agents()):
                agent.update_belief(agent.observe(self.public_memory_for_agents(), agent.private_memory))
            if self.verbose_printer:
                saved_target = kill_target if action.get("use_antidote") else None
                poisoned_target = action.get("poison_target") if action.get("use_poison") else None
                self.verbose_printer.print_block(
                    "witch_view_decision",
                    {
                        "round_id": self.public_memory.day,
                        "saved_target": saved_target,
                        "poisoned_target": poisoned_target,
                        "reason": action.get("reason", "no reason"),
                    },
                )
        if witch_action:
            self.log_event("night_witch", witch_action)

        return {
            "kill_target": kill_target,
            "witch_action": witch_action,
        }

    def resolve_night_deaths(self, night_result: Dict[str, object]) -> List[int]:
        deaths: List[int] = []
        kill_target = night_result.get("kill_target")
        witch_action = night_result.get("witch_action") or {}
        if kill_target:
            use_antidote = bool(witch_action.get("use_antidote"))
            if not use_antidote:
                deaths.append(kill_target)
        poison_target = witch_action.get("poison_target")
        if witch_action.get("use_poison") and poison_target:
            if poison_target not in deaths:
                deaths.append(poison_target)
        for pid in deaths:
            if self.players[pid].alive:
                self.players[pid].alive = False
        self.update_public_alive_dead()
        return deaths

    def day_phase(self, night_deaths: List[int]) -> Optional[int]:
        if night_deaths:
            message = "Night deaths: " + ", ".join([f"P{pid}" for pid in night_deaths])
        else:
            message = "Night deaths: none"
        self.public_memory.announcements.append({"day": self.public_memory.day, "message": message})
        self.log_event("announce_deaths", {"day": self.public_memory.day, "message": message})
        if self.verbose_printer:
            self.verbose_printer.print_block(
                "day_start",
                {
                    "round_id": self.public_memory.day,
                    "announcement": message,
                    "alive_players": self.public_memory.alive_players,
                },
            )

        speak_order = self.public_memory.alive_players[:]
        self.speaker_rng.shuffle(speak_order)
        for pid in speak_order:
            if self.verbose_printer:
                self.verbose_printer.print_block("speech_start", {"round_id": self.public_memory.day, "speaker": pid})
            agent = self.agents[pid]
            obs = agent.observe(self.public_memory_for_agents(), agent.private_memory)
            # Optional belief update before speech (day_vote covers later updates)
            belief_snapshot = self.serialize_belief(agent.belief)
            full_snapshot = self.serialize_belief(self._update_full_belief(pid, agent.belief))
            self.belief_history.append(
                {
                    "day": self.public_memory.day,
                    "phase": "day",
                    "action": "speech",
                    "player": pid,
                    "belief": belief_snapshot,
                    "belief_full": full_snapshot,
                }
            )
            text, suspects = agent.speak(self.public_memory_for_agents())
            self.public_memory.speech_history.append(
                {"day": self.public_memory.day, "speaker": pid, "text": text, "suspects": suspects}
            )
            self.speech_lengths[pid] += len(text.split())
            speech_meta = agent.last_speech_meta or {}
            self.phase_state.setdefault("player_speech_role", {})[pid] = speech_meta.get("role_view")
            self.phase_state.setdefault("player_speech", []).append(
                {
                    "day": self.public_memory.day,
                    "speaker": pid,
                    "text": text,
                    "target": speech_meta.get("target"),
                    "reason": speech_meta.get("reason"),
                }
            )
            self.log_event(
                "speech",
                {
                    "day": self.public_memory.day,
                    "speaker": pid,
                    "text": text,
                    "belief": belief_snapshot,
                    "role_view": speech_meta.get("role_view"),
                    "target": speech_meta.get("target"),
                    "reason": speech_meta.get("reason"),
                    "vote_target": speech_meta.get("vote_target"),
                },
            )
            if self.verbose_printer:
                self.verbose_printer.print_block(
                    "speech_content",
                    {
                        "role_view": speech_meta.get("role_view", self.players[pid].role),
                        "target": speech_meta.get("target"),
                        "reason": speech_meta.get("reason", "insufficient information"),
                        "vote_target": speech_meta.get("vote_target"),
                        "speech": text,
                    },
                )
                self.verbose_printer.print_block("speech_end", {"round_id": self.public_memory.day, "speaker": pid})

        if self.verbose_printer:
            self.verbose_printer.print_block(
                "voting_start",
                {"round_id": self.public_memory.day, "alive_players": speak_order},
            )
        votes: Dict[int, int] = {}
        for pid in speak_order:
            agent = self.agents[pid]
            obs = agent.observe(self.public_memory_for_agents(), agent.private_memory)
            if not agent.update_belief_from_prompt("day_vote", self.public_memory_for_agents()):
                agent.update_belief(obs)
            belief_snapshot = self.serialize_belief(agent.belief)
            full_snapshot = self.serialize_belief(self._update_full_belief(pid, agent.belief))
            self.belief_history.append(
                {
                    "day": self.public_memory.day,
                    "phase": "day",
                    "action": "vote",
                    "player": pid,
                    "belief": belief_snapshot,
                    "belief_full": full_snapshot,
                }
            )
            target = agent.vote(self.public_memory_for_agents())
            if target is None or target == pid or not self.players.get(target) or not self.players[target].alive:
                candidates = [p for p in speak_order if p != pid]
                target = candidates[0] if candidates else None
            if target:
                votes[pid] = target
                self.public_memory.vote_history.append({"day": self.public_memory.day, "voter": pid, "target": target})
                self.vote_history.append({"day": self.public_memory.day, "voter": pid, "target": target})
                if self.verbose_printer:
                    self.verbose_printer.print_block(
                        "individual_vote",
                        {
                            "round_id": self.public_memory.day,
                            "speaker": pid,
                            "vote_target": target,
                            "reason": agent.last_vote_reason or "insufficient information",
                        },
                    )
                self.log_event(
                    "vote",
                    {
                        "day": self.public_memory.day,
                        "voter": pid,
                        "target": target,
                        "belief": belief_snapshot,
                    },
                )

        eliminated = self.resolve_votes(votes)
        if eliminated:
            self.players[eliminated].alive = False
            self.update_public_alive_dead()
            message = f"Day elimination: P{eliminated}"
        else:
            message = "Day elimination: none"
        self.public_memory.announcements.append({"day": self.public_memory.day, "message": message})
        self.log_event("resolve_day", {"day": self.public_memory.day, "message": message})
        if self.verbose_printer:
            if eliminated:
                self.verbose_printer.print_block(
                    "vote_summary",
                    {
                        "round_id": self.public_memory.day,
                        "vote_map": votes,
                        "eliminated": eliminated,
                        "alive_players": self.public_memory.alive_players,
                    },
                )
            else:
                self.verbose_printer.print_block(
                    "no_elimination",
                    {
                        "round_id": self.public_memory.day,
                        "vote_map": votes,
                        "alive_players": self.public_memory.alive_players,
                    },
                )
        self.phase_state["player_speech"] = []
        self.phase_state["player_speech_role"] = {}
        self.trim_public_memory()
        return eliminated

    def resolve_votes(self, votes: Dict[int, int]) -> Optional[int]:
        if not votes:
            return None
        counts: Dict[int, int] = {}
        for target in votes.values():
            counts[target] = counts.get(target, 0) + 1
        max_count = max(counts.values())
        top = [pid for pid, c in counts.items() if c == max_count]
        if len(top) == 1:
            return top[0]

        # re-vote among tied players
        revote_targets = set(top)
        revotes: Dict[int, int] = {}
        alive = [pid for pid, p in self.players.items() if p.alive]
        for pid in alive:
            choices = [t for t in revote_targets if t != pid]
            if not choices:
                continue
            revotes[pid] = choices[0]
        if not revotes:
            return None
        counts = {}
        for target in revotes.values():
            counts[target] = counts.get(target, 0) + 1
        max_count = max(counts.values())
        top = [pid for pid, c in counts.items() if c == max_count]
        if len(top) == 1:
            return top[0]
        return None

    def run(self) -> RoundResult:
        winner = None
        while not winner and self.public_memory.day <= self.config.max_days:
            self.public_memory.phase = "night"
            night_result = self.night_phase()
            night_deaths = self.resolve_night_deaths(night_result)
            if self.verbose_printer:
                kill_target = night_result.get("kill_target")
                witch_action = night_result.get("witch_action") or {}
                saved_target = kill_target if witch_action.get("use_antidote") else None
                poisoned_target = witch_action.get("poison_target") if witch_action.get("use_poison") else None
                self.verbose_printer.print_block(
                    "night_result",
                    {
                        "round_id": self.public_memory.day,
                        "killed_target": kill_target,
                        "saved_target": saved_target,
                        "poisoned_target": poisoned_target,
                        "night_dead_players": night_deaths,
                        "alive_players": self.public_memory.alive_players,
                    },
                )
            winner = self.check_win_condition()
            if winner:
                if self.verbose_printer:
                    wolves, good = count_teams(self.players)
                    self.verbose_printer.print_block(
                        "round_summary",
                        {
                            "round_id": self.round_id,
                            "night_dead_players": night_deaths,
                            "eliminated": None,
                            "alive_players": self.public_memory.alive_players,
                            "werewolf_count": wolves,
                            "good_count": good,
                        },
                    )
                    self.verbose_printer.print_block("win_check_end", {"round_id": self.round_id, "winner": winner})
                break
            self.public_memory.phase = "day"
            eliminated = self.day_phase(night_deaths)
            winner = self.check_win_condition()
            if self.verbose_printer:
                wolves, good = count_teams(self.players)
                self.verbose_printer.print_block(
                    "round_summary",
                    {
                        "round_id": self.round_id,
                        "night_dead_players": night_deaths,
                        "eliminated": eliminated,
                        "alive_players": self.public_memory.alive_players,
                        "werewolf_count": wolves,
                        "good_count": good,
                    },
                )
            if winner:
                if self.verbose_printer:
                    self.verbose_printer.print_block("win_check_end", {"round_id": self.round_id, "winner": winner})
                break
            if self.verbose_printer:
                self.verbose_printer.print_block("win_check_continue", {"round_id": self.round_id})
            self.public_memory.day += 1

        if not winner:
            wolves, good = count_teams(self.players)
            winner = "wolf_team" if wolves >= good else "good_team"

        self.winner = winner
        self.final_day = self.public_memory.day

        for pid, p in self.players.items():
            self.survival_rounds[pid] = self.public_memory.day if not p.alive else self.public_memory.day + 1

        return RoundResult(
            round_id=self.round_id,
            winner=winner,
            day_count=self.public_memory.day,
            runtime_seed=self.runtime_seed,
            events=self.events,
            role_assignment={pid: p.role for pid, p in self.players.items()},
            survival_rounds=self.survival_rounds,
            seer_checks=self.seer_checks,
            witch_usage=self.witch_usage,
            vote_history=self.vote_history,
            speech_lengths=self.speech_lengths,
            player_models=self.player_models,
            belief_history=self.belief_history,
        )

    def save_transcript(self, log_dir: str) -> None:
        os.makedirs(log_dir, exist_ok=True)
        seed = getattr(self.config, "global_seed", None)
        seed_tag = f"_seed_{seed}" if seed is not None else ""
        path = os.path.join(log_dir, f"round_{self.round_id:03d}{seed_tag}_transcript.json")
        role_assignment = {pid: p.role for pid, p in self.players.items()}
        team_assignment = {pid: p.team for pid, p in self.players.items()}
        alive_status = {pid: p.alive for pid, p in self.players.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "round_id": self.round_id,
                    "runtime_seed": self.runtime_seed,
                    "winner": self.winner,
                    "final_day": self.final_day,
                    "role_assignment": role_assignment,
                    "team_assignment": team_assignment,
                    "alive_status": alive_status,
                    "player_models": self.player_models,
                    "phase_instruction": self.phase_instruction.get("game_pipeline", {}).get("meta", {}),
                    "phase_state": self.phase_state,
                    "events": self.events,
                    "vote_history": self.vote_history,
                    "belief_history": self.belief_history,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
