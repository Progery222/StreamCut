const API_BASE = "/api";

let currentJobId = null;
let pollingInterval = null;
let startTime = null;
let authMode = "login";
let currentBatchId = null;
let batchPollingInterval = null;

// === Auth ===

function getToken() {
  return localStorage.getItem("token");
}

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function checkAuth() {
  const token = getToken();
  if (!token) {
    // Гостевой режим — работаем без авторизации
    hideAuthModal();
    document.getElementById("user-name").textContent = "Гость";
    document.getElementById("user-info").style.display = "flex";
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
    if (!res.ok) throw new Error();
    const data = await res.json();
    hideAuthModal();
    document.getElementById("user-name").textContent = data.username;
    document.getElementById("user-info").style.display = "flex";
    loadConnections();
    loadHistory();
  } catch {
    localStorage.removeItem("token");
    hideAuthModal();
    document.getElementById("user-name").textContent = "Гость";
    document.getElementById("user-info").style.display = "flex";
  }
}

function showAuthModal() {
  document.getElementById("auth-modal").classList.remove("hidden");
  document.getElementById("user-info").style.display = "none";
}

function hideAuthModal() {
  document.getElementById("auth-modal").classList.add("hidden");
}

function toggleAuthMode() {
  authMode = authMode === "login" ? "register" : "login";
  document.getElementById("auth-modal-title").textContent =
    authMode === "login" ? "Вход в StreamCUT" : "Регистрация";
  document.getElementById("auth-submit-btn").textContent =
    authMode === "login" ? "Войти" : "Создать аккаунт";
  document.getElementById("auth-toggle-text").textContent =
    authMode === "login" ? "Нет аккаунта?" : "Уже есть аккаунт?";
  document.getElementById("auth-toggle-link").textContent =
    authMode === "login" ? "Создать" : "Войти";
  document.getElementById("auth-error").textContent = "";
}

async function authSubmit() {
  const username = document.getElementById("auth-username").value.trim();
  const password = document.getElementById("auth-password").value;
  const errEl = document.getElementById("auth-error");
  errEl.textContent = "";
  if (!username || !password) { errEl.textContent = "Заполните все поля"; return; }

  try {
    if (authMode === "register") {
      const res = await fetch(`${API_BASE}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) { const err = await res.json(); throw new Error(err.detail || "Ошибка"); }
    }
    const form = new URLSearchParams();
    form.append("username", username);
    form.append("password", password);
    const res = await fetch(`${API_BASE}/auth/login`, { method: "POST", body: form });
    if (!res.ok) { const err = await res.json(); throw new Error(err.detail || "Ошибка входа"); }
    const data = await res.json();
    localStorage.setItem("token", data.access_token);
    await checkAuth();
  } catch (e) { errEl.textContent = e.message; }
}

function logout() {
  localStorage.removeItem("token");
  showAuthModal();
}

// === Settings Toggle ===

function toggleSettings() {
  const panel = document.getElementById("settings-panel");
  const chevron = document.getElementById("settings-chevron");
  const isOpen = panel.style.display !== "none";
  panel.style.display = isOpen ? "none" : "block";
  chevron.classList.toggle("open", !isOpen);
}

// === Publishing ===

async function loadConnections() {
  try {
    const res = await fetch(`${API_BASE}/auth/connections`, { headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    for (const platform of ["youtube", "tiktok"]) {
      const checkbox = document.getElementById(`publish-${platform}`);
      const link = document.getElementById(`connect-${platform}`);
      if (data[platform]) {
        checkbox.disabled = false;
        link.textContent = "Подключено";
        link.style.color = "var(--success)";
        link.onclick = null;
      }
    }
  } catch {}
}

function connectPlatform(platform) {
  window.open(`${API_BASE}/auth/${platform}/connect`, "_blank");
}

function getPublishTargets() {
  const targets = [];
  for (const platform of ["youtube", "tiktok"]) {
    const cb = document.getElementById(`publish-${platform}`);
    if (cb && cb.checked && !cb.disabled) targets.push(platform);
  }
  return targets.length > 0 ? targets : null;
}

// === SRT Upload ===

let srtTimecodes = null;

function handleSrtUpload(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    srtTimecodes = parseSrt(e.target.result);
    document.getElementById("srt-label").textContent = `${file.name} (${srtTimecodes.length} клипов)`;
    document.getElementById("srt-label").style.color = "var(--success)";
  };
  reader.readAsText(file);
}

function parseSrt(text) {
  const blocks = text.trim().split(/\n\n+/);
  const timecodes = [];
  for (const block of blocks) {
    const lines = block.split("\n");
    if (lines.length < 2) continue;
    const timeMatch = lines[1]?.match(/(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})/);
    if (!timeMatch) continue;
    const start = +timeMatch[1]*3600 + +timeMatch[2]*60 + +timeMatch[3] + +timeMatch[4]/1000;
    const end = +timeMatch[5]*3600 + +timeMatch[6]*60 + +timeMatch[7] + +timeMatch[8]/1000;
    const title = lines.slice(2).join(" ").trim() || `Клип ${timecodes.length + 1}`;
    timecodes.push({ start, end, title });
  }
  return timecodes;
}

// === Video Preview ===

let previewTimeout = null;

function setupPreview() {
  const input = document.getElementById("url-input");
  input.addEventListener("input", () => {
    clearTimeout(previewTimeout);
    const urls = getUrls();
    if (urls.length === 1) {
      previewTimeout = setTimeout(() => loadPreview(urls[0]), 800);
    } else {
      document.getElementById("video-preview").style.display = "none";
    }
  });
}

async function loadPreview(url) {
  if (!url || url.length < 10) {
    document.getElementById("video-preview").style.display = "none";
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/video-info?url=${encodeURIComponent(url)}`);
    if (!res.ok) return;
    const info = await res.json();
    if (info.thumbnail) {
      document.getElementById("preview-thumb").src = info.thumbnail;
      document.getElementById("preview-title").textContent = info.title || "";
      const dur = info.duration ? formatDuration(info.duration) : "";
      const by = info.uploader || "";
      document.getElementById("preview-meta").textContent = [by, dur].filter(Boolean).join(" · ");
      document.getElementById("video-preview").style.display = "flex";
    }
  } catch {}
}

function downloadVideo() {
  const urls = getUrls();
  const url = urls[0];
  if (!url) return;
  const btn = document.getElementById("btn-download-video");
  btn.classList.add("loading");
  btn.querySelector("span").textContent = "Скачивание...";

  window.open(`${API_BASE}/download-video?url=${encodeURIComponent(url)}`, "_blank");

  setTimeout(() => {
    btn.classList.remove("loading");
    btn.querySelector("span").textContent = "Скачать";
  }, 5000);
}

// === History ===

async function loadHistory() {
  try {
    const res = await fetch(`${API_BASE}/jobs`, { headers: authHeaders() });
    if (!res.ok) return;
    const jobs = await res.json();
    const done = jobs.filter(j => j.status === "done" && j.shorts?.length);
    if (done.length === 0) return;
    document.getElementById("history-section").style.display = "block";
    const list = document.getElementById("history-list");
    list.innerHTML = done.map(j => `
      <div class="history-item">
        <div class="history-info">
          <span class="history-title">${j.shorts.length} шортс · ${j.message}</span>
        </div>
        <a href="${API_BASE}/jobs/${j.job_id}/zip" class="link-sm" style="margin-left:auto">ZIP</a>
      </div>
    `).join("");
  } catch {}
}

function toggleHistory() {
  const list = document.getElementById("history-list");
  const chevron = document.getElementById("history-chevron");
  const isOpen = list.style.display !== "none";
  list.style.display = isOpen ? "none" : "block";
  chevron.classList.toggle("open", !isOpen);
}

// === Smart URL Input ===

function getUrls() {
  return document.getElementById("url-input").value
    .split("\n").map(u => u.trim()).filter(u => u.length > 5);
}

function autoResizeInput() {
  const el = document.getElementById("url-input");
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";

  const urls = getUrls();
  const wrap = document.getElementById("url-input-wrap");
  const bar = document.getElementById("url-count-bar");
  const label = document.getElementById("submit-label");

  if (urls.length > 1) {
    wrap.classList.add("multi");
    bar.style.display = "flex";
    document.getElementById("url-count-badge").textContent = `${urls.length} видео`;
    label.textContent = "Обработать все";
  } else {
    wrap.classList.remove("multi");
    bar.style.display = "none";
    label.textContent = "Создать";
  }
}

function handleSubmit() {
  const urls = getUrls();
  if (urls.length === 0) return;
  if (urls.length === 1) {
    startProcessing();
  } else {
    startBatchProcessing(urls);
  }
}

// === Batch Processing ===

async function startBatchProcessing(urls) {
  const btn = document.getElementById("submit-btn");
  btn.disabled = true;
  startTime = Date.now();

  document.getElementById("input-section").style.display = "none";
  document.getElementById("batch-progress-section").style.display = "block";
  document.getElementById("results-section").style.display = "none";
  document.getElementById("error-section").style.display = "none";

  try {
    const response = await fetch(`${API_BASE}/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        urls,
        language: document.getElementById("language").value,
        max_shorts: parseInt(document.getElementById("max-shorts").value),
        caption_style: document.getElementById("caption-style").value,
        reframe_mode: document.getElementById("reframe-mode").value,
        add_music: document.getElementById("add-music").value,
        footage_layout: document.getElementById("footage-layout").value,
        footage_category: document.getElementById("footage-category").value || null,
        caption_position: document.getElementById("caption-position").value,
        publish_targets: getPublishTargets(),
        min_duration: 15,
        max_duration: 60,
      }),
    });
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "Ошибка создания batch");
    }
    const batch = await response.json();
    currentBatchId = batch.batch_id;
    renderBatchJobs(batch.jobs);
    startBatchPolling(currentBatchId);
  } catch (err) {
    showError(err.message);
    btn.disabled = false;
  }
}

function startBatchPolling(batchId) {
  if (batchPollingInterval) clearInterval(batchPollingInterval);
  batchPollingInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/batch/${batchId}`, { headers: authHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      updateBatchProgress(data);
      if (data.completed === data.total) {
        clearInterval(batchPollingInterval);
        showBatchResults(data.jobs);
      }
    } catch (e) { console.error("Batch poll error:", e); }
  }, 2000);
}

function updateBatchProgress(data) {
  const pct = Math.round((data.completed / data.total) * 100);
  document.getElementById("batch-progress-fill").style.width = `${pct}%`;
  document.getElementById("batch-progress-percent").textContent = `${pct}%`;

  const elapsed = Math.round((Date.now() - startTime) / 1000);
  document.getElementById("batch-progress-status").textContent =
    `Готово ${data.completed}/${data.total} · ${formatElapsed(elapsed)}`;

  renderBatchJobs(data.jobs);
}

function renderBatchJobs(jobs) {
  const list = document.getElementById("batch-jobs-list");
  list.innerHTML = "";
  jobs.forEach((job, i) => {
    const statusIcon = job.status === "done" ? "&#10003;"
      : job.status === "error" ? "&#10007;"
      : job.progress > 0 ? `${job.progress}%` : "&#8226;";
    const statusClass = job.status === "done" ? "done"
      : job.status === "error" ? "error" : "active";
    const el = document.createElement("div");
    el.className = `batch-job-item ${statusClass}`;
    el.innerHTML = `
      <span class="batch-job-icon">${statusIcon}</span>
      <span class="batch-job-label">${escapeHtml(job.message)}</span>
      <span class="batch-job-pct">${job.progress}%</span>
    `;
    list.appendChild(el);
  });
}

function showBatchResults(jobs) {
  document.getElementById("batch-progress-section").style.display = "none";
  document.getElementById("results-section").style.display = "block";

  const allShorts = [];
  const errors = [];
  jobs.forEach(job => {
    if (job.shorts) allShorts.push(...job.shorts);
    if (job.status === "error") errors.push(job.error || "Ошибка");
  });

  const elapsed = Math.round((Date.now() - startTime) / 1000);
  const errText = errors.length > 0 ? ` (${errors.length} ошибок)` : "";
  document.getElementById("results-title").textContent =
    `${allShorts.length} шортс${allShorts.length > 1 ? (allShorts.length < 5 ? 'а' : 'ов') : ''} из ${jobs.length} видео за ${formatElapsed(elapsed)}${errText}`;

  const grid = document.getElementById("shorts-grid");
  grid.innerHTML = "";

  allShorts.forEach((short) => {
    const size = formatSize(short.file_size);
    const dur = formatDuration(short.duration);
    const videoUrl = `${API_BASE.replace('/api', '')}${short.url}`;
    const card = document.createElement("div");
    card.className = "short-card";
    card.innerHTML = `
      <div class="short-video-wrap">
        <video class="short-video" src="${videoUrl}" controls preload="metadata"></video>
        <span class="short-score">${short.score}/10</span>
      </div>
      <div class="short-info">
        <div class="short-title">${escapeHtml(short.title)}</div>
        <div class="short-desc">${escapeHtml(short.description)}</div>
        <div class="short-meta">
          <span>${dur}</span>
          <span>${size}</span>
        </div>
        <a href="${videoUrl}" class="short-download" download>Скачать MP4</a>
      </div>`;
    grid.appendChild(card);
  });

  const wrap = document.getElementById("results-section");
  wrap.querySelectorAll(".results-actions").forEach(e => e.remove());
  const actions = document.createElement("div");
  actions.className = "results-actions";
  actions.style.cssText = "display:flex;gap:12px;margin-top:1.5rem;flex-wrap:wrap;";
  const doneJobs = jobs.filter(j => j.status === "done" && j.shorts?.length);
  const zipButtons = doneJobs.map(j =>
    `<button class="btn-primary" onclick="downloadZip('${j.job_id}')">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      ZIP${doneJobs.length > 1 ? ` · ${j.shorts.length} шортс` : ""}
    </button>`
  ).join("");
  actions.innerHTML = `${zipButtons}<button class="btn-secondary" onclick="resetUI()">Новая обработка</button>`;
  wrap.appendChild(actions);
}

// === Processing ===

async function startProcessing() {
  const urls = getUrls();
  const url = urls[0];
  if (!url) return;

  const btn = document.getElementById("submit-btn");
  btn.disabled = true;
  startTime = Date.now();

  document.getElementById("input-section").style.display = "none";
  document.getElementById("progress-section").style.display = "block";
  document.getElementById("results-section").style.display = "none";
  document.getElementById("error-section").style.display = "none";

  renderSteps([
    { id: "download", label: "Скачивание видео", status: "pending" },
    { id: "transcribe", label: "Транскрипция аудио", status: "pending" },
    { id: "analyze", label: "AI-анализ моментов", status: "pending" },
    { id: "cut", label: "Нарезка шортсов", status: "pending" },
    { id: "reframe", label: "AI рефрейминг", status: "pending" },
    { id: "render", label: "Рендеринг субтитров", status: "pending" },
    { id: "publish", label: "Публикация", status: "pending" },
  ]);

  try {
    const response = await fetch(`${API_BASE}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        url,
        language: document.getElementById("language").value,
        max_shorts: parseInt(document.getElementById("max-shorts").value),
        caption_style: document.getElementById("caption-style").value,
        reframe_mode: document.getElementById("reframe-mode").value,
        add_music: document.getElementById("add-music").value,
        footage_layout: document.getElementById("footage-layout").value,
        footage_category: document.getElementById("footage-category").value || null,
        caption_position: document.getElementById("caption-position").value,
        srt_timecodes: srtTimecodes,
        publish_targets: getPublishTargets(),
        min_duration: 15,
        max_duration: 60,
      }),
    });
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "Ошибка создания задачи");
    }
    const job = await response.json();
    currentJobId = job.job_id;
    startPolling(currentJobId);
  } catch (err) {
    showError(err.message);
    btn.disabled = false;
  }
}

function startPolling(jobId) {
  if (pollingInterval) clearInterval(pollingInterval);
  pollingInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/jobs/${jobId}`, { headers: authHeaders() });
      if (!res.ok) return;
      const job = await res.json();
      updateProgress(job);
      if (job.status === "done") { clearInterval(pollingInterval); showResults(job.shorts); }
      else if (job.status === "error") { clearInterval(pollingInterval); showError(job.error || "Неизвестная ошибка"); }
    } catch (e) { console.error("Poll error:", e); }
  }, 1500);
}

function updateProgress(job) {
  const fill = document.getElementById("progress-fill");
  const percent = document.getElementById("progress-percent");
  const status = document.getElementById("progress-status");
  fill.style.width = `${job.progress}%`;
  percent.textContent = `${job.progress}%`;

  const elapsed = Math.round((Date.now() - startTime) / 1000);
  const names = {
    pending: "В очереди", downloading: "Скачиваем видео",
    transcribing: "Транскрипция", analyzing: "AI анализ",
    cutting: "Нарезка", rendering: "Рендеринг", publishing: "Публикация", done: "Готово!",
  };
  let eta = "";
  if (job.progress > 5 && job.progress < 100) {
    const totalEst = Math.round(elapsed / (job.progress / 100));
    const remaining = Math.max(0, totalEst - elapsed);
    eta = ` · ~${formatElapsed(remaining)} осталось`;
  }
  status.textContent = `${names[job.status] || job.status} · ${formatElapsed(elapsed)}${eta}`;
  if (job.steps) renderSteps(job.steps);
}

function renderSteps(steps) {
  const container = document.getElementById("steps-list");
  const existing = container.children.length > 0;
  if (!existing) {
    container.innerHTML = "";
    steps.forEach((step) => {
      const el = document.createElement("div");
      el.className = `step-item ${step.status}`;
      el.dataset.id = step.id;
      el.innerHTML = `
        <div class="step-icon">${getStepIcon(step.status)}</div>
        <div class="step-content">
          <div class="step-label">${step.label}</div>
          ${step.detail ? `<div class="step-detail">${step.detail}</div>` : ""}
        </div>`;
      container.appendChild(el);
    });
  } else {
    steps.forEach((step) => {
      const el = container.querySelector(`[data-id="${step.id}"]`);
      if (!el) return;
      const prev = el.className.replace("step-item ", "").trim();
      if (prev !== step.status) {
        el.className = `step-item ${step.status}`;
        el.querySelector(".step-icon").innerHTML = getStepIcon(step.status);
      }
      const detailEl = el.querySelector(".step-detail");
      if (step.detail) {
        if (detailEl) detailEl.textContent = step.detail;
        else {
          const d = document.createElement("div");
          d.className = "step-detail";
          d.textContent = step.detail;
          el.querySelector(".step-content").appendChild(d);
        }
      } else if (detailEl) detailEl.remove();
    });
  }
}

function getStepIcon(status) {
  switch (status) {
    case "done": return "&#10003;";
    case "active": return '<div class="spinner"></div>';
    case "error": return "&#10007;";
    default: return "&#8226;";
  }
}

// === Results ===

function showResults(shorts) {
  document.getElementById("progress-section").style.display = "none";
  document.getElementById("results-section").style.display = "block";

  const elapsed = Math.round((Date.now() - startTime) / 1000);
  document.getElementById("results-title").textContent =
    `${shorts.length} шортс${shorts.length > 1 ? (shorts.length < 5 ? 'а' : 'ов') : ''} за ${formatElapsed(elapsed)}`;

  const grid = document.getElementById("shorts-grid");
  grid.innerHTML = "";

  shorts.forEach((short) => {
    const size = formatSize(short.file_size);
    const dur = formatDuration(short.duration);
    const videoUrl = `${API_BASE.replace('/api', '')}${short.url}`;

    const card = document.createElement("div");
    card.className = "short-card";
    card.innerHTML = `
      <div class="short-video-wrap">
        <video class="short-video" src="${videoUrl}" controls preload="metadata"></video>
        <span class="short-score">${short.score}/10</span>
      </div>
      <div class="short-info">
        <div class="short-title">${escapeHtml(short.title)}</div>
        <div class="short-desc">${escapeHtml(short.description)}</div>
        <div class="short-meta">
          <span>${dur}</span>
          <span>${size}</span>
        </div>
        <a href="${videoUrl}" class="short-download" download>Скачать MP4</a>
      </div>`;
    grid.appendChild(card);
  });

  const wrap = document.getElementById("results-section");
  // Remove old buttons
  wrap.querySelectorAll(".results-actions").forEach(e => e.remove());

  const actions = document.createElement("div");
  actions.className = "results-actions";
  actions.style.cssText = "display:flex;gap:12px;margin-top:1.5rem;flex-wrap:wrap;";
  actions.innerHTML = `
    <button class="btn-primary" onclick="downloadZip('${currentJobId}')">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      Скачать все ZIP
    </button>
    <button class="btn-secondary" onclick="resetUI()">Новое видео</button>
  `;
  wrap.appendChild(actions);
}

function showError(message) {
  document.getElementById("progress-section").style.display = "none";
  document.getElementById("error-section").style.display = "block";
  document.getElementById("error-message").textContent = message;
  document.getElementById("submit-btn").disabled = false;
}

function resetUI() {
  currentJobId = null;
  currentBatchId = null;
  startTime = null;
  if (pollingInterval) clearInterval(pollingInterval);
  if (batchPollingInterval) clearInterval(batchPollingInterval);
  document.getElementById("input-section").style.display = "block";
  document.getElementById("progress-section").style.display = "none";
  document.getElementById("batch-progress-section").style.display = "none";
  document.getElementById("results-section").style.display = "none";
  document.getElementById("error-section").style.display = "none";
  document.getElementById("steps-list").innerHTML = "";
  document.getElementById("batch-jobs-list").innerHTML = "";
  document.getElementById("submit-btn").disabled = false;
}

// === Download ZIP ===

async function downloadZip(jobId) {
  try {
    const res = await fetch(`${API_BASE}/jobs/${jobId}/zip`, { headers: authHeaders() });
    if (!res.ok) throw new Error("Ошибка скачивания");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `shorts-${jobId.slice(0, 8)}.zip`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) { alert(e.message); }
}

// === Utils ===

function formatElapsed(sec) {
  if (sec < 60) return `${sec}с`;
  return `${Math.floor(sec / 60)}м ${sec % 60}с`;
}

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}м ${s}с` : `${s}с`;
}

function formatSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// === Init ===

async function loadFootageCategories() {
  try {
    const resp = await fetch(`${API_BASE}/footage/categories`);
    if (!resp.ok) return;
    const data = await resp.json();
    const select = document.getElementById("footage-category");
    for (const cat of data.categories || []) {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      select.appendChild(opt);
    }
  } catch (err) {
    console.warn("Failed to load footage categories:", err);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  checkAuth();
  setupPreview();
  loadFootageCategories();
  const urlInput = document.getElementById("url-input");
  urlInput.addEventListener("input", autoResizeInput);
  urlInput.addEventListener("paste", () => setTimeout(autoResizeInput, 50));
  urlInput.addEventListener("keydown", (e) => {
    // Enter без Shift = отправить (если одна ссылка)
    if (e.key === "Enter" && !e.shiftKey) {
      const urls = getUrls();
      if (urls.length <= 1) {
        e.preventDefault();
        handleSubmit();
      }
      // Shift+Enter или несколько строк — обычный перенос
    }
  });
  document.getElementById("auth-password").addEventListener("keypress", (e) => {
    if (e.key === "Enter") authSubmit();
  });
});
