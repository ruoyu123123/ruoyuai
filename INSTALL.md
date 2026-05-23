# 安装与首次运行

> 5 分钟从 0 到「你好」看到主菜单。

## 1. 系统要求

| 项 | 最低版本 | 检查命令 |
|---|---|---|
| **Node.js** | 18+ | `node --version` |
| **Claude Code** | 任意 | `claude --version` |
| **Python** | 3.10+ | `python --version` |
| **Git** | 任意 | `git --version` |
| **OS** | Win / macOS / Linux | — |

## 2. 装 Anthropic Claude Code

本项目运行在 Claude Code 之上，必须先装官方 CLI：

```bash
# 全局安装
npm install -g @anthropic-ai/claude-code

# 验证
claude --version
```

详细文档：https://docs.claude.com/en/docs/claude-code

> ⚠️ 装好后请先登录：第一次跑 `claude` 时会引导。

## 3. 克隆本仓库

```bash
git clone https://github.com/<your-username>/ruoyuai.git
cd ruoyuai
```

> 💡 如果你想 fork 后做修改并自建仓库，初始化时建议用 `main` 作为默认分支：
>
> ```bash
> git init -b main
> ```

## 4. 装 Python 依赖

```bash
pip install -r requirements.txt
```

依赖清单（5 个包）：
- `openai` — gen-model 调用层（OpenAI 兼容协议）
- `python-dotenv` — `.env` 配置加载
- `scipy` + `numpy` — 风格保真度量化
- `requests` — 模型能力探测

## 5. 配置 Gen-Model（写作用的二级模型）

```bash
cp .env.example .env
```

编辑 `.env`，填一个 OpenAI 兼容 API：

```env
GEN_MODEL_ACTIVE=my_profile

GEN__my_profile__MODEL=deepseek-v4-pro
GEN__my_profile__BASE_URL=https://your-proxy.com/v1
GEN__my_profile__API_KEY=sk-your-key-here
GEN__my_profile__TEMPERATURE=0.8
```

**国内常用 API 快选（按性价比排序）**：

| 供应商 | MODEL 字段 | BASE_URL 示例 | 备注 |
|---|---|---|---|
| 🟢 **DeepSeek 官方**（推荐起步） | `deepseek-chat` 或 `deepseek-reasoner` | `https://api.deepseek.com/v1` | 中文写作强、价格便宜（输入 ¥1/百万 token） |
| 🟢 **Kimi 官方** | `moonshot-v1-32k` | `https://api.moonshot.cn/v1` | 长上下文友好，适合长章节写作 |
| 🟡 GLM | `glm-4-plus` | `https://open.bigmodel.cn/api/paas/v4` | 智谱清言 |
| 🟡 Qwen | `qwen-max` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 阿里通义 |
| 🟡 OpenRouter | 各模型名 | `https://openrouter.ai/api/v1` | 聚合站，可调海外模型 |

90%+ 中转站兼容 OpenAI 协议，按上面格式填即可。

> 💡 也可以用 CLI 添加：`python core/scripts/gen_model.py add <name>`
>
> 💡 写作过程中 429（限流）会自动按 `GEN_MODEL_FALLBACK_CHAIN` 切换 profile，建议至少配 2 个 profile 互备。

## 6. 启动

### Windows

```cmd
start.cmd
```

### macOS / Linux

```bash
chmod +x start.sh  # 首次运行需赋执行权
./start.sh
```

### 或手动启动

```bash
claude --dangerously-skip-permissions
```

> 💡 **关于 `--dangerously-skip-permissions`**：这只是告诉 Claude Code 跳过每次工具调用的「允许/拒绝」交互提示（让流水线连贯跑），**不会**开放任何系统权限或安全漏洞。本系统涉及大量内部工具调用（plan_tracker / scanner / gen_writer），手动确认每一次会让写作流程崩溃。如果你只是评估系统，可以去掉这个参数手动确认每一步。

---

## 📎 占位符约定

文档和模板里出现以下占位符（你看到时替换为实际值）：

| 占位符 | 含义 | 替换示例 |
|---|---|---|
| `<REPO_ROOT>` | 本仓库克隆后的根路径（取决于你 `git clone` 到哪） | `D:\code\ruoyuai`、`/home/me/ruoyuai` |
| `<书名>` | 你的小说项目名 | `修仙外卖员`、`沙原诸子` |
| `<your-username>` | 你的 GitHub 用户名 | `mygithub` |

**注意**：Python 脚本里**不会**出现 `<REPO_ROOT>` 这种占位符——脚本都用 `Path(__file__).resolve().parents[N]` 动态计算项目根。占位符只在**文档示例 / agent prompt 模板**里出现，提示你按当前环境替换。


启动后随便打个「你好」或「写小说」，会看到主菜单：

```
╔══════════════════════════════════════╗
║        若渝AI · 智能写作助手         ║
╠══════════════════════════════════════╣
║  [1] 新建项目（选择/蒸馏风格）       ║
║  [2] 继续写作（恢复上次进度）        ║
║  [3] 自由模式（不用参考风格）        ║
...
```

## 7. 首次跑的常见问题

### Q1：跑 `start.cmd` 后没反应？

确认 PATH 里有 `claude` 命令：

```bash
where claude    # Windows
which claude    # macOS/Linux
```

没装好的话回到 [Step 2](#2-装-anthropic-claude-code)。

### Q2：`/write` 时报 `ModuleNotFoundError: No module named 'openai'`？

Python 依赖没装。重跑：

```bash
pip install -r requirements.txt
```

如系统有多个 Python，确认装到了 Claude Code 跑脚本时用的那个：

```bash
python -c "import openai; print(openai.__version__)"
```

### Q3：hooks 不生效（PLAN_ID 拦截没触发）？

确认 `.claude/settings.json` 存在 + 内容含 `hooks` 字段：

```bash
cat .claude/settings.json
```

如果该文件丢了，重新从仓库拉一份。

### Q4：报「写产出到 `.claude/`」错误？

本项目 v6.2.4 起，用户产出（蒸馏数据 / 小说）统一走 `workspace/`：
- 风格库：`workspace/styles/{书名}/`
- 小说项目：`workspace/novels/{书名}/`

`.claude/` 只放 Claude Code 配置（agents/commands/settings.json），别往里写产出。

### Q5：第一次写章节卡很久？

**完全正常**。新手最容易在这里恐慌。

**首章预期耗时 3-8 分钟**，涉及完整流水线：

```
1. 调研先行（联网搜热点）     ~30s
2. outline-planner（拟走向卡）  ~30s
3. gen-writer（写正文）         60-180s（按 gen-model 速度）
4. chapter-splitter（切自然截断点）  ~10s
5. validator + voice-checker      ~30s
6. cross-chapter scanner 30+ 项   ~30s
7. reading-reflector（8 维度阅读）  ~60s
8. save-state（11 步流水线）      ~30s
```

只要终端有输出滚动就是在跑。**别打断、别按 Ctrl+C**。

### Q6：写到一半 API 报 `RateLimitError` / `429` / 网络超时？

- 系统会自动按 `.env` 里 `GEN_MODEL_FALLBACK_CHAIN` 切到备用 profile（如果配了）
- 没配 fallback 的话流水线会停下，**用 `/continue` 断点续写**，从上次 save-state 恢复
- 反复 429 = 该供应商限流，换一个或加备用 profile

### Q7：报错信息看不懂（`openai.AuthenticationError` 之类）？

常见报错对照：

| 错误关键词 | 原因 | 解决 |
|---|---|---|
| `AuthenticationError` / `401` | API key 错或失效 | 检查 `.env` 里 `GEN__<name>__API_KEY` |
| `NotFoundError` / `404` / `model not found` | MODEL 字段填错 | 对照供应商文档的可用模型名 |
| `RateLimitError` / `429` | 限流 | 等 1 分钟重试 / 加 fallback profile |
| `Connection timeout` | 网络问题 / base_url 不可达 | 检查 BASE_URL / 用代理 |
| `Invalid base URL` | BASE_URL 格式错 | 必须以 `https://` 开头，结尾 `/v1` 或具体 endpoint |

如果以上都不是，跑 `python core/scripts/model_probe.py` 探测 base_url 可达性 + 列出可用模型。

### Q6：想用 DeepSeek 但 API 报 429？

`.env` 加 fallback 链：

```env
GEN_MODEL_FALLBACK_CHAIN=kimi_main,glm_backup
```

主 profile 429/timeout 时按顺序尝试。

## 8. 卸载

直接删除整个 `ruoyuai/` 目录即可（本项目不写系统注册表、不改全局配置）。

如果想保留你写的小说：

```bash
# 你的所有小说项目存在 workspace/novels/{书名}/，是独立 Git 仓库
# 想保留就先把它们拷走
cp -r workspace/novels ~/my-novels-backup/
```

## 9. 升级

```bash
git pull
pip install -r requirements.txt   # 重装依赖（万一新版加了包）
```

升级**不会**影响 `workspace/` 下你写的小说（这些被 `.gitignore` 排除）。

## 10. 反馈 / 报 bug

提 issue：https://github.com/<your-username>/ruoyuai/issues

附上：
- 操作系统 + Python / Node / Claude Code 版本
- 报错完整日志
- 复现步骤
