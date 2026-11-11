/**
 * 界面冒烟测试：打开声线实验室页面，走一遍「导入音频 → 选段 → 填文字 → 建音色」，
 * 检查没有 JS 报错、按钮状态正确、布局不溢出，并截图。
 *
 * 需要服务已经在运行（python voice_server.py）以及 playwright：
 *   npm i -D playwright && npx playwright install chromium
 *   node tools/ui_smoke_test.cjs            # 默认 http://localhost:8000
 *   VOICE_LAB_URL=http://localhost:8100 node tools/ui_smoke_test.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const baseUrl = process.env.VOICE_LAB_URL || 'http://localhost:8000';
const output = path.resolve(__dirname, '..', '.test-output');
const sample = process.env.SAMPLE_AUDIO || path.resolve(__dirname, '..', '标记音.mp4');
const browserPath = process.env.BROWSER_PATH || undefined;

async function noOverflow(page) {
  const result = await page.evaluate(() => ({
    width: window.innerWidth,
    body: document.documentElement.scrollWidth,
    overflow: [...document.querySelectorAll('.main-panel input, .main-panel select, .main-panel textarea, .main-panel button, .top-actions span')]
      .filter(element => element.getClientRects().length && element.getBoundingClientRect().right > innerWidth + 1)
      .map(element => element.id || element.className)
  }));
  assert(result.body <= result.width + 1, JSON.stringify(result));
  assert.deepEqual(result.overflow, []);
}

async function main() {
  await fs.mkdir(output, { recursive: true });
  const status = await (await fetch(`${baseUrl}/api/status`)).json();
  console.log('引擎状态：', status.engine, '-', status.detail);

  const launch = browserPath ? { executablePath: browserPath } : {};
  const browser = await chromium.launch({ ...launch, headless: true, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });

    await page.goto(baseUrl, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.getElementById('engineBadge').textContent !== '检测引擎…');
    assert(await page.locator('#createCloneBtn').isDisabled(), '没有音频时“建立克隆音色”应该是禁用的');
    assert(await page.locator('#speechEditor').isHidden(), '没有音色时朗读区应该隐藏');
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'clone-empty.png'), fullPage: true });
    console.log('✓ 空状态渲染正常');

    await page.locator('#fileInput').setInputFiles(sample);
    await page.waitForFunction(() => {
      const chips = document.querySelectorAll('.segment-chip');
      return chips.length > 0;
    }, { timeout: 180000 });
    await page.waitForFunction(() => !document.getElementById('createCloneBtn').disabled, { timeout: 60000 });
    const segmentCount = await page.locator('.segment-chip').count();
    assert(segmentCount > 0, '应该给出候选片段');
    console.log(`✓ 分析完成，候选片段 ${segmentCount} 个`);

    await page.waitForFunction(() => document.getElementById('cloneText').value.length > 0, { timeout: 120000 }).catch(() => {
      console.log('! 没有自动识别出文字（未安装 faster-whisper 时正常），手动填写');
    });
    if (!(await page.locator('#cloneText').inputValue())) {
      await page.locator('#cloneText').fill('这是测试用的参考文字。');
    }
    if (!(await page.locator('#cloneName').inputValue())) {
      await page.locator('#cloneName').fill('冒烟测试音色');
    }
    await page.locator('#cloneName').fill('冒烟测试音色');
    await page.screenshot({ path: path.join(output, 'clone-analyzed.png'), fullPage: true });

    await page.locator('#createCloneBtn').click();
    await page.waitForFunction(() => !document.getElementById('speechEditor').hidden, { timeout: 120000 });
    const activeName = await page.locator('#activeCloneName').textContent();
    assert(activeName.includes('冒烟测试音色'), activeName);
    console.log('✓ 克隆音色已建立：', activeName);

    const listCount = await page.locator('#cloneList .saved-voice').count();
    assert(listCount >= 1, '音色应该出现在“我的克隆音色”列表里');
    assert(!(await page.locator('#generateSpeechBtn').isDisabled()) === false, '没有文字时生成按钮应禁用');
    await page.locator('#speechText').fill('你好，这是冒烟测试的朗读文字。');
    await page.waitForFunction(() => !document.getElementById('generateSpeechBtn').disabled, { timeout: 20000 });
    await page.locator('.style-list .preset[data-style="broadcast"]').click();
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'clone-ready.png'), fullPage: true });
    console.log('✓ 朗读区就绪，风格切换正常');

    await page.setViewportSize({ width: 420, height: 900 });
    await noOverflow(page);
    await page.screenshot({ path: path.join(output, 'clone-mobile.png'), fullPage: true });
    console.log('✓ 移动端布局不溢出');

    assert.deepEqual(errors, [], `页面有 JS 报错：${errors.join(' | ')}`);
    console.log('\n全部通过 ✔（截图在 .test-output/）');
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error('测试失败：', error);
  process.exit(1);
});
