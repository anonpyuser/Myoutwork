/* Grounded Strength — frontend. All workout state persists server-side
   (SQLite) on every change, so switching tabs never loses a workout. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, method = "GET", body = null) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : null,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  setTimeout(() => (t.hidden = true), 2200);
}

const state = {
  tab: "today",
  settings: null,
  styles: {},
  movements: [],
  week: null,
  today: null,
  progress: null,
  openWorkoutId: null,
};

/* ---------------- theme ---------------- */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("theme", theme);
  const toggle = $("#themeToggle");
  if (toggle) toggle.setAttribute("aria-checked", theme === "dark" ? "true" : "false");
}

/* ---------------- tabs ---------------- */
function switchTab(tab) {
  state.tab = tab;
  $$("#tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$("main .view").forEach((v) => v.classList.toggle("active", v.id === `view-${tab}`));
  if (tab === "today") loadToday();
  if (tab === "week") loadWeek();
  if (tab === "movements") loadMovements();
  if (tab === "progress") loadProgress();
  if (tab === "settings") renderSettings();
}

/* ---------------- shared bits ---------------- */
function setLine(s) {
  // The core mechanic, unmistakable: computed weight AND % of max.
  if (s.pct != null && s.prescribed_weight != null)
    return `<span class="w">${s.prescribed_weight} lb</span> <span class="p">· ${s.pct}%</span> <span class="r">× ${s.prescribed_reps ?? "—"}</span>`;
  if (s.prescribed_weight != null)
    return `<span class="w">${s.prescribed_weight} lb</span> <span class="r">× ${s.prescribed_reps ?? "—"}</span>`;
  return `<span class="w">— lb</span> <span class="r">× ${s.prescribed_reps ?? "—"} reps</span>`;
}

function statusBadge(w) {
  if (w.status === "completed") return `<span class="badge done">Done</span>`;
  if (w.status === "in_progress") return `<span class="badge">In progress</span>`;
  return `<span class="badge dim">Planned</span>`;
}

/* ---------------- Today ---------------- */
async function loadToday() {
  state.today = await api("/api/today");
  const t = state.today;
  $("#todayDate").textContent = new Date(t.date + "T12:00:00")
    .toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

  $("#todayReminders").innerHTML = t.reminders.map((r) => `
    <div class="card ${r.due ? "reminder-due" : ""}">
      <div class="row-between">
        <strong>${r.type === "gym" ? "🏋️" : r.type === "prayer" ? "🙏" : "💧"} ${esc(r.label)}</strong>
        <span class="badge ${r.due ? "" : "dim"}">${r.due ? "Due" : esc(r.time || "")}</span>
      </div>
      <div class="hint">${esc(r.detail)}</div>
    </div>`).join("");

  const w = t.workout;
  $("#todayWorkout").innerHTML = !w ? `
    <div class="card empty">No workout scheduled today.<br>
    Generate a week from the Week tab and your training days land here.</div>` : `
    <div class="card">
      <div class="row-between"><h3>${esc(w.title)}</h3>${statusBadge(w)}</div>
      ${w.exercises.map((ex) => `
        <div style="margin:8px 0">
          <strong>${esc(ex.name)}</strong>${ex.is_circuit ? ' <span class="badge dim">Circuit</span>' : ""}
          <div class="hint">${ex.sets.map(setLine).join(" &nbsp;·&nbsp; ")}</div>
        </div>`).join("")}
      <button class="btn btn-accent btn-block" onclick="openWorkout(${w.id})">
        ${w.status === "completed" ? "Review workout" : "Open workout"}</button>
    </div>`;

  const water = t.reminders.find((r) => r.type === "water");
  $("#todayWater").innerHTML = water ? `
    <div class="card">
      <div class="row-between"><h3>💧 Water</h3><span class="water-big">${water.glasses}/${water.goal}</span></div>
      <div class="row-between">
        <button class="btn" onclick="addWater(-1)">− Glass</button>
        <button class="btn btn-accent" onclick="addWater(1)">+ Glass</button>
      </div>
    </div>` : "";
}

async function addWater(delta) {
  await api("/api/water", "POST", { delta });
  loadToday();
}

/* ---------------- Week ---------------- */
function stylePickerHTML(elId, active) {
  return Object.entries(state.styles).map(([key, s]) =>
    `<button data-style="${key}" class="${key === active ? "active" : ""}">${esc(s.label)}</button>`).join("");
}

async function loadWeek() {
  const [{ week }, settingsResp] = await Promise.all([
    api("/api/week/current"), api("/api/settings")]);
  state.week = week;
  state.settings = settingsResp.settings;
  const active = state.settings.training_style;
  $("#stylePicker").innerHTML = stylePickerHTML("stylePicker", active);
  $("#styleSummary").textContent = state.styles[active]?.summary || "";
  bindStylePicker($("#stylePicker"), async (style) => {
    await api("/api/settings", "PUT", { training_style: style });
    loadWeek();
  });
  renderWeek();
}

function renderWeek() {
  const w = state.week;
  if (!w) {
    $("#weekContent").innerHTML = `<div class="card empty">
      No week generated yet.<br>Add your maxes in the Maxes tab, then tap
      <strong>Generate week</strong> — every main-lift set is prescribed
      from your percentages.</div>`;
    return;
  }
  $("#weekContent").innerHTML = `
    <div class="card">
      <div class="row-between">
        <strong>Week of ${esc(w.week_start)}</strong>
        <span class="badge">${esc(w.style_label)}</span>
      </div>
      <div class="hint">${esc(w.wave_label)} week — ${esc(w.style_summary)}</div>
    </div>
    ${w.workouts.map((wo) => `
      <div class="card">
        <div class="row-between">
          <h3>${esc(wo.title)}</h3>${statusBadge(wo)}
        </div>
        <div class="hint">${esc(wo.date)}</div>
        ${wo.exercises.map((ex) => `
          <div style="margin:8px 0">
            <strong>${esc(ex.name)}</strong>${ex.is_circuit ? ' <span class="badge dim">Circuit</span>' : ""}
            <div class="hint">${ex.sets.map(setLine).join(" &nbsp;·&nbsp; ")}</div>
          </div>`).join("")}
        <button class="btn btn-block" onclick="openWorkout(${wo.id})">Open</button>
      </div>`).join("")}`;
}

function bindStylePicker(container, onPick) {
  $$("button", container).forEach((b) =>
    b.addEventListener("click", () => onPick(b.dataset.style)));
}

let generating = false;
async function generateWeek(styleOverride = null) {
  if (generating) return;
  generating = true;
  const btns = [$("#generateBtn"), $("#regenStyleBtn")].filter(Boolean);
  btns.forEach((b) => { b.disabled = true; b.dataset.label = b.textContent; b.textContent = "Generating…"; });
  try {
    const body = styleOverride ? { style: styleOverride } : {};
    const { week } = await api("/api/weeks/generate", "POST", body);
    state.week = week;
    toast(`${week.style_label} week generated — ${week.wave_label}`);
    switchTab("week"); // land on the Week tab so the new week is in front of you
  } catch (e) {
    toast(`Couldn't generate the week: ${e.message}`);
  } finally {
    generating = false;
    btns.forEach((b) => { b.disabled = false; b.textContent = b.dataset.label; });
  }
}

/* ---------------- Workout detail ---------------- */
async function openWorkout(id) {
  state.openWorkoutId = id;
  const { workout } = await api(`/api/workouts/${id}`);
  renderWorkout(workout);
  $("#view-workout").hidden = false;
  $("#tabbar").style.display = "none";
}

function closeWorkout() {
  state.openWorkoutId = null;
  $("#view-workout").hidden = true;
  $("#tabbar").style.display = "";
  if (state.tab === "today") loadToday();
  if (state.tab === "week") loadWeek();
  if (state.tab === "progress") loadProgress();
}

function renderWorkout(w) {
  const focus = state.movements.find((m) => m.id === w.focus_movement_id);
  $("#workoutDetail").innerHTML = `
    <div class="row-between"><h2>${esc(w.title)}</h2>${statusBadge(w)}</div>
    <div class="hint">${esc(w.date)}${focus ? ` — max ${focus.current_max ?? "—"} lb` : ""}</div>
    ${w.exercises.map((ex) => `
      <div class="card" data-exercise="${ex.id}">
        <div class="row-between">
          <input type="text" class="ex-name" value="${esc(ex.name)}" aria-label="Exercise name"
                 style="margin:0;font-weight:800">
          <button class="btn btn-small btn-danger" onclick="deleteExercise(${ex.id})">Delete</button>
        </div>
        ${ex.is_circuit ? '<div class="hint">Circuit: move exercise to exercise, rest after each round.</div>' : ""}
        ${ex.rest_seconds ? `<div class="hint">Rest ~${ex.rest_seconds >= 120 ? Math.round(ex.rest_seconds / 60) + " min" : ex.rest_seconds + " sec"} between sets.</div>` : ""}
        <div style="margin-top:10px">
        ${ex.sets.map((s) => setRowHTML(s)).join("")}
        </div>
        <button class="btn btn-small" onclick="addSet(${ex.id})">+ Add set</button>
      </div>`).join("")}
    <button class="btn btn-block" id="addExerciseBtn">+ Add exercise</button>
    ${focus ? `
      <div class="card">
        <h3>New max?</h3>
        <div class="hint">Beat ${esc(focus.name)} ${focus.current_max ?? "—"} lb? Log it and the
          next generated week uses the new numbers.</div>
        <div class="row-between">
          <input type="number" id="newMaxInput" inputmode="decimal" step="5"
                 placeholder="New max (lb)" style="margin:0">
          <button class="btn btn-accent" onclick="updateMaxFromWorkout(${focus.id})">Save</button>
        </div>
      </div>` : ""}
    ${w.status !== "completed"
      ? `<button class="btn btn-accent btn-block" onclick="completeWorkout(${w.id})">Mark workout complete ✓</button>`
      : `<button class="btn btn-block" onclick="reopenWorkout(${w.id})">Reopen workout</button>`}
  `;
  $$(".ex-name", $("#workoutDetail")).forEach((input) => {
    input.addEventListener("change", async () => {
      const exId = input.closest("[data-exercise]").dataset.exercise;
      await api(`/api/exercises/${exId}`, "PUT", { name: input.value });
      toast("Exercise renamed");
    });
  });
  $("#addExerciseBtn").addEventListener("click", async () => {
    const name = prompt("Exercise name:");
    if (!name) return;
    await api(`/api/workouts/${w.id}/exercises`, "POST", { name });
    openWorkout(w.id);
  });
}

function setRowHTML(s) {
  const aw = s.actual_weight ?? s.prescribed_weight ?? 0;
  const ar = s.actual_reps ?? s.prescribed_reps ?? 0;
  return `
  <div class="set-row ${s.done ? "done" : ""}" data-set="${s.id}">
    <div class="set-num">${s.set_number}</div>
    <div class="set-prescribed">${setLine(s)}</div>
    <div class="stepper">
      <button onclick="stepSet(${s.id}, 'actual_weight', -5)">−</button>
      <div class="val">${aw}</div>
      <button onclick="stepSet(${s.id}, 'actual_weight', 5)">+</button>
    </div>
    <div class="stepper reps">
      <button onclick="stepSet(${s.id}, 'actual_reps', -1)">−</button>
      <div class="val">${ar}</div>
      <button onclick="stepSet(${s.id}, 'actual_reps', 1)">+</button>
    </div>
    <button class="check ${s.done ? "on" : ""}" onclick="toggleSet(${s.id})">✓</button>
    <button class="del-set" onclick="deleteSet(${s.id})" aria-label="Delete set">✕</button>
  </div>`;
}

async function refreshOpenWorkout() {
  if (state.openWorkoutId) {
    const { workout } = await api(`/api/workouts/${state.openWorkoutId}`);
    renderWorkout(workout);
  }
}

async function stepSet(id, field, delta) {
  const row = $(`[data-set="${id}"]`);
  const idx = field === "actual_weight" ? 0 : 1;
  const valEl = $$(".stepper .val", row)[idx];
  const next = Math.max(0, Number(valEl.textContent) + delta);
  await api(`/api/sets/${id}`, "PUT", { [field]: next });
  refreshOpenWorkout();
}

async function toggleSet(id) {
  const row = $(`[data-set="${id}"]`);
  const turningOn = !row.classList.contains("done");
  const body = { done: turningOn ? 1 : 0 };
  if (turningOn) {
    // Default actuals to the prescription when checking a set off.
    const vals = $$(".stepper .val", row);
    body.actual_weight = Number(vals[0].textContent);
    body.actual_reps = Number(vals[1].textContent);
    // First touch of a workout flips it to in_progress (server keeps it).
    if (state.openWorkoutId)
      await api(`/api/workouts/${state.openWorkoutId}`, "PUT", { status: "in_progress" });
  }
  await api(`/api/sets/${id}`, "PUT", body);
  refreshOpenWorkout();
}

async function addSet(exId) {
  await api(`/api/exercises/${exId}/sets`, "POST", {});
  refreshOpenWorkout();
}
async function deleteSet(id) {
  await api(`/api/sets/${id}`, "DELETE");
  refreshOpenWorkout();
}
async function deleteExercise(id) {
  if (!confirm("Delete this exercise and its sets?")) return;
  await api(`/api/exercises/${id}`, "DELETE");
  refreshOpenWorkout();
}
async function completeWorkout(id) {
  await api(`/api/workouts/${id}/complete`, "POST");
  toast("Workout complete 💪");
  refreshOpenWorkout();
}
async function reopenWorkout(id) {
  await api(`/api/workouts/${id}`, "PUT", { status: "in_progress" });
  refreshOpenWorkout();
}
async function updateMaxFromWorkout(movementId) {
  const v = Number($("#newMaxInput").value);
  if (!v) return toast("Enter the new max first");
  await api(`/api/movements/${movementId}/max`, "POST", { max: v, note: "Updated from workout" });
  state.movements = (await api("/api/movements")).movements;
  toast("Max updated — next generated week uses it");
  refreshOpenWorkout();
}

/* ---------------- Movements ---------------- */
const STARTERS = ["Squat", "Bench Press", "Deadlift", "Overhead Press"];

async function loadMovements() {
  state.movements = (await api("/api/movements")).movements;
  $("#starterChips").innerHTML = STARTERS.map((s) =>
    `<button type="button" class="chip" onclick="fillStarter('${s}')">${s}</button>`).join("");
  const list = $("#movementList");
  if (!state.movements.length) {
    list.innerHTML = `<div class="card empty">No movements yet.<br>
      Add your big lifts below — squat, bench, deadlift, overhead press —
      with your current max for each. Everything else is built from those numbers.</div>`;
    return;
  }
  list.innerHTML = state.movements.map((m) => `
    <div class="card">
      <div class="row-between">
        <input type="text" class="mv-name" data-id="${m.id}" value="${esc(m.name)}"
               style="margin:0;font-weight:800" aria-label="Movement name">
        <button class="btn btn-small btn-danger" onclick="deleteMovement(${m.id})">Delete</button>
      </div>
      <div class="row-between" style="margin-top:6px">
        <span class="hint">Current max</span>
        <span class="water-big">${m.current_max ?? "—"}${m.current_max ? " lb" : ""}</span>
      </div>
      <div class="row-between">
        <input type="number" id="max-${m.id}" inputmode="decimal" step="5"
               placeholder="New max (lb)" style="margin:0">
        <button class="btn btn-accent" onclick="updateMax(${m.id})">Update max</button>
      </div>
      ${m.pct_table.length ? `
      <table class="pct-table">
        <tr><th>% of max</th><th>Weight</th><th>% of max</th><th>Weight</th></tr>
        ${m.pct_table.map((r, i, arr) => i % 2 ? "" : `
          <tr><td>${r.pct}%</td><td class="w">${r.weight} lb</td>
              <td>${arr[i + 1] ? arr[i + 1].pct + "%" : ""}</td>
              <td class="w">${arr[i + 1] ? arr[i + 1].weight + " lb" : ""}</td></tr>`).join("")}
      </table>` : `<p class="hint">Set a max to see your percentage table.</p>`}
      ${m.history.length ? `<div class="hint" style="margin-top:8px">History: ${
        m.history.slice(0, 4).map((h) => `${h.max_value} lb (${esc(h.created_at.slice(0, 10))})`).join(" · ")
      }</div>` : ""}
    </div>`).join("");
  $$(".mv-name").forEach((input) =>
    input.addEventListener("change", async () => {
      await api(`/api/movements/${input.dataset.id}`, "PUT", { name: input.value });
      toast("Movement renamed");
    }));
}

function fillStarter(name) {
  $("#newMovementName").value = name;
  $("#newMovementMax").focus();
}
async function updateMax(id) {
  const v = Number($(`#max-${id}`).value);
  if (!v) return toast("Enter the new max first");
  await api(`/api/movements/${id}/max`, "POST", { max: v });
  toast("Max updated");
  loadMovements();
}
async function deleteMovement(id) {
  if (!confirm("Delete this movement and its max history?")) return;
  await api(`/api/movements/${id}`, "DELETE");
  loadMovements();
}

/* ---------------- Progress ---------------- */
async function loadProgress() {
  state.progress = await api("/api/progress");
  const p = state.progress;
  $("#progressMaxes").innerHTML = p.movements.length ? p.movements.map((m) => `
    <div class="card">
      <div class="row-between"><strong>${esc(m.name)}</strong>
        <span class="water-big" style="font-size:24px">${m.current_max ?? "—"}${m.current_max ? " lb" : ""}</span></div>
      ${m.history.length > 1 ? `<canvas class="spark" data-history='${JSON.stringify(m.history.map((h) => h.max_value))}'></canvas>` : ""}
      <div class="hint">${m.history.map((h) =>
        `${h.max_value} lb — ${esc(h.created_at.slice(0, 10))}${h.note ? " · " + esc(h.note) : ""}`).join("<br>") || "No max logged yet."}</div>
    </div>`).join("") : `<div class="card empty">No movements yet — add your maxes first.</div>`;
  $$("canvas.spark").forEach(drawSpark);
  $("#progressWorkouts").innerHTML = p.completed_workouts.length
    ? p.completed_workouts.map((w) => `
      <div class="card"><div class="row-between">
        <strong>${esc(w.title)}</strong><span class="badge done">Done</span></div>
        <div class="hint">${esc(w.date)}</div></div>`).join("")
    : `<div class="card empty">No completed workouts yet. Finish one and it shows up here.</div>`;
}

function drawSpark(canvas) {
  const vals = JSON.parse(canvas.dataset.history);
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth * dpr, h = canvas.clientHeight * dpr;
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  const min = Math.min(...vals), max = Math.max(...vals), span = (max - min) || 1;
  ctx.strokeStyle = "#ff6a00"; ctx.lineWidth = 3 * dpr; ctx.beginPath();
  vals.forEach((v, i) => {
    const x = (i / (vals.length - 1)) * (w - 12 * dpr) + 6 * dpr;
    const y = h - 8 * dpr - ((v - min) / span) * (h - 16 * dpr);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
}

/* ---------------- Settings ---------------- */
async function renderSettings() {
  if (!state.settings) state.settings = (await api("/api/settings")).settings;
  if (!Object.keys(state.styles).length)
    state.styles = (await api("/api/styles")).styles;
  const s = state.settings;
  applyTheme(s.theme);
  $("#settingsStylePicker").innerHTML = stylePickerHTML("settingsStylePicker", s.training_style);
  $("#settingsStyleSummary").textContent = state.styles[s.training_style]?.summary || "";
  bindStylePicker($("#settingsStylePicker"), async (style) => {
    state.settings = (await api("/api/settings", "PUT", { training_style: style })).settings;
    renderSettings();
  });

  const r = s.reminders;
  const sw = (id, on) => `<button class="switch" id="${id}" role="switch" aria-checked="${on}"><span></span></button>`;
  $("#reminderForm").innerHTML = `
    <div class="row-between" style="margin:10px 0"><span>🏋️ Gym reminder (training days)</span>${sw("remGymOn", r.gym_enabled)}</div>
    <label>Time <input type="time" id="remGymTime" value="${esc(r.gym_time)}"></label>
    <div class="row-between" style="margin:10px 0"><span>🙏 Daily prayer reminder</span>${sw("remPrayerOn", r.prayer_enabled)}</div>
    <label>Time <input type="time" id="remPrayerTime" value="${esc(r.prayer_time)}"></label>
    <div class="row-between" style="margin:10px 0"><span>💧 Water reminders</span>${sw("remWaterOn", r.water_enabled)}</div>
    <label>Mode
      <select id="remWaterMode">
        <option value="interval" ${r.water_mode === "interval" ? "selected" : ""}>Every N hours</option>
        <option value="fixed" ${r.water_mode === "fixed" ? "selected" : ""}>Fixed times</option>
      </select></label>
    <div class="row-between">
      <label style="flex:1">Start <input type="time" id="remWaterStart" value="${esc(r.water_start)}"></label>
      <label style="flex:1">End <input type="time" id="remWaterEnd" value="${esc(r.water_end)}"></label>
      <label style="flex:1">Every (hrs) <input type="number" id="remWaterInterval" min="1" max="8" value="${r.water_interval_hours}"></label>
    </div>
    <label>Fixed times (comma separated, 24h) <input type="text" id="remWaterTimes" value="${esc((r.water_times || []).join(", "))}"></label>
    <label>Daily goal (glasses) <input type="number" id="remWaterGoal" min="1" max="30" value="${r.water_goal}"></label>`;
  ["remGymOn", "remPrayerOn", "remWaterOn"].forEach((id) => {
    $("#" + id).addEventListener("click", (e) => {
      const b = e.currentTarget;
      b.setAttribute("aria-checked", b.getAttribute("aria-checked") !== "true");
    });
  });
}

async function saveReminders() {
  const on = (id) => $("#" + id).getAttribute("aria-checked") === "true";
  const reminders = {
    gym_enabled: on("remGymOn"), gym_time: $("#remGymTime").value || "16:00",
    prayer_enabled: on("remPrayerOn"), prayer_time: $("#remPrayerTime").value || "07:00",
    water_enabled: on("remWaterOn"), water_mode: $("#remWaterMode").value,
    water_start: $("#remWaterStart").value || "08:00",
    water_end: $("#remWaterEnd").value || "21:00",
    water_interval_hours: Number($("#remWaterInterval").value) || 2,
    water_times: $("#remWaterTimes").value.split(",").map((t) => t.trim()).filter(Boolean),
    water_goal: Number($("#remWaterGoal").value) || 8,
  };
  state.settings = (await api("/api/settings", "PUT", { reminders })).settings;
  toast("Reminders saved");
}

/* In-app notifications: honest best-effort. iOS Safari web apps cannot
   reliably push in the background; while the app is open we check every
   minute and fire a Notification for items that just became due. */
const firedToday = new Set(JSON.parse(localStorage.getItem("firedToday") || "[]"));
async function checkNotifications() {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try {
    const t = await api("/api/today");
    for (const r of t.reminders) {
      const key = `${t.date}:${r.type}`;
      if (r.due && !firedToday.has(key)) {
        firedToday.add(key);
        localStorage.setItem("firedToday", JSON.stringify([...firedToday]));
        new Notification(r.label, { body: r.detail, icon: "/static/icons/icon-192.png" });
      }
    }
  } catch { /* offline etc. — Today tab still shows due items */ }
}

/* ---------------- boot ---------------- */
document.addEventListener("DOMContentLoaded", async () => {
  applyTheme(localStorage.getItem("theme") || "dark");
  $$("#tabbar button").forEach((b) =>
    b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("#backToTabs").addEventListener("click", closeWorkout);
  $("#generateBtn").addEventListener("click", () => generateWeek());
  $("#regenStyleBtn").addEventListener("click", () =>
    generateWeek(state.settings.training_style));
  $("#themeToggle").addEventListener("click", async () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    state.settings = (await api("/api/settings", "PUT", { theme: next })).settings;
  });
  $("#saveRemindersBtn").addEventListener("click", saveReminders);
  $("#notifyBtn").addEventListener("click", async () => {
    if (!("Notification" in window)) return toast("Notifications not supported here");
    const p = await Notification.requestPermission();
    toast(p === "granted" ? "Notifications on (while app is open)" : "Notifications not granted");
  });
  $("#addMovementForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#newMovementName").value.trim();
    const max = Number($("#newMovementMax").value) || null;
    await api("/api/movements", "POST", { name, max });
    $("#newMovementName").value = ""; $("#newMovementMax").value = "";
    toast("Movement added");
    loadMovements();
  });

  try {
    state.styles = (await api("/api/styles")).styles;
    state.settings = (await api("/api/settings")).settings;
    state.movements = (await api("/api/movements")).movements;
    applyTheme(state.settings.theme || "dark");
  } catch { /* first paint still works; views load lazily */ }
  switchTab("today");
  setInterval(checkNotifications, 60000);
  checkNotifications();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
});
