from typing import Dict, List, Optional


def _model_tag(entry: Optional[Dict[str, str]]) -> str:
    if not isinstance(entry, dict):
        return "unknown"
    provider = entry.get("provider")
    model = entry.get("model") or entry.get("model_name")
    if provider and model:
        return f"{provider}:{model}"
    if model:
        return str(model)
    return "unknown"


def _final_belief_by_player(
    belief_history: List[Dict[str, object]],
    player_ids: List[int],
) -> Dict[int, Dict[int, float]]:
    latest: Dict[int, Dict[int, float]] = {}
    for entry in belief_history or []:
        pid = entry.get("player")
        belief = entry.get("belief")
        if not isinstance(pid, int) or not isinstance(belief, dict):
            continue
        target_map = latest.setdefault(pid, {})
        for k, v in belief.items():
            try:
                key = int(k)
            except Exception:
                continue
            try:
                target_map[key] = float(v)
            except Exception:
                continue
    complete: Dict[int, Dict[int, float]] = {}
    for pid in player_ids:
        base = latest.get(pid)
        if not base:
            continue
        full: Dict[int, float] = {}
        for other in player_ids:
            if other == pid:
                continue
            full[other] = float(base.get(other, 0.0))
        complete[pid] = full
    return complete


def aggregate_metrics(results, num_players: int) -> Dict:
    seat_wins = {pid: 0 for pid in range(1, num_players + 1)}
    seat_games = {pid: 0 for pid in range(1, num_players + 1)}
    role_wins: Dict[str, int] = {}
    role_games: Dict[str, int] = {}
    survival_sum = {pid: 0 for pid in range(1, num_players + 1)}
    seer_checks = {pid: {"checks": 0, "hits": 0} for pid in range(1, num_players + 1)}
    witch_usage = {pid: {"antidote": 0, "poison": 0} for pid in range(1, num_players + 1)}
    speech_sum = {pid: 0 for pid in range(1, num_players + 1)}
    vote_consistency = {pid: {"votes": 0, "majority": 0} for pid in range(1, num_players + 1)}
    wolf_wins_by_model: Dict[str, int] = {}
    wolf_games_by_model: Dict[str, int] = {}
    good_wins_by_model: Dict[str, int] = {}
    good_games_by_model: Dict[str, int] = {}
    wolf_survival_sum_by_model: Dict[str, int] = {}
    wolf_survival_games_by_model: Dict[str, int] = {}
    wolf_elim_by_model: Dict[str, int] = {}
    wolf_elim_games_by_model: Dict[str, int] = {}
    good_id_hits_by_model: Dict[str, int] = {}
    good_id_total_by_model: Dict[str, int] = {}
    wolf_id_hits_by_model: Dict[str, int] = {}
    wolf_id_total_by_model: Dict[str, int] = {}

    for result in results:
        winner = result.winner
        role_assignment = result.role_assignment
        player_models = getattr(result, "player_models", {}) or {}
        final_day = int(getattr(result, "day_count", 0) or 0)
        player_ids = list(role_assignment.keys())
        final_beliefs = _final_belief_by_player(getattr(result, "belief_history", []) or [], player_ids)
        for pid, role in role_assignment.items():
            seat_games[pid] += 1
            role_games[role] = role_games.get(role, 0) + 1
            team = "wolf_team" if role == "werewolf" else "good_team"
            if winner == team:
                seat_wins[pid] += 1
                role_wins[role] = role_wins.get(role, 0) + 1
            model_id = _model_tag(player_models.get(pid))
            if role == "werewolf":
                wolf_games_by_model[model_id] = wolf_games_by_model.get(model_id, 0) + 1
                if winner == "wolf_team":
                    wolf_wins_by_model[model_id] = wolf_wins_by_model.get(model_id, 0) + 1
                survived_days = int(result.survival_rounds.get(pid, 0))
                wolf_survival_sum_by_model[model_id] = wolf_survival_sum_by_model.get(model_id, 0) + survived_days
                wolf_survival_games_by_model[model_id] = wolf_survival_games_by_model.get(model_id, 0) + 1
                died = survived_days <= final_day
                if died:
                    wolf_elim_by_model[model_id] = wolf_elim_by_model.get(model_id, 0) + 1
                wolf_elim_games_by_model[model_id] = wolf_elim_games_by_model.get(model_id, 0) + 1
            else:
                good_games_by_model[model_id] = good_games_by_model.get(model_id, 0) + 1
                if winner == "good_team":
                    good_wins_by_model[model_id] = good_wins_by_model.get(model_id, 0) + 1

            belief = final_beliefs.get(pid)
            if not belief:
                continue
            if pid in belief:
                belief = {k: v for k, v in belief.items() if k != pid}
            if role == "werewolf":
                sorted_desc = sorted(belief.items(), key=lambda x: x[1], reverse=True)
                predicted_good = [p for p, _ in sorted_desc[:5]]
                if len(predicted_good) == 5:
                    true_good = {p for p, r in role_assignment.items() if r != "werewolf"}
                    hits = sum(1 for p in predicted_good if p in true_good)
                    wolf_id_hits_by_model[model_id] = wolf_id_hits_by_model.get(model_id, 0) + hits
                    wolf_id_total_by_model[model_id] = wolf_id_total_by_model.get(model_id, 0) + 5
            else:
                sorted_asc = sorted(belief.items(), key=lambda x: x[1])
                predicted_wolves = [p for p, _ in sorted_asc[:3]]
                if len(predicted_wolves) == 3:
                    true_wolves = {p for p, r in role_assignment.items() if r == "werewolf"}
                    hits = sum(1 for p in predicted_wolves if p in true_wolves)
                    good_id_hits_by_model[model_id] = good_id_hits_by_model.get(model_id, 0) + hits
                    good_id_total_by_model[model_id] = good_id_total_by_model.get(model_id, 0) + 3
        for pid, day_survived in result.survival_rounds.items():
            survival_sum[pid] += day_survived
        for pid, stats in result.seer_checks.items():
            seer_checks[pid]["checks"] += stats.get("checks", 0)
            seer_checks[pid]["hits"] += stats.get("hits", 0)
        for pid, stats in result.witch_usage.items():
            witch_usage[pid]["antidote"] += stats.get("antidote", 0)
            witch_usage[pid]["poison"] += stats.get("poison", 0)
        for pid, length in result.speech_lengths.items():
            speech_sum[pid] += length

        # vote consistency by day
        votes_by_day: Dict[int, Dict[int, int]] = {}
        for vote in result.vote_history:
            day = vote["day"]
            votes_by_day.setdefault(day, {})
            votes_by_day[day][vote["voter"]] = vote["target"]
        for day, votes in votes_by_day.items():
            counts: Dict[int, int] = {}
            for target in votes.values():
                counts[target] = counts.get(target, 0) + 1
            if not counts:
                continue
            max_count = max(counts.values())
            majority_targets = {t for t, c in counts.items() if c == max_count}
            for voter, target in votes.items():
                vote_consistency[voter]["votes"] += 1
                if target in majority_targets:
                    vote_consistency[voter]["majority"] += 1

    seat_win_rate = {pid: (seat_wins[pid] / seat_games[pid]) if seat_games[pid] else 0 for pid in seat_wins}
    role_win_rate = {role: (role_wins.get(role, 0) / role_games[role]) if role_games[role] else 0 for role in role_games}
    avg_survival = {pid: (survival_sum[pid] / seat_games[pid]) if seat_games[pid] else 0 for pid in survival_sum}
    speech_avg = {pid: (speech_sum[pid] / seat_games[pid]) if seat_games[pid] else 0 for pid in speech_sum}
    vote_consistency_rate = {
        pid: (vote_consistency[pid]["majority"] / vote_consistency[pid]["votes"]) if vote_consistency[pid]["votes"] else 0
        for pid in vote_consistency
    }
    wolf_win_rate_by_model = {
        model: (wolf_wins_by_model.get(model, 0) / wolf_games_by_model[model]) if wolf_games_by_model[model] else 0
        for model in wolf_games_by_model
    }
    good_win_rate_by_model = {
        model: (good_wins_by_model.get(model, 0) / good_games_by_model[model]) if good_games_by_model[model] else 0
        for model in good_games_by_model
    }
    wolf_survival_days_by_model = {
        model: (wolf_survival_sum_by_model.get(model, 0) / wolf_survival_games_by_model[model])
        if wolf_survival_games_by_model[model]
        else 0
        for model in wolf_survival_games_by_model
    }
    wolf_elim_rate_by_model = {
        model: (wolf_elim_by_model.get(model, 0) / wolf_elim_games_by_model[model])
        if wolf_elim_games_by_model[model]
        else 0
        for model in wolf_elim_games_by_model
    }
    good_id_hit_rate_by_model = {
        model: (good_id_hits_by_model.get(model, 0) / good_id_total_by_model[model])
        if good_id_total_by_model.get(model)
        else 0
        for model in good_id_total_by_model
    }
    wolf_id_hit_rate_by_model = {
        model: (wolf_id_hits_by_model.get(model, 0) / wolf_id_total_by_model[model])
        if wolf_id_total_by_model.get(model)
        else 0
        for model in wolf_id_total_by_model
    }

    return {
        "win_rate_by_seat": seat_win_rate,
        "win_rate_by_role": role_win_rate,
        "avg_survival_rounds_by_seat": avg_survival,
        "seer_check_value_by_seat": seer_checks,
        "witch_save_usage_by_seat": witch_usage,
        "vote_consistency_by_seat": vote_consistency_rate,
        "speech_length_by_seat": speech_avg,
        "wolf_win_rate_by_model": wolf_win_rate_by_model,
        "good_win_rate_by_model": good_win_rate_by_model,
        "wolf_survival_days_by_model": wolf_survival_days_by_model,
        "wolf_elim_rate_by_model": wolf_elim_rate_by_model,
        "good_id_hit_rate_by_model": good_id_hit_rate_by_model,
        "wolf_id_hit_rate_by_model": wolf_id_hit_rate_by_model,
    }
