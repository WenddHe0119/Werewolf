import re
from typing import Dict, List, Optional, Tuple

from .llm import OpenAICompatClient, resolve_model_spec
from .phase_prompts import PhasePromptEngine

ROLE_TEAMS = {
    "werewolf": "wolf_team",
    "seer": "good_team",
    "witch": "good_team",
    "villager": "good_team",
}


def init_belief(num_players: int, self_id: int) -> Dict[int, float]:
    prob = 1.0 / max(1, (num_players - 1))
    return {pid: prob for pid in range(1, num_players + 1) if pid != self_id}


def normalize_belief(belief: Dict[int, float]) -> Dict[int, float]:
    total = sum(belief.values())
    if total <= 0:
        return belief
    return {pid: val / total for pid, val in belief.items()}


def filter_no_hard_role_reveal(text: str) -> str:
    patterns = [
        r"\bI am the seer\b",
        r"\bI am the witch\b",
        r"\bI am a werewolf\b",
        r"\bI am the werewolf\b",
        r"\bI am a villager\b",
        r"\bI claim the seer role\b",
        r"\bI claim seer\b",
        r"\bI am seer\b",
        r"\bI am the seer role\b",
        r"\bmy role is (seer|witch|werewolf|villager)\b",
    ]
    filtered = text
    for pat in patterns:
        filtered = re.sub(pat, "I have information", filtered, flags=re.IGNORECASE)
    return filtered


def summarize_public_memory(public_memory: Dict, max_speeches: int = 6, max_votes: int = 6) -> str:
    lines: List[str] = []
    announcements = public_memory.get("announcements", [])[-3:]
    if announcements:
        lines.append("Announcements:")
        for a in announcements:
            lines.append(f"- Day {a.get('day')}: {a.get('message')}")
    speeches = public_memory.get("speech_history", [])[-max_speeches:]
    if speeches:
        lines.append("Recent speeches:")
        for s in speeches:
            lines.append(f"- Day {s.get('day')} P{s.get('speaker')}: {s.get('text')}")
    votes = public_memory.get("vote_history", [])[-max_votes:]
    if votes:
        lines.append("Recent votes:")
        for v in votes:
            lines.append(f"- Day {v.get('day')} P{v.get('voter')} -> P{v.get('target')}")
    alive = public_memory.get("alive_players", [])
    if alive:
        lines.append(f"Alive players: {', '.join([f'P{pid}' for pid in alive])}")
    return "\n".join(lines)


class Agent:
    def __init__(
        self,
        player,
        config,
        llm_client: Optional[OpenAICompatClient],
        rng,
        speech_engine=None,
        allow_claim_roles: Optional[set] = None,
        prompt_engine: Optional[PhasePromptEngine] = None,
        role_settings: Optional[Dict[str, Dict]] = None,
    ):
        self.player = player
        self.config = config
        self.llm_client = llm_client
        self.rng = rng
        self.speech_engine = speech_engine
        self.allow_claim_roles = allow_claim_roles or set()
        self.prompt_engine = prompt_engine
        self.role_settings = role_settings or {}
        self.public_memory = None
        self.private_memory: Dict[str, object] = {}
        self.belief = init_belief(config.num_players, player.id)
        self.action_history: List[Dict] = []
        self.speech_length = 0
        self.last_speech_meta: Dict[str, object] = {}
        self.last_vote_reason: str = ""
        self.private_memory.setdefault("last_night_action", {"action": None, "player": None})
        self.last_model_targets: List[int] = []
        self.private_memory.setdefault("speech_role", None)

    def observe(self, public_memory: Dict, private_memory: Dict) -> Dict:
        return {
            "public_memory": public_memory,
            "private_memory": private_memory,
        }

    def update_belief(self, observation: Dict) -> None:
        public_memory = observation.get("public_memory", {})
        updated = self.model_belief_update(public_memory)
        if not updated:
            # Fallback to heuristic update
            recent_speeches = public_memory.get("speech_history", [])[-6:]
            for speech in recent_speeches:
                suspects = speech.get("suspects") or []
                for s in suspects:
                    if s in self.belief:
                        self.belief[s] = max(0.01, self.belief[s] - 0.05)
            recent_votes = public_memory.get("vote_history", [])[-6:]
            for vote in recent_votes:
                target = vote.get("target")
                if target in self.belief:
                    self.belief[target] = max(0.01, self.belief[target] - 0.03)
            self.belief = normalize_belief(self.belief)

    def build_role_prompt(self) -> str:
        role_name = self.player.role
        if self.player.role in self.allow_claim_roles:
            return (
                f"In an 8-player Werewolf game, you are a {role_name} as P{self.player.id}. "
                "You may explicitly claim your role if it helps. "
                "No future information. Speak once per day. Provide concise reasoning and one main suspect."
            )
        return (
            f"In an 8-player Werewolf game, you are a {role_name} as P{self.player.id}. "
            "Follow the rules: do not explicitly reveal your true role. "
            "No future information. Speak once per day. Provide concise reasoning and one main suspect."
        )

    def apply_speech_filter(self, text: str) -> str:
        if self.player.role in self.allow_claim_roles:
            return text
        return filter_no_hard_role_reveal(text)

    def build_reason(self, public_memory: Dict, suspect: Optional[int]) -> str:
        if not suspect:
            return "insufficient information"
        if self.player.role == "seer":
            checked = self.private_memory.get("checked_results", [])
            if checked and checked[-1].get("target") == suspect:
                return "night verification suggests alignment"
        if self.player.role == "witch":
            observed = self.private_memory.get("observed_kill_targets", [])
            if observed and observed[-1] == suspect:
                return "night events indicate high risk"
        vote_history = public_memory.get("vote_history", [])
        if vote_history:
            return "voting patterns appear inconsistent"
        return "speech consistency concerns"

    def normalize_reason(self, reason: Optional[str]) -> str:
        if not reason:
            return "insufficient information"
        cleaned = " ".join(str(reason).split())
        if not cleaned:
            return "insufficient information"
        if cleaned.endswith("."):
            cleaned = cleaned[:-1].strip()
        max_len = 120
        if len(cleaned) > max_len:
            cleaned = cleaned[: max_len - 3].rstrip() + "..."
        return cleaned

    def format_speech_output(self, role_view: str, reason: str, target: Optional[int]) -> str:
        target_label = f"P{target}" if target else "none"
        reason_norm = self.normalize_reason(reason)
        return f"{role_view} | reason: {reason_norm} | target: {target_label}"

    def role_config(self) -> Dict:
        return self.role_settings.get(self.player.role, {}) if self.role_settings else {}

    def set_teammates(self, teammates: List[int]) -> None:
        self.private_memory["teammates"] = teammates
        if self.player.role != "werewolf":
            return
        total_others = self.config.num_players - 1
        denom = total_others - len(teammates)
        if denom <= 0:
            return
        new_belief: Dict[int, float] = {}
        for pid in range(1, self.config.num_players + 1):
            if pid == self.player.id:
                continue
            if pid in teammates:
                new_belief[pid] = 0.0
            else:
                new_belief[pid] = 1.0 / denom
        self.belief = new_belief

    def choose_speech_role(self) -> str:
        if self.player.role in self.allow_claim_roles:
            self.private_memory["speech_role"] = self.player.role
            return self.player.role
        cfg = self.role_config()
        speech_type = cfg.get("speech_type", "base")
        fixed_role = cfg.get("fixed_role")
        enable_roles = cfg.get("enable_role")
        if not enable_roles:
            if self.player.role == "werewolf":
                enable_roles = ["villager", "seer", "witch"]
            elif self.player.role == "seer":
                enable_roles = ["villager", "witch"]
            elif self.player.role == "witch":
                enable_roles = ["villager", "seer"]
            else:
                enable_roles = ["villager"]
        if speech_type == "fixed" and fixed_role:
            role_choice = fixed_role
        else:
            role_choice = self.rng.choice(enable_roles) if enable_roles else self.player.role
        self.private_memory["speech_role"] = role_choice
        return role_choice

    def build_prompt_context(self, public_memory: Dict, target_role: Optional[str] = None) -> Dict:
        context = {
            "partner": self.private_memory.get("teammates", []),
            "belief": {pid: self.belief.get(pid, 0.0) for pid in range(1, self.config.num_players + 1) if pid != self.player.id},
            "player_claim_role": self.player.role,
            "alive_player": public_memory.get("alive_players", []),
            "known_player_role": public_memory.get("known_player_role", {}),
            "actioned_player": public_memory.get("actioned_player", {}),
            "all_player_speech": public_memory.get("all_player_speech", []),
            "known_partner_speech": public_memory.get("known_partner_speech", []),
        }
        if not context["known_partner_speech"] and self.player.role == "werewolf":
            teammates = set(self.private_memory.get("teammates", []))
            partner_speeches = []
            for speech in context["all_player_speech"]:
                speaker = speech.get("speaker")
                if speaker in teammates:
                    partner_speeches.append(speech)
            context["known_partner_speech"] = partner_speeches

        role_cfg = self.role_config()
        speech_type = role_cfg.get("speech_type", "base")
        enable_roles = role_cfg.get("enable_role")
        fixed_role = role_cfg.get("fixed_role")
        if not enable_roles:
            if self.player.role == "werewolf":
                enable_roles = ["villager", "seer", "witch"]
            elif self.player.role == "seer":
                enable_roles = ["villager", "seer"]
            elif self.player.role == "witch":
                enable_roles = ["villager", "witch"]
            else:
                enable_roles = ["villager"]
        if speech_type == "fixed" and fixed_role:
            enable_roles = [fixed_role]
            if not target_role:
                target_role = fixed_role

        context["player_speech_role"] = enable_roles

        if target_role:
            context["target_role"] = target_role
        return context

    def call_phase_prompt(
        self,
        role: str,
        phase: str,
        variant: Optional[str],
        context: Dict,
        max_tokens: int = 256,
    ) -> Optional[Dict]:
        if not self.prompt_engine or not self.llm_client or not self.config.model_name:
            return None
        prompt = self.prompt_engine.render(role, phase, variant, context) # 替换字段
        if not prompt:
            return None
        messages = [
            {"role": "system", "content": self.build_role_prompt()},
            {"role": "user", "content": prompt},
        ]
        try:
            text = self.llm_client.chat(
                model=self.config.model_name,
                messages=messages,
                temperature=min(self.config.temperature, 0.7),
                top_p=self.config.top_p,
                max_tokens=max_tokens,
            )
        except Exception:
            return None
        return PhasePromptEngine.parse_json(text)

    def update_belief_from_prompt(self, phase: str, public_memory: Dict, variant: Optional[str] = None) -> bool:
        context = self.build_prompt_context(public_memory, target_role=self.private_memory.get("speech_role"))
        parsed = self.call_phase_prompt(self.player.role, phase, variant, context, max_tokens=256)
        if not parsed:
            return False
        belief_raw = parsed.get("belief", {})
        vote_target_raw = parsed.get("vote_target")
        belief: Dict[int, float] = {}
        if isinstance(belief_raw, dict):
            for k, v in belief_raw.items():
                if isinstance(k, str) and k.startswith("P"):
                    try:
                        pid = int(k[1:])
                    except ValueError:
                        continue
                    try:
                        belief[pid] = float(v)
                    except Exception:
                        continue
        candidates = [pid for pid in public_memory.get("alive_players", []) if pid != self.player.id]
        normalized = self.normalize_model_belief(belief, candidates)
        if not normalized:
            return False
        self.belief = normalized
        if isinstance(vote_target_raw, str) and vote_target_raw.startswith("P"):
            try:
                self.last_model_targets = [int(vote_target_raw[1:])]
            except ValueError:
                self.last_model_targets = []
        elif isinstance(vote_target_raw, int):
            self.last_model_targets = [vote_target_raw]
        return True

    def generate_day_speech_from_prompt(self, public_memory: Dict) -> Optional[Tuple[Optional[int], str, str]]:
        role_cfg = self.role_config()
        speech_type = role_cfg.get("speech_type", "base")
        target_role = self.choose_speech_role()
        context = self.build_prompt_context(public_memory, target_role=target_role)
        parsed = self.call_phase_prompt(self.player.role, "day_speech", speech_type, context, max_tokens=200)
        if not parsed:
            return None
        target_raw = parsed.get("target")
        reason = parsed.get("reason", "") or "insufficient information"
        target = None
        if isinstance(target_raw, str) and target_raw.startswith("P"):
            try:
                target = int(target_raw[1:])
            except ValueError:
                target = None
        elif isinstance(target_raw, int):
            target = target_raw
        return target, str(reason), target_role

    def _alignment_label(self, alignment: Optional[str]) -> Optional[str]:
        if not alignment:
            return None
        if alignment == "wolf_team":
            return "werewolf"
        if alignment == "good_team":
            return "good"
        return alignment

    def generate_claim_speech(self, public_memory: Dict) -> Optional[Tuple[str, Optional[int], Optional[str], Optional[int]]]:
        if not self.llm_client or not self.config.model_name:
            return None
        if not self.speech_engine or self.player.role not in {"seer", "witch"}:
            return None

        check_list: List[Dict[str, str]] = []
        last_checked_role = None
        last_checked_player = None
        if self.player.role == "seer":
            checked = self.private_memory.get("checked_results", [])
            if not checked:
                return None
            for entry in checked[-3:]:
                target = entry.get("target")
                alignment = self._alignment_label(entry.get("alignment"))
                if target:
                    check_list.append({"player": f"P{target}", "checked_role": alignment or "unknown"})
            last = checked[-1]
            last_checked_role = self._alignment_label(last.get("alignment"))
            last_checked_player = last.get("target")
        elif self.player.role == "witch":
            last_action = self.private_memory.get("last_night_action", {})
            action_player = last_action.get("player")
            if not action_player:
                return None
            action_label = last_action.get("action") or "observed"
            check_list.append({"player": f"P{action_player}", "checked_role": action_label})
            last_checked_player = action_player

        if not check_list:
            return None

        alive_ids = public_memory.get("alive_players", [])
        candidates = [pid for pid in alive_ids if pid != self.player.id]
        belief_snapshot = {f"P{pid}": round(self.belief.get(pid, 0.0), 4) for pid in candidates}
        templates = self.speech_engine.get_role_templates(self.player.role)

        payload = {
            "check": check_list,
            "belief": belief_snapshot,
            "templates": templates,
        }

        user_prompt = (
            "You will receive check info, current trust belief, and templates. "
            "Higher belief means more likely good; lower means more likely werewolf. "
            "Decide who is likely good or werewolf, update belief accordingly, and provide a short reason. "
            "Return JSON ONLY with keys: 'updated_belief' (probability map) and 'reason' (short). "
            "Constraints: updated_belief must include all candidates as keys, values in [0,1], sum to 1.\n"
            f"Input: {payload}"
        )
        messages = [
            {"role": "system", "content": self.build_role_prompt()},
            {"role": "user", "content": user_prompt},
        ]
        try:
            text = self.llm_client.chat(
                model=self.config.model_name,
                messages=messages,
                temperature=min(self.config.temperature, 0.7),
                top_p=self.config.top_p,
                max_tokens=120,
            )
            parsed = self.parse_claim_update_json(text)
            if not parsed:
                return None
            updated_belief, reason = parsed
            normalized = self.normalize_model_belief(updated_belief, candidates)
            if not normalized:
                return None
            self.belief = normalized

            vote_target = None
            if self.player.role == "seer" and last_checked_role == "werewolf" and last_checked_player in candidates:
                vote_target = last_checked_player
            if self.player.role == "seer" and last_checked_role == "good":
                candidates = [pid for pid in candidates if pid != last_checked_player]
            if not vote_target and candidates:
                vote_target = min(candidates, key=lambda pid: self.belief.get(pid, 0.0))
            return reason, vote_target, last_checked_role, last_checked_player
        except Exception:
            return None

    def generate_reason_with_llm(self, public_memory: Dict, suspect: Optional[int]) -> Optional[str]:
        if not self.llm_client or not self.config.model_name or not suspect:
            return None
        summary = summarize_public_memory(public_memory, max_speeches=4, max_votes=4)
        user_prompt = (
            f"Provide a short reason (5-12 words) why P{suspect} is suspicious.\n"
            f"Public memory summary:\n{summary}\n"
        )
        messages = [
            {"role": "system", "content": self.build_role_prompt()},
            {"role": "user", "content": user_prompt},
        ]
        try:
            text = self.llm_client.chat(
                model=self.config.model_name,
                messages=messages,
                temperature=min(self.config.temperature, 0.7),
                top_p=self.config.top_p,
                max_tokens=64,
            )
            text = self.apply_speech_filter(text)
            return text.strip()
        except Exception:
            return None

    def rule_based_suspect(self, alive_ids: List[int]) -> Optional[int]:
        candidates = [pid for pid in alive_ids if pid != self.player.id]
        if self.player.role == "werewolf":
            teammates = set(self.private_memory.get("teammates", []))
            candidates = [pid for pid in candidates if pid not in teammates]
        if self.player.role == "seer":
            checked = self.private_memory.get("checked_results", [])
            if checked:
                last = checked[-1]
                if last.get("alignment") == "good_team":
                    good_target = last.get("target")
                    if good_target in candidates:
                        candidates = [pid for pid in candidates if pid != good_target]
        if self.last_model_targets:
            filtered = [pid for pid in self.last_model_targets if pid in candidates]
            if filtered:
                return min(filtered, key=lambda pid: self.belief.get(pid, 0.0))
        if not candidates:
            return None
        return min(candidates, key=lambda pid: self.belief.get(pid, 0.0))

    def model_belief_update(self, public_memory: Dict) -> bool:
        if not self.llm_client or not self.config.model_name:
            return False
        alive_ids = public_memory.get("alive_players", [])
        candidates = [pid for pid in alive_ids if pid != self.player.id]
        if not candidates:
            return False

        recent_speeches = public_memory.get("speech_history", [])[-6:]
        recent_votes = public_memory.get("vote_history", [])[-6:]
        belief_snapshot = {f"P{pid}": round(self.belief.get(pid, 0.0), 4) for pid in candidates}
        speech_lines = []
        for s in recent_speeches:
            speech_lines.append(f"Day {s.get('day')} P{s.get('speaker')}: {s.get('text')}")
        vote_lines = []
        for v in recent_votes:
            vote_lines.append(f"Day {v.get('day')} P{v.get('voter')} -> P{v.get('target')}")

        user_prompt = (
            "Update trust beliefs based on recent public info. "
            "Higher belief means more likely good; lower means more likely werewolf. "
            "Return JSON only with keys: 'targte' (multi-select list of least-trusted players) and 'update belief' (probability map).\\n"
            f"Candidates (choose from these only): {[f'P{pid}' for pid in candidates]}\\n"
            f"Recent speeches (last 6): {speech_lines}\\n"
            f"Recent votes (last 6): {vote_lines}\\n"
            f"Current belief: {belief_snapshot}\\n"
            "Constraints: 'update belief' must include all candidates as keys, values in [0,1], sum to 1."
        )
        messages = [
            {"role": "system", "content": self.build_role_prompt()},
            {"role": "user", "content": user_prompt},
        ]
        try:
            text = self.llm_client.chat(
                model=self.config.model_name,
                messages=messages,
                temperature=min(self.config.temperature, 0.7),
                top_p=self.config.top_p,
                max_tokens=256,
            )
        except Exception:
            return False

        parsed = self.parse_belief_json(text)
        if not parsed:
            return False
        targets, new_belief = parsed

        normalized = self.normalize_model_belief(new_belief, candidates)
        if not normalized:
            return False
        self.belief = normalized
        self.last_model_targets = [pid for pid in targets if pid in candidates]
        return True

    def parse_belief_json(self, text: str) -> Optional[Tuple[List[int], Dict[int, float]]]:
        import json

        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        snippet = text[start : end + 1]
        try:
            data = json.loads(snippet)
        except Exception:
            return None
        targets_raw = data.get("targte", data.get("target", []))
        belief_raw = data.get("update belief", data.get("update_belief", {}))

        targets: List[int] = []
        if isinstance(targets_raw, list):
            for t in targets_raw:
                if isinstance(t, str) and t.startswith("P"):
                    try:
                        targets.append(int(t[1:]))
                    except ValueError:
                        continue
        belief: Dict[int, float] = {}
        if isinstance(belief_raw, dict):
            for k, v in belief_raw.items():
                if isinstance(k, str) and k.startswith("P"):
                    try:
                        pid = int(k[1:])
                    except ValueError:
                        continue
                    try:
                        belief[pid] = float(v)
                    except Exception:
                        continue
        return targets, belief

    def normalize_model_belief(self, belief: Dict[int, float], candidates: List[int]) -> Optional[Dict[int, float]]:
        if not belief:
            return None
        cleaned: Dict[int, float] = {}
        for pid in candidates:
            val = belief.get(pid, 0.0)
            if val < 0:
                val = 0.0
            cleaned[pid] = float(val)
        total = sum(cleaned.values())
        if total <= 0:
            return None
        return {pid: val / total for pid, val in cleaned.items()}

    def parse_claim_update_json(self, text: str) -> Optional[Tuple[Dict[int, float], str]]:
        import json

        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        snippet = text[start : end + 1]
        try:
            data = json.loads(snippet)
        except Exception:
            return None
        belief_raw = data.get("updated_belief", {})
        reason = data.get("reason", "")
        belief: Dict[int, float] = {}
        if isinstance(belief_raw, dict):
            for k, v in belief_raw.items():
                if isinstance(k, str) and k.startswith("P"):
                    try:
                        pid = int(k[1:])
                    except ValueError:
                        continue
                    try:
                        belief[pid] = float(v)
                    except Exception:
                        continue
        if not reason:
            reason = "insufficient information"
        return belief, str(reason)

    def speak(self, public_memory: Dict) -> Tuple[str, List[int]]:
        alive_ids = public_memory.get("alive_players", [])
        suspect = None
        reason = None
        target_role = None
        prompt_result = self.generate_day_speech_from_prompt(public_memory)
        if prompt_result:
            suspect, reason, target_role = prompt_result
        if suspect is None:
            suspect = self.rule_based_suspect(alive_ids)
        suspects = [suspect] if suspect else []
        vote_target = suspect
        if not reason:
            reason = self.generate_reason_with_llm(public_memory, suspect) or self.build_reason(public_memory, suspect)
        reason = self.normalize_reason(reason)
        result = None
        action = None
        action_player = None
        if self.player.role == "seer":
            checked = self.private_memory.get("checked_results", [])
            if checked:
                result = self._alignment_label(checked[-1].get("alignment"))
        if self.player.role == "witch":
            last_action = self.private_memory.get("last_night_action", {})
            action = last_action.get("action")
            action_player = last_action.get("player")
            if action_player:
                belief = self.belief.get(action_player, 0.5)
                result = "good" if belief > 0.5 else "werewolf"

        claim_context = self.generate_claim_speech(public_memory)
        if claim_context:
            claim_reason, claim_vote_target, claim_last_role, claim_checked_player = claim_context
            if claim_vote_target:
                vote_target = claim_vote_target
            reason = self.normalize_reason(claim_reason or reason)
            if self.player.role == "seer" and claim_last_role:
                result = claim_last_role
                if claim_checked_player:
                    suspect = claim_checked_player
            if self.player.role == "witch" and action_player:
                belief = self.belief.get(action_player, 0.5)
                result = "good" if belief > 0.5 else "werewolf"
                suspect = action_player
            role_view = target_role or self.player.role
            text = self.format_speech_output(role_view, reason, suspect)
            text = self.apply_speech_filter(text)
            self.speech_length += len(text.split())
            self.last_speech_meta = {
                "role_view": role_view,
                "target": suspect,
                "reason": reason,
                "vote_target": vote_target,
                "speech": text,
            }
            return text, suspects

        if self.speech_engine:
            role_view = target_role or self.player.role
            text = self.format_speech_output(role_view, reason, suspect)
            text = self.apply_speech_filter(text)
            self.speech_length += len(text.split())
            self.last_speech_meta = {
                "role_view": role_view,
                "target": suspect,
                "reason": reason,
                "vote_target": vote_target,
                "speech": text,
            }
            return text, suspects

        role_view = target_role or self.player.role
        text = self.format_speech_output(role_view, reason, suspect)
        text = self.apply_speech_filter(text)
        self.speech_length += len(text.split())
        self.last_speech_meta = {
            "role_view": role_view,
            "target": suspect,
            "reason": reason,
            "vote_target": vote_target,
            "speech": text,
        }
        return text, suspects

    def vote(self, public_memory: Dict) -> Optional[int]:
        alive_ids = public_memory.get("alive_players", [])
        target = self.rule_based_suspect(alive_ids)
        if self.last_model_targets:
            filtered = [pid for pid in self.last_model_targets if pid in alive_ids and pid != self.player.id]
            if filtered:
                target = min(filtered, key=lambda pid: self.belief.get(pid, 0.0))
        self.last_vote_reason = self.build_reason(public_memory, target)
        return target

    def night_action(self, public_memory: Dict, wolf_kill_target: Optional[int]) -> Dict:
        alive_ids = public_memory.get("alive_players", [])
        if self.player.role == "werewolf":
            candidates = [pid for pid in alive_ids if pid != self.player.id]
            teammates = set(self.private_memory.get("teammates", []))
            candidates = [pid for pid in candidates if pid not in teammates]
            if not candidates:
                return {"kill_target": None, "reason": "no valid targets"}
            target = self.rng.choice(candidates)
            reason = self.build_reason(public_memory, target)
            return {"kill_target": target, "reason": reason}
        if self.player.role == "seer":
            checked = {c["target"] for c in self.private_memory.get("checked_results", [])}
            candidates = [pid for pid in alive_ids if pid != self.player.id and pid not in checked]
            if not candidates:
                return {"check_target": None, "reason": "no valid targets"}
            target = self.rng.choice(candidates) # 现在预言家随机验
            reason = self.build_reason(public_memory, target) # ？？？
            return {"check_target": target, "reason": reason}
        if self.player.role == "witch":
            antidote = int(self.private_memory.get("antidote_remaining", 1))
            poison = int(self.private_memory.get("poison_remaining", 1))
            antidote_used = bool(self.private_memory.get("antidote_used", False))
            poison_used = bool(self.private_memory.get("poison_used", False))
            use_antidote = False
            use_poison = False
            poison_target = None
            reason = "no action"
            if not antidote_used and antidote > 0 and wolf_kill_target and wolf_kill_target != self.player.id:
                use_antidote = True
                reason = "fixed order: used antidote first"
            elif antidote_used and not poison_used and poison > 0:
                candidates = [pid for pid in alive_ids if pid != self.player.id]
                if candidates:
                    target = min(candidates, key=lambda pid: self.belief.get(pid, 0.0))
                    use_poison = True
                    poison_target = target
                    reason = "fixed order: used poison second"
            action_label = None
            action_player = None
            if use_poison and poison_target:
                action_label = "poisoned"
                action_player = poison_target
            elif use_antidote and wolf_kill_target:
                action_label = "saved"
                action_player = wolf_kill_target
            elif wolf_kill_target:
                action_label = "observed"
                action_player = wolf_kill_target
            if use_antidote:
                self.private_memory["antidote_used"] = True
            if use_poison:
                self.private_memory["poison_used"] = True
            self.private_memory["last_night_action"] = {
                "action": action_label,
                "player": action_player,
            }
            return {
                "use_antidote": use_antidote,
                "use_poison": use_poison,
                "poison_target": poison_target,
                "reason": reason,
            }
        return {}


def build_agent_config(base_config, model_spec: Optional[str]):
    provider, model = resolve_model_spec(model_spec)
    llm_backend = provider
    model_name = model
    return {
        "llm_backend": llm_backend,
        "model_name": model_name,
        "temperature": base_config.temperature,
        "top_p": base_config.top_p,
        "max_tokens": base_config.max_tokens,
        "deterministic_action": base_config.deterministic_action,
        "num_players": base_config.num_players,
    }
