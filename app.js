/* 声线实验室 · 前端
 *
 * 流程：导入音频/视频 → 本机分析（VAD 选段 + 自动识别文字）→ 建立克隆音色
 *       → 输入文字 → 后端的 AI 克隆模型用这个音色朗读 → 试听 / 导出
 *
 * 所有重活（解码、选段、识别、合成、后处理）都在本机 Python 服务里做，
 * 浏览器只负责界面和试听。参考音频与文字不会离开这台电脑。
 */
const $ = id => document.getElementById(id);

const CONTROL_IDS = ['pitch', 'formant', 'speed', 'brightness', 'warmth', 'presence', 'breath', 'room', 'deess'];

const STYLES = {
  natural:   { pitch: 0, formant: 0, speed: 0, brightness: 0, warmth: 0, presence: 0, breath: 0, room: 0, deess: 35, style: 'natural' },
  broadcast: { pitch: 0, formant: 6, speed: 3, brightness: 12, warmth: -6, presence: 30, breath: 0, room: 2, deess: 45, style: 'broadcast' },
  warm:      { pitch: -1, formant: -8, speed: -2, brightness: -6, warmth: 32, presence: 8, breath: 6, room: 4, deess: 35, style: 'warm' },
  deep:      { pitch: -3, formant: -26, speed: -4, brightness: -18, warmth: 40, presence: 6, breath: 0, room: 5, deess: 30, style: 'deep' },
  soft:      { pitch: 1, formant: 4, speed: -2, brightness: -4, warmth: 8, presence: -10, breath: 45, room: 10, deess: 40, style: 'soft' },
  female:    { pitch: 4, formant: 34, speed: 2, brightness: 14, warmth: -14, presence: 16, breath: 14, room: 3, deess: 40, style: 'natural' },
  cute:      { pitch: 7, formant: 52, speed: 6, brightness: 26, warmth: -24, presence: 26, breath: 24, room: 2, deess: 45, style: 'natural' },
  cartoon:   { pitch: 10, formant: 70, speed: 9, brightness: 36, warmth: -34, presence: 34, breath: 12, room: 2, deess: 35, style: 'broadcast' }
};

const state = {
  status: null,
  analysis: null,        // 当前导入音频的分析结果
  selected: null,        // 选中的候选片段 {start, stop}
  buffer: null,          // 解码后的 AudioBuffer（画波形 + 试听原始录音）
  originalUrl: null,
  clones: [],
  activeClone: null,
  playingOriginal: false,
  playingSegment: false,
  audio: null,           // 复用的 <audio>（播放原声 / 片段 / 结果）
  speechUrl: null,
  speechBlob: null,
  busy: false,
  jobTimer: null,
  jobId: null,
  recording: false,
  recorder: null,
  chunks: []
};

/* ------------------------------------------------------------------ 工具 */
function setStatus(text, kind = '') {
  $('statusText').textContent = text;
  $('statusDot').className = 'status-dot' + (kind ? ` ${kind}` : '');
}
const fmt = seconds => {
  seconds = Math.max(0, Number(seconds) || 0);
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
};
function setMessage(id, text, error = false) {
  const node = $(id);
  node.textContent = text;
  node.classList.toggle('error', Boolean(error));
}
function refreshIcons() { if (window.lucide) window.lucide.createIcons(); }
function readStored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 忽略隐私模式 */ }
}
function values() {
  return Object.fromEntries(CONTROL_IDS.map(id => [id, Number($(id).value)]));
}
function selectedStyle() { return $('presetSelect').value || 'custom'; }

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      if (data?.error) message = data.error;
    } catch { /* 非 JSON 响应 */ }
    throw new Error(message);
  }
  return response;
}
const apiJson = (path, options) => api(path, options).then(r => r.json());

/* ------------------------------------------------------------- 引擎状态 */
async function loadStatus(silent = false) {
  try {
    const data = await apiJson('/api/status');
    state.status = data;
    const ai = data.ai || {};
    const engine = data.engine;
    $('engineBadge').textContent = engine === 'ai' ? 'AI 克隆' : engine === 'fallback' ? '本机朗读' : '不可用';
    $('engineBadge').dataset.kind = engine;
    const device = data.device || {};
    const hardware = device.cuda
      ? `GPU ${device.gpu || 'CUDA'}${device.vram_gb ? ` · ${device.vram_gb}GB` : ''}`
      : `CPU${device.gpu ? `（未检测到可用 CUDA）` : ''}`;
    if (engine === 'ai') {
      $('engineStatus').textContent = `${ai.detail}｜${hardware}｜${data.settings?.num_step || 16} 步`;
      $('builderBadge').textContent = 'AI 声音克隆';
      if (!silent) setStatus(`就绪 · ${hardware}`);
    } else if (engine === 'fallback') {
      $('engineStatus').textContent = `未启用 AI 克隆：${ai.detail || '缺少模型'}（当前只能用 Windows 本机朗读 + 音色处理）`;
      $('builderBadge').textContent = '轻量模式';
      if (!silent) setStatus('轻量模式：建议安装 AI 克隆引擎');
    } else {
      $('engineStatus').textContent = data.detail || '没有可用的语音引擎';
      $('builderBadge').textContent = '引擎不可用';
    }
    // 没装 AI 引擎时，直接给一个“一键安装”面板（后台跑 tools/setup_ai.py）
    const install = data.install || {};
    const showInstall = engine !== 'ai';
    $('installPanel').hidden = !showInstall;
    if (showInstall) {
      if (install.running) {
        $('installBtn').disabled = true;
        $('installBtn').querySelector('span').textContent = '正在安装…';
        pollInstall();
      } else if (install.done) {
        $('installDetail').textContent = '安装完成，正在重新检测引擎…';
      } else if (install.error) {
        $('installDetail').textContent = `上次安装失败：${install.error}。也可以手动双击“安装AI克隆引擎.bat”。`;
      } else {
        $('installBtn').disabled = false;
        $('installBtn').querySelector('span').textContent = '一键安装到本机';
      }
    }
    const stt = data.transcription || {};
    $('transcribeBtn').disabled = !stt.available;
    $('transcribeBtn').title = stt.available ? '用本机语音识别自动填写参考文字' : (stt.detail || '未安装语音识别模型，请手动填写');
    updateButtons();
  } catch (error) {
    $('engineStatus').textContent = `无法连接本机服务：${error.message}。请先运行“启动声线实验室.bat”。`;
    $('engineBadge').textContent = '服务未启动';
    setStatus('本机服务未连接', 'error');
  }
}

/* ------------------------------------------------------- 一键安装 AI 引擎 */
function renderInstallLog(lines) {
  const node = $('installLog');
  if (!lines || !lines.length) return;
  node.hidden = false;
  node.textContent = lines.slice(-14).join('\n');
  node.scrollTop = node.scrollHeight;
}

async function pollInstall() {
  if (state.installTimer) return;
  const tick = async () => {
    try {
      const data = await apiJson('/api/install/status');
      state.installTimer = null;
      if (data.log) renderInstallLog(data.log);
      if (data.running) {
        $('installDetail').textContent = '正在安装，请保持网络畅通，不要关闭这个页面…';
        state.installTimer = setTimeout(tick, 2500);
        return;
      }
      $('installBtn').disabled = false;
      $('installBtn').querySelector('span').textContent = '一键安装到本机';
      if (data.done) {
        $('installDetail').textContent = '安装完成，正在重新检测引擎…';
        await loadStatus(true);
        setStatus('AI 克隆引擎已安装，可以建立音色了');
      } else if (data.error) {
        $('installDetail').textContent = `安装失败：${data.error}。可以改用“安装AI克隆引擎.bat”，或加参数 --mirror 重试。`;
        setStatus('安装失败', 'error');
      }
    } catch (error) {
      state.installTimer = null;
      setStatus(`无法查询安装进度：${error.message}`, 'error');
    }
  };
  state.installTimer = setTimeout(tick, 1200);
}

async function startInstall() {
  $('installBtn').disabled = true;
  $('installBtn').querySelector('span').textContent = '正在安装…';
  $('installLog').hidden = false;
  $('installLog').textContent = '开始安装…';
  setStatus('正在安装 AI 克隆引擎…');
  try {
    await apiJson('/api/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mirror: $('installMirror').checked, cpu: $('installCpu').checked })
    });
    await pollInstall();
  } catch (error) {
    $('installDetail').textContent = `无法启动安装：${error.message}。请手动双击“安装AI克隆引擎.bat”。`;
    $('installBtn').disabled = false;
    setStatus('安装未启动', 'error');
  }
}

/* --------------------------------------------------------------- 音色列表 */
async function loadClones() {
  try {
    const data = await apiJson('/api/clones');
    state.clones = Array.isArray(data.clones) ? data.clones : [];
    const activeId = readStored('voiceActiveClone', null);
    if (state.activeClone && !state.clones.some(item => item.id === state.activeClone.id)) state.activeClone = null;
    if (!state.activeClone && activeId) {
      const found = state.clones.find(item => item.id === activeId);
      if (found) selectClone(found, { silent: true });
    }
    renderClones();
  } catch (error) {
    $('cloneList').innerHTML = `<p class="saved-empty error-text">${error.message}</p>`;
  }
}

function renderClones() {
  const list = $('cloneList');
  list.replaceChildren();
  if (!state.clones.length) {
    const empty = document.createElement('p');
    empty.className = 'saved-empty';
    empty.textContent = '还没有克隆音色，导入一段声音即可建立';
    list.append(empty);
    updateButtons();
    return;
  }
  state.clones.forEach(clone => {
    const row = document.createElement('div');
    row.className = `saved-voice${clone.id === state.activeClone?.id ? ' active' : ''}`;
    const select = document.createElement('button');
    select.type = 'button';
    select.className = 'saved-voice-select';
    select.title = `使用音色：${clone.name}`;
    const icon = document.createElement('i');
    icon.dataset.lucide = 'mic';
    icon.setAttribute('aria-hidden', 'true');
    const labels = document.createElement('span');
    const name = document.createElement('b');
    name.textContent = clone.name;
    const source = document.createElement('small');
    source.textContent = `${clone.seconds || 0}s 参考 · ${(clone.text || '').slice(0, 18) || '无文字'}`;
    labels.append(name, source);
    select.append(icon, labels);
    select.addEventListener('click', () => selectClone(clone));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-btn';
    remove.title = `删除音色：${clone.name}`;
    remove.setAttribute('aria-label', remove.title);
    const trash = document.createElement('i');
    trash.dataset.lucide = 'trash-2';
    trash.setAttribute('aria-hidden', 'true');
    remove.append(trash);
    remove.addEventListener('click', async event => {
      event.stopPropagation();
      if (!confirm(`删除音色“${clone.name}”？参考音频会一起删除。`)) return;
      try {
        await api(`/api/clones/${clone.id}`, { method: 'DELETE' });
        if (state.activeClone?.id === clone.id) {
          state.activeClone = null;
          store('voiceActiveClone', null);
          $('speechEditor').hidden = true;
        }
        await loadClones();
        setStatus('音色已删除');
      } catch (error) { setStatus(error.message, 'error'); }
    });
    row.append(select, remove);
    list.append(row);
  });
  refreshIcons();
  updateButtons();
}

function selectClone(clone, { silent = false } = {}) {
  state.activeClone = clone;
  store('voiceActiveClone', clone.id);
  $('speechEditor').hidden = false;
  $('activeCloneName').textContent = clone.name;
  $('cloneName').value = clone.name;
  $('cloneText').value = clone.text || '';
  setMessage('cloneStatus', `正在使用克隆音色：${clone.name}`);
  setMessage('speechStatus', `已切换音色：${clone.name}，输入文字后即可朗读`);
  renderClones();
  updateButtons();
  if (!silent) {
    setStatus(`当前音色：${clone.name}`);
    $('speechEditor').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('speechText').focus({ preventScroll: true });
  }
}

/* ------------------------------------------------------------ 导入与选段 */
function clearAnalysis() {
  state.analysis = null;
  state.selected = null;
  state.buffer = null;
  if (state.originalUrl) URL.revokeObjectURL(state.originalUrl);
  state.originalUrl = null;
  $('fileName').textContent = '还没有音频';
  $('duration').textContent = $('totalTime').textContent = '00:00';
  $('segmentBar').hidden = true;
  $('segmentChips').replaceChildren();
  $('waveSegments').replaceChildren();
  $('playBestBtn').disabled = true;
  $('referenceStatus').textContent = '导入一段 3–10 秒的清晰人声，或者直接导入整段视频，程序会自动挑出最适合克隆的片段';
  stopVoice();
  drawWave();
  updateButtons();
}

async function handleFile(file) {
  if (!file) return;
  if (file.size > 200 * 1024 * 1024) { setStatus('文件超过 200 MB，请先裁剪', 'error'); return; }
  stopAll();
  clearAnalysis();
  $('fileName').textContent = file.name;
  $('referenceStatus').textContent = '正在本机解码并挑选最适合克隆的片段…';
  setMessage('cloneStatus', '正在分析音频…');
  setStatus('正在分析音频…');
  state.busy = true;
  updateButtons();
  try {
    // 1) 本地解码：用于画波形 + 试听原始录音
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const ctx = new AudioCtx();
      state.buffer = await ctx.decodeAudioData(await file.arrayBuffer());
      await ctx.close();
      state.originalUrl = URL.createObjectURL(file);
      $('duration').textContent = $('totalTime').textContent = fmt(state.buffer.duration);
      drawWave();
    } catch { /* 有些容器浏览器解不开，但服务端的 ffmpeg 可以 */ }

    // 2) 服务端分析：选段 + 自动转写
    const data = await apiJson('/api/clone/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) },
      body: file
    });
    applyAnalysis(data);
    setStatus('分析完成，确认文字后建立音色');
  } catch (error) {
    $('referenceStatus').textContent = `分析失败：${error.message}`;
    setMessage('cloneStatus', error.message, true);
    setStatus('分析失败', 'error');
  } finally {
    state.busy = false;
    updateButtons();
  }
}

function applyAnalysis(data) {
  state.analysis = data;
  state.selected = data.best ? { start: data.best.start, stop: data.best.stop } : null;
  $('duration').textContent = fmt(data.duration);
  $('totalTime').textContent = fmt(data.duration);
  const best = data.best || {};
  $('referenceStatus').textContent = best.start !== undefined
    ? `已自动挑选 ${fmt(best.start)} – ${fmt(best.stop)}（${(best.stop - best.start).toFixed(1)} 秒，匹配度 ${best.score}）`
    : '已导入音频';
  if (!$('cloneName').value.trim()) $('cloneName').value = data.suggestedName || '我的克隆音色';
  $('cloneText').value = data.text || '';
  if (data.text) setMessage('cloneStatus', '已自动识别参考文字，确认无误后建立音色');
  else if (data.textError) setMessage('cloneStatus', `自动识别不可用（${data.textError}），请手动填写片段里说的那句话`, true);
  else setMessage('cloneStatus', '请填写参考片段里说的那句话，越准确克隆越像');
  renderSegments();
  drawWave();
  updateButtons();
}

function renderSegments() {
  const chips = $('segmentChips');
  chips.replaceChildren();
  const segments = state.analysis?.segments || [];
  $('segmentBar').hidden = !segments.length;
  segments.slice(0, 24).forEach((segment, index) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    const chosen = state.selected && Math.abs(segment.start - state.selected.start) < 0.02;
    chip.className = `segment-chip${chosen ? ' active' : ''}`;
    chip.innerHTML = `<b>${fmt(segment.start)} – ${fmt(segment.stop)}</b><small>${(segment.stop - segment.start).toFixed(1)}s · ${segment.score}分</small>`;
    chip.title = `音高 ${segment.detail?.f0 || '?'}Hz · 音量 ${segment.detail?.level ?? '?'} · 点击选用`;
    chip.addEventListener('click', () => {
      state.selected = { start: segment.start, stop: segment.stop };
      renderSegments();
      drawWave();
      $('referenceStatus').textContent = `已选择 ${fmt(segment.start)} – ${fmt(segment.stop)}（推荐片段排在最前）`;
      setMessage('cloneStatus', '片段已更换，参考文字要改成这段里说的内容');
    });
    chips.append(chip);
    if (index === 0 && state.selected) chip.scrollIntoView({ block: 'nearest' });
  });
  renderWaveSegments();
}

function renderWaveSegments() {
  const layer = $('waveSegments');
  layer.replaceChildren();
  const total = state.analysis?.duration || state.buffer?.duration || 0;
  if (!total || !state.analysis?.segments) return;
  state.analysis.segments.slice(0, 40).forEach(segment => {
    const band = document.createElement('div');
    band.className = 'wave-segment';
    band.style.left = `${(segment.start / total) * 100}%`;
    band.style.width = `${Math.max(0.4, ((segment.stop - segment.start) / total) * 100)}%`;
    band.title = `${fmt(segment.start)} – ${fmt(segment.stop)}`;
    if (state.selected && Math.abs(segment.start - state.selected.start) < 0.02) band.classList.add('active');
    band.addEventListener('click', () => {
      state.selected = { start: segment.start, stop: segment.stop };
      renderSegments();
      drawWave();
    });
    layer.append(band);
  });
}

function drawWave() {
  const canvas = $('waveform');
  const ctx = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#f7f9fa';
  ctx.fillRect(0, 0, width, height);
  if (!state.buffer) {
    $('emptyWave').style.display = 'flex';
    return;
  }
  $('emptyWave').style.display = 'none';
  const data = state.buffer.getChannelData(0);
  const step = Math.ceil(data.length / width);
  const amp = height * 0.4;
  ctx.beginPath();
  ctx.strokeStyle = '#72a7a2';
  ctx.lineWidth = 1.4;
  for (let i = 0; i < width; i += 1) {
    let min = 1;
    let max = -1;
    for (let j = 0; j < step; j += 1) {
      const v = data[i * step + j] || 0;
      if (v < min) min = v;
      if (v > max) max = v;
    }
    ctx.moveTo(i, height / 2 + min * amp);
    ctx.lineTo(i, height / 2 + max * amp);
  }
  ctx.stroke();
  // 高亮选中的片段
  const total = state.buffer.duration;
  if (state.selected && total) {
    const x1 = (state.selected.start / total) * width;
    const x2 = (state.selected.stop / total) * width;
    ctx.fillStyle = 'rgba(78,150,145,.16)';
    ctx.fillRect(x1, 0, Math.max(2, x2 - x1), height);
    ctx.strokeStyle = 'rgba(78,150,145,.75)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x1, 0); ctx.lineTo(x1, height);
    ctx.moveTo(x2, 0); ctx.lineTo(x2, height);
    ctx.stroke();
  }
  renderWaveSegments();
}

/* ------------------------------------------------------------------ 试听 */
function ensureAudio() {
  if (!state.audio) {
    state.audio = new Audio();
    state.audio.addEventListener('timeupdate', () => {
      if (!state.buffer) return;
      const ratio = Math.min(1, state.audio.currentTime / (state.buffer.duration || 1));
      $('progressFill').style.width = `${ratio * 100}%`;
      $('playhead').style.left = `${ratio * 100}%`;
      $('currentTime').textContent = fmt(state.audio.currentTime);
    });
    state.audio.addEventListener('ended', () => {
      state.playingOriginal = false;
      state.playingSegment = false;
      $('playBtn').textContent = '▶';
      setStatus('试听结束');
    });
    state.audio.addEventListener('pause', () => { state.playingOriginal = false; $('playBtn').textContent = '▶'; });
  }
  return state.audio;
}

function stopVoice() {
  if (state.audio) {
    state.audio.pause();
    state.audio.removeAttribute('src');
    state.audio.load();
  }
  state.playingOriginal = false;
  state.playingSegment = false;
  $('playBtn').textContent = '▶';
}

function stopAll() {
  stopVoice();
  const result = $('speechAudio');
  if (result && !result.paused) result.pause();
}

function playOriginal() {
  if (!state.originalUrl) { setStatus('这段格式浏览器不能直接试听，可以直接建立音色', 'error'); return; }
  const audio = ensureAudio();
  if (state.playingOriginal) { audio.pause(); return; }
  audio.pause();
  audio.src = state.originalUrl;
  audio.currentTime = 0;
  state.playingOriginal = true;
  state.playingSegment = false;
  audio.play().then(() => {
    $('playBtn').textContent = 'Ⅱ';
    setStatus('正在试听原始录音');
  }).catch(() => setStatus('浏览器无法播放这个格式', 'error'));
}

function playSegment() {
  if (!state.selected) { setStatus('先选择一个片段'); return; }
  if (!state.originalUrl) { setStatus('这段格式浏览器不能直接试听，可以直接建立音色', 'error'); return; }
  const audio = ensureAudio();
  audio.pause();
  audio.src = state.originalUrl;
  audio.currentTime = state.selected.start;
  state.playingSegment = true;
  state.playingOriginal = false;
  audio.play().then(() => {
    $('playBtn').textContent = 'Ⅱ';
    const stopAt = state.selected.stop;
    const watcher = () => {
      if (audio.currentTime >= stopAt) {
        audio.pause();
        audio.removeEventListener('timeupdate', watcher);
      }
    };
    audio.addEventListener('timeupdate', watcher);
    setStatus(`正在试听片段 ${fmt(state.selected.start)} – ${fmt(state.selected.stop)}`);
  }).catch(() => setStatus('浏览器无法播放这个格式', 'error'));
}

/* --------------------------------------------------------------- 建立音色 */
async function transcribeNow() {
  const analysis = state.analysis;
  if (!analysis?.token) { setStatus('先导入音频', 'error'); return; }
  $('transcribeBtn').disabled = true;
  const original = $('transcribeBtn').textContent;
  $('transcribeBtn').textContent = '识别中…';
  try {
    const segment = state.selected || analysis.best || {};
    const data = await apiJson('/api/clone/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: analysis.token,
        filename: analysis.filename,
        start: segment.start ?? 0,
        stop: segment.stop ?? analysis.duration
      })
    });
    $('cloneText').value = data.text || '';
    setMessage('cloneStatus', data.text ? '识别完成，请核对文字与音频是否一致' : '没识别出文字，请手动填写');
    updateButtons();
  } catch (error) {
    setMessage('cloneStatus', `识别失败：${error.message}`, true);
  } finally {
    $('transcribeBtn').textContent = original;
    $('transcribeBtn').disabled = false;
  }
}

async function createClone() {
  const analysis = state.analysis;
  const name = $('cloneName').value.trim();
  const text = $('cloneText').value.trim();
  if (!analysis?.token) { setStatus('先导入音频', 'error'); return; }
  if (!name) { setMessage('cloneStatus', '给这个音色起个名字', true); $('cloneName').focus(); return; }
  if (!text) {
    setMessage('cloneStatus', '请填写参考音频说的那句话（可以先点“自动识别”）', true);
    $('cloneText').focus();
    return;
  }
  const segment = state.selected || analysis.best || {};
  state.busy = true;
  $('createCloneLabel').textContent = '正在建立…';
  setMessage('cloneStatus', '正在保存参考音频并分析音色特征…');
  updateButtons();
  try {
    const clone = await apiJson('/api/clones', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: analysis.token,
        filename: analysis.filename,
        name,
        text,
        start: segment.start,
        stop: segment.stop,
        autoText: false,
        engine: state.status?.engine || ''
      })
    });
    await loadClones();
    selectClone(clone, { silent: true });
    setMessage('cloneStatus', `音色“${clone.name}”已建立，可以在下面输入文字试听`);
    setStatus('克隆音色已建立');
    $('speechEditor').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('speechText').focus({ preventScroll: true });
  } catch (error) {
    setMessage('cloneStatus', error.message, true);
    setStatus('音色建立失败', 'error');
  } finally {
    state.busy = false;
    $('createCloneLabel').textContent = '建立克隆音色';
    updateButtons();
  }
}

/* --------------------------------------------------------------- 克隆朗读 */
function settingsPayload() {
  return { ...values(), style: selectedStyle(), engine: state.status?.engine };
}

/** 修掉进度条动画卡在 99% 的视觉问题：完成后停在使用者看得懂的位置 */
function setJobProgress(progress, detail) {
  $('jobProgress').hidden = false;
  $('jobFill').style.width = `${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%`;
  $('jobDetail').textContent = detail || '合成中…';
}

async function synthesize(text) {
  if (!state.activeClone) { setStatus('先选择一个克隆音色', 'error'); return null; }
  if (!state.status?.available) {
    setMessage('speechStatus', '还没有可用的合成引擎：请双击“安装AI克隆引擎.bat”安装后刷新页面。', true);
    setStatus('引擎未就绪', 'error');
    return null;
  }
  if (!text.trim()) { setStatus('请输入要朗读的文字', 'error'); return null; }
  state.busy = true;
  updateButtons();
  setJobProgress(0.05, '正在准备…');
  setMessage('speechStatus', `正在用“${state.activeClone.name}”朗读…`);
  setStatus('克隆合成中…');
  try {
    const job = await apiJson('/api/tts/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, cloneId: state.activeClone.id, settings: settingsPayload() })
    });
    state.jobId = job.id;
    return await waitJob(job.id);
  } catch (error) {
    setMessage('speechStatus', error.message, true);
    setStatus('合成失败', 'error');
    $('jobProgress').hidden = true;
    return null;
  } finally {
    state.busy = false;
    updateButtons();
  }
}

function waitJob(jobId) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const job = await apiJson(`/api/tts/jobs/${jobId}`);
        setJobProgress(job.progress || 0, job.detail || '合成中…');
        if (job.status === 'done') {
          clearTimeout(state.jobTimer);
          setJobProgress(1, `完成 · ${job.engine?.detail || ''}`);
          const response = await api(`/api/tts/jobs/${jobId}/audio`);
          const blob = await response.blob();
          resolve(blob);
          return;
        }
        if (job.status === 'error') {
          clearTimeout(state.jobTimer);
          $('jobProgress').hidden = true;
          reject(new Error(job.detail || '合成失败'));
          return;
        }
        state.jobTimer = setTimeout(tick, 700);
      } catch (error) {
        clearTimeout(state.jobTimer);
        reject(error);
      }
    };
    tick();
  });
}

function showResult(blob, label) {
  if (state.speechUrl) URL.revokeObjectURL(state.speechUrl);
  state.speechBlob = blob;
  state.speechUrl = URL.createObjectURL(blob);
  const audio = $('speechAudio');
  audio.src = state.speechUrl;
  $('speechResult').hidden = false;
  $('speechResultName').textContent = label;
  setMessage('speechStatus', '朗读已生成，可以试听或导出 WAV');
  setStatus('朗读已生成');
  audio.play().catch(() => {});
}

async function generateSpeech() {
  const text = $('speechText').value.trim();
  const blob = await synthesize(text);
  if (!blob) return;
  showResult(blob, `${state.activeClone.name} · 朗读结果`);
}

async function previewSpeech() {
  const text = $('speechText').value.trim() || '你好，这是用你的声音克隆出来的朗读效果，听起来自然吗？';
  if (!$('speechText').value.trim()) $('speechText').value = text;
  const blob = await synthesize(text.slice(0, 120));
  if (!blob) return;
  showResult(blob, `${state.activeClone.name} · 试听`);
}

/* ------------------------------------------------------------------ 按钮态 */
function updateButtons() {
  const hasAnalysis = Boolean(state.analysis?.token);
  $('createCloneBtn').disabled = state.busy || !hasAnalysis;
  $('transcribeBtn').disabled = state.busy || !hasAnalysis || !(state.status?.transcription?.available);
  $('playBtn').disabled = !state.originalUrl;
  $('playBestBtn').disabled = !state.originalUrl || !state.selected;
  $('generateSpeechBtn').disabled = state.busy || !state.activeClone || !$('speechText').value.trim() || !state.status?.available;
  $('previewBtn').disabled = state.busy || !state.activeClone;
  $('generateSpeechLabel').textContent = state.busy ? '合成中…' : '生成朗读';
  $('speechCount').textContent = `${$('speechText').value.length} / 12000`;
  $('fileInput').disabled = state.busy || state.recording;
  $('recordBtn').disabled = state.busy;
  $('warmupBtn').disabled = state.busy || !(state.status?.ai?.available) || state.status?.engine !== 'ai';
  $('releaseBtn').disabled = state.busy || state.status?.engine !== 'ai';
}

/* ---------------------------------------------------------------- 录音输入 */
async function toggleRecord() {
  if (state.recording) {
    state.recorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
    state.chunks = [];
    state.recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    state.recorder.ondataavailable = event => { if (event.data.size) state.chunks.push(event.data); };
    state.recorder.onstop = () => {
      stream.getTracks().forEach(track => track.stop());
      state.recording = false;
      $('recordLabel').textContent = '麦克风录音';
      $('recordBtn').classList.remove('recording');
      const blob = new Blob(state.chunks, { type: state.recorder.mimeType || 'audio/webm' });
      handleFile(new File([blob], `麦克风录音-${new Date().toLocaleTimeString().replace(/[:]/g, '')}.webm`, { type: blob.type }));
    };
    state.recorder.start();
    state.recording = true;
    $('recordLabel').textContent = '停止录音';
    $('recordBtn').classList.add('recording');
    setStatus('正在录音，请连续说一句完整的话…');
  } catch {
    setStatus('无法录音，请检查麦克风权限', 'error');
  }
}

/* ------------------------------------------------------------------ 事件绑定 */
function applyStyle(name) {
  const preset = STYLES[name];
  if (!preset) return;
  CONTROL_IDS.forEach(id => { if (preset[id] !== undefined) $(id).value = preset[id]; });
  $('presetSelect').value = name;
  document.querySelectorAll('.style-list .preset').forEach(button => button.classList.toggle('active', button.dataset.style === name));
  updateLabels();
  store('voiceSettings', { style: name, values: values() });
  if (state.activeClone) setMessage('speechStatus', `风格已切换到「${name === 'natural' ? '自然克隆' : $('presetSelect').selectedOptions[0].textContent}」，重新生成即可听到效果`);
}

function updateLabels() {
  const v = values();
  const signed = value => `${value > 0 ? '+' : ''}${value}`;
  $('pitchValue').textContent = `${signed(v.pitch)} st`;
  $('formantValue').textContent = signed(v.formant);
  $('speedValue').textContent = `${signed(v.speed)}%`;
  $('brightnessValue').textContent = signed(v.brightness);
  $('warmthValue').textContent = signed(v.warmth);
  $('presenceValue').textContent = signed(v.presence);
  $('breathValue').textContent = `${v.breath}%`;
  $('roomValue').textContent = `${v.room}%`;
  $('deessValue').textContent = `${v.deess}%`;
}

function bind() {
  $('fileInput').addEventListener('change', event => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) handleFile(file);
  });
  $('recordBtn').addEventListener('click', toggleRecord);
  $('playBtn').addEventListener('click', playOriginal);
  $('playBestBtn').addEventListener('click', playSegment);
  $('clearBtn').addEventListener('click', () => { stopAll(); clearAnalysis(); setStatus('已清空'); });
  $('createCloneBtn').addEventListener('click', createClone);
  $('transcribeBtn').addEventListener('click', transcribeNow);
  $('retryEngine').addEventListener('click', () => loadStatus());
  $('warmupBtn').addEventListener('click', async () => {
    $('warmupBtn').disabled = true;
    setStatus('正在预载 AI 模型（4GB 显存约 20–40 秒）…');
    try {
      const data = await apiJson('/api/warmup', { method: 'POST' });
      setStatus(data.ok ? '模型已预载，可以开始合成' : `预载失败：${data.error}`, data.ok ? '' : 'error');
      await loadStatus(true);
    } catch (error) { setStatus(error.message, 'error'); }
    $('warmupBtn').disabled = false;
  });
  $('installBtn').addEventListener('click', startInstall);
  $('releaseBtn').addEventListener('click', async () => {
    $('releaseBtn').disabled = true;
    try {
      const data = await apiJson('/api/release', { method: 'POST' });
      setStatus(data.released ? '已释放显存，下次合成会重新载入模型' : '模型当前未占用显存');
      await loadStatus(true);
    } catch (error) { setStatus(error.message, 'error'); }
    $('releaseBtn').disabled = false;
  });
  $('generateSpeechBtn').addEventListener('click', generateSpeech);
  $('previewBtn').addEventListener('click', previewSpeech);
  $('reEditBtn').addEventListener('click', () => { $('speechResult').hidden = true; $('speechText').focus(); });
  $('exportSpeechBtn').addEventListener('click', () => {
    if (!state.speechBlob) return;
    const url = URL.createObjectURL(state.speechBlob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${(state.activeClone?.name || '克隆朗读').replace(/[<>:"/\\|?*]/g, '-')}.wav`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    setStatus('已导出 WAV');
  });
  $('speechText').addEventListener('input', () => {
    $('speechResult').hidden = true;
    store('voiceTextDraft', $('speechText').value);
    updateButtons();
  });
  $('speechText').addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !$('generateSpeechBtn').disabled) generateSpeech();
  });
  $('cloneText').addEventListener('input', updateButtons);
  $('cloneName').addEventListener('input', updateButtons);
  CONTROL_IDS.forEach(id => {
    $(id).addEventListener('input', () => {
      $('presetSelect').value = 'custom';
      document.querySelectorAll('.style-list .preset').forEach(button => button.classList.remove('active'));
      updateLabels();
      store('voiceSettings', { style: 'custom', values: values() });
      if (state.activeClone) setMessage('speechStatus', '参数已修改，重新生成即可听到效果');
    });
  });
  document.querySelectorAll('.style-list .preset').forEach(button => {
    button.addEventListener('click', () => applyStyle(button.dataset.style));
  });
  $('presetSelect').addEventListener('change', event => {
    if (event.target.value === 'custom') {
      store('voiceSettings', { style: 'custom', values: values() });
      document.querySelectorAll('.style-list .preset').forEach(button => button.classList.remove('active'));
    } else applyStyle(event.target.value);
  });
  window.addEventListener('pagehide', () => {
    stopVoice();
    if (state.recorder?.state === 'recording') state.recorder.stop();
  });
}

/* -------------------------------------------------------------------- 启动 */
function restoreSettings() {
  const saved = readStored('voiceSettings', null);
  if (saved?.values) {
    CONTROL_IDS.forEach(id => {
      const value = Number(saved.values[id]);
      if (Number.isFinite(value)) $(id).value = value;
    });
    $('presetSelect').value = saved.style && (saved.style === 'custom' || STYLES[saved.style]) ? saved.style : 'custom';
    document.querySelectorAll('.style-list .preset').forEach(button => {
      button.classList.toggle('active', button.dataset.style === saved.style);
    });
  }
  const draft = readStored('voiceTextDraft', '');
  if (typeof draft === 'string') $('speechText').value = draft.slice(0, 12000);
}

bind();
restoreSettings();
updateLabels();
drawWave();
updateButtons();
loadStatus();
loadClones();
refreshIcons();
