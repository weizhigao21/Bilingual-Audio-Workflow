# Fish-Speech 1.4B 独立部署方案（Windows）

> 日期：2026-08-20　｜　状态：待执行　｜　验证目标：音质 ≥ 微软 Edge + 速度 5~10x 实时
> 关联文档：《TTS提速方案.md》（本方案是其中阶段 1 的独立项目落地版）

---

## 0. 方案定位

在**独立项目**（不触碰现有双语音声工作流代码）中部署 Fish-Speech 1.4B 推理服务，用 curl / 脚本验证**音质**与**速度**达标后，再决定是否接入工作流。

```
独立部署项目:  G:\code\fish-speech-deploy\
                ├── fish-speech\        # 官方源码（git clone）
                ├── env\                # Python 3.10 虚拟环境
                └── test\               # 验证脚本（本方案 §6）
服务端口:       8001（避开 GPT-SoVITS 的 8000）
模型:           fishaudio/fish-speech-1.5（1.4B 中英双语，非 0.3B 中文版）
```

---

## 1. 前置核查（已完成 ✅）

| 项 | 状态 | 说明 |
|---|---|---|
| GPU | ✅ RTX 2080 Ti 11GB | Turing 架构，满足 ≥6GB 要求 |
| 驱动 | ✅ 610.62 | 支持 CUDA 12.1~12.6，PyTorch cu124 可用 |
| git | ✅ 2.54.0 | |
| conda | ⚠️ 不在 PATH | 用你部署 GPT-SoVITS 时的环境管理方式（Anaconda Prompt / 系统 Python 3.10） |
| 磁盘 | 需 ≥15GB | 源码 + 模型(~1.6GB) + 依赖 |
| 端口 | 8000 被 GPT-SoVITS 占用 | 本服务用 **8001** |

---

## 2. 环境准备（S1）

```bash
# 方式 A：conda（若可用）
conda create -n fish-speech python=3.10 -y
conda activate fish-speech

# 方式 B：venv（系统已有 Python 3.10 时）
cd G:\code\fish-speech-deploy
py -3.10 -m venv env
env\Scripts\activate
```

---

## 3. 拉取源码 + 安装（S2~S4）

```bash
cd G:\code\fish-speech-deploy
# 官方仓库（网络不佳可换 gitclone 镜像: https://gitclone.com/github.com/fishaudio/fish-speech.git）
git clone https://github.com/fishaudio/fish-speech.git
cd fish-speech

# PyTorch（CUDA 12.4；驱动 610.62 兼容）
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Fish-Speech 本体
pip install -e .

# 可选：Windows Triton 加速（torch.compile 生效需要，Turing 架构收益视版本而定）
pip install https://github.com/AnyaCoder/fish-speech/releases/download/v0.1.0/triton_windows-0.1.0-py3-none-any.whl
```

---

## 4. 下载模型（S5，国内网络走 hf-mirror）

```bash
# 方式 A：huggingface-cli（推荐）
set HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download fishaudio/fish-speech-1.5 --local-dir checkpoints/fish-speech-1.5

# 方式 B：git clone（网络差时）
git clone https://hf-mirror.com/fishaudio/fish-speech-1.5 checkpoints/fish-speech-1.5

# 方式 C：浏览器手动下载（hf-mirror.com/fishaudio/fish-speech-1.5/tree/main）
#   需下载 2 个文件放入 checkpoints\fish-speech-1.5\：
#   ① model.pth                        （LLaMA 文本→语义，~1.4GB）
#   ② firefly-gan-vq-fsq-8x1024-21hz-generator.pth  （VQGAN 声码器，~180MB）
```

✅ 完成后目录结构应为：
```
checkpoints\fish-speech-1.5\
    ├── model.pth
    ├── firefly-gan-vq-fsq-8x1024-21hz-generator.pth
    └── (其他配置 json，若有)
```

---

## 5. 启动 API 服务（S6）

**新版命令**（仓库重构后，OpenAI 兼容接口）：
```bash
python -m tools.api_server --host 127.0.0.1 --port 8001 --half
```

**旧版命令**（若上面报 ModuleNotFoundError）：
```bash
python tools/api_server.py --listen 127.0.0.1:8001 --half
```

- `--half`：FP16 推理，显存 ~4-6GB，2080 Ti 11GB 充裕（**无需 int8**）
- 首次启动有 **60~90s CUDA 内核编译**，日志出现 `Uvicorn running on http://127.0.0.1:8001` 即就绪，**勿中途强杀**
- 浏览器打开 `http://127.0.0.1:8001/docs` 可看 Swagger 接口清单（确认路由是 `/v1/audio/speech` 还是自定义格式，§7 据此适配）

**一键启动脚本**（存为 `G:\code\fish-speech-deploy\start_api.bat`，注意 cmd 用 GBK 编码）：
```bat
@echo off
chcp 936 >nul
cd /d %~dp0fish-speech
call conda activate fish-speech
echo Starting Fish-Speech API on 127.0.0.1:8001 ...
echo First launch may take 60-90s (CUDA compile), please wait...
python -m tools.api_server --host 127.0.0.1 --port 8001 --half
pause
```

---

## 6. 验证（S7）

### 6.1 连通性
```bash
curl http://127.0.0.1:8001/v1/models
```

### 6.2 合成一段测试音频（OpenAI 兼容格式）
```bash
curl -X POST http://127.0.0.1:8001/v1/audio/speech ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"fish-speech-1.5\",\"input\":\"今天天气不错，我们一起去公园散步吧。\",\"response_format\":\"wav\"}" ^
  -o test\sample.wav
```

### 6.3 速度基准（RTF 实测，存为 `test\bench.py`）
```python
# -*- coding: utf-8 -*-
"""Fish-Speech 速度基准：RTF = 合成音频时长 / 处理耗时。RTF < 1 即快于实时。"""
import time, requests, wave, io

URL = "http://127.0.0.1:8001/v1/audio/speech"
TEXT = "人工智能正在改变我们的工作方式，这是语音合成技术的实际应用测试。" * 3  # ~60字

t0 = time.time()
resp = requests.post(URL, json={"model": "fish-speech-1.5", "input": TEXT, "response_format": "wav"}, timeout=120)
elapsed = time.time() - t0
assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"

with wave.open(io.BytesIO(resp.content)) as w:
    dur = w.getnframes() / w.getframerate()
print(f"文本长度: {len(TEXT)} 字 | 音频时长: {dur:.2f}s | 处理耗时: {elapsed:.2f}s")
print(f"RTF = {elapsed/dur:.3f}  (越接近 0 越快，<0.2 即 5x 实时)")

with open("test/bench_out.wav", "wb") as f:
    f.write(resp.content)
print("已保存 test/bench_out.wav，请试听对比 Edge TTS 音质")
```

---

## 7. 验收标准

| 维度 | 达标线 | 判定方法 |
|---|---|---|
| 音质 | ≥ Edge TTS（zh-CN-XiaoxiaoNeural） | 同一段文本分别用 Edge 与本服务合成，双盲试听 |
| 速度 | RTF ≤ 0.2（即 5x 实时以上） | §6.3 bench.py 输出 |
| 稳定性 | 连续 50 句不失败 | 循环调用 §6.2 |

**若音质不达标** → 切备选 CosyVoice 2（中文 MOS 4.7）；**若速度不达标** → 检查 `--half` 是否生效、安装 triton-windows 后加 `--compile`。

---

## 8. 接入现有工作流的接口契约（下一步，本次不实施）

确认 §5 实际路由后，适配层改动（供参考，独立验证通过后再动）：
- `src/steps/tts_utils.py` 新增 `fish_tts_task()`：POST `/v1/audio/speech`（OpenAI 兼容），复用 `_session` / 缓存
- `src/steps/tts_worker.py` 新增 `tts_mode=="fish"` 分支，并发数按显存余量设 4~8，**无微软限流闸门**
- `src/config.py` / `src/widgets.py` 增加 fish 配置项与引擎选项
- 详见《TTS提速方案.md》§4.3 文件级改动点

---

## 9. Windows 避坑清单

| 坑 | 现象 | 对策 |
|---|---|---|
| CUDA 编译久 | 启动卡 1~2 分钟 | 正常，等 `Uvicorn running` 出现 |
| 端口冲突 | 8001 被占 | `netstat -ano \| findstr 8001` 查占用，换端口 |
| hf 下载慢/失败 | 卡在 huggingface | 用 hf-mirror（§4）或迅雷手动下 model.pth |
| torch 与驱动不匹配 | CUDA not available | `python -c "import torch;print(torch.cuda.is_available())"` 应为 True |
| conda 命令找不到 | 'conda' 不是内部命令 | 用 Anaconda Prompt 执行，或改用 venv |
| 长文本 OOM | 显存溢出 | 字幕已按句切分（单句 <50 字），天然规避；仍报错则缩短 max_new_tokens |
| 音质差 | 听感机械 | 确认加载的是 1.4B（checkpoints/fish-speech-1.5/model.pth ~1.4GB），勿误用 0.3B |

---

## 10. 参考

- 官方仓库：https://github.com/fishaudio/fish-speech
- 模型镜像：https://hf-mirror.com/fishaudio/fish-speech-1.5
- Windows Triton：https://github.com/AnyaCoder/fish-speech/releases/tag/v0.1.0
- 文档：https://deepwiki.com/fishaudio/fish-speech/2-installation-and-setup
