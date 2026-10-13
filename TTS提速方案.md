# TTS 提速方案（评审稿）

> 日期：2026-08-20　｜　状态：待评审（v2 修订：新增质量对标约束）　｜　约束：RTX 2080 Ti 11GB / Windows / 音色无硬性要求，但**音质须接近微软 Edge TTS 水平**

---

## 1. 背景与目标

批量双语配音任务中，步骤 2（语音生成）是全流程主要耗时瓶颈。当前主力走 **Edge TTS**，几百句字幕按句逐个请求，每句 2~3 秒（含网络往返），8 并发下 300 句约需 10~15 分钟。

**目标**：将步骤 2 吞吐提升 5~10 倍，且尽量不引入新的运维负担。

---

## 2. 瓶颈诊断（基于代码现状）

| 通道 | 根因 | 关键位置 |
|---|---|---|
| Edge TTS（主力） | 每句 = 1 次 WebSocket 请求，**吞吐被"请求次数 × 网络往返"锁死**；微软限流把并发卡在 8 | `src/steps/tts_utils.py:28`（`_edge_max_concurrent=8`） |
| API 模式（本机 GPT-SoVITS v4，127.0.0.1:8000） | GPT-SoVITS 1.5B 自回归推理本身**慢于实时**（RTF≈0.5~1）；`infer_bulk` 是服务端循环逐条，非真 batch；`sample_steps=16` 是慢档 | `src/steps/tts_utils.py:113-135, 218-240` |

结论：**Edge 慢在网络、GPT-SoVITS 慢在模型**，调并发收益都有限。

---

## 3. 质量对标：微软 Edge 是什么水平（关键约束）

Edge TTS 用的就是 **Azure Neural 神经语音**（如 zh-CN-XiaoxiaoNeural），中文自然度约 **MOS 4.0~4.2**。2026 年开源模型横评实测（MOS，满分 5）：

| 引擎 | 中文自然度 | 中英混读 | 与微软对比 |
|---|---|---|---|
| **CosyVoice 2**（阿里通义） | **4.7（开源第一）** | 4.5 | ✅ 明确超过 |
| **Higgs Audio V2**（Boson AI） | 4.6 | 优 | ✅ 超过 |
| **Fish-Speech 1.5**（1.4B） | 4.5 | 优 | ✅ 超过（略逊 CosyVoice） |
| **Fish-Speech 0.3B 中文** | ~4.0（小模型打折） | 一般 | ⚠️ 临界，**不推荐** |
| **VibeVoice-1.5B**（微软开源） | 4.2 | 良 | ≈ 持平 |
| Kokoro-82M | 3.9 | 一般 | ❌ 不达标 |
| ChatTTS | 3.5~3.9 | 差 | ❌ 不达标 |

> 结论：**能稳过"接近微软"这道线的只有 CosyVoice 2 / Higgs Audio V2 / Fish-Speech 1.4B 三档**。ChatTTS、Kokoro、Fish-Speech 0.3B 出局——之前"快就行"阶段建议的 0.3B 小模型，在质量约束下不成立。

---

## 3b. 候选方案对比（v2 修订）

| 方案 | 相对实时速度 | 中文质量 | 部署成本 | 改造量 | 结论 |
|---|---|---|---|---|---|
| Edge TTS 并发微调 | 提升有限（≤1.5x） | 优（基准） | 0 | 0 | 今天顺手试 |
| GPT-SoVITS 调参（sample_steps 16→8） | ~2x | 良（低于基准） | 0 | 改 1 行 | 今天顺手试 |
| **Fish-Speech 1.5（1.4B int8）本地部署** ⭐ | **5~10x** | 4.5（>微软） | 低（pip + 1 条命令） | 中（4 文件） | **主推荐：速度×质量平衡** |
| **CosyVoice 2（0.5B）本地部署** | 2~4x（流式首包 150ms） | **4.7（中文第一）** | 中（Windows 需社区版/Docker） | 中 | 质量天花板，可商用 |
| Azure 官方 TTS API | 高并发（无 Edge 限流） | **= Edge（同模型）** | 低（注册，免费层 50 万字符/月） | 低（换 key） | 零质量风险的兜底 |
| Higgs Audio V2 | 中等 | 4.6（情感最强） | 中 | 中 | 备选（新项目生态待验证） |

硬件核查：Fish-Speech 1.4B int8 ≈ 4~6GB、CosyVoice 2 ≈ 4.9GB（fp16）—— **2080 Ti（11GB）均可跑**，但与本机 GPT-SoVITS（~3-4GB）同跑会接近显存上限（见 R1）。

---

## 4. 推荐方案：Fish-Speech 1.5 本地引擎

### 4.1 为什么是它

- **质量达标**：1.4B 中英双语模型中文自然度 MOS 4.5，高于微软 Edge（~4.0-4.2），多语种混读好（适合中英双语配音）。**注意：必须用 1.4B，0.3B 中文模型质量打折到临界线，不满足约束。**
- **速度快**：1.4B int8 在 2080 Ti 上 5~10x 实时（10 秒音频约 1~2 秒合成），无网络往返。
- 音色无所谓 → 不需要克隆能力，直接用默认音色。
- 部署轻：`pip install -e .` + `tools/api_server.py` 一条命令，OpenAI 兼容 `/v1/audio/speech`。
- 字幕场景每句一次请求天然适配。

> ⚠️ **协议提示**：Fish-Speech 为 CC BY-NC-SA 许可，**仅限个人/非商用**。个人自用没问题；若未来商用配音，需切 CosyVoice 2（Apache 2.0）。

### 4.2 部署步骤（阶段 1，半天）

```bash
# 建议独立 conda 环境，与本机 GPT-SoVITS 环境隔离
conda create -n fish python=3.10 -y && conda activate fish
git clone https://github.com/fishaudio/fish-speech.git
cd fish-speech
pip install -e .
# 下载模型 —— 务必 1.4B 中英双语（int8 量化省显存），不要用 0.3B 中文
python tools/download_models.py --repo_id fishaudio/fish-speech-1.5
# 启动 API 服务（端口避开已占用的 8000；首次启动有 60~90s CUDA 编译）
python tools/api_server.py --host 127.0.0.1 --port 8001 --half
# 验证
curl -X POST http://127.0.0.1:8001/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"fish-speech-1.5","input":"你好，测试。","response_format":"wav"}' \
  -o test.wav
```

> ⚠️ 不同版本 API 路由/字段略有差异（`/v1/audio/speech` vs `/v1/tts`），**部署后以 curl 实测返回为准**，适配器按实测格式实现。

### 4.3 代码改造（模块图 + 文件级改动点）

```
现有链路（不改）:  batch_executor.py → step2_tts.py(TTSBridgeWorker) → TTSWorker.run()
                                                                        │ tts_mode 分支
                                        ┌───────────────┬───────────────┼──────────────┐
                                   tts_mode="edge"  tts_mode="api"  tts_mode="fish"(新增)
                                        │               │               │
                                   _run_edge_tts   _run_api_tts   _run_fish_tts(新增)
                                        │               │               │
                                   edge_tts_task   tts_task/       fish_tts_task(新增)
                                       │        tts_bulk_task          │
                                       │               │               │
                                       └────── AudioCache（SQLite+文件，复用，不改）──────┘
```

| # | 文件 | 改动 | 量级 |
|---|---|---|---|
| 1 | `src/config.py` | `DEFAULT_CONFIG["tts"]` 新增：`fish_api_url`(默认 `http://127.0.0.1:8001`)、`fish_model`(默认 `fish-speech-1.5`)、`fish_voice`(参考音频路径，可空)、`fish_threads`(默认 4) | ~8 行 |
| 2 | `src/steps/tts_utils.py` | 新增 `fish_tts_task()`：POST `/v1/audio/speech`，复用 `_session`/`_download_audio`/缓存；参数适配 OpenAI 兼容格式 | ~50 行 |
| 3 | `src/steps/tts_worker.py` | `run()` 增加 `elif tts_mode == "fish"` 分支（L286 附近）；新增 `_run_fish_tts()`：参考 `_run_api_tts`（L430）用线程池 + 队列并发，但**无微软限流闸门**，并发数 = `fish_threads`（本地 GPU 推理，线程数按显存余量设 4~8） | ~80 行 |
| 4 | `src/widgets.py` | `TTSConfigPanel`（L421）：mode_combo 增加 `("Fish-Speech（本地）", "fish")`；条件显示 URL/模型/并发数配置行；`_save`（L654）写入 `fish_*` 字段 | ~40 行 |
| 5 | `README.md` | TTS 配置表补充 Fish 模式说明 | ~10 行 |
| — | `batch_executor.py` / `step2_tts.py` / `tts_cache.py` | **不改**（TTSWorker 接口不变，pipeline 调度自动复用；缓存天然复用） | 0 |

**Scope 边界**：只新增 `fish` 分支，edge/api 现有逻辑零改动；配置项全部带默认值，旧配置文件 `_deep_merge` 后自动补全，无迁移成本。

---

## 5. 快速验证项（阶段 0，今天可做，不换引擎）

| 项 | 做法 | 预期 |
|---|---|---|
| Edge 并发微调 | GUI 把「全局并发上限」8→12、「线程池」5→8，跑一小批实测 | 若微软不限流则 +30~50%，限流则回退 |
| GPT-SoVITS 调参 | `tts_utils.py:125` 的 `sample_steps` 16→8（或服务器端改），客户端 `use_multi_api` 开启多 worker | ~2x，质量听感验收 |

> 阶段 0 验证的结论决定是否还需要阶段 1/2（若 Edge 12 并发稳定 + GPT-SoVITS 2x 已够用，可暂缓上 Fish-Speech）。

---

## 6. 实施计划（分阶段，每阶段独立验证）

- **阶段 0**（今天）：Edge 并发微调 + GPT-SoVITS 调参 → 实测吞吐 → 决策是否继续
- **阶段 1**（半天）：部署 Fish-Speech 1.5 → curl 验证 API 格式 → 确定适配器参数
- **阶段 2**（半天）：代码改造 4 文件 → 本机跑通一个小任务（10 句字幕）→ 听感 + 速度验收
- **阶段 3**：README 同步 → PyInstaller 打包 → cmd 手动验证 → 等确认后 commit/push

---

## 7. 风险与备选

| 编号 | 风险 | 对策 |
|---|---|---|
| R1 | 2080 Ti 同时跑 GPT-SoVITS(8000) + Fish-Speech(8001) 显存可能 7~10GB，接近 11GB 上限 | 用 **1.4B int8**（~4-6GB）；必要时二选一，GPT-SoVITS 与 Fish 不同时开 |
| R2 | Fish-Speech 首次启动 CUDA 编译 60~90s，易误判卡死 | 文档标注，日志看到 `Running on` 即为就绪 |
| R3 | 不同版本 API 路由/字段差异 | 部署后先 curl 实测，适配器按实测写 |
| R4 | 本地推理占用 GPU 时，步骤 1（whisper）与步骤 3（混音）同机并行可能互相抢显存/CPU | 建议 Fish 服务与 whisper 分批跑；混音是 CPU 密集不受影响 |
| R5 | Fish-Speech 为 **CC BY-NC-SA（非商用）** | 个人自用无碍；未来商用切 CosyVoice 2（Apache 2.0），适配器接口可复用 |
| R6 | 若 Fish-Speech 听感验收不达标 | 备选 **CosyVoice 2**（中文 MOS 4.7，质量天花板，Windows 用 v3ucn 社区版或 WSL/Docker）；再不行走 **Azure 官方 API**（质量 100% 等于 Edge + 高并发） |
