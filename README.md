# 声线实验室 · 本机 AI 声音克隆

导入一段声音（3–10 秒，也可以直接扔进一整段 mp4），程序在本机自动挑出最适合克隆的片段、
识别出这段话说的是什么，然后用 **AI 克隆模型** 用这个音色朗读任意文字。
配上「视频解说剪辑」页，就是你自己的配音 + 剪辑工作台。

全部在本机跑，参考音频、音色和文字都不上传云端。

---

## 快速开始

### 1. 安装 AI 克隆引擎（只需一次）

双击 **`安装AI克隆引擎.bat`**。它会：

1. 在项目里建 `.venv` 虚拟环境（不动系统 Python）；
2. 安装 PyTorch：优先 CUDA 12.1 版本，装不上自动退回 CPU 版；
3. 安装 ZipVoice 推理依赖（核心包 + 可选的语音识别 faster-whisper、音素化 piper_phonemize，
   可选项装不上不影响克隆合成）；
4. 下载 ZipVoice-Distill 模型（约 500 MB）和声码器（约 50 MB）；
5. 打印显卡型号与显存，并写 `voice/config.json`。

后续想换参数可以重跑：

```powershell
python tools\setup_ai.py --mirror     # 国内网络用 hf-mirror.com 下模型
python tools\setup_ai.py --cpu        # 强制 CPU 版 PyTorch（没显卡/驱动有问题时）
python tools\setup_ai.py --skip-model # 只装依赖，不重新下模型
```

> 也可以在页面里点 **「一键安装到本机」**：服务会后台执行同一个安装脚本，
> 页面实时显示日志，装完自动切换成「AI 克隆」。
> 国内网络建议勾选「用镜像下载模型」。

### 2. 打开页面

双击 **`启动声线实验室.bat`** → 浏览器打开 `http://localhost:8000/`。

### 3. 建立克隆音色

1. 左侧「导入音频 / 视频」——支持 WAV / MP3 / M4A / MP4 / WebM（视频会自动取音轨）；
2. 页面会**自动**选出一段最适合克隆的片段（VAD 切段 + 打分：长度、音量、稳定度、清音比例、削波），
   并在波形上高亮，下面「候选片段」可以随时换；
3. 参考文字会自动识别填好（**这段文字必须和音频内容一致**，否则音色会跑偏），也可以手改；
4. 点「建立克隆音色」→ 音色保存到左侧「我的克隆音色」。

### 4. 克隆朗读

输入文字 → 「生成朗读」。长文会自动断句、分批合成再拼接，进度条实时显示。

---

## 针对 4GB 显存（GTX 1050 Ti）做的设计

| 项目 | 选择 | 原因 |
| --- | --- | --- |
| 模型 | ZipVoice-Distill（123M 参数，蒸馏 8 步） | 显存占用最小、速度最快，音色相似度仍是同体量里最好的 |
| 精度 | fp32（可在 `voice/config.json` 改成 `fp16`） | Pascal 架构 fp16 计算反而慢，fp32 更稳 |
| 分块 | 参考 + 生成 ≤ 12 秒/句，显存不足自动降到 8s、5s 重试 | 长句是爆显存的主因，按预算切句基本不会 OOM |
| 模型载入 | 懒加载 + `warmup` 预热按钮 + 结束后 `empty_cache` | 打开页面不占显卡，剪辑时显存可以完全让给别的软件 |
| 参考音频 | 2–8 秒、去掉首尾静音、音量对齐 | 参考过长会变慢且更不像；短而干净最像 |

想进一步省显存/换更好的模型：把其他 ZipVoice 模型（如原版 `zipvoice`）放到 `models/zipvoice/`
（含 `model.pt`、`model.json`、`tokens.txt`），在 `voice/config.json` 里把 `zipvoice_model` 指过去即可。

---

## 目录结构

```
index.html / app.js            声线实验室界面（导入 → 选段 → 建音色 → 朗读）
video.html / video.js          视频解说剪辑（用克隆音色生成解说并导出）
voice_server.py                启动入口：python voice_server.py --port 8000
voice/                         本机服务与处理引擎
  service.py                   HTTP 接口 + 静态文件
  engines.py                   ZipVoice AI 克隆引擎 / SAPI 备选引擎
  processor.py                 音色匹配与成片级润色
  dsp.py                       PSOLA 变调、共振峰搬迁、频响匹配、压缩/齿音
  segment.py                   断句、VAD、参考片段打分
  asr.py                       参考音频自动转写（faster-whisper）
  store.py                     克隆音色库（data/clones）
  audio_io.py / config.py      音频读写、重采样、配置
tools/setup_ai.py              一键安装脚本
tests/                         pytest 自测（不需要 GPU）
```

运行数据（都只在你的电脑上，已被 `.gitignore` 忽略）：

- `data/clones/<id>/` 克隆音色：`clone.json` + `reference.wav`
- `data/uploads/` 导入的原始音频（建立音色后即可删除）
- `data/jobs/` 生成的长文朗读结果
- `models/`、`third_party/` 模型与 ZipVoice 代码

---

## 常用命令

```powershell
# 启动服务（前台，方便看日志）
.venv\Scripts\python.exe voice_server.py --port 8000

# 只是自测（不需要显卡/模型）
.venv\Scripts\python.exe -m pytest tests\test_voice_core.py -q

# 换端口
set CREATVIDEO_PORT=8100 && .venv\Scripts\python.exe voice_server.py
```

配置文件 `voice/config.json` 常用项：

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `engine` | `auto` / `ai` / `fallback` | `auto` |
| `device` | `auto` / `cuda` / `cpu` | `auto` |
| `dtype` | `fp32` / `fp16` | `fp32` |
| `max_duration` | 单块 参考+生成 秒数上限 | `12` |
| `retry_max_durations` | 显存不足时的重试预算 | `[8, 5]` |
| `prompt_min/max_seconds` | 参考片段长度范围 | `2 / 8` |
| `asr_model` | faster-whisper 模型大小 | `small` |
| `num_step` | 扩散步数（0 = 按模型自动：蒸馏 8 步） | `0` |

---

## 排查

| 现象 | 处理 |
| --- | --- |
| 页面显示「未安装 PyTorch」 | 双击 `安装AI克隆引擎.bat`；或 `python tools\setup_ai.py` |
| 显示「未找到 ZipVoice 模型」 | `python tools\setup_ai.py --skip-torch --mirror` |
| 合成报「显存不足」 | 引擎会自动缩小分块并降级 fp16；仍失败就把 `voice/config.json` 的 `max_duration` 调到 8，关掉游戏/剪辑软件 |
| 想立刻把显卡让给别的软件 | 页面点 🔌「释放显存」，或 `POST /api/release` |
| 声音不像 | 换一个更干净的 3–6 秒片段；**核对参考文字和音频是否一致**；参考音频别带背景音乐 |
| 自动识别不可用 | `pip install faster-whisper`，或直接手动填参考文字 |
| 打不开 localhost:8000 | 用 `启动声线实验室.bat` 启动；或看命令行窗口的报错 |

参考音频的版权与授权请自行确认：只用本人声音，或已获得明确授权的声音。
