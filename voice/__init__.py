"""声线实验室 - 本机声音克隆引擎。

这个包把「导入音频 → 克隆音色 → 用克隆音色朗读」的整套流程搬到本机 Python 服务里，
不再依赖浏览器里那套「本机 Windows 朗读 + 简单滤波」的近似方案。

组成：
- audio_io   读写 WAV、重采样
- dsp        定制的语音信号处理（PSOLA 变调、共振峰搬迁、频谱包络匹配、动态处理）
- segment    断句、VAD、自动挑选最适合做克隆的参考片段
- engines    AI 克隆引擎（ZipVoice，123M 参数，4GB 显存可跑）与轻量引擎
- processor  把合成结果按参考音色的特征做后处理
- store      克隆音色库（参考音频 + 转写 + 特征，落盘在 data/clones）
- service    给前端用的 HTTP 接口
"""

__version__ = "2.0.0"
