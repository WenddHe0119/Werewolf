const state = {
  rounds: new Map(),
  runs: new Map(),
  currentRunId: null,
  currentRoundKey: null,
  currentDay: 1,
  playTimer: null,
  beliefMode: "day",
  playerFilter: new Set(),
  metricsCache: new Map(),
};

const els = {
  fileInput: document.getElementById("fileInput"),
  dirInput: document.getElementById("dirInput"),
  runSelect: document.getElementById("runSelect"),
  roundSelect: document.getElementById("roundSelect"),
  dayRange: document.getElementById("dayRange"),
  dayLabel: document.getElementById("dayLabel"),
  playBtn: document.getElementById("playBtn"),
  pauseBtn: document.getElementById("pauseBtn"),
  playerFilters: document.getElementById("playerFilters"),
  beliefPlayer: document.getElementById("beliefPlayer"),
  beliefModeDay: document.getElementById("beliefModeDay"),
  beliefModeLatest: document.getElementById("beliefModeLatest"),
  nightEvents: document.getElementById("nightEvents"),
  speechList: document.getElementById("speechList"),
  voteList: document.getElementById("voteList"),
  beliefPanel: document.getElementById("beliefPanel"),
  roundMeta: document.getElementById("roundMeta"),
  metricsBtn: document.getElementById("metricsBtn"),
  metricsPanel: document.getElementById("metricsPanel"),
  playerRoster: document.getElementById("playerRoster"),
};

const NIGHT_TYPES = new Set([
  "werewolf_discussion",
  "night_wolf",
  "night_seer",
  "night_witch",
  "announce_deaths",
]);

const NIGHT_INFER_TYPES = new Set([
  "werewolf_discussion",
  "night_wolf",
  "night_seer",
  "night_witch",
]);

function inferNightEvents(round) {
  if (round.__nightEvents) return round.__nightEvents;
  const events = round.events || [];
  const out = [];
  let pending = [];
  let lastDay = 1;
  for (const e of events) {
    const hasDay = typeof e.day === "number";
    if (hasDay) lastDay = e.day;
    if (NIGHT_INFER_TYPES.has(e.type) && !hasDay) {
      pending.push(e);
      continue;
    }
    if (e.type === "announce_deaths" && hasDay) {
      pending.forEach((p) => out.push({ ...p, day: e.day }));
      pending = [];
      out.push(e);
      continue;
    }
    out.push(e);
  }
  if (pending.length) {
    const day = lastDay || 1;
    pending.forEach((p) => out.push({ ...p, day }));
  }
  round.__nightEvents = out;
  return out;
}

function parseJsonFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        resolve(data);
      } catch (err) {
        reject(err);
      }
    };
    reader.onerror = reject;
    reader.readAsText(file);
  });
}

async function handleFiles(fileList, runId = "local") {
  const files = Array.from(fileList || []).filter((file) => file.name.endsWith(".json"));
  if (!files.length) return;

  for (const file of files) {
    try {
      const data = await parseJsonFile(file);
      if (!data || typeof data.round_id !== "number") continue;
      data.__run_id = runId;
      const key = `${runId}::${data.round_id}`;
      state.rounds.set(key, data);
      if (!state.runs.has(runId)) {
        state.runs.set(runId, { rounds: [], metrics: [] });
      }
    } catch (err) {
      console.warn("Failed to parse", file.name, err);
    }
  }
  refreshRoundSelect();
}

function refreshRunSelect() {
  const runIds = Array.from(state.runs.keys()).sort((a, b) => a.localeCompare(b));
  els.runSelect.innerHTML = "";
  if (!runIds.length) {
    els.runSelect.innerHTML = "<option>未加载</option>";
    return;
  }
  for (const id of runIds) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = id;
    els.runSelect.appendChild(opt);
  }
  state.currentRunId = runIds[0];
  els.runSelect.value = state.currentRunId;
  refreshRoundSelect();
}

function refreshRoundSelect() {
  const runId = state.currentRunId;
  const roundKeys = Array.from(state.rounds.keys())
    .filter((key) => key.startsWith(`${runId}::`))
    .sort((a, b) => a.localeCompare(b));
  els.roundSelect.innerHTML = "";
  if (!roundKeys.length) {
    els.roundSelect.innerHTML = "<option>未加载</option>";
    return;
  }
  for (const key of roundKeys) {
    const data = state.rounds.get(key);
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = `Round ${data?.round_id ?? "?"}`;
    els.roundSelect.appendChild(opt);
  }
  state.currentRoundKey = roundKeys[0];
  els.roundSelect.value = state.currentRoundKey;
  updateMetricsButton();
  onRoundChange();
}

function maxDayForRound(round) {
  if (!round) return 1;
  const days = [];
  if (typeof round.final_day === "number") days.push(round.final_day);
  for (const e of round.events || []) {
    if (typeof e.day === "number") days.push(e.day);
  }
  for (const v of round.vote_history || []) {
    if (typeof v.day === "number") days.push(v.day);
  }
  for (const b of round.belief_history || []) {
    if (typeof b.day === "number") days.push(b.day);
  }
  return Math.max(1, ...days);
}

function updateDayRange(maxDay) {
  els.dayRange.min = 1;
  els.dayRange.max = String(maxDay);
  if (state.currentDay > maxDay) state.currentDay = maxDay;
  els.dayRange.value = String(state.currentDay);
  els.dayLabel.textContent = `Day ${state.currentDay}`;
}

function onRoundChange() {
  const roundKey = els.roundSelect.value;
  const round = state.rounds.get(roundKey);
  if (!round) return;
  state.currentRoundKey = roundKey;
  state.currentDay = 1;
  updateDayRange(maxDayForRound(round));
  renderRoundMeta(round);
  renderPlayerRoster(round);
  renderPlayerFilters(round);
  renderBeliefPlayers(round);
  renderDay();
}

function onRunChange() {
  state.currentRunId = els.runSelect.value;
  refreshRoundSelect();
}

function renderRoundMeta(round) {
  const winner = round.winner || "unknown";
  const seed = round.runtime_seed ?? "-";
  const finalDay = round.final_day ?? "-";
  const runId = round.__run_id || "local";
  els.roundMeta.textContent = `Run: ${runId} | Winner: ${winner} | Final day: ${finalDay} | Runtime seed: ${seed}`;
}

function renderPlayerRoster(round) {
  const roleAssignment = round.role_assignment || {};
  const teamAssignment = round.team_assignment || {};
  const models = round.player_models || {};
  const items = [];
  Object.keys(roleAssignment)
    .map((id) => Number(id))
    .sort((a, b) => a - b)
    .forEach((pid) => {
      const role = roleAssignment[pid];
      const team = teamAssignment[pid];
      const model = models[pid] ? `${models[pid].provider}:${models[pid].model}` : "unknown";
      items.push(`
        <div class="list-item">
          <div class="title">P${pid}</div>
          <div class="sub">Role: ${role} | Team: ${team} | Model: ${model}</div>
        </div>
      `);
    });
  els.playerRoster.innerHTML = items.join("");
}

function renderPlayerFilters(round) {
  const roleAssignment = round.role_assignment || {};
  const ids = Object.keys(roleAssignment)
    .map((id) => Number(id))
    .sort((a, b) => a - b);
  if (!state.playerFilter.size) {
    ids.forEach((id) => state.playerFilter.add(id));
  }
  els.playerFilters.innerHTML = "";
  ids.forEach((pid) => {
    const wrapper = document.createElement("label");
    wrapper.className = "player-tag";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = pid;
    checkbox.checked = state.playerFilter.has(pid);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.playerFilter.add(pid);
      else state.playerFilter.delete(pid);
      renderDay();
    });
    const text = document.createElement("span");
    text.textContent = `P${pid}`;
    wrapper.appendChild(checkbox);
    wrapper.appendChild(text);
    els.playerFilters.appendChild(wrapper);
  });
}

function renderBeliefPlayers(round) {
  const roleAssignment = round.role_assignment || {};
  const ids = Object.keys(roleAssignment)
    .map((id) => Number(id))
    .sort((a, b) => a - b);
  els.beliefPlayer.innerHTML = "";
  ids.forEach((pid) => {
    const opt = document.createElement("option");
    opt.value = pid;
    opt.textContent = `P${pid}`;
    els.beliefPlayer.appendChild(opt);
  });
  if (ids.length) {
    els.beliefPlayer.value = ids[0];
  }
}

function passesFilter(pid) {
  return state.playerFilter.size === 0 || state.playerFilter.has(pid);
}

function renderNightEvents(round) {
  const events = inferNightEvents(round).filter((e) => e.day === state.currentDay && NIGHT_TYPES.has(e.type));
  if (!events.length) {
    els.nightEvents.innerHTML = "<div class=\"event-block\"><h3>夜晚</h3><div class=\"list\"><div class=\"list-item\">无夜晚事件</div></div></div>";
    return;
  }

  const deathEvents = events.filter((e) => e.type === "announce_deaths");
  const wolfDiscuss = events.filter((e) => e.type === "werewolf_discussion");
  const wolfKill = events.filter((e) => e.type === "night_wolf");
  const seerActions = events.filter((e) => e.type === "night_seer");
  const witchActions = events.filter((e) => e.type === "night_witch");

  const deathBlock = deathEvents.length
    ? deathEvents
        .map((e) => `<div class="list-item"><div class="title">夜晚死亡</div><div class="sub">${e.message || "未知"}</div></div>`)
        .join("")
    : "<div class=\"list-item\"><div class=\"title\">夜晚死亡</div><div class=\"sub\">无死亡记录</div></div>";

  const wolfBlock = wolfDiscuss.length
    ? wolfDiscuss
        .map((e) => {
          const reason = e.reason ? ` | ${e.reason}` : "";
          return `<div class="list-item"><div class="title">狼人讨论 · P${e.speaker}</div><div class="sub">Target: P${e.target || "-"}${reason}</div></div>`;
        })
        .join("")
    : "<div class=\"list-item\"><div class=\"title\">狼人讨论</div><div class=\"sub\">无记录</div></div>";

  const killBlock = wolfKill.length
    ? wolfKill
        .map((e) => `<div class="list-item"><div class="title">狼人击杀</div><div class="sub">Target: P${e.kill_target ?? "-"}</div></div>`)
        .join("")
    : "<div class=\"list-item\"><div class=\"title\">狼人击杀</div><div class=\"sub\">无记录</div></div>";

  const seerBlock = seerActions.length
    ? seerActions
        .map((e) => {
          const alignment = e.alignment || "unknown";
          return `<div class="list-item"><div class="title">预言家 · P${e.seer}</div><div class="sub">Check: P${e.target} -> ${alignment}</div></div>`;
        })
        .join("")
    : "<div class=\"list-item\"><div class=\"title\">预言家</div><div class=\"sub\">无记录</div></div>";

  const witchBlock = witchActions.length
    ? witchActions
        .map((e) => {
          const poison = e.use_poison ? `Poison: P${e.poison_target}` : "Poison: no";
          const antidote = e.use_antidote ? "Antidote: yes" : "Antidote: no";
          return `<div class="list-item"><div class="title">女巫 · P${e.witch}</div><div class="sub">${antidote} | ${poison}</div></div>`;
        })
        .join("")
    : "<div class=\"list-item\"><div class=\"title\">女巫</div><div class=\"sub\">无记录</div></div>";

  els.nightEvents.innerHTML = `
    <div class="event-block">
      <h3>夜晚</h3>
      <div class="list">${deathBlock}</div>
      <div class="list">${wolfBlock}</div>
      <div class="list">${killBlock}</div>
      <div class="list">${seerBlock}</div>
      <div class="list">${witchBlock}</div>
    </div>
  `;
}

function renderSpeeches(round) {
  const speeches = (round.events || []).filter((e) => e.type === "speech" && e.day === state.currentDay);
  const items = speeches
    .filter((e) => passesFilter(e.speaker))
    .map((e) => {
      const role = e.role_view || "unknown";
      const target = e.target ? `P${e.target}` : "none";
      const reason = e.reason || "-";
      const voteTarget = e.vote_target ? `P${e.vote_target}` : "-";
      return `
        <div class="list-item">
          <div class="title speech-title">P${e.speaker} · ${role}<span class="speech-target">target：${target}</span></div>
          <div class="sub">${reason}</div>
          <div class="sub">Vote target: ${voteTarget}</div>
        </div>
      `;
    });
  els.speechList.innerHTML = items.length ? items.join("") : "<div class=\"list-item\">无发言记录</div>";
}

function renderVotes(round) {
  const votes = (round.vote_history || []).filter((v) => v.day === state.currentDay);
  const items = votes
    .filter((v) => passesFilter(v.voter))
    .map((v) => `
      <div class="list-item">
        <div class="title">P${v.voter} -> P${v.target}</div>
      </div>
    `);
  els.voteList.innerHTML = items.length ? items.join("") : "<div class=\"list-item\">无投票记录</div>";
}

function beliefEntryForPlayer(round, playerId) {
  const entries = (round.belief_history || []).filter((e) => e.player === playerId && e.day <= state.currentDay);
  if (!entries.length) return null;
  const sameDay = entries.filter((e) => e.day === state.currentDay);
  if (state.beliefMode === "day") {
    const dayVote = sameDay.filter((e) => e.action === "vote");
    if (dayVote.length) return dayVote[dayVote.length - 1];
    if (sameDay.length) return sameDay[sameDay.length - 1];
  }
  return entries[entries.length - 1];
}

function renderBelief(round) {
  const playerId = Number(els.beliefPlayer.value);
  const entry = beliefEntryForPlayer(round, playerId);
  if (!entry) {
    els.beliefPanel.innerHTML = "<div class=\"belief-card\">没有 belief 记录</div>";
    return;
  }
  const belief = entry.belief_full || entry.belief || {};
  const sorted = Object.entries(belief)
    .map(([pid, val]) => [Number(pid), Number(val)])
    .sort((a, b) => b[1] - a[1]);

  const rows = sorted
    .map(([pid, val]) => {
      const pct = Math.round(val * 1000) / 10;
      return `
        <div class="belief-row">
          <span>P${pid}</span>
          <span>${pct}%</span>
        </div>
        <div class="belief-bar"><span style="width:${Math.min(100, pct)}%"></span></div>
      `;
    })
    .join("");
  els.beliefPanel.innerHTML = `
    <div class="belief-card">
      <h4>P${playerId} 的 belief</h4>
      <div class="sub">Day ${entry.day} · ${entry.action || ""}</div>
      ${rows}
    </div>
  `;
}

function renderDay() {
  const round = state.rounds.get(state.currentRoundKey);
  if (!round) return;
  els.dayLabel.textContent = `Day ${state.currentDay}`;
  renderNightEvents(round);
  renderSpeeches(round);
  renderVotes(round);
  renderBelief(round);
}

function play() {
  const round = state.rounds.get(state.currentRoundKey);
  if (!round) return;
  const maxDay = maxDayForRound(round);
  if (state.playTimer) return;
  state.playTimer = setInterval(() => {
    if (state.currentDay >= maxDay) {
      pause();
      return;
    }
    state.currentDay += 1;
    updateDayRange(maxDay);
    renderDay();
  }, 1800);
}

function pause() {
  if (state.playTimer) {
    clearInterval(state.playTimer);
    state.playTimer = null;
  }
}

els.fileInput.addEventListener("change", (e) => handleFiles(e.target.files, "local"));
els.dirInput.addEventListener("change", (e) => handleFiles(e.target.files, "local"));
els.runSelect.addEventListener("change", onRunChange);
els.roundSelect.addEventListener("change", onRoundChange);
els.dayRange.addEventListener("input", (e) => {
  state.currentDay = Number(e.target.value);
  renderDay();
});
els.playBtn.addEventListener("click", play);
els.pauseBtn.addEventListener("click", pause);
els.beliefPlayer.addEventListener("change", renderDay);
els.beliefModeDay.addEventListener("click", () => {
  state.beliefMode = "day";
  els.beliefModeDay.classList.add("active");
  els.beliefModeLatest.classList.remove("active");
  renderDay();
});
els.beliefModeLatest.addEventListener("click", () => {
  state.beliefMode = "latest";
  els.beliefModeLatest.classList.add("active");
  els.beliefModeDay.classList.remove("active");
  renderDay();
});
els.metricsBtn.addEventListener("click", () => {
  const runId = state.currentRunId;
  if (!runId) return;
  showMetrics(runId);
});

async function loadManifest() {
  try {
    const resp = await fetch("./logs/index.json");
    if (!resp.ok) return;
    const data = await resp.json();
    if (Array.isArray(data.runs)) {
      for (const run of data.runs) {
        const runId = run.run_id || "local";
        state.runs.set(runId, { rounds: run.rounds || [], metrics: run.metrics || [] });
        for (const entry of run.rounds || []) {
          if (!entry?.path) continue;
          try {
            const r = await fetch(entry.path);
            if (!r.ok) continue;
            const roundData = await r.json();
            if (!roundData || typeof roundData.round_id !== "number") continue;
            roundData.__run_id = runId;
            const key = `${runId}::${roundData.round_id}`;
            state.rounds.set(key, roundData);
          } catch (err) {
            console.warn("Failed to load", entry.path, err);
          }
        }
      }
      refreshRunSelect();
      return;
    }

    const rounds = data.rounds || [];
    for (const entry of rounds) {
      if (!entry?.path) continue;
      try {
        const r = await fetch(entry.path);
        if (!r.ok) continue;
        const roundData = await r.json();
        if (!roundData || typeof roundData.round_id !== "number") continue;
        const runId = entry.run_id || "local";
        roundData.__run_id = runId;
        const key = `${runId}::${roundData.round_id}`;
        state.rounds.set(key, roundData);
        if (!state.runs.has(runId)) {
          state.runs.set(runId, { rounds: [], metrics: [] });
        }
        state.runs.get(runId).rounds.push(entry);
      } catch (err) {
        console.warn("Failed to load", entry.path, err);
      }
    }
    refreshRunSelect();
  } catch (err) {
    console.warn("No manifest found", err);
  }
}

loadManifest();
renderDay();

function updateMetricsButton() {
  const run = state.runs.get(state.currentRunId || "");
  const hasMetrics = !!(run && Array.isArray(run.metrics) && run.metrics.length > 0);
  els.metricsBtn.disabled = !hasMetrics;
  if (!hasMetrics) {
    els.metricsPanel.innerHTML = "<div class=\"metric-group\">无指标文件</div>";
  }
}

async function showMetrics(runId) {
  const run = state.runs.get(runId);
  if (!run || !run.metrics || !run.metrics.length) return;
  const metricsPath = run.metrics[0];
  if (!metricsPath) return;
  if (state.metricsCache.has(metricsPath)) {
    renderMetrics(state.metricsCache.get(metricsPath));
    return;
  }
  try {
    const resp = await fetch(metricsPath);
    if (!resp.ok) return;
    const data = await resp.json();
    state.metricsCache.set(metricsPath, data);
    renderMetrics(data);
  } catch (err) {
    console.warn("Failed to load metrics", err);
  }
}

function renderMetrics(metrics) {
  if (!metrics || typeof metrics !== "object") {
    els.metricsPanel.innerHTML = "<div class=\"metric-group\">无法解析指标</div>";
    return;
  }
  const groups = [];
  for (const [key, value] of Object.entries(metrics)) {
    if (value && typeof value === "object") {
      const rows = Object.entries(value)
        .map(([k, v]) => {
          const display = typeof v === "number" ? v.toFixed(4) : JSON.stringify(v);
          return `<div class=\"metric-row\"><span>${k}</span><span>${display}</span></div>`;
        })
        .join("");
      groups.push(`<div class=\"metric-group\"><h4>${key}</h4>${rows || "<div class=\\\"metric-row\\\">(empty)</div>"}</div>`);
    } else {
      const display = typeof value === "number" ? value.toFixed(4) : String(value);
      groups.push(`<div class=\"metric-group\"><h4>${key}</h4><div class=\"metric-row\"><span>value</span><span>${display}</span></div></div>`);
    }
  }
  els.metricsPanel.innerHTML = groups.join("") || "<div class=\"metric-group\">无指标数据</div>";
}
