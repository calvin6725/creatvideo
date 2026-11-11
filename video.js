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
  localVoicesReady: false,
  generatingVoice: false,
  referenceProfile: null
};

const TTS_STYLES = {
  cinemaMale: { pitch: -3, brightness: -12, warmth: 48, presence: 26, room: 5, drive: 2, resonance: -34, nasality: -6, breath: 3, vibrato: 4, speed: -5 },
  cinemaFemale: { pitch: 2, brightness: 15, warmth: 22, presence: 22, room: 5, drive: 0, resonance: 18, nasality: -3, breath: 13, vibrato: 6, speed: -2 },
  documentary: { pitch: -1, brightness: 4, warmth: 32, presence: 34, room: 3, drive: 0, resonance: -15, nasality: -4, breath: 2, vibrato: 2, speed: -4 },
  suspense: { pitch: -4, brightness: -24, warmth: 42, presence: 8, room: 13, drive: 5, resonance: -38, nasality: -8, breath: 28, vibrato: 3, speed: -12 },
  energetic: { pitch: 2, brightness: 35, warmth: -5, presence: 48, room: 2, drive: 1, resonance: 26, nasality: 5, breath: 5, vibrato: 7, speed: 12 }
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

function voiceSettingsRecord() {
  const fallback = {
    pitch: 0,
    brightness: 0,
    warmth: 0,
    presence: 0,
    room: 0,
    drive: 0,
    resonance: 0,
    nasality: 0,
    breath: 0,
    vibrato: 0,
    speed: 0
  };
  try {
    const saved = JSON.parse(localStorage.getItem('voiceLabSettings') || '{}');
    return { name: saved.name || 'custom', values: { ...fallback, ...(saved.values || {}) } };
  } catch (error) {
    return { name: 'custom', values: fallback };
  }
}

function buildVoiceGraph(ctx, destination, values) {
  const input = ctx.createGain();
  const high = ctx.createBiquadFilter();
  const low = ctx.createBiquadFilter();
  const resonance = ctx.createBiquadFilter();
  const nasal = ctx.createBiquadFilter();
  const peak = ctx.createBiquadFilter();
  const shaper = ctx.createWaveShaper();
  const compressor = ctx.createDynamicsCompressor();
  const wet = ctx.createGain();
  const dry = ctx.createGain();
  const delay = ctx.createDelay(1);
  const feedback = ctx.createGain();

  high.type = 'highshelf';
  high.frequency.value = 3200;
  high.gain.value = values.brightness * .13 + values.breath * .07;
  low.type = 'lowshelf';
  low.frequency.value = 220;
  low.gain.value = values.warmth * .12;
  resonance.type = 'peaking';
  resonance.frequency.value = 900 + values.resonance * 5;
  resonance.Q.value = .85;
  resonance.gain.value = values.resonance * .08;
  nasal.type = 'peaking';
  nasal.frequency.value = 1100;
  nasal.Q.value = 1.35;
  nasal.gain.value = values.nasality * .12;
  peak.type = 'peaking';
  peak.frequency.value = 1900;
  peak.Q.value = 1.1;
  peak.gain.value = values.presence * .1;

  const amount = values.drive / 100;
  if (amount > 0) {
    const curve = new Float32Array(44100);
    for (let index = 0; index < curve.length; index += 1) {
      const point = index * 2 / curve.length - 1;
      curve[index] = (1 + amount * 18) * point / (1 + amount * 18 * Math.abs(point));
    }
    shaper.curve = curve;
    shaper.oversample = '4x';
  }

  compressor.threshold.value = -18 - values.breath * .035;
  compressor.knee.value = 18;
  compressor.ratio.value = 3;
  compressor.attack.value = .003;
  compressor.release.value = .2;
  wet.gain.value = values.room / 100;
  dry.gain.value = 1 - values.room / 180;
  delay.delayTime.value = .09;
  feedback.gain.value = .18;

  input.connect(high).connect(low).connect(resonance).connect(nasal).connect(peak).connect(shaper).connect(compressor);
  compressor.connect(dry).connect(destination);
  compressor.connect(delay).connect(wet).connect(destination);
  delay.connect(feedback).connect(delay);
  return input;
}

function encodeWav(buffer) {
  const channels = buffer.numberOfChannels;
  const byteLength = buffer.length * channels * 2 + 44;
  const output = new ArrayBuffer(byteLength);
  const view = new DataView(output);
  const write = (offset, value) => [...value].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  write(0, 'RIFF');
  view.setUint32(4, 36 + buffer.length * channels * 2, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, buffer.length * channels * 2, true);
  let position = 44;
  for (let frame = 0; frame < buffer.length; frame += 1) {
    for (let channel = 0; channel < channels; channel += 1) {
      const sample = Math.max(-1, Math.min(1, buffer.getChannelData(channel)[frame]));
      view.setInt16(position, sample < 0 ? sample * 32768 : sample * 32767, true);
      position += 2;
    }
  }
  return new Blob([output], { type: 'audio/wav' });
}

async function processVoiceBlob(blob, values) {
  const decodeContext = new AudioContext();
  try {
    const sourceBuffer = await decodeContext.decodeAudioData(await blob.arrayBuffer());
    const playbackRate = Math.max(.7, Math.min(1.3, 1 + values.speed / 100));
    const pitchRate = Math.pow(2, values.pitch / 12);
    const tail = values.room > 0 ? .35 : .06;
    const duration = sourceBuffer.duration / (playbackRate * pitchRate) + tail;
    const frameCount = Math.max(1, Math.ceil(duration * sourceBuffer.sampleRate));
    const offline = new OfflineAudioContext(sourceBuffer.numberOfChannels, frameCount, sourceBuffer.sampleRate);
    const source = offline.createBufferSource();
    source.buffer = sourceBuffer;
    source.detune.value = values.pitch * 100;
    source.playbackRate.value = playbackRate;
    source.connect(buildVoiceGraph(offline, offline.destination, values));

    if (values.vibrato > 0) {
      const oscillator = offline.createOscillator();
      const depth = offline.createGain();
      oscillator.frequency.value = 4.8;
      depth.gain.value = values.vibrato * 1.6;
      oscillator.connect(depth).connect(source.detune);
      oscillator.start();
    }

    source.start();
    return encodeWav(await offline.startRendering());
  } finally {
    await decodeContext.close();
  }
}

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
      status('正在套用声线实验室参数…');
      try {
        const raw = new Blob(S.chunks, { type: S.recorder.mimeType || 'audio/webm' });
        const processed = await processVoiceBlob(raw, voiceSettingsRecord().values);
        loadFile(new File([processed], '我的解说-已套用声线.wav', { type: 'audio/wav' }), 'voice');
        status('录制解说已加入“我的解说”轨道');
      } catch (error) {
        console.error(error);
        status('录音已完成，但声线处理失败');
      }
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
  const needsReference = $('ttsStyle').value === 'myVoice' && !S.referenceProfile;
  $('generateVoice').disabled = !S.localVoicesReady || !hasText || S.generatingVoice || needsReference;
}

function clamp(value, min = -100, max = 100) {
  return Math.max(min, Math.min(max, value));
}

async function filteredEnergy(buffer, frequency, q = .8) {
  const frames = Math.min(buffer.length, Math.floor(buffer.sampleRate * 20));
  const offline = new OfflineAudioContext(1, frames, buffer.sampleRate);
  const mono = offline.createBuffer(1, frames, buffer.sampleRate);
  const target = mono.getChannelData(0);
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    const source = buffer.getChannelData(channel);
    for (let index = 0; index < frames; index += 1) target[index] += source[index] / buffer.numberOfChannels;
  }
  const node = offline.createBufferSource();
  const filter = offline.createBiquadFilter();
  node.buffer = mono;
  filter.type = 'bandpass';
  filter.frequency.value = frequency;
  filter.Q.value = q;
  node.connect(filter).connect(offline.destination);
  node.start();
  const rendered = await offline.startRendering();
  const data = rendered.getChannelData(0);
  let sum = 0;
  for (let index = 0; index < data.length; index += 1) sum += data[index] * data[index];
  return Math.sqrt(sum / Math.max(1, data.length));
}

async function analyzeReferenceVoice(file) {
  const context = new AudioContext();
  try {
    const buffer = await context.decodeAudioData(await file.arrayBuffer());
    if (buffer.duration < 3) throw new Error('参考录音至少需要 3 秒');
    const [low, mid, high] = await Promise.all([
      filteredEnergy(buffer, 180),
      filteredEnergy(buffer, 1100),
      filteredEnergy(buffer, 4200)
    ]);
    const total = Math.max(.00001, low + mid + high);
    const lowRatio = low / total, midRatio = mid / total, highRatio = high / total;
    return {
      pitch: 0,
      brightness: clamp((highRatio - .22) * 260),
      warmth: clamp((lowRatio - .28) * 260),
      presence: clamp((midRatio - .34) * 220),
      room: 2,
      drive: 0,
      resonance: clamp((lowRatio - highRatio) * -120),
      nasality: clamp((midRatio - .38) * 130),
      breath: clamp((highRatio - .18) * 100, 0, 45),
      vibrato: 3,
      speed: 0
    };
  } finally {
    await context.close();
  }
}

function selectedTtsSettings() {
  const style = $('ttsStyle').value;
  if (style === 'current') return voiceSettingsRecord();
  if (style === 'myVoice') return { name: '我的声音参考', values: S.referenceProfile || voiceSettingsRecord().values };
  return { name: $('ttsStyle').selectedOptions[0].textContent, values: { ...voiceSettingsRecord().values, ...TTS_STYLES[style] } };
}

async function loadLocalVoices() {
  try {
    const response = await fetch('/api/voices', { cache: 'no-store' });
    if (!response.ok) throw new Error('本机语音服务不可用');
    const data = await response.json();
    const voices = Array.isArray(data.voices) ? data.voices : [];
    if (!voices.length) throw new Error('未找到 Windows 本机声线');

    const select = $('ttsVoice');
    select.innerHTML = '';
    voices.forEach(voice => {
      const option = document.createElement('option');
      option.value = voice.name;
      option.textContent = voice.name;
      select.appendChild(option);
    });
    const preferred = voices.findIndex(voice => /Huihui|Chinese|中文/i.test(`${voice.name} ${voice.language || ''}`));
    if (preferred >= 0) select.selectedIndex = preferred;
    S.localVoicesReady = true;
    $('ttsAvailability').textContent = `可用 ${voices.length} 个本机声线 · 沿用当前音色`;
    updateTtsButton();
  } catch (error) {
    console.error(error);
    S.localVoicesReady = false;
    $('ttsVoice').innerHTML = '<option>本机语音不可用</option>';
    $('ttsAvailability').textContent = '本机语音服务未启动';
    $('generateVoice').disabled = true;
  }
}

$('openTts').addEventListener('click', () => {
  $('ttsPanel').hidden = !$('ttsPanel').hidden;
  if (!$('ttsPanel').hidden) $('ttsText').focus();
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

$('ttsStyle').addEventListener('change', () => {
  const style = $('ttsStyle').value;
  $('voiceReference').hidden = style !== 'myVoice';
  const options = [...$('ttsVoice').options];
  const prefersFemale = style === 'cinemaFemale';
  const prefersMale = style === 'cinemaMale' || style === 'documentary' || style === 'suspense';
  const match = options.findIndex(option => prefersFemale
    ? /Yaoyao|Huihui|Zira/i.test(option.textContent)
    : prefersMale ? /Kangkang|David|Mark/i.test(option.textContent) : false);
  if (match >= 0) $('ttsVoice').selectedIndex = match;
  updateTtsButton();
});

$('referenceVoice').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  $('referenceStatus').textContent = '正在本机分析音色特征…';
  try {
    S.referenceProfile = await analyzeReferenceVoice(file);
    localStorage.setItem('authorizedVoiceProfile', JSON.stringify(S.referenceProfile));
    $('referenceStatus').textContent = `已建立轻量音色参考：${file.name}`;
    status('本人的声音参考已保存到当前浏览器');
  } catch (error) {
    S.referenceProfile = null;
    $('referenceStatus').textContent = error.message || '无法分析该录音';
  }
  updateTtsButton();
});

$('generateVoice').addEventListener('click', async () => {
  const text = $('ttsText').value.trim();
  if (!text || !S.localVoicesReady || S.generatingVoice) return;

  S.generatingVoice = true;
  updateTtsButton();
  const button = $('generateVoice');
  const originalLabel = button.textContent;
  button.textContent = 'Windows 本机合成中…';
  status('正在用 Windows 本机语音生成解说…');

  try {
    const response = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice: $('ttsVoice').value })
    });
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      throw new Error(failure.error || '本机语音合成失败');
    }

    const raw = await response.blob();
    button.textContent = '正在套用当前声线…';
    status('正在套用声线实验室的音调、语速和音色…');
    const settings = selectedTtsSettings();
    const processed = await processVoiceBlob(raw, settings.values);
    const file = new File([processed], '文字生成解说-已套用声线.wav', { type: 'audio/wav' });
    loadFile(file, 'voice');
    $('voiceName').textContent = `文字生成解说 · ${settings.name}`;
    status('文字解说已生成并加入“我的解说”轨道');
    $('ttsPanel').hidden = true;
  } catch (error) {
    console.error(error);
    status(error.message || '文字解说生成失败');
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
try {
  S.referenceProfile = JSON.parse(localStorage.getItem('authorizedVoiceProfile') || 'null');
  if (S.referenceProfile) $('referenceStatus').textContent = '已载入此前保存的本人/授权音色参考。';
} catch (error) {
  S.referenceProfile = null;
}
loadLocalVoices();
