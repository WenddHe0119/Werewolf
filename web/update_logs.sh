#!/usr/bin/env bash
set -euo pipefail

SRC="/data0/hewenwen/Werewolf/logs"
DST="/data0/hewenwen/Werewolf/web/logs"

mkdir -p "$DST"
rsync -a "$SRC/" "$DST/"

python - <<'PY'
import os, json, re
root='/data0/hewenwen/Werewolf/web/logs'
run_map={}
for dirpath, _, filenames in os.walk(root):
    rel_dir=os.path.relpath(dirpath, root)
    run_id = rel_dir if rel_dir != '.' else 'root'
    for name in filenames:
        if not name.endswith('.json'):
            continue
        if 'round_' in name and 'transcript' in name:
            m=re.search(r'round_(\d+)', name)
            round_id=int(m.group(1)) if m else None
            run_map.setdefault(run_id, {'rounds': [], 'metrics': []})
            run_map[run_id]['rounds'].append({
                'round_id': round_id,
                'path': f"logs/{run_id}/{name}" if run_id != 'root' else f"logs/{name}",
            })
        elif name.startswith('metrics'):
            run_map.setdefault(run_id, {'rounds': [], 'metrics': []})
            run_map[run_id]['metrics'].append(
                f"logs/{run_id}/{name}" if run_id != 'root' else f"logs/{name}"
            )

runs=[]
for run_id, payload in run_map.items():
    payload['rounds'].sort(key=lambda x: x['round_id'] or 0)
    payload['metrics'].sort()
    runs.append({'run_id': run_id, **payload})

runs.sort(key=lambda x: x['run_id'])
index_path=os.path.join(root,'index.json')
with open(index_path,'w',encoding='utf-8') as f:
    json.dump({'runs': runs}, f, ensure_ascii=False, indent=2)
print('runs', len(runs))
PY

echo "Updated logs at $DST and rebuilt index.json"
