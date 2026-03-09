#!/usr/bin/env bash
set -euo pipefail

SRC="/data0/hewenwen/Werewolf/logs"
DST="/data0/hewenwen/Werewolf/web/logs"

mkdir -p "$DST"
rsync -a "$SRC/" "$DST/"

python - <<'PY'
import os, json, re
root='/data0/hewenwen/Werewolf/web/logs'
entries=[]
for dirpath, _, filenames in os.walk(root):
    for name in filenames:
        if not name.endswith('.json'):
            continue
        if 'round_' not in name or 'transcript' not in name:
            continue
        rel_dir=os.path.relpath(dirpath, root)
        run_id = rel_dir if rel_dir != '.' else 'root'
        m=re.search(r'round_(\d+)', name)
        round_id=int(m.group(1)) if m else None
        entries.append({
            'run_id': run_id,
            'round_id': round_id,
            'path': f"logs/{run_id}/{name}" if run_id != 'root' else f"logs/{name}",
        })
entries.sort(key=lambda x: (x['run_id'], x['round_id'] or 0))
with open(os.path.join(root,'index.json'),'w',encoding='utf-8') as f:
    json.dump({'rounds': entries}, f, ensure_ascii=False, indent=2)
print('entries', len(entries))
PY

echo "Updated logs at $DST and rebuilt index.json"
