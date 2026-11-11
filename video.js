const $ = id => document.getElementById(id);

const S = {
  video: null,
  music: null,
  voice: null,
  image: null,
  videoUrl: null,
  musicUrl: null,
  voiceUrl: null,
  imageUrl: null,
  imagePosition: { x: .5, y: .5 },
  recorder: null,
  chunks: [],
  recording: false,
  voiceCtx: null,
  generatingVoice: false,
  clones: []
};

const TTS_STYLE_ADJUST = {
  natural: { pitch: 0, speed: 0, presence: 0 },
  broadcast: { pitch: 0, speed: 3, presence: 26 },
  warm: { pitch: -1, speed: -2, warmth: 18 },
  deep: { pitch: -3, speed: -4, warmth: 24, brightness: -12 },
  soft: { pitch: 1, speed: -2, breath: 32 },
  energetic: { pitch: 1, speed: 12, presence: 30 }
};

const fmt = value => {
  const seconds = Math.max(0, value || 0);
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
};

function status(text) {
  $('videoStatus').textContent = text;
}

function loadFile(file, type) {
  if (!file) return;
  const url = URL.createObjectURL(file);

  if (type === 'video') {
    if (S.videoUrl) URL.revokeObjectURL(S.videoUrl);
    S.videoUrl = url;
    S.video = file;
    const video = $('video');
    video.src = url;
    video.style.display = 'block';
    $('previewEmpty').style.display = 'none';
    $('previewBadge').style.display = 'block';
    $('videoName').textContent = file.name;
    $('videoTrackName').textContent = file.name;
    video.onloadedmetadata = () => {
      const duration = video.duration;
      $('seek').max = duration;
      $('trimStart').max = duration;
      $('trimEnd').max = duration;
      $('trimEnd').value = duration;
      $('imageStart').max = duration;
      $('imageDuration').max = duration;
      $('timeLabel').textContent = `00:00 / ${fmt(duration)}`;
      $('exportBtn').disabled = false;
      $('exportHint').textContent = '可以加入背景音乐或你的解说';
      status('视频已载入');
    };
    return;
  }

  if (type === 'music') {
    if (S.musicUrl) URL.revokeObjectURL(S.musicUrl);
    S.musicUrl = url;
    S.music = file;
    $('musicName').textContent = file.name;
    status('背景音乐已添加');
    return;
  }

  if (type === 'image') {
    if (S.imageUrl) URL.revokeObjectURL(S.imageUrl);
    S.imageUrl = url;
    S.image = file;
    $('imageOverlay').src = url;
    $('imageOverlay').style.display = 'block';
    $('stickerPanel').hidden = false;
    $('stickerFileName').textContent = file.name;
    status('图片 / 表情包已添加');
    updateImageOverlay($('video').currentTime || 0);
    return;
  }

  if (S.voiceUrl) URL.revokeObjectURL(S.voiceUrl);
  S.voiceUrl = url;
  S.voice = file;
  $('voiceName').textContent = file.name;
  status('解说音频已添加');
}

$('videoInput').addEventListener('change', event => loadFile(event.target.files[0], 'video'));
$('musicInput').addEventListener('change', event => loadFile(event.target.files[0], 'music'));
$('voiceInput').addEventListener('change', event => loadFile(event.target.files[0], 'voice'));
$('imageInput').addEventListener('change', event => loadFile(event.target.files[0], 'image'));

function imageSettings() {
  return {
    start: Math.max(0, Number($('imageStart').value) || 0),
    duration: Math.max(.1, Number($('imageDuration').value) || 3),
    position: $('imagePosition').value,
    scale: Number($('imageScale').value) / 100,
    opacity: Number($('imageOpacity').value) / 100
  };
}

function updateImageOverlay(time) {
  const overlay = $('imageOverlay');
  if (!S.image) return;
  const settings = imageSettings();
  const visible = time >= settings.start && time < settings.start + settings.duration;
  overlay.style.display = visible ? 'block' : 'none';
  overlay.style.width = `${settings.scale * 100}%`;
  overlay.style.opacity = settings.opacity;
  overlay.dataset.position = settings.position;
  if (settings.position === 'custom') {
    overlay.style.left = `${S.imagePosition.x * 100}%`;
    overlay.style.top = `${S.imagePosition.y * 100}%`;
  } else {
    overlay.style.left = '';
    overlay.style.top = '';
  }
}

['imageStart', 'imageDuration', 'imagePosition', 'imageScale', 'imageOpacity'].forEach(id => {
  $(id).addEventListener('input', () => {
    $('imageScaleValue').textContent = `${$('imageScale').value}%`;
    $('imageOpacityValue').textContent = `${$('imageOpacity').value}%`;
    updateImageOverlay($('video').currentTime || 0);
  });
});
$('removeImage').addEventListener('click', () => {
  if (S.imageUrl) URL.revokeObjectURL(S.imageUrl);
  S.image = null;
  S.imageUrl = null;
  $('imageOverlay').removeAttribute('src');
  $('imageOverlay').style.display = 'none';
  $('stickerPanel').hidden = true;
  $('stickerFileName').textContent = '尚未选择图片';
  status('图片已移除');
});
$('resetImage').addEventListener('click', () => {
  $('imageStart').value = 0;
  $('imageDuration').value = 3;
  $('imagePosition').value = 'center';
  $('imageScale').value = 32;
  $('imageOpacity').value = 100;
  S.imagePosition = { x: .5, y: .5 };
  $('imageScaleValue').textContent = '32%';
  $('imageOpacityValue').textContent = '100%';
  $('stickerHint').textContent = '选择“自定义拖拽”后，可以直接拖动画面里的图片。';
  updateImageOverlay($('video').currentTime || 0);
});

$('imagePosition').addEventListener('change', () => {
  $('stickerHint').textContent = $('imagePosition').value === 'custom'
    ? '现在可以直接拖动画面里的图片。'
    : '选择“自定义拖拽”后，可以直接拖动画面里的图片。';
  updateImageOverlay($('video').currentTime || 0);
});

let draggingImage = false;
$('imageOverlay').addEventListener('pointerdown', event => {
  if ($('imagePosition').value !== 'custom') return;
  draggingImage = true;
  $('imageOverlay').setPointerCapture(event.pointerId);
  event.preventDefault();
});
$('imageOverlay').addEventListener('pointermove', event => {
  if (!draggingImage || $('imagePosition').value !== 'custom') return;
  const bounds = $('imageOverlay').parentElement.getBoundingClientRect();
  S.imagePosition.x = Math.max(.05, Math.min(.95, (event.clientX - bounds.left) / bounds.width));
  S.imagePosition.y = Math.max(.05, Math.min(.95, (event.clientY - bounds.top) / bounds.height));
  updateImageOverlay($('video').currentTime || 0);
});
$('imageOverlay').addEventListener('pointerup', () => { draggingImage = false; });
$('imageOverlay').addEventListener('pointercancel', () => { draggingImage = false; });

$('video').addEventListener('timeupdate', () => {
  $('seek').value = $('video').currentTime;
  $('timeLabel').textContent = `${fmt($('video').currentTime)} / ${fmt($('video').duration)}`;
  updateImageOverlay($('video').currentTime);
});

$('video').addEventListener('ended', () => {
  $('playBtn').textContent = '▶';
});

$('seek').addEventListener('input', event => {
  $('video').currentTime = Number(event.target.value);
});

$('playBtn').addEventListener('click', () => {
  const video = $('video');
  if (!S.video) {
    status('请先导入视频');
    return;
  }
  if (video.paused) {
    video.play();
    $('playBtn').textContent = 'Ⅱ';
  } else {
    video.pause();
    $('playBtn').textContent = '▶';
  }
});

$('muteOriginal').addEventListener('click', () => {
  const video = $('video');
  video.muted = !video.muted;
  $('muteOriginal').textContent = video.muted ? '原声：静音' : '原声：开启';
  $('muteOriginal').classList.toggle('active', video.muted);
  $('previewBadge').textContent = video.muted ? '原声已静音' : '原声已开启';
});

function toggle(button, onText, offText) {
  button.classList.toggle('active');
  button.textContent = button.classList.contains('active') ? onText : offText;
}

$('originalToggle').addEventListener('click', () => {
  toggle($('originalToggle'), '静音', '启用');
  $('video').muted = $('originalToggle').classList.contains('active');
});
$('musicToggle').addEventListener('click', () => toggle($('musicToggle'), '启用', '关闭'));
$('voiceToggle').addEventListener('click', () => toggle($('voiceToggle'), '启用', '关闭'));

$('recordVoice').addEventListener('click', async () => {
  if (S.recording) {
    S.recorder.stop();
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    S.chunks = [];
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
    S.recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    S.recorder.ondataavailable = event => event.data.size && S.chunks.push(event.data);
    S.recorder.onstop = async () => {
      stream.getTracks().forEach(track => track.stop());
      S.recording = false;
      $('recordText').textContent = '录制解说';
      const raw = new Blob(S.chunks, { type: S.recorder.mimeType || 'audio/webm' });
      loadFile(new File([raw], '我的解说-录制.wav', { type: raw.type || 'audio/webm' }), 'voice');
      status('录制解说已加入“我的解说”轨道');
    };
    S.recorder.start();
    S.recording = true;
    $('recordText').textContent = '停止录音';
    status('正在录制解说…');
  } catch (error) {
    console.error(error);
    status('麦克风权限未开启或录音失败');
  }
});

function updateTtsButton() {
  const hasText = $('ttsText').value.trim().length > 0;
  $('generateVoice').disabled = !S.clones.length || !hasText || S.generatingVoice;
}

/** 从声线实验室同步过来的默认参数（兼容旧格式） */
function voiceSettingsRecord() {
  const fallback = { pitch: 0, formant: 0, speed: 0, brightness: 0, warmth: 0, presence: 0, breath: 0, room: 0, deess: 35 };
  try {
    const saved = JSON.parse(localStorage.getItem('voiceSettings') || 'null');
    if (saved?.values) return { name: saved.style || 'natural', values: { ...fallback, ...saved.values } };
  } catch (error) { /* 忽略 */ }
  return { name: 'natural', values: fallback };
}

function ttsSettings() {
  const base = voiceSettingsRecord().values;
  const style = $('ttsStyle').value;
  const adjust = TTS_STYLE_ADJUST[style] || {};
  return {
    ...base,
    ...adjust,
    pitch: Number(base.pitch || 0) + Number($('ttsPitch').value || 0),
    speed: Number(base.speed || 0) + Number($('ttsSpeed').value || 0),
    style
  };
}

async function loadClonesIntoVideo() {
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) throw new Error('本机服务未响应');
    const statusData = await response.json();
    const data = await (await fetch('/api/clones', { cache: 'no-store' })).json();
    S.clones = Array.isArray(data.clones) ? data.clones : [];
    const select = $('ttsClone');
    select.innerHTML = '';
    if (!S.clones.length) {
      select.innerHTML = '<option value="">还没有克隆音色</option>';
      $('ttsAvailability').textContent = '先去声线实验室建立克隆音色';
      $('ttsPanel').querySelector('.tts-generate').disabled = true;
      const hint = document.createElement('div');
      hint.className = 'tts-setup';
      hint.innerHTML = '还没有克隆音色。<a href="index.html">打开声线实验室 →</a> 导入一段 3–10 秒的清晰人声（或整段视频），一键克隆后再回来。';
      if (!document.querySelector('.tts-setup')) $('ttsPanel').insertBefore(hint, $('generateVoice'));
      updateTtsButton();
      return;
    }
    document.querySelector('.tts-setup')?.remove();
    S.clones.forEach(clone => select.appendChild(new Option(`${clone.name}（${clone.seconds || 0}s）`, clone.id)));
    const activeId = JSON.parse(localStorage.getItem('voiceActiveClone') || 'null');
    if (activeId && S.clones.some(item => item.id === activeId)) select.value = activeId;
    const engine = statusData.engine === 'ai';
    $('ttsAvailability').textContent = engine
      ? `AI 克隆可用 · ${S.clones.length} 个音色`
      : `轻量模式 · ${S.clones.length} 个音色（未安装 AI 引擎）`;
    updateTtsButton();
  } catch (error) {
    console.error(error);
    $('ttsAvailability').textContent = '本机服务未启动';
    $('ttsClone').innerHTML = '<option value="">本机服务未启动</option>';
    $('generateVoice').disabled = true;
  }
}

$('openTts').addEventListener('click', () => {
  $('ttsPanel').hidden = !$('ttsPanel').hidden;
  if (!$('ttsPanel').hidden) {
    loadClonesIntoVideo();
    $('ttsText').focus();
  }
});

$('closeTts').addEventListener('click', () => {
  $('ttsPanel').hidden = true;
});

$('ttsText').addEventListener('input', () => {
  $('ttsCount').textContent = `${$('ttsText').value.length} / 12000`;
  updateTtsButton();
});

$('ttsText').addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !$('generateVoice').disabled) {
    $('generateVoice').click();
  }
});

$('ttsPitch').addEventListener('input', () => { $('ttsPitchValue').textContent = `${$('ttsPitch').value > 0 ? '+' : ''}${$('ttsPitch').value} st`; });
$('ttsSpeed').addEventListener('input', () => { $('ttsSpeedValue').textContent = `${$('ttsSpeed').value > 0 ? '+' : ''}${$('ttsSpeed').value}%`; });
$('ttsClone').addEventListener('change', updateTtsButton);

async function waitForJob(jobId) {
  for (let attempt = 0; attempt < 1500; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 700));
    const job = await (await fetch(`/api/tts/jobs/${jobId}`)).json();
    if (job.detail) $('ttsAvailability').textContent = `${Math.round((job.progress || 0) * 100)}% · ${job.detail}`;
    if (job.status === 'done') return true;
    if (job.status === 'error') throw new Error(job.detail || '合成失败');
  }
  throw new Error('合成超时');
}

$('generateVoice').addEventListener('click', async () => {
  const text = $('ttsText').value.trim();
  const cloneId = $('ttsClone').value;
  if (!text || !cloneId || S.generatingVoice) return;

  S.generatingVoice = true;
  updateTtsButton();
  const button = $('generateVoice');
  const originalLabel = button.textContent;
  button.textContent = '克隆合成中…';
  status('正在用克隆音色生成解说…');

  try {
    const response = await fetch('/api/tts/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, cloneId, settings: ttsSettings() })
    });
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      throw new Error(failure.error || '合成请求失败');
    }
    const job = await response.json();
    await waitForJob(job.id);
    const audio = await fetch(`/api/tts/jobs/${job.id}/audio`);
    if (!audio.ok) throw new Error('取回合成音频失败');
    const blob = await audio.blob();
    const cloneName = $('ttsClone').selectedOptions[0]?.textContent || '克隆音色';
    loadFile(new File([blob], '文字解说-克隆音色.wav', { type: 'audio/wav' }), 'voice');
    $('voiceName').textContent = `克隆解说 · ${cloneName}`;
    status('文字解说已生成并加入“我的解说”轨道');
    $('ttsAvailability').textContent = `AI 克隆可用 · ${S.clones.length} 个音色`;
    $('ttsPanel').hidden = true;
  } catch (error) {
    console.error(error);
    status(error.message || '文字解说生成失败');
    $('ttsAvailability').textContent = error.message || '生成失败';
  } finally {
    S.generatingVoice = false;
    button.textContent = originalLabel;
    updateTtsButton();
  }
});

function makeAudioTrack(ctx, destination, file, volume, loop) {
  if (!file) return null;
  const audio = new Audio();
  audio.src = URL.createObjectURL(file);
  audio.loop = loop;
  audio.preload = 'auto';
  const node = ctx.createMediaElementSource(audio);
  const gain = ctx.createGain();
  gain.gain.value = volume;
  node.connect(gain).connect(destination);
  return { audio, gain };
}

function drawImageOverlay(canvas, painter, image, time) {
  if (!image || !image.complete || !image.naturalWidth) return;
  const settings = imageSettings();
  if (time < settings.start || time >= settings.start + settings.duration) return;
  const width = canvas.width * settings.scale;
  const height = width * image.naturalHeight / image.naturalWidth;
  const margin = Math.max(12, canvas.width * .03);
  let x = margin;
  let y = margin;
  if (settings.position.includes('right')) x = canvas.width - width - margin;
  if (settings.position.includes('center')) x = (canvas.width - width) / 2;
  if (settings.position.includes('bottom')) y = canvas.height - height - margin;
  if (settings.position === 'center') y = (canvas.height - height) / 2;
  if (settings.position === 'custom') {
    x = canvas.width * S.imagePosition.x - width / 2;
    y = canvas.height * S.imagePosition.y - height / 2;
  }
  painter.save();
  painter.globalAlpha = settings.opacity;
  painter.drawImage(image, x, y, width, height);
  painter.restore();
}

async function exportVideo() {
  if (!S.video) {
    status('请先导入视频');
    return;
  }

  const video = $('video');
  const start = Math.max(0, Number($('trimStart').value) || 0);
  const end = Math.min(video.duration, Number($('trimEnd').value) || video.duration);
  if (end <= start) {
    status('结束时间必须大于开始时间');
    return;
  }

  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth || 1280;
  canvas.height = video.videoHeight || 720;
  const painter = canvas.getContext('2d');
  const stream = canvas.captureStream(30);
  const context = new AudioContext();
  const mix = context.createMediaStreamDestination();
  const tracks = [];
  const original = context.createGain();
  original.gain.value = $('originalToggle').classList.contains('active') ? 0 : Number($('originalVolume').value) / 100;
  context.createMediaElementSource(video).connect(original).connect(mix);

  if (S.music && $('musicToggle').classList.contains('active')) {
    tracks.push(makeAudioTrack(context, mix, S.music, Number($('musicVolume').value) / 100, true));
  }
  if (S.voice && $('voiceToggle').classList.contains('active')) {
    tracks.push(makeAudioTrack(context, mix, S.voice, Number($('voiceVolume').value) / 100, false));
  }
  mix.stream.getAudioTracks().forEach(track => stream.addTrack(track));

  const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9,opus') ? 'video/webm;codecs=vp9,opus' : 'video/webm';
  const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 6000000 });
  const chunks = [];
  recorder.ondataavailable = event => event.data.size && chunks.push(event.data);
  const done = new Promise(resolve => { recorder.onstop = resolve; });

  let exportImage = null;
  if (S.image && S.imageUrl) {
    exportImage = new Image();
    exportImage.src = S.imageUrl;
    await new Promise(resolve => {
      if (exportImage.complete) resolve();
      else exportImage.addEventListener('load', resolve, { once: true });
    });
  }

  video.pause();
  video.currentTime = start;
  await new Promise(resolve => video.addEventListener('seeked', resolve, { once: true }));
  tracks.forEach(track => track?.audio.play().catch(() => {}));
  await context.resume();
  recorder.start(100);
  await video.play();
  status('正在导出，请等待视频播放完成…');

  const draw = () => {
    if (video.currentTime >= end || video.ended) {
      recorder.stop();
      return;
    }
    painter.drawImage(video, 0, 0, canvas.width, canvas.height);
    drawImageOverlay(canvas, painter, exportImage, video.currentTime);
    requestAnimationFrame(draw);
  };
  draw();
  await done;

  tracks.forEach(track => {
    if (!track?.audio) return;
    track.audio.pause();
    URL.revokeObjectURL(track.audio.src);
  });
  video.pause();
  $('playBtn').textContent = '▶';
  await context.close();

  const blob = new Blob(chunks, { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${S.video.name.replace(/\.[^.]+$/, '')}-解说版.webm`;
  anchor.click();
  URL.revokeObjectURL(url);
  status('导出完成');
  $('exportHint').textContent = '文件已下载到浏览器下载目录';
}

$('exportBtn').addEventListener('click', exportVideo);
loadClonesIntoVideo();
