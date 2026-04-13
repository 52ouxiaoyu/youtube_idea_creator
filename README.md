# Idea Creator - YouTube Edition

从 YouTube 评论中自动挖掘“真实需求 / 痛点 / 工具机会”的脚本。

它会先抓取公开视频评论，再用本地关键词规则做第一轮筛选，最后把高信号评论交给大模型整理成结构化商机清单，并导出为 Markdown 和 Excel。

## 这套工具适合做什么

- 从热门视频里发现用户抱怨、求助、找替代方案的表达
- 从单条视频里抽取“用户到底卡在哪”
- 把散乱评论整理成可直接查看的需求列表
- 给产品点子、SaaS 选题、自动化脚本、插件方向做素材池

## 工作流

```mermaid
flowchart LR
    A[YouTube 视频 URL / 热门视频] --> B[抓取 CommentThreads]
    B --> C[抓取二级回复]
    C --> D[关键词 + 评分初筛]
    D --> E[按视频去重 / 限量]
    E --> F[LLM 中文结构化分析]
    F --> G[Markdown / Excel 导出]
```

## 核心特性

- 支持单视频模式和热门视频模式
- 使用 `google-api-python-client` 抓取评论线程和二级回复
- 内置“强意图 / 弱意图 / 痛点词”评分过滤，减少无效 token 消耗
- 支持 Ollama、OpenAI、Gemini
- 默认输出中文结果
- 支持 Markdown 和 XLSX 导出
- 支持分页、重试、限速和启动前预检
- 支持自适应停止：当一个视频已经收集到足够多的高信号评论时，会提前收手，避免无限翻页

## 目录结构

```text
youtube_idea_creator/
├── app.py                # 入口与流程编排
├── analyzer.py           # LLM 分析
├── config.py             # 配置与关键词
├── exporter.py           # Markdown / Excel 导出
├── models.py             # 数据结构
├── pain_filter.py       # 评分过滤器
├── preflight.py         # 网络与模型预检
├── youtube_client.py    # YouTube API 抓取
└── README.md
```

## 安装

建议使用 Python 3.11+。

```bash
pip install -r requirements.txt
```

如果你要使用本地 Ollama，请先确保服务已启动：

```bash
ollama serve
```

## 配置

在仓库根目录新建 `.env` 文件，推荐如下配置：

```bash
YOUTUBE_API_KEY=你的_YouTube_Data_API_v3_Key
YOUTUBE_API_ENDPOINT=https://www.googleapis.com

AI_PROVIDER=ollama
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=qwen3.5:9b
AI_API_KEY=ollama

OUTPUT_DIR=outputs
LOG_LEVEL=INFO
SKIP_PREFLIGHT=0
OLLAMA_THINK=false

FILTER_MIN_SCORE=3
POPULAR_FETCH_MULTIPLIER=3
POPULAR_ANALYSIS_FLOOR=3
PREFERRED_CATEGORY_IDS=26,27,28
DEPRIORITIZED_CATEGORY_IDS=1,10,20,23,24
DEDUPE_STATE_PATH=outputs/idea_creator_seen.json
RESET_DEDUPE=0

MAX_COMMENTS_PER_VIDEO=25
MAX_COMMENT_PAGES_PER_VIDEO=20
MAX_REPLY_PAGES_PER_THREAD=5
TARGET_HIGH_SIGNAL_COMMENTS_PER_VIDEO=12

MIN_REQUEST_INTERVAL_SECONDS=1.0
YOUTUBE_HTTP_TIMEOUT_SECONDS=20
MAX_RETRIES=4
BACKOFF_BASE_SECONDS=1.5
MAX_COMMENT_CHARS=700
BATCH_SIZE=8
```

### 配置说明

- `YOUTUBE_API_KEY`：YouTube Data API v3 Key，必填。
- `AI_PROVIDER`：`ollama`、`openai`、`gemini` 三选一。
- `AI_BASE_URL`：本地 Ollama 常用 `http://localhost:11434/v1`。
- `AI_MODEL`：例如 `qwen3.5:9b`、`gpt-4o-mini`。
- `AI_API_KEY`：本地 Ollama 可用占位值 `ollama`；OpenAI / Gemini 请填真实 Key。
- `FILTER_MIN_SCORE`：过滤阈值，越高越保守。
- `POPULAR_ANALYSIS_FLOOR`：热门模式至少分析多少个视频，避免结果太少。
- `POPULAR_FETCH_MULTIPLIER`：热门榜抓取扩展倍率，用来扩大候选池。
- `PREFERRED_CATEGORY_IDS`：优先类别，默认偏向 `Howto & Style / Education / Science & Technology`。
- `DEPRIORITIZED_CATEGORY_IDS`：默认后排的类别，主要是更偏娱乐和泛内容的分类。
- `DEDUPE_STATE_PATH`：保存“已看过的视频 / 评论”的状态文件路径。
- `RESET_DEDUPE`：设为 `1` 时清空去重缓存。
- `MAX_COMMENT_PAGES_PER_VIDEO`：单个视频最多翻多少页顶层评论，防止极端视频耗时过长。
- `MAX_REPLY_PAGES_PER_THREAD`：单条评论下最多翻多少页回复。
- `TARGET_HIGH_SIGNAL_COMMENTS_PER_VIDEO`：达到多少条高信号评论后可以提前停止继续翻页。

## 运行方式

### 1. 单视频模式

抓取某个视频下的评论：

```bash
python -m youtube_idea_creator --video-url "https://www.youtube.com/watch?v=VIDEO_ID"
```

### 2. 热门视频模式

抓取某个地区最热门的视频评论：

```bash
python -m youtube_idea_creator --popular-mode --region-code US --popular-count 10
```

如果你想先小规模试跑，建议：

```bash
python -m youtube_idea_creator --popular-mode --region-code US --popular-count 3 --max-comments-per-video 10
```

### 3. 常用参数

- `--popular-mode`：启用热门视频模式
- `--video-url`：单视频模式输入
- `--region-code`：热门榜地区，默认 `US`
- `--popular-count`：要分析的热门视频数量
- `--category-id`：可选，限定某个视频分类
- `--max-comments-per-video`：每个视频最多保留多少条评论进入 AI 分析
- `--batch-size`：LLM 批次大小
- `--model`：覆盖 `.env` 中的模型名
- `--provider`：覆盖 `.env` 中的 AI 提供商
- `--output-dir`：覆盖输出目录
- `--skip-preflight`：跳过启动前网络 / 模型检查
- `--reset-dedupe`：清空去重缓存

可以查看完整帮助：

```bash
python -m youtube_idea_creator --help
```

## 输出内容

每次运行会在 `outputs/` 下生成两份文件：

- `idea_creator_youtube_<timestamp>.md`
- `idea_creator_youtube_<timestamp>.xlsx`

导出表包含这些字段：

- `视频标题`
- `原评论内容`
- `中文翻译`
- `原评论语言`
- `需求分数`
- `原评论链接`
- `痛点总结`
- `AI 工具构思`
- `预计开发难度`

## 过滤逻辑说明

脚本不会把所有评论都送进大模型。

它会先做本地低成本筛选：

- 先看是否命中明确需求句式，比如 `how can I`、`wish there was`、`is there an app`
- 再看是否命中痛点信号，比如 `broken`、`expensive`、`manually`、`doesn't work`
- 再结合问号、长度、工具词等做加权评分
- 分数不够的评论会直接丢掉，节省 token 和时间

这套策略的目标不是“全量抓完”，而是“尽量少喂垃圾给模型，尽量保留真实需求信号”。

## 热门模式的策略

热门视频模式默认会：

- 优先抓 `Howto & Style / Education / Science & Technology`
- 如果这些类别不够，再补其它热门视频，但会先避开默认后排的娱乐类目
- 先多抓一些候选视频，再按评分挑最值得分析的内容
- 在每个视频里，达到足够多的高信号评论后会提前停止继续翻页

这样做的目的，是在结果数量和运行时长之间找平衡。

## Ollama 使用建议

如果你使用本地 Ollama：

```bash
ollama serve
ollama list
ollama run qwen3.5:9b "hello"
```

推荐把 `OLLAMA_THINK=false` 保持关闭，这样更快、更适合结构化提取。

如果模型名不一致，以 `ollama list` 显示的名字为准。

## 常见问题

### 1. `No module named youtube_idea_creator`

通常是因为你不在仓库根目录。

### 2. `Unable to find the server at www.googleapis.com`

这是当前运行环境的 DNS / 出网问题，不是代码路径问题。先确认终端里能 `ping www.googleapis.com`，并检查系统网络。

### 3. `model 'qwen3.5:9b' not found`

说明 Ollama 当前没有看到这个模型名。请先执行：

```bash
ollama list
```

然后把 `.env` 里的 `AI_MODEL` 改成真实模型名。

### 4. 运行太久

优先尝试：

- 降低 `POPULAR_COUNT`
- 降低 `MAX_COMMENTS_PER_VIDEO`
- 保持 `FILTER_MIN_SCORE=3`
- 保持 `TARGET_HIGH_SIGNAL_COMMENTS_PER_VIDEO=12`

### 5. 输出太少

可以：

- 适当增加 `POPULAR_COUNT`
- 适当提高 `TARGET_HIGH_SIGNAL_COMMENTS_PER_VIDEO`
- 让 `POPULAR_ANALYSIS_FLOOR` 保持在 `3` 或 `5`

### 6. `commentsDisabled`

这表示某个热门视频关闭了评论。脚本会自动跳过这类视频并继续处理其它视频，不会因此中断整轮任务。

## 推荐起步参数

如果你只是想先看一版结果，建议：

```bash
AI_PROVIDER=ollama
AI_MODEL=qwen3.5:9b
FILTER_MIN_SCORE=3
MAX_COMMENTS_PER_VIDEO=10
TARGET_HIGH_SIGNAL_COMMENTS_PER_VIDEO=12
python -m youtube_idea_creator --popular-mode --region-code US --popular-count 3 --max-comments-per-video 10
```

如果你想更广一点，再慢慢加量即可。

## 安全提示

- 不要把真实 API Key 提交到仓库。
- `.env` 建议只保存在本地。
- 如果你使用的是公开热门视频，仍然要注意 YouTube API 配额和抓取节奏。
