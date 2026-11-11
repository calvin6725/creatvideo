const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const baseUrl = process.env.VOICE_LAB_URL || 'http://localhost:8000';
const output = path.resolve(__dirname, '..', '.test-output');
const browserPath = process.env.BROWSER_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';

function inspectWav(bytes) {
  assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
  assert.equal(bytes.toString('ascii', 8, 12), 'WAVE');
  let byteRate = 0, dataOffset = 0, dataLength = 0;
  for (let offset = 12; offset + 8 <= bytes.length;) {
    const tag = bytes.toString('ascii', offset, offset + 4);
    const length = bytes.readUInt32LE(offset + 4);
    if (tag === 'fmt ') byteRate = bytes.readUInt32LE(offset + 16);
    if (tag === 'data') { dataOffset = offset + 8; dataLength = length; break; }
    offset += 8 + length + (length % 2);
  }
  assert(byteRate > 0 && dataLength > 0);
  assert(dataOffset + dataLength <= bytes.length);
  let peak = 0, tailPeak = 0;
  for (let offset = dataOffset; offset + 1 < dataOffset + dataLength; offset += 2) {
    const value = Math.abs(bytes.readInt16LE(offset));
    peak = Math.max(peak, value);
    if (offset > dataOffset + dataLength * .75) tailPeak = Math.max(tailPeak, value);
  }
  assert(peak > 100, 'WAV must contain audible samples');
  return { duration: dataLength / byteRate, peak, tailPeak, bytes: bytes.length };
}

async function noOverflow(page) {
  const result = await page.evaluate(() => ({
    width: window.innerWidth,
    body: document.documentElement.scrollWidth,
    overflow: [...document.querySelectorAll('.main-panel input, .main-panel select, .main-panel textarea, .main-panel button')]
      .filter(element => element.getClientRects().length && element.getBoundingClientRect().right > innerWidth + 1)
      .map(element => element.id)
  }));
  assert(result.body <= result.width + 1, JSON.stringify(result));
  assert.deepEqual(result.overflow, []);
}

async function main() {
  await fs.mkdir(output, { recursive: true });
  const voiceResponse = await fetch(`${baseUrl}/api/voices`);
  assert(voiceResponse.ok);
  const { voices } = await voiceResponse.json();
  const voice = voices.find(item => /Huihui|Chinese|zh/i.test(`${item.name} ${item.language}`)) || voices[0];
  assert(voice, 'A local Windows voice is required');
  const response = await fetch(`${baseUrl}/api/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text: '\u4f60\u597d\uff0c\u8fd9\u662f\u58f0\u7ebf\u5b9e\u9a8c\u5ba4\u7684\u58f0\u97f3\u53c2\u8003\u3002\u6211\u4eec\u7528\u65b0\u7684\u97f3\u8272\u8bb2\u8ff0\u6545\u4e8b\uff0c\u5e76\u4fdd\u7559\u6700\u540e\u4e00\u53e5\u7684\u5b8c\u6574\u5185\u5bb9\u3002',
      voice: voice.name
    })
  });
  assert(response.ok);
  const referenceBytes = Buffer.from(await response.arrayBuffer());
  const referenceInfo = inspectWav(referenceBytes);
  const referencePath = path.join(output, 'reference.wav');
  await fs.writeFile(referencePath, referenceBytes);

  const browser = await chromium.launch({ executablePath: browserPath, headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, acceptDownloads: true });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(baseUrl);
    await page.waitForFunction(() => !document.getElementById('baseVoice').disabled);
    assert(await page.locator('#createVoiceBtn').isDisabled());
    assert(await page.locator('#speechEditor').isHidden());
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'desktop-empty.png'), fullPage: true });
    await page.locator('#fileInput').setInputFiles(referencePath);
    await page.waitForFunction(() => !document.getElementById('createVoiceBtn').disabled, null, { timeout: 20000 });
    await page.locator('#pitch').fill('-4');
    await page.locator('#pitch').dispatchEvent('input');
    await page.locator('#speed').fill('-20');
    await page.locator('#speed').dispatchEvent('input');
    await page.locator('#warmth').fill('30');
    await page.locator('#warmth').dispatchEvent('input');
    await page.locator('#voiceName').fill('Test custom voice');

    const sourceDownload = page.waitForEvent('download');
    await page.locator('#exportBtn').click();
    const sourceFile = await sourceDownload;
    const processedPath = path.join(output, 'processed-reference.wav');
    await sourceFile.saveAs(processedPath);
    const processedInfo = inspectWav(await fs.readFile(processedPath));
    const expectedDuration = referenceInfo.duration / (Math.pow(2, -4 / 12) * .8);
    assert(Math.abs(processedInfo.duration - expectedDuration - .06) < .1);
    assert(processedInfo.tailPeak > 100, 'The final quarter of slow/lowered audio must not be truncated');

    await page.locator('#createVoiceBtn').click();
    await page.waitForFunction(() => !document.getElementById('speechEditor').hidden && !document.getElementById('voiceName').disabled, null, { timeout: 60000 });
    assert.equal(await page.locator('.saved-voice').count(), 1);
    assert.equal(await page.locator('#activeVoiceName').textContent(), 'Test custom voice');
    assert(await page.locator('#generateSpeechBtn').isDisabled());
    await page.locator('#speechText').fill('\u4f60\u597d\uff0c\u8fd9\u6bb5\u6587\u5b57\u5c06\u4f7f\u7528\u65b0\u7684\u97f3\u8272\u6717\u8bfb\u3002\u8fd9\u662f\u6700\u540e\u4e00\u53e5\u8bdd\uff0c\u8bf7\u4fdd\u7559\u5b8c\u6574\u7ed3\u5c3e\u3002');
    await page.locator('#generateSpeechBtn').click();
    await page.waitForFunction(() => !document.getElementById('speechResult').hidden, null, { timeout: 60000 });
    await page.locator('#speechAudio').evaluate(audio => audio.pause());
    assert.equal(await page.locator('#fileName').textContent(), 'reference.wav');
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'desktop-result.png'), fullPage: true });

    const speechDownload = page.waitForEvent('download');
    await page.locator('#exportSpeechBtn').click();
    const speechFile = await speechDownload;
    const speechPath = path.join(output, 'generated-speech.wav');
    await speechFile.saveAs(speechPath);
    const speechInfo = inspectWav(await fs.readFile(speechPath));
    assert(speechInfo.duration > 2);
    assert.equal(await page.locator('.saved-voice').count(), 1);

    await page.locator('#speechText').fill('Changed text');
    assert(await page.locator('#speechResult').isHidden());
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('baseVoice').disabled);
    assert(await page.locator('#speechEditor').isVisible());
    assert.equal(await page.locator('#speechText').inputValue(), 'Changed text');
    assert.equal(await page.locator('#activeVoiceName').textContent(), 'Test custom voice');
    assert(await page.locator('#generateSpeechBtn').isEnabled());
    assert(await page.locator('#createVoiceBtn').isEnabled());
    await page.locator('#brightness').fill('50');
    await page.locator('#brightness').dispatchEvent('input');
    await page.locator('#createVoiceBtn').click();
    await page.waitForFunction(() => !document.getElementById('voiceName').disabled);
    assert.equal(await page.locator('.saved-voice').count(), 1, 'Updating a voice must not duplicate it');
    await page.locator('#voiceName').fill('Second voice');
    await page.locator('#createVoiceBtn').click();
    await page.waitForFunction(() => document.querySelectorAll('.saved-voice').length === 2 && !document.getElementById('voiceName').disabled);
    await page.locator('#fileInput').setInputFiles({
      name: 'other-reference.wav', mimeType: 'audio/wav', buffer: referenceBytes
    });
    await page.waitForFunction(() => !document.getElementById('createVoiceBtn').disabled);
    await page.locator('.saved-voice-select').filter({ hasText: 'Test custom voice' }).click();
    assert.equal(await page.locator('#activeVoiceName').textContent(), 'Test custom voice');
    await page.locator('#createVoiceBtn').click();
    await page.waitForFunction(() => !document.getElementById('voiceName').disabled);
    const savedReference = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('voiceLabProfiles')).find(profile => profile.name === 'Test custom voice').reference.name
    );
    assert.equal(savedReference, 'reference.wav', 'Switching a saved voice must retain its reference when another recording is loaded');
    assert.equal(await page.locator('#fileName').textContent(), 'other-reference.wav', 'The original recording must remain available');

    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'mobile-saved.png'), fullPage: true });
    await page.locator('#speechText').fill('Mobile preview');
    await page.locator('#generateSpeechBtn').click();
    await page.waitForFunction(() => !document.getElementById('speechResult').hidden, null, { timeout: 60000 });
    await page.locator('#speechAudio').evaluate(audio => audio.pause());
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'mobile-result.png'), fullPage: true });

    await page.locator('.saved-voice.active .icon-btn').click();
    assert(await page.locator('#speechEditor').isHidden());
    assert.equal(await page.locator('.saved-voice').count(), 1);
    await page.locator('.saved-voice-select').click();
    assert.deepEqual(pageErrors, []);
    console.log(JSON.stringify({ referenceInfo, processedInfo, speechInfo, pageErrors, output }, null, 2));

    const offlinePage = await context.newPage();
    await offlinePage.route('**/api/voices', route => route.abort());
    await offlinePage.goto(baseUrl);
    await offlinePage.waitForFunction(() => document.getElementById('voiceServiceStatus').textContent.includes('\u91cd\u8bd5'));
    assert(await offlinePage.locator('#generateSpeechBtn').isDisabled());
    await offlinePage.unroute('**/api/voices');
    await offlinePage.locator('#retryVoices').click();
    await offlinePage.waitForFunction(() => !document.getElementById('baseVoice').disabled);
    assert(await offlinePage.locator('#generateSpeechBtn').isEnabled());

    const corruptContext = await browser.newContext();
    const corruptPage = await corruptContext.newPage();
    await corruptPage.addInitScript(() => {
      localStorage.setItem('voiceLabSettings', '{broken');
      localStorage.setItem('voiceLabProfiles', '{"not":"an array"}');
      localStorage.setItem('voiceLabTextDraft', '123');
    });
    await corruptPage.goto(baseUrl);
    await corruptPage.waitForFunction(() => !document.getElementById('baseVoice').disabled);
    assert.equal(await corruptPage.locator('.saved-voice').count(), 0);
    await corruptPage.locator('#fileInput').setInputFiles({
      name: 'invalid.wav', mimeType: 'audio/wav', buffer: Buffer.from('not an audio file')
    });
    await corruptPage.waitForFunction(() => document.getElementById('referenceStatus').textContent !== '\u6b63\u5728\u8bfb\u53d6\u5e76\u5206\u6790\u58f0\u97f3\u2026');
    assert(await corruptPage.locator('#createVoiceBtn').isDisabled());
    console.log('PASS: import, shaping, complete WAV export, save/update/switch/delete voices, TTS WAV, draft persistence, mobile layout, service retry, corrupt storage, invalid audio');
    await corruptContext.close();
    await context.close();
  } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
