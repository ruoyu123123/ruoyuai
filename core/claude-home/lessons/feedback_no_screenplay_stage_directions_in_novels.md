---
name: feedback-no-screenplay-stage-directions-in-novels
description: 全局：小说正文严禁剧本体舞台指示（如「（镜头拉远，离开 X 的视角）」「（切镜）」「（旁白）」）· cluster_001 ch4 翻车实例
metadata:
  type: feedback
---

# 小说严禁剧本体舞台指示

**用户原话**（2026-05-28，看到 cluster_001 ch4 「（镜头拉远，离开陆建国的视角）」）：「这个是什么情况，正常小说不应该出现这种东西吧」

## 规则

小说正文**绝对不能**出现以下任何一种剧本/电影脚本式标记：

- `（镜头拉远）` / `（镜头特写）` / `（推近）` / `（拉远）` / `（切镜）`
- `（旁白：XX）` / `（画外音：XX）`
- `（音效：XX）` / `（背景音：XX）`
- `（OS）` / `（V.O.）` / `（CUT TO）` / `（FADE IN）`
- 任何形如「（XX 的视角）」「（离开 XX 的视角）」的指令式括号
- 任何形如「演员表」「场记」「场号」的剧本结构

**Why**：剧本是给导演/演员的指令，小说是给读者的体验。读者看到「（镜头拉远）」会立刻出戏——意识到「这是被写出来的」而不是「这是发生的」。

**How to apply**：

1. writer prompt 强制约束：禁用上述清单
2. validate_style scanner 加 banned_patterns（hard_gate 级）
3. 项目级写入 `_数据库/style_scanner_overrides.json.banned_patterns`
4. 主代理在 reflector 派 fix 单时，如果 reflector 建议加 POV 过渡标记，**禁止直译为舞台指示**，必须用文学化形式

## POV/场景过渡的正确写法（文学化清单）

| 类型 | 形式 | 举例 |
|---|---|---|
| 分隔符 | 单独一行的 `*` / `***` / `······` / `——————` （居中或左对齐） | `　　　　　*` |
| 时间跳转 | 一个具体时间名词起句 | 「下午三点」「夜里十一点」「第二天清晨」 |
| 听觉淡出 | 声音远去 / 消失 / 切断 | 「走廊里的脚步声越来越远，最后只剩一截，被一扇关上的门切掉了」 |
| 视觉淡出 | 灯灭 / 影暗 / 物件留白 | 「灯一盏一盏熄了」「桌上只剩一本登记簿」 |
| 物件聚焦 | 切到一个不属于人物的物件 | 「那面没有数字的钟挂在墙上，指针停在某个位置上，不动了」 |
| 空白段 | 直接空一段，下一段切到新视角 | （强 POV 文学常用，但需要前文铺垫） |

## 翻车实例（实证支撑）

- **cluster_001 ch4 末段**：reflector round 4 指出 POV 末尾切全知缺过渡 → 主代理图省事加了「（镜头拉远，离开陆建国的视角）」剧本体 → 用户察觉立刻打回 → 必须改成「走廊里的脚步声远去 / `*` / 第七窗口空着」文学形式
- **reflector round 4 报告 lesson_seed 字段**已经明确写过：「`feedback_pov_explicit_transition_marker_form.md`（推荐 *** / 时间标 / 过渡句，禁用『（镜头XX）』剧本体）」—— 主代理没读完整 reflector 建议，犯了已被警告的错

## 关联

- [[feedback-no-investigation-no-voice-universal]]：reflector 报告已经标过禁用形式，主代理没尽读=没调研
- 项目级 `_数据库/style_scanner_overrides.json` 应配 banned_patterns
