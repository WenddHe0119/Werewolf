import json
import os
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path("/data0/hewenwen/Werewolf/logs/20260309_230127_42")
OUT_DIR = ROOT / "belief_charts"
EXCEL_PATH = ROOT / "belief_timeseries.xlsx"

OUT_DIR.mkdir(parents=True, exist_ok=True)

round_files = sorted(ROOT.glob("round_*_transcript.json"))

all_round_rows = {}

for path in round_files:
    m = re.search(r"round_(\d+)", path.name)
    round_id = int(m.group(1)) if m else None
    if round_id is None:
        continue
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    role_assignment = {int(k): v for k, v in data.get("role_assignment", {}).items()} if isinstance(data.get("role_assignment"), dict) else data.get("role_assignment", {})
    player_ids = sorted(role_assignment.keys())
    wolves = [pid for pid, role in role_assignment.items() if role == "werewolf"]

    belief_history = data.get("belief_history", [])

    round_rows = []

    round_out = OUT_DIR / f"round_{round_id:03d}"
    round_out.mkdir(parents=True, exist_ok=True)

    for pid in player_ids:
        role = role_assignment.get(pid)
        others = [p for p in player_ids if p != pid]
        init = {}
        if role == "werewolf":
            for other in others:
                if other in wolves:
                    init[other] = 0.0
                else:
                    init[other] = 1.0 / max(1, (len(others) - (len(wolves) - (1 if pid in wolves else 0))))
        else:
            val = 1.0 / max(1, len(others))
            for other in others:
                init[other] = val

        series = []
        labels = []

        def record_snapshot(index, day, phase, action, belief_map):
            for target, val in belief_map.items():
                round_rows.append(
                    {
                        "round_id": round_id,
                        "time_index": index,
                        "day": day,
                        "phase": phase,
                        "action": action,
                        "player": pid,
                        "target": target,
                        "belief": val,
                    }
                )

        current = init.copy()
        series.append(current.copy())
        labels.append("init")
        record_snapshot(0, 0, "init", "init", current)

        entries = [e for e in belief_history if e.get("player") == pid]
        for i, entry in enumerate(entries, start=1):
            belief_map = entry.get("belief_full") or entry.get("belief") or {}
            updated = {}
            for k, v in belief_map.items():
                try:
                    tid = int(k)
                except Exception:
                    continue
                updated[tid] = float(v)
            for tid in others:
                if tid in updated:
                    current[tid] = updated[tid]
            series.append(current.copy())
            day = entry.get("day", 0)
            phase = entry.get("phase", "")
            action = entry.get("action", "")
            labels.append(f"D{day}-{phase}-{action}")
            record_snapshot(i, day, phase, action, current)

        fig, ax = plt.subplots(figsize=(8, 5))
        x = list(range(len(series)))
        for target in others:
            y = [snap.get(target, 0.0) for snap in series]
            ax.plot(x, y, label=f"P{target}", linewidth=1.5)
        ax.set_title(f"Round {round_id} · P{pid} ({role}) belief")
        ax.set_xlabel("time index")
        ax.set_ylabel("trust belief")
        ax.set_ylim(0, 1)
        ax.legend(ncol=2, fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(round_out / f"P{pid}.png", dpi=150)
        plt.close(fig)

    all_round_rows[f"round_{round_id:03d}"] = pd.DataFrame(round_rows)

with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl") as writer:
    for sheet, df in all_round_rows.items():
        df.to_excel(writer, sheet_name=sheet[:31], index=False)

print(f"Charts saved to {OUT_DIR}")
print(f"Excel saved to {EXCEL_PATH}")
