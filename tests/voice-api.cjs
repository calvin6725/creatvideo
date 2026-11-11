/**
 * 接口级冒烟测试：不需要浏览器，直接打本机服务的 API。
 * 覆盖：状态、导入音频分析（选段）、建立音色、列出音色、参考音频回放、删除音色。
 *
 *   node tests/voice-api.cjs
 *   VOICE_LAB_URL=http://localhost:8100 SAMPLE_AUDIO=./标记音.mp4 node tests/voice-api.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const baseUrl = process.env.VOICE_LAB_URL || 'http://localhost:8000';
const sample = process.env.SAMPLE_AUDIO || path.resolve(__dirname, '..', '标记音.mp4');

function inspectWav(bytes) {
  assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
  assert.equal(bytes.toString('ascii', 8, 12), 'WAVE');
  let byteRate = 0;
  let dataLength = 0;
  for (let offset = 12; offset + 8 <= bytes.length;) {
    const tag = bytes.toString('ascii', offset, offset + 4);
    const length = bytes.readUInt32LE(offset + 4);
    if (tag === 'fmt ') byteRate = bytes.readUInt32LE(offset + 16);
    if (tag === 'data') { dataLength = length; break; }
    offset += 8 + length + (length % 2);
  }
  assert(byteRate > 0 && dataLength > 0, '参考音频应该是有效 WAV');
  return { seconds: dataLength / byteRate };
}

async function main() {
  const status = await (await fetch(`${baseUrl}/api/status`)).json();
  console.log(`✓ 引擎：${status.engine} - ${status.detail}`);
  assert(['ai', 'fallback', 'none'].includes(status.engine));
  assert(status.prompts && status.prompts.min > 0);

  const raw = await fs.readFile(sample);
  const analysis = await (await fetch(`${baseUrl}/api/clone/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(path.basename(sample)) },
    body: raw
  })).json();
  assert(analysis.token, '应该返回上传 token');
  assert(analysis.best && analysis.best.stop > analysis.best.start, '应该挑出最佳片段');
  assert(analysis.segments.length > 0, '应该有候选片段');
  console.log(`✓ 分析完成：${analysis.duration}s，最佳片段 ${analysis.best.start}s-${analysis.best.stop}s（${analysis.segments.length} 个候选）`);

  const name = `接口测试音色-${Date.now()}`;
  const createResponse = await fetch(`${baseUrl}/api/clones`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      token: analysis.token,
      filename: path.basename(sample),
      name,
      text: '这是接口测试用的参考文字。',
      start: analysis.best.start,
      stop: analysis.best.stop
    })
  });
  const createBody = await createResponse.text();
  assert(createResponse.ok, `建立音色失败：${createResponse.status} ${createBody}`);
  const clone = JSON.parse(createBody);
  assert.equal(clone.name, name);
  assert(clone.seconds > 0.5);
  console.log(`✓ 音色已建立：${clone.name}（${clone.seconds}s，${clone.rate}Hz，基频 ${clone.features?.f0}Hz）`);

  const list = await (await fetch(`${baseUrl}/api/clones`)).json();
  assert(list.clones.some(item => item.id === clone.id), '音色应该出现在列表里');

  const reference = Buffer.from(await (await fetch(`${baseUrl}/api/clones/${clone.id}/reference`)).arrayBuffer());
  const info = inspectWav(reference);
  assert(info.seconds > 0.5);
  console.log(`✓ 参考音频可回放（${info.seconds.toFixed(2)}s）`);

  // 没有参考文字时必须拒绝（否则克隆会跑偏）
  const missingText = await fetch(`${baseUrl}/api/clones`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: analysis.token, name: '缺文字', text: '', start: analysis.best.start, stop: analysis.best.stop })
  });
  assert.equal(missingText.status, 400);
  console.log('✓ 缺少参考文字会被正确拒绝');

  const updateResponse = await fetch(`${baseUrl}/api/clones/${clone.id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: `${name}-改名` })
  });
  assert((await updateResponse.json()).name === `${name}-改名`);
  console.log('✓ 改名成功');

  assert.equal((await fetch(`${baseUrl}/api/clones/${clone.id}`, { method: 'DELETE' })).status, 200);
  const after = await (await fetch(`${baseUrl}/api/clones`)).json();
  assert(!after.clones.some(item => item.id === clone.id));
  console.log('✓ 删除成功');

  if (status.engine === 'none') {
    console.log('! 当前没有可用的合成引擎，跳过合成测试（先安装 AI 克隆引擎或使用 Windows 本机朗读）');
  } else if (status.engine === 'ai') {
    console.log('（合成测试需要真实引擎，请在装有模型的环境执行：node tests/voice-api.cjs --synth）');
  }
  console.log('\n接口测试全部通过 ✔');
}

main().catch(error => {
  console.error('测试失败：', error);
  process.exit(1);
});
