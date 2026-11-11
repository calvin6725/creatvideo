const $ = id => document.getElementById(id);
const state = {
  buffer: null, source: null, context: null, playing: false, startedAt: 0,
  offset: 0, raf: null, recording: false, recorder: null, chunks: [],
  reference: null, loading: false, loadVersion: 0, saving: false, exporting: false,
  voices: [], profiles: [], activeProfile: null, generating: false,
  speechBlob: null, speechUrl: null, speechVersion: 0, voiceRequest: 0,
  playbackNodes: [], rate: 1
};
const presets = {
  clean:   { pitch: 0, brightness: 0, warmth: 0, presence: 0, room: 0, drive: 0, resonance: 0, nasality: 0, breath: 0, vibrato: 0, speed: 0 },
  bright:  { pitch: 2, brightness: 42, warmth: -8, presence: 28, room: 5, drive: 0, resonance: 30, nasality: 8, breath: 8, vibrato: 4, speed: 4 },
  deep:    { pitch: -4, brightness: -25, warmth: 58, presence: 8, room: 8, drive: 3, resonance: -55, nasality: -10, breath: 0, vibrato: 5, speed: -4 },
  warm:    { pitch: -2, brightness: -10, warmth: 72, presence: 16, room: 6, drive: 2, resonance: -25, nasality: -8, breath: 4, vibrato: 8, speed: -2 },
  young:   { pitch: 4, brightness: 36, warmth: -20, presence: 35, room: 3, drive: 0, resonance: 45, nasality: 6, breath: 18, vibrato: 5, speed: 8 },
  mature:  { pitch: -3, brightness: -18, warmth: 48, presence: 20, room: 5, drive: 1, resonance: -38, nasality: -6, breath: 2, vibrato: 7, speed: -5 },
  soft:    { pitch: 1, brightness: 12, warmth: 18, presence: -8, room: 11, drive: 0, resonance: 20, nasality: -4, breath: 62, vibrato: 8, speed: -2 },
  raspy:   { pitch: -2, brightness: -8, warmth: 38, presence: 38, room: 2, drive: 18, resonance: -15, nasality: 14, breath: 18, vibrato: 6, speed: -3 },
  cartoon: { pitch: 6, brightness: 48, warmth: -28, presence: 48, room: 4, drive: 1, resonance: 55, nasality: 25, breath: 10, vibrato: 18, speed: 10 }
  ,female: { pitch: 5, brightness: 25, warmth: -8, presence: 24, room: 4, drive: 0, resonance: 38, nasality: 4, breath: 22, vibrato: 6, speed: 3 }
  ,girl: { pitch: 7, brightness: 42, warmth: -24, presence: 38, room: 3, drive: 0, resonance: 52, nasality: 12, breath: 30, vibrato: 9, speed: 8 }
  ,woman: { pitch: 3, brightness: 8, warmth: 25, presence: 20, room: 5, drive: 0, resonance: 18, nasality: -4, breath: 12, vibrato: 7, speed: 0 }
  ,neutral: { pitch: 1, brightness: 12, warmth: 5, presence: 20, room: 3, drive: 0, resonance: 8, nasality: 0, breath: 8, vibrato: 4, speed: 0 }
  ,femaleBright: { pitch: 8, brightness: 62, warmth: -62, presence: 42, room: 2, drive: 0, resonance: 62, nasality: 2, breath: 20, vibrato: 5, speed: 2 }
  ,sweetGirl: { pitch: 10, brightness: 70, warmth: -72, presence: 52, room: 3, drive: 0, resonance: 72, nasality: 10, breath: 34, vibrato: 8, speed: 7 }
  ,chipmunk: { pitch: 12, brightness: 76, warmth: -82, presence: 58, room: 2, drive: 4, resonance: 80, nasality: 24, breath: 20, vibrato: 12, speed: 14 }
  ,duck: { pitch: 3, brightness: 18, warmth: -12, presence: 36, room: 8, drive: 12, resonance: 28, nasality: 78, breath: 6, vibrato: 16, speed: 5 }
  ,alien: { pitch: 5, brightness: 52, warmth: -38, presence: 42, room: 30, drive: 26, resonance: 48, nasality: 36, breath: 18, vibrato: 42, speed: -4 }
  ,monster: { pitch: -10, brightness: -46, warmth: 86, presence: 14, room: 12, drive: 42, resonance: -86, nasality: -24, breath: 0, vibrato: 4, speed: -14 }
};
const controls = ['pitch','brightness','warmth','presence','room','drive','resonance','nasality','breath','vibrato','speed'];
const fmt = seconds => { seconds = Math.max(0, seconds || 0); return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`; };
function setStatus(text){ $('statusText').textContent = text; }
function drawWave(){
  const canvas=$('waveform'), ctx=canvas.getContext('2d'), w=canvas.width, h=canvas.height; ctx.clearRect(0,0,w,h);
  ctx.fillStyle='#f7f9fa'; ctx.fillRect(0,0,w,h);
  if(!state.buffer){ $('emptyWave').style.display='flex'; return; }
  $('emptyWave').style.display='none'; const data=state.buffer.getChannelData(0), step=Math.ceil(data.length/w), amp=h*.38;
  ctx.beginPath(); ctx.strokeStyle='#72a7a2'; ctx.lineWidth=1.5;
  for(let i=0;i<w;i++){ let min=1,max=-1; for(let j=0;j<step;j++){const v=data[i*step+j]||0; if(v<min)min=v;if(v>max)max=v;} ctx.moveTo(i,h/2+min*amp);ctx.lineTo(i,h/2+max*amp); } ctx.stroke();
  ctx.strokeStyle='rgba(78,150,145,.14)'; ctx.lineWidth=1; for(let y=35;y<h;y+=50){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
}
function audioContext() {
  if (!state.context || state.context.state === 'closed') {
    state.context = new (window.AudioContext || window.webkitAudioContext)();
  }
  return state.context;
}

async function loadBlob(blob, name) {
  const version = ++state.loadVersion;
  stop();
  state.loading = true;
  state.buffer = null;
  state.reference = null;
  state.offset = 0;
  $('fileName').textContent = name;
  $('duration').textContent = $('totalTime').textContent = '00:00';
  $('referenceStatus').textContent = '正在读取并分析声音…';
  drawWave();
  updateProgress(0);
  updateBuilder();
  try {
    if (blob.size > 100 * 1024 * 1024) throw new Error('请选择小于 100 MB 的音频');
    const buffer = await audioContext().decodeAudioData(await blob.arrayBuffer());
    if (version !== state.loadVersion) return;
    state.buffer = buffer;
    $('duration').textContent = $('totalTime').textContent = fmt(buffer.duration);
    drawWave();
    const features = await voiceFeatures(buffer);
    if (version !== state.loadVersion) return;
    if (buffer.duration < 1) throw new Error('建立音色需要至少 1 秒的清晰人声');
    if (features.level < .0001) throw new Error('录音音量太低，无法提取音色，请换一段清晰人声');
    state.reference = { name, duration: buffer.duration, features };
    $('referenceStatus').textContent = `已提取音色参考 · ${buffer.numberOfChannels} 声道 · ${Math.round(buffer.sampleRate / 100) / 10} kHz`;
    if (!$('voiceName').value.trim()) $('voiceName').value = `${name.replace(/\.[^.]+$/, '').slice(0, 40)} · 新音色`;
    $('engineHint').textContent = '试听调节效果，满意后生成新音色';
    setStatus('声音已载入，音色参考已就绪');
  } catch (error) {
    if (version !== state.loadVersion) return;
    $('referenceStatus').textContent = error.message || '无法读取音频，请换一个文件';
    setStatus('声音参考未建立');
  } finally {
    if (version === state.loadVersion) {
      state.loading = false;
      updateBuilder();
    }
  }
}
function values(){ return Object.fromEntries(controls.map(id=>[id,Number($(id).value)])); }
function updateLabels(){ const v=values(); $('pitchValue').textContent=`${v.pitch>0?'+':''}${v.pitch} st`; $('brightnessValue').textContent=`${v.brightness>0?'+':''}${v.brightness}`; $('warmthValue').textContent=`${v.warmth>0?'+':''}${v.warmth}`; $('presenceValue').textContent=`${v.presence>0?'+':''}${v.presence}`; $('roomValue').textContent=`${v.room}%`; $('driveValue').textContent=`${v.drive}%`; $('resonanceValue').textContent=`${v.resonance>0?'+':''}${v.resonance}`; $('nasalityValue').textContent=`${v.nasality>0?'+':''}${v.nasality}`; $('breathValue').textContent=`${v.breath}%`; $('vibratoValue').textContent=`${v.vibrato}%`; $('speedValue').textContent=`${v.speed>0?'+':''}${v.speed}%`; }
function readStored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; }
  catch { return fallback; }
}
function persistSettings(name) {
  try {
    localStorage.setItem('voiceLabSettings', JSON.stringify({
      name: name || $('presetSelect').value || 'custom', values: values()
    }));
  } catch { setStatus('浏览器存储不可用，当前参数仍可使用'); }
}
function applyPreset(name) {
  const preset = presets[name];
  if (!preset) return;
  controls.forEach(id => $(id).value = preset[id]);
  $('presetSelect').value = name;
  document.querySelectorAll('.preset').forEach(button => button.classList.toggle('active', button.dataset.preset === name));
  updateLabels();
  persistSettings(name);
  settingsChanged();
}
function makeGraph(ctx, destination, v, nodes = []){
  const input=ctx.createGain(), highpass=ctx.createBiquadFilter(), high=ctx.createBiquadFilter(), low=ctx.createBiquadFilter(), air=ctx.createBiquadFilter(), resonance=ctx.createBiquadFilter(), nasal=ctx.createBiquadFilter(), peak=ctx.createBiquadFilter(), shaper=ctx.createWaveShaper(), compressor=ctx.createDynamicsCompressor(), wet=ctx.createGain(), dry=ctx.createGain(), delay=ctx.createDelay(1), feedback=ctx.createGain();
  nodes.push(input, highpass, high, low, air, resonance, nasal, peak, shaper, compressor, wet, dry, delay, feedback);
  highpass.type='highpass'; highpass.frequency.value=Math.max(35,70+Math.max(0,v.pitch)*12+Math.max(0,-v.warmth)*.35); highpass.Q.value=.55;
  high.type='highshelf'; high.frequency.value=3200; high.gain.value=v.brightness*.15+v.breath*.08; low.type='lowshelf'; low.frequency.value=220; low.gain.value=v.warmth*.18;
  air.type='highshelf'; air.frequency.value=6200; air.gain.value=Math.max(-6,v.brightness*.08+v.breath*.04);
  resonance.type='peaking'; resonance.frequency.value=900+v.resonance*5; resonance.Q.value=.85; resonance.gain.value=v.resonance*.08;
  nasal.type='peaking'; nasal.frequency.value=1100; nasal.Q.value=1.35; nasal.gain.value=v.nasality*.12;
  peak.type='peaking'; peak.frequency.value=1700+v.pitch*85; peak.Q.value=1.1; peak.gain.value=v.presence*.12;
  const amount=v.drive/100; if(amount>0){ const curve=new Float32Array(44100); for(let i=0;i<curve.length;i++){const x=i*2/curve.length-1;curve[i]=(1+amount*18)*x/(1+amount*18*Math.abs(x));} shaper.curve=curve; shaper.oversample='4x'; } else shaper.curve=null;
  compressor.threshold.value=-18; compressor.knee.value=18; compressor.ratio.value=3; compressor.attack.value=.003; compressor.release.value=.2;
  wet.gain.value=v.room/100; dry.gain.value=1-v.room/180; delay.delayTime.value=.09; feedback.gain.value=.18;
  compressor.threshold.value -= v.breath*.035;
  input.connect(highpass).connect(high).connect(low).connect(air).connect(resonance).connect(nasal).connect(peak).connect(shaper).connect(compressor); compressor.connect(dry).connect(destination); compressor.connect(delay).connect(wet).connect(destination); delay.connect(feedback).connect(delay); return input;
}
function stop(preserveOffset = false) {
  if (preserveOffset && state.playing) {
    state.offset = Math.min(state.buffer.duration, state.offset + (state.context.currentTime - state.startedAt) * state.rate);
  }
  state.playing = false;
  cancelAnimationFrame(state.raf);
  if (state.source) {
    state.source.onended = null;
    try { state.source.stop(); } catch {}
    state.source.disconnect();
    state.source = null;
  }
  if (state.modulator) {
    try { state.modulator.stop(); } catch {}
    state.modulator.disconnect();
    state.modulator = null;
  }
  state.playbackNodes.forEach(node => node.disconnect());
  state.playbackNodes = [];
  $('playBtn').textContent = '▶';
}
function tuneSource(ctx, source, v, nodes = []) {
  source.detune.value = v.pitch * 100;
  source.playbackRate.value = Math.max(.7, Math.min(1.3, 1 + v.speed / 100));
  if (v.vibrato <= 0) return null;
  const lfo = ctx.createOscillator(), depth = ctx.createGain();
  lfo.frequency.value = 4.8;
  depth.gain.value = v.vibrato * 1.6;
  lfo.connect(depth).connect(source.detune);
  nodes.push(depth);
  lfo.start();
  return lfo;
}
async function play() {
  if (!state.buffer) return;
  if (state.playing) { stop(true); setStatus('试听已暂停'); return; }
  try {
    const ctx = audioContext();
    await ctx.resume();
    if (state.playing || !state.buffer) return;
    $('speechAudio').pause();
    const v = values(), source = ctx.createBufferSource();
    source.buffer = state.buffer;
    state.modulator = tuneSource(ctx, source, v, state.playbackNodes);
    source.connect(makeGraph(ctx, ctx.destination, v, state.playbackNodes));
    state.rate = Math.pow(2, v.pitch / 12) * Math.max(.7, Math.min(1.3, 1 + v.speed / 100));
    if (state.offset >= state.buffer.duration) state.offset = 0;
    source.onended = () => {
      if (state.source !== source) return;
      state.offset = 0;
      stop();
      updateProgress(0);
      setStatus('试听结束');
    };
    state.source = source;
    state.startedAt = ctx.currentTime;
    state.playing = true;
    source.start(0, state.offset);
    $('playBtn').textContent = 'Ⅱ';
    animate();
    setStatus('正在试听处理效果');
  } catch { stop(); setStatus('无法播放音频，请重试'); }
}
function animate() {
  if (!state.playing || !state.buffer) return;
  const elapsed = (state.context.currentTime - state.startedAt) * state.rate + state.offset;
  updateProgress(Math.min(1, elapsed / state.buffer.duration));
  state.raf = requestAnimationFrame(animate);
}
function updateProgress(ratio){ $('progressFill').style.width=`${ratio*100}%`; $('playhead').style.left=`${ratio*100}%`; $('playhead').style.display=state.buffer?'block':'none'; $('currentTime').textContent=fmt((state.buffer?.duration||0)*ratio); }
async function record() {
  if (state.recording) {
    if (state.recorder.state === 'recording') state.recorder.stop();
    return;
  }
  let stream;
  try {
    stop();
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
    state.chunks = [];
    state.recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    state.recorder.ondataavailable = event => { if (event.data.size) state.chunks.push(event.data); };
    state.recorder.onstop = () => {
      stream.getTracks().forEach(track => track.stop());
      state.recording = false;
      $('recordLabel').textContent = '开始录音';
      $('recordBtn').classList.remove('recording');
      loadBlob(new Blob(state.chunks, { type: state.recorder.mimeType || 'audio/webm' }), '我的录音.webm');
    };
    state.recorder.start();
    state.recording = true;
    updateBuilder();
    $('recordLabel').textContent = '停止录音';
    $('recordBtn').classList.add('recording');
    setStatus('正在录音…');
  } catch {
    stream?.getTracks().forEach(track => track.stop());
    setStatus('无法录音，请检查麦克风权限');
  }
}
async function renderVoice(buffer, v) {
  const rate = Math.max(.7, Math.min(1.3, 1 + v.speed / 100)) * Math.pow(2, v.pitch / 12);
  const minimumRate = rate * Math.pow(2, -v.vibrato * 1.6 / 1200);
  const duration = buffer.duration / minimumRate + (v.room > 0 ? .65 : .06);
  const offline = new OfflineAudioContext(buffer.numberOfChannels, Math.ceil(duration * buffer.sampleRate), buffer.sampleRate);
  const source = offline.createBufferSource();
  source.buffer = buffer;
  const modulator = tuneSource(offline, source, v);
  source.connect(makeGraph(offline, offline.destination, v));
  source.start();
  const rendered = await offline.startRendering();
  if (modulator) modulator.stop();
  return encodeWav(rendered);
}
function downloadWav(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${filename.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-').slice(0, 100)}.wav`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
async function exportWav() {
  if (!state.buffer || state.exporting) return;
  state.exporting = true;
  updateBuilder();
  setStatus('正在生成 WAV…');
  const name = $('fileName').textContent.replace(/\.[^.]+$/, '');
  try {
    const blob = await renderVoice(state.buffer, values());
    downloadWav(blob, `${name}-变声`);
    setStatus('处理录音已导出');
  } catch { setStatus('导出失败，请重试'); }
  finally { state.exporting = false; updateBuilder(); }
}
function encodeWav(buffer){ const channels=buffer.numberOfChannels, length=buffer.length*channels*2+44, out=new ArrayBuffer(length), view=new DataView(out); const write=(o,s)=>[...s].forEach((c,i)=>view.setUint8(o+i,c.charCodeAt(0))); write(0,'RIFF');view.setUint32(4,36+buffer.length*channels*2,true);write(8,'WAVE');write(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,channels,true);view.setUint32(24,buffer.sampleRate,true);view.setUint32(28,buffer.sampleRate*channels*2,true);view.setUint16(32,channels*2,true);view.setUint16(34,16,true);write(36,'data');view.setUint32(40,buffer.length*channels*2,true); let pos=44; for(let i=0;i<buffer.length;i++)for(let c=0;c<channels;c++){let x=Math.max(-1,Math.min(1,buffer.getChannelData(c)[i]));view.setInt16(pos,x<0?x*0x8000:x*0x7fff,true);pos+=2;} return new Blob([out],{type:'audio/wav'}); }

function boundedSettings(settings = {}) {
  return Object.fromEntries(controls.map(id => {
    const value = Number(settings[id]);
    return [id, Math.max(Number($(id).min), Math.min(Number($(id).max), Number.isFinite(value) ? value : 0))];
  }));
}
function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}
function setMessage(id, text, error = false) {
  $(id).textContent = text;
  $(id).classList.toggle('error', error);
}
function updateBuilder() {
  $('exportBtn').disabled = !state.buffer || state.loading || state.exporting;
  $('playBtn').disabled = !state.buffer || state.loading || state.recording;
  const hasReference = Boolean(state.reference || state.activeProfile?.reference);
  $('createVoiceBtn').disabled = !hasReference || !state.voices.length || state.loading || state.saving || state.recording || !$('voiceName').value.trim();
  $('voiceName').disabled = state.saving;
  $('baseVoice').disabled = state.saving || !state.voices.length;
  $('fileInput').disabled = state.saving || state.recording;
  $('recordBtn').disabled = state.saving || state.loading;
  $('presetSelect').disabled = state.saving;
  $('resetBtn').disabled = state.saving;
  controls.forEach(id => $(id).disabled = state.saving);
  document.querySelectorAll('.preset, .saved-voice button').forEach(button => button.disabled = state.saving);
  const updating = state.activeProfile && $('voiceName').value.trim() === state.activeProfile.name;
  $('createVoiceLabel').textContent = state.saving ? '正在建立音色…' : updating ? '更新音色' : '生成新音色';
  const validVoice = state.activeProfile && state.voices.some(voice => voice.name === state.activeProfile.baseVoice);
  $('generateSpeechBtn').disabled = !validVoice || !$('speechText').value.trim() || state.generating || state.saving;
  $('generateSpeechLabel').textContent = state.generating ? '正在生成朗读…' : '生成朗读';
  $('speechCount').textContent = `${$('speechText').value.length} / 12000`;
}
function settingsChanged() {
  if (state.activeProfile) setMessage('voiceBuildStatus', '参数已修改，更新音色后用于下一次朗读');
  else if (state.reference) setMessage('voiceBuildStatus', '音色参考已就绪');
  if (state.playing) { stop(true); play(); }
  updateBuilder();
}

function estimatePitch(samples, sampleRate) {
  const pitches = [];
  const size = 2048;
  const stride = Math.max(size, Math.floor(samples.length / 16));
  for (let start = 0; start + size <= samples.length; start += stride) {
    let energy = 0, mean = 0;
    for (let i = 0; i < size; i++) mean += samples[start + i] / size;
    for (let i = 0; i < size; i++) energy += (samples[start + i] - mean) ** 2;
    if (energy / size < .00002) continue;
    const correlations = [];
    const minLag = Math.floor(sampleRate / 400), maxLag = Math.ceil(sampleRate / 65);
    for (let lag = minLag; lag <= maxLag; lag++) {
      let sum = 0, a = 0, b = 0;
      for (let i = 0; i < size - lag; i += 2) {
        const x = samples[start + i] - mean, y = samples[start + i + lag] - mean;
        sum += x * y; a += x * x; b += y * y;
      }
      correlations[lag] = sum / Math.sqrt(Math.max(1e-12, a * b));
    }
    const best = Math.max(...correlations.slice(minLag));
    for (let lag = minLag + 1; lag < maxLag; lag++) {
      if (correlations[lag] > Math.max(.72, best * .93) &&
          correlations[lag] >= correlations[lag - 1] && correlations[lag] > correlations[lag + 1]) {
        pitches.push(sampleRate / lag);
        break;
      }
    }
  }
  pitches.sort((a, b) => a - b);
  return pitches.length >= 2 ? pitches[Math.floor(pitches.length / 2)] : 0;
}

async function voiceFeatures(buffer) {
  const sampleRate = 16000, duration = Math.min(15, buffer.duration);
  const offline = new OfflineAudioContext(4, Math.max(1, Math.ceil(duration * sampleRate)), sampleRate);
  const source = offline.createBufferSource(), mono = offline.createGain(), merger = offline.createChannelMerger(4);
  source.buffer = buffer;
  mono.channelCount = 1;
  mono.channelCountMode = 'explicit';
  source.connect(mono);
  [180, 1100, 4200].forEach((frequency, index) => {
    const filter = offline.createBiquadFilter();
    filter.type = 'bandpass';
    filter.frequency.value = frequency;
    filter.Q.value = .8;
    mono.connect(filter).connect(merger, 0, index);
  });
  mono.connect(merger, 0, 3);
  merger.connect(offline.destination);
  source.start(0, Math.max(0, (buffer.duration - duration) / 2), duration);
  const rendered = await offline.startRendering();
  const energy = [0, 1, 2, 3].map(channel => {
    const data = rendered.getChannelData(channel);
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    return Math.sqrt(sum / Math.max(1, data.length));
  });
  const total = Math.max(.00001, energy[0] + energy[1] + energy[2]);
  return {
    low: energy[0] / total, mid: energy[1] / total, high: energy[2] / total,
    level: energy[3], pitch: estimatePitch(rendered.getChannelData(3), sampleRate)
  };
}

function matchedSettings(target, base, adjustments) {
  const limit = (value, min, max) => Math.max(min, Math.min(max, value));
  const difference = key => limit(20 * Math.log10(Math.max(.01, target[key]) / Math.max(.01, base[key])), -9, 9);
  const matching = {
    ...presets.clean,
    brightness: difference('high') / .23,
    warmth: difference('low') / .18,
    presence: difference('mid') / .12,
    resonance: limit(((target.high - target.low) - (base.high - base.low)) * 100, -35, 35),
    pitch: target.pitch && base.pitch ? limit(12 * Math.log2(target.pitch / base.pitch), -7, 7) : 0
  };
  return boundedSettings(Object.fromEntries(controls.map(id => [id, matching[id] + adjustments[id]])));
}

async function requestSpeech(text, voice) {
  const response = await fetch('/api/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice }),
    signal: AbortSignal.timeout(120000)
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw new Error(failure.error || '本机语音合成失败，请重试');
  }
  const blob = await response.blob();
  if (blob.size < 44) throw new Error('语音服务没有返回有效音频');
  return audioContext().decodeAudioData(await blob.arrayBuffer());
}

async function loadLocalVoices() {
  const request = ++state.voiceRequest;
  $('retryVoices').disabled = true;
  $('voiceServiceStatus').textContent = '正在检查本机语音服务…';
  try {
    const response = await fetch('/api/voices', { cache: 'no-store', signal: AbortSignal.timeout(20000) });
    if (!response.ok) throw new Error('本机语音服务未响应');
    const data = await response.json();
    const voices = Array.isArray(data.voices) ? data.voices.filter(voice => typeof voice.name === 'string' && voice.name) : [];
    if (!voices.length) throw new Error('没有找到可用的本机语音包');
    if (request !== state.voiceRequest) return;
    const previous = $('baseVoice').value;
    state.voices = voices;
    $('baseVoice').replaceChildren(...voices.map(voice => new Option(voice.name, voice.name)));
    const preferred = [state.activeProfile?.baseVoice, previous, voices.find(voice => /Huihui|Chinese|中文|zh/i.test(`${voice.name} ${voice.language}`))?.name];
    $('baseVoice').value = preferred.find(name => voices.some(voice => voice.name === name)) || voices[0].name;
    $('baseVoice').disabled = false;
    $('voiceServiceStatus').textContent = `本机语音可用 · ${voices.length} 个基础声线`;
  } catch (error) {
    if (request !== state.voiceRequest) return;
    state.voices = [];
    $('baseVoice').replaceChildren(new Option('本机语音不可用', ''));
    $('baseVoice').disabled = true;
    $('voiceServiceStatus').textContent = location.protocol === 'file:'
      ? '请通过“启动声线实验室.bat”打开页面'
      : `${error.message || '无法连接本机语音'}，请启动本地服务后重试`;
  } finally {
    if (request === state.voiceRequest) {
      $('retryVoices').disabled = false;
      updateBuilder();
    }
  }
}

function clearSpeechResult() {
  state.speechVersion++;
  $('speechAudio').pause();
  $('speechAudio').removeAttribute('src');
  $('speechAudio').load();
  if (state.speechUrl) URL.revokeObjectURL(state.speechUrl);
  state.speechUrl = null;
  state.speechBlob = null;
  $('speechResult').hidden = true;
}
function saveProfiles(profiles) {
  localStorage.setItem('voiceLabProfiles', JSON.stringify(profiles));
  state.profiles = profiles;
}
function activateProfile(profile, restoreControls = true) {
  state.activeProfile = profile;
  clearSpeechResult();
  if (restoreControls) {
    stop();
    state.offset = 0;
    state.reference = null;
    updateProgress(0);
    $('referenceStatus').textContent = state.buffer
      ? '当前录音保留用于试听，音色参考沿用已保存的音色'
      : '已载入保存的音色参考，可直接生成文字朗读';
    controls.forEach(id => $(id).value = profile.values[id]);
    $('presetSelect').value = 'custom';
    document.querySelectorAll('.preset').forEach(button => button.classList.remove('active'));
    $('voiceName').value = profile.name;
    if (state.voices.some(voice => voice.name === profile.baseVoice)) $('baseVoice').value = profile.baseVoice;
    updateLabels();
    persistSettings('custom');
  }
  try { localStorage.setItem('voiceLabActiveProfile', JSON.stringify(profile.id)); } catch {}
  $('speechEditor').hidden = false;
  $('activeVoiceName').textContent = profile.name;
  setMessage('speechStatus', '音色已就绪');
  setMessage('voiceBuildStatus', `已保存音色：${profile.name}`);
  renderProfiles();
  updateBuilder();
}
function renderProfiles() {
  const list = $('savedVoices');
  list.replaceChildren();
  if (!state.profiles.length) {
    const empty = document.createElement('p');
    empty.className = 'saved-empty';
    empty.textContent = '尚未保存音色';
    list.append(empty);
  }
  state.profiles.forEach(profile => {
    const row = document.createElement('div');
    row.className = `saved-voice${profile.id === state.activeProfile?.id ? ' active' : ''}`;
    const select = document.createElement('button');
    select.type = 'button';
    select.className = 'saved-voice-select';
    const icon = document.createElement('i');
    icon.dataset.lucide = 'audio-lines';
    icon.setAttribute('aria-hidden', 'true');
    const labels = document.createElement('span'), name = document.createElement('b'), source = document.createElement('small');
    name.textContent = profile.name;
    source.textContent = profile.reference.name;
    labels.append(name, source);
    select.append(icon, labels);
    select.addEventListener('click', () => { activateProfile(profile); $('speechEditor').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-btn';
    remove.title = `删除音色：${profile.name}`;
    remove.setAttribute('aria-label', remove.title);
    const trash = document.createElement('i');
    trash.dataset.lucide = 'trash-2';
    trash.setAttribute('aria-hidden', 'true');
    remove.append(trash);
    remove.addEventListener('click', () => {
      try {
        saveProfiles(state.profiles.filter(item => item.id !== profile.id));
        if (state.activeProfile?.id === profile.id) {
          state.activeProfile = null;
          clearSpeechResult();
          $('speechEditor').hidden = true;
          localStorage.removeItem('voiceLabActiveProfile');
          setMessage('voiceBuildStatus', '音色已删除');
        }
        renderProfiles();
        updateBuilder();
      } catch { setStatus('无法保存删除结果，请检查浏览器存储'); }
    });
    row.append(select, remove);
    list.append(row);
  });
  refreshIcons();
}

async function createVoice() {
  const reference = state.reference || state.activeProfile?.reference;
  const name = $('voiceName').value.trim(), baseVoice = $('baseVoice').value;
  if (!reference || !name || !baseVoice || state.saving || state.loading || state.recording) return;
  const adjustments = values();
  const updating = state.activeProfile?.name === name;
  const existingId = updating ? state.activeProfile.id : null;
  const existingBase = state.activeProfile?.baseVoice === baseVoice ? state.activeProfile.baseFeatures : null;
  state.saving = true;
  updateBuilder();
  setMessage('voiceBuildStatus', '正在建立参考音色与朗读声线的匹配…');
  try {
    let baseFeatures = existingBase;
    if (!baseFeatures) {
      const chinese = /Huihui|Chinese|中文|zh/i.test(`${baseVoice} ${state.voices.find(voice => voice.name === baseVoice)?.language}`);
      const sample = chinese ? '你好，欢迎来到声线实验室。今天阳光很好，让我们用新的声音，讲述一个温暖而有趣的故事。' : 'Hello and welcome to Voice Lab. Today is a beautiful day. Let us tell a warm and interesting story with a new voice.';
      baseFeatures = await voiceFeatures(await requestSpeech(sample, baseVoice));
    }
    const profile = {
      id: existingId || crypto.randomUUID(), name, baseVoice,
      reference, baseFeatures, values: adjustments,
      settings: matchedSettings(reference.features, baseFeatures, adjustments),
      updatedAt: new Date().toISOString()
    };
    const profiles = state.profiles.filter(item => item.id !== profile.id);
    if (profiles.length >= 50) throw new Error('最多保存 50 个音色，请先删除不需要的音色');
    saveProfiles([profile, ...profiles]);
    activateProfile(profile, false);
    setStatus(updating ? '音色已更新' : '新音色已生成');
    $('speechEditor').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('speechText').focus({ preventScroll: true });
  } catch (error) {
    setMessage('voiceBuildStatus', error.message || '音色生成失败，请重试', true);
    setStatus('音色未保存');
  } finally { state.saving = false; updateBuilder(); }
}

async function generateSpeech() {
  const profile = state.activeProfile, text = $('speechText').value.trim();
  if (!profile || !text || state.generating || state.saving) return;
  clearSpeechResult();
  const version = state.speechVersion;
  state.generating = true;
  updateBuilder();
  stop(true);
  setMessage('speechStatus', `正在使用“${profile.name}”生成朗读…`);
  setStatus('正在合成朗读…');
  try {
    const buffer = await requestSpeech(text, profile.baseVoice);
    const blob = await renderVoice(buffer, profile.settings);
    if (version !== state.speechVersion || state.activeProfile?.id !== profile.id) return;
    state.speechBlob = blob;
    state.speechUrl = URL.createObjectURL(blob);
    $('speechAudio').src = state.speechUrl;
    $('speechResultName').textContent = `${profile.name} · 朗读结果`;
    $('speechResult').hidden = false;
    setMessage('speechStatus', '朗读已生成，可以试听或导出 WAV');
    setStatus('朗读已生成');
    $('speechAudio').play().catch(() => {});
  } catch (error) {
    if (version !== state.speechVersion) return;
    setMessage('speechStatus', error.message || '朗读生成失败，请重试', true);
    setStatus('朗读生成失败');
  } finally { state.generating = false; updateBuilder(); }
}

$('fileInput').addEventListener('change', event => {
  const file = event.target.files[0];
  if (file) loadBlob(file, file.name);
  event.target.value = '';
});
$('recordBtn').addEventListener('click', record);
$('playBtn').addEventListener('click', play);
$('resetBtn').addEventListener('click', () => applyPreset('clean'));
$('exportBtn').addEventListener('click', exportWav);
controls.forEach(id => {
  $(id).setAttribute('aria-label', $(id).closest('.control-card').querySelector('b').textContent);
  $(id).addEventListener('input', () => {
    updateLabels();
    $('presetSelect').value = 'custom';
    document.querySelectorAll('.preset').forEach(button => button.classList.remove('active'));
    persistSettings('custom');
    settingsChanged();
  });
});
document.querySelectorAll('.preset').forEach(button => button.addEventListener('click', () => applyPreset(button.dataset.preset)));
$('presetSelect').addEventListener('change', event => event.target.value === 'custom' ? persistSettings('custom') : applyPreset(event.target.value));
$('createVoiceBtn').addEventListener('click', createVoice);
$('voiceName').addEventListener('input', updateBuilder);
$('baseVoice').addEventListener('change', settingsChanged);
$('retryVoices').addEventListener('click', loadLocalVoices);
$('generateSpeechBtn').addEventListener('click', generateSpeech);
$('speechText').addEventListener('input', () => {
  clearSpeechResult();
  setMessage('speechStatus', '文本已修改，等待生成朗读');
  try { localStorage.setItem('voiceLabTextDraft', JSON.stringify($('speechText').value)); } catch {}
  updateBuilder();
});
$('speechText').addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !$('generateSpeechBtn').disabled) generateSpeech();
});
$('speechAudio').addEventListener('play', () => stop(true));
$('exportSpeechBtn').addEventListener('click', () => {
  if (state.speechBlob && state.activeProfile) {
    downloadWav(state.speechBlob, `${state.activeProfile.name}-文字朗读`);
    setStatus('朗读 WAV 已导出');
  }
});
window.addEventListener('pagehide', () => {
  stop();
  if (state.recorder?.state === 'recording') state.recorder.stop();
  if (state.speechUrl) URL.revokeObjectURL(state.speechUrl);
});

const saved = readStored('voiceLabSettings', null);
if (saved?.values) {
  const settings = boundedSettings(saved.values);
  controls.forEach(id => $(id).value = settings[id]);
  $('presetSelect').value = presets[saved.name] ? saved.name : 'custom';
  document.querySelectorAll('.preset').forEach(button => button.classList.toggle('active', button.dataset.preset === saved.name));
}
const storedProfiles = readStored('voiceLabProfiles', []);
state.profiles = (Array.isArray(storedProfiles) ? storedProfiles : []).filter(profile =>
  profile && typeof profile.id === 'string' && typeof profile.name === 'string' &&
  typeof profile.baseVoice === 'string' && typeof profile.reference?.name === 'string' &&
  ['low', 'mid', 'high', 'level', 'pitch'].every(key => Number.isFinite(profile.reference?.features?.[key])) &&
  profile.values && profile.settings &&
  ['low', 'mid', 'high', 'level', 'pitch'].every(key => Number.isFinite(profile.baseFeatures?.[key]))
).map(profile => ({ ...profile, values: boundedSettings(profile.values), settings: boundedSettings(profile.settings) }));
const draft = readStored('voiceLabTextDraft', '');
$('speechText').value = typeof draft === 'string' ? draft.slice(0, 12000) : '';
const activeId = readStored('voiceLabActiveProfile', null);
const activeProfile = state.profiles.find(profile => profile.id === activeId);
if (activeProfile) activateProfile(activeProfile);
else renderProfiles();
updateLabels();
drawWave();
updateBuilder();
loadLocalVoices();
refreshIcons();
