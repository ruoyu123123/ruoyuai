#!/usr/bin/env python3
"""
style_analyzer.py — 中文小说风格量化分析器

用法：
  python style_analyzer.py <文件路径>
  python style_analyzer.py <文件路径> --output result.json
  python style_analyzer.py <目录> --batch
  python style_analyzer.py <文件A> --compare <文件B>
"""
from __future__ import annotations
import json
import os
import re
import sys
import math
from pathlib import Path
from collections import Counter


SENTENCE_ENDINGS = re.compile(r"[。！？!?]")
# v2 修复：引号字符必须包含 ASCII " (0x22) + 中文弯引号 U+201C/201D + 单引号 U+2018/2019 + 中文「」『』
# 原版只用 ASCII " 导致在中文小说上完全失效
_Q_OPEN = '"' + '“' + '‘' + '「' + '『'   # " “ ‘ 「 『
_Q_CLOSE = '"' + '”' + '’' + '」' + '』'  # " ” ’ 」 』
_Q_ANY = _Q_OPEN + _Q_CLOSE
# v2：双引号匹配按对配对，内部允许嵌套引号（如 ""灰隼" 案例" 内部的「灰隼」）
# 使用非贪婪 + 配对的左右引号，避免嵌套被截断
DIALOGUE_QUOTED = re.compile(
    r'"[^"]*?"'         # ASCII 双引号
    r'|“[^“”]*?”'        # 中文弯双引号（不能含同型嵌套）
    r"|‘[^‘’]*?’"        # 中文弯单引号
    r"|「[^「」]*?」"     # 中文方头引号
    r"|『[^『』]*?』"     # 中文双层方头引号
)
ELLIPSIS_PATTERN = re.compile(r"[…]+|\.{3,}")
EXCLAMATION_PATTERN = re.compile(r"[！!]")
QUESTION_PATTERN = re.compile(r"[？?]")
DASH_PATTERN = re.compile(r"[—]{2,}|──")
COMMA_PATTERN = re.compile(r"[，,]")
PERIOD_PATTERN = re.compile(r"[。]")
BRACKET_SETTING = re.compile(r"【[^】]+】")
CHINESE_CHAR = re.compile(r"[一-鿿]")
PRONOUNS = {"他", "她", "它", "我", "你", "这", "那", "谁", "什", "哪", "也", "都", "就", "又", "但", "却", "已", "正", "才", "还", "不", "没", "被", "把", "让", "给", "从", "在", "到", "向", "对", "和", "与"}

# v2 (E1)：扩展 SPEAKER_PATTERN 容忍更多 attribution 形式
SPEAKER_PATTERN = re.compile(
    r"(?:^|[，。！？\s" + re.escape(_Q_ANY) + r"])([一-鿿]{2,4})"
    r"(说道?|道|问道?|答道?|笑道?|骂道?|喊道?|叫道?|吼道?|嘀咕|嘟囔|低声道?|开口|沉声道?|轻声道?|皱眉|轻笑|苦笑|冷笑|疑惑|喝道|喝问|喃喃|沉吟)"
)
# v2 (E1)：引号尾随 attribution（"……"HeroC说道）
DIALOGUE_WITH_TAG = re.compile(
    r"[" + re.escape(_Q_OPEN) + r"][^" + re.escape(_Q_ANY) + r"]+["
    + re.escape(_Q_CLOSE) + r"][，,]?\s*([一-鿿]{2,4})"
    r"(说道?|道|问道?|答道?|笑道?|喊道?|叫道?|吼道?|嘟囔|皱眉|低声道?|沉声道?|轻声道?|喃喃)"
)
# v2 (E1)：人名前 attribution（"HeroC沉声道："、"CharC1疑惑问道："）
SPEAKER_PREFIX = re.compile(
    r"([一-鿿]{2,4})(?:[一-鿿]{0,6})?(说道?|道|问道?|答道?|笑道?|喊道?|叫道?|吼道?|喝问|喝道|沉声道?|轻声道?|疑惑(?:问道?|地?说道?)?|皱眉|低声(?:道?|说道?)|开口(?:道?|说道?)|沉吟(?:道?|着)?|喃喃(?:道?|地?说道?)?)[：:]?\s*["
    + re.escape(_Q_OPEN) + r"]"
)

# v2 (E3)：拟声段豁免
ONOMATOPOEIA_WORDS = [
    "啪", "砰", "嘭", "咣", "咚", "哐当", "哒", "哒哒", "嗒", "嗒嗒",
    "呼", "呼呼", "嗖", "嗖嗖", "咻", "咻咻", "呜", "呜呜",
    "嘶", "嘶嘶", "唰", "唰唰", "刷", "哗", "哗啦",
    "噗", "噗嗤", "扑通", "咔", "咔嚓", "喀嚓", "咯吱",
    "嘎吱", "嘎嘎", "嗡", "嗡嗡", "叮", "叮当", "铛", "当",
    "汪", "喵", "啾", "吱", "哒", "嘿", "呵", "哈",
    "咳", "咳咳",
]
ONOMATOPOEIA_PARA = re.compile(
    r"^(?:" + "|".join(re.escape(w) for w in ONOMATOPOEIA_WORDS) + r")"
    r"(?:[—…的一]{0,3}[一-鿿]{0,4})?"
    r"[！？!?。\s]*$"
)
# 保留原破折号格式
ONOMATOPOEIA_DASH = re.compile(r"^[一-鿿]{1,6}[—]+[！!]?\s*$")

# v2 (E6)：独白识别标签（引号紧邻独白动词 → 算 monologue 而非 dialogue）
MONOLOGUE_TAGS = [
    "思考着", "心想", "暗忖", "暗道", "心道", "想到", "在心里",
    "默默地想", "暗自琢磨", "无声吐槽", "无声地想", "心头一动",
    "暗自思忖", "心中暗道", "腹诽", "凝重地思考",
]

FUNCTION_WORDS = [
    "的", "了", "着", "却", "便", "竟",
    "倒", "只", "又", "不过", "只是", "毕竟", "但", "而", "也",
]

# 2026-05-29 北极星 P4 [H2-style]：拆两类（守原则5「不干涉模型判断」）——
# ① AI 结构套话：结构性机器腔，任何作者都不用 → 永远 FAIL（作者档不能放行）。
# ② 工艺签名词：可能是某作者的签名笔法（似乎/仿佛/淡淡…）→ 有作者风格档时降 WARN
#    （审核不硬毙作者签名词），无作者档时按默认仍 FAIL。
#
# 2026-05-30 北极星⑤ [事实上误分类修]：「事实上」从 ① 降到 ②——实测蛊真人原文 118 章
# 高频用「事实上」作议论体签名连接词（非机器腔），无条件硬 FAIL = 通用反 AI 腔规则
# 苛求真作者（矫枉过正）。改归工艺签名词后：有作者档时降 WARN 可豁免（写出真作者高频
# 签名词不被硬毙），无作者档时通用写作仍 FAIL（防 AI 腔）。其余 3 个 AI 结构套话
# （与此同时/值得一提的是/不仅如此）未证伪、仍是典型机器腔，保持 ① 永久 FAIL 不动。
AI_STRUCTURAL_BANNED = [
    "与此同时", "值得一提的是", "不仅如此",
]
CRAFT_SIGNATURE_BANNED = [
    "顿时", "紧锁", "显然", "此刻", "淡淡",
    "心中一凛", "眼中闪过一丝", "微微挑眉",
    "嘴角勾起一抹", "深吸一口气", "缓缓地说", "沉吟片刻",
    "波涛汹涌", "不容置疑", "事实上",
]
BANNED_WORDS = AI_STRUCTURAL_BANNED + CRAFT_SIGNATURE_BANNED  # 全集（向后兼容）

# 🔴 2026-06-27 P1-09：加「瞬间」入 QUOTA_WORDS（cluster_003 单簇 31 次实证）·非签名节奏转场词
QUOTA_WORDS = ["突然", "瞬间", "下一刻", "下意识", "莫名", "似乎", "仿佛", "顿时", "微微"]

# 2026-05-30 北极星⑤ [C-配额词作者档降级]：配额词里的**工艺签名类**子集——
# 这些是软性 hedge / 笔法签名词（顿时/微微/似乎/仿佛），可能是某作者的签名笔法，
# 与 CRAFT_SIGNATURE_BANNED 同源（顿时直接在内，微微来自「微微挑眉」笔法家族），
# 故 validate_style 在有作者档时把它们超额降 WARN（对齐 _chk_banned 的作者档优先逻辑）。
# 反之 突然/下一刻/下意识/莫名 是叙事节奏/转场堆砌词（非签名），超额仍 FAIL（不放松真问题）。
CRAFT_SIGNATURE_QUOTA_WORDS = ["顿时", "微微", "似乎", "仿佛"]

AI_DIALOGUE_TAGS = ["淡淡地说", "缓缓地说", "沉吟片刻", "不容置疑", "微微一笑道"]


# ════════════════════════════════════════════════════════════════════════
# 作者级叙事功能序列（narrative function sequence）· 2026-05-31 · 北极星⑤
# ════════════════════════════════════════════════════════════════════════
# 背景（2603.14430 实证）：中文网文同质化根因在**结构层**——LLM 只复现高频默认
# 模板（Save-the-Cat 通用编剧节拍），不复现某作者**专属的因果叙事功能链**（如
# 蛊真人「设定投放→反高潮拒战→打脸」、惊悚乐园「危机→算计揭示→获益」）。
# 前 8 轮加固都在散文/检测/评估/流程层，这是缺失的**结构语法层**。
#
# 本模块从作者语料蒸馏「叙事功能序列」：
#   1) 每章用词典命中给 7 个功能维度打分；
#   2) **关键防误判**——分数对全语料 baseline 取相对显著度（z-score），把
#      always-present 的世界观词汇（蛊/真元/元海）normalize 掉，只留真正
#      OVER-index 的结构功能（小验证证实：原始计数法 90% 章全是 setting，
#      z-score 法分布均衡 + 命中 face_slap→gain_reward 等真因果链）；
#   3) 跨章功能标签序列 → bigram/trigram 频次 = 作者专属功能链模式。
#
# 北极星纪律：纯**advisory 软提示**（写入 skill 作软提示维，与 beat_map 并列但
# 更深、作者专属非通用）——绝不 hard_gate、绝不干涉模型创作判断、纯启发式黑箱
# （不调 LLM）。env NARRATIVE_SEQ_MODE 控制：active(默认) / off。抽取准确率
# 存疑是已知短板 → 输出永远标 advisory + 附 confidence，由模型自行取舍。
#
# 功能维度词典（causal/structural beat，非世界观名词）：
NARRATIVE_FUNCTION_CUES = {
    # 设定投放：世界观机制/原理/规则的解释性铺陈（用「解释动作」词而非世界名词，
    # 防被书内高频专名淹没）
    "setting_injection": [
        "设定", "规则", "原理", "机制", "所谓", "等阶", "体系", "构造", "本质",
        "据说", "传说", "相传", "原本", "其实是", "之所以", "换句话说", "也就是说",
    ],
    # 反高潮拒战：主角主动拒绝/回避预期冲突（网文反套路签名）
    "anti_climax_refuse": [
        "不必", "无需", "拒绝", "懒得", "何必", "不屑", "算了", "没兴趣", "不愿",
        "不想理", "置之不理", "不予理会", "懒得理", "毫无兴趣", "不放在眼里",
    ],
    # 打脸/反转：他人认知被颠覆的瞬间
    "face_slap": [
        "震惊", "不可能", "怎么会", "瞠目", "哑口", "傻眼", "难以置信", "倒吸",
        "呆住", "僵住", "大跌眼镜", "没想到", "出乎意料", "万万没想到", "脸色大变",
    ],
    # 冲突/战斗：实时对抗
    "confrontation": [
        "厮杀", "搏杀", "交手", "出手", "战斗", "撕咬", "重创", "拼杀", "对峙",
        "激战", "缠斗", "迎击", "反击", "围攻", "blade",
    ],
    # 算计揭示：隐藏计谋/真相的揭露
    "scheme_reveal": [
        "原来", "真相", "算计", "布局", "早已", "竟是", "图谋", "圈套", "陷阱",
        "计谋", "一切尽在", "棋子", "局中局", "暗中谋划", "蓄谋",
    ],
    # 获益/爽点：兑现收获（与钩子相反）
    "gain_reward": [
        "得到", "获得", "收获", "炼成", "突破", "晋级", "到手", "囊中", "收入囊",
        "圆满", "如愿", "大功告成", "满载而归", "终于成功",
    ],
    # 危机/威胁：悬念升级、致命压力
    "crisis_threat": [
        "危险", "危机", "杀机", "追杀", "险些", "千钧", "致命", "绝境", "九死",
        "濒死", "警觉", "不妙", "大事不妙", "命悬一线", "凶险",
    ],
}
_NARRATIVE_FUNCTION_KEYS = sorted(NARRATIVE_FUNCTION_CUES.keys())
# z-score 显著阈值：章节在某功能上超出语料 baseline 多少个标准差才算「该章命中」。
# 0.6 来自小验证（30 章蛊真人）——更低噪声大、更高漏真链。
_NARR_SEQ_Z_THRESHOLD = 0.6
_NARR_SEQ_TOP_K = 2  # 每章最多取 2 个 over-index 功能（主+次）


def narrative_seq_mode() -> str:
    """env NARRATIVE_SEQ_MODE：active(默认) / off。非法值回退 active。

    北极星纪律 2「默认全开 active·env 可关」——本结构层默认生效，
    用户/CI 可设 NARRATIVE_SEQ_MODE=off 关闭（如纯量化对比场景不需要）。"""
    m = (os.environ.get("NARRATIVE_SEQ_MODE") or "active").strip().lower()
    return m if m in ("active", "off") else "active"


def count_chinese(text: str) -> int:
    return len(CHINESE_CHAR.findall(text))


# ============================================================
# v2：拟声段 / 独白 / 说话人 识别工具函数
# ============================================================

# 常见非人名候选词（被 NAME_CANDIDATES 误抓的高频中文短串）
_COMMON_NON_NAMES = {
    # 称谓 / 代词
    "自己", "他们", "她们", "我们", "你们", "它们", "大家", "众人",
    "对方", "彼此", "诸位", "几位", "各位", "先生", "女士", "夫人",
    "老者", "少年", "少女", "中年", "青年", "店主", "客人",
    # 量词 / 副词高频
    "一切", "所有", "这个", "那个", "这些", "那些", "这样", "那样",
    "如果", "因为", "所以", "但是", "可是", "然后", "只是", "只有",
    "似乎", "仿佛", "好像", "立刻", "立即", "马上", "刚刚", "已经",
    "正在", "依然", "仍然", "始终", "终于", "突然", "忽然", "顿时",
    "或许", "也许", "大概", "可能", "应该", "肯定", "必然", "或者",
    "另一", "其中", "其他", "其余", "之中", "之间", "之内", "之外",
    "之上", "之下", "目前", "如此", "因此", "于是", "尽管",
    # 普通名词
    "时候", "时间", "地方", "地点", "事情", "事件", "问题", "方法",
    "方式", "结果", "原因", "情况", "状况", "感觉", "想法", "心思",
    "念头", "念想", "眼睛", "鼻子", "嘴巴", "耳朵", "身上", "身体",
    "脸上", "手上", "脚下", "心中", "脑中", "口中", "脑海", "心头",
    "肩膀", "手指", "指尖", "掌心", "手心", "胸口", "颈部",
    "嘴唇", "眉头", "眉心", "眼角", "鼻尖", "耳后", "耳边",
    "皮肤", "面孔", "面貌", "面庞", "面色", "脸色", "脸庞",
    "身影", "影子", "雾气", "灰雾", "油状", "液体", "黑线",
    "蜡烛", "火苗", "光芒", "气场", "气息", "声响", "声音",
    "东西", "事物", "物品", "物件", "工具", "武器", "卷宗", "档案",
    "纸面", "纸笺", "笔记", "记录", "封皮", "封面", "封套",
    "诱饵", "线索", "证据", "信号", "信息", "情报",
    "条例", "规则", "规矩", "规范", "制度", "法律",
    # 描写动词带"道""说"的伪 attribution（在 SPEAKER_PREFIX 中常出现）
    "说道", "回答", "回应", "继续", "接着", "之后", "之前",
    "刚才", "刚刚", "现在", "最近", "曾经", "记得",
    "听见", "听到", "看见", "看到", "感到", "察觉", "发现",
    "想到", "意识", "明白", "知道", "了解", "理解", "考虑",
    "回想", "回忆", "回头", "转身", "回身", "转头", "回神",
    "皱眉", "凝重", "沉吟", "沉思", "凝视", "注视", "盯着",
    "低头", "擡头", "抬头", "点头", "摇头", "歪头", "侧头",
    "开口", "闭口", "缓缓", "缓慢", "急忙", "连忙", "赶紧",
    "微微", "悄悄", "默默", "静静", "悠悠", "轻轻", "缓缓",
    "慢慢", "渐渐", "渐次", "逐渐", "逐步", "终于",
    # 描写动词
    "想要", "需要", "希望", "打算", "准备", "决定", "选择",
    "继续", "停止", "开始", "结束", "完成", "完毕", "结果",
    "出现", "消失", "存在", "消亡", "诞生", "死亡", "生死",
    "看着", "听着", "想着", "做着", "走着", "站着", "坐着",
    # 文中常见非人名 2-3 字组合
    "下午", "上午", "晚上", "今天", "明天", "昨天", "今夜", "今晚",
    "白天", "黑夜", "黑暗", "光明", "明亮", "幽暗", "昏暗",
    # 高频组合（"长期供货""被夜风搅散"等）
    "供货", "供给", "买卖", "交易", "生意", "商铺", "店铺",
    "组织", "团伙", "团体", "组合", "集团", "成员",
    "皇家", "档案", "卷帙", "残卷", "古卷", "经卷",
    "图书", "管理", "管理员", "信使", "信息", "检索",
    "记得", "想起", "回忆", "记忆",
    # 量词类（"一个""两个"等）
    "一个", "两个", "三个", "几个", "一只", "一些", "一种",
    "几种", "一类", "一群", "一片", "一阵", "一道",
    "一次", "一回", "一遍", "一段",
    # 抽象短语
    "名册", "名单", "册子", "册页", "登记", "登录",
    "某种", "某个", "某些", "某位", "此种", "此类",
    "长期", "短期", "中期", "暂时", "永久", "一时", "片刻",
    "之类", "等等", "诸如",
    "默契", "信任", "亲密", "陌生", "熟悉",
    "诱饵", "陷阱", "圈套", "把戏", "招数", "手段",
    "层面", "方面", "维度", "角度",
    # 高频附着
    "相信", "怀疑", "确认", "确定", "确信", "断定",
    "想到", "觉得", "认为", "以为", "估计",
    "可能", "也许", "或许", "大概",
    "除非", "因为", "由于", "鉴于", "至于", "对于",
    "面前", "身边", "周围", "周边", "附近", "近旁",
    "之中", "之内", "之外", "之间", "之上", "之下", "之前", "之后",
}

# 高频 attribution 动词后缀（用于过滤误判为人名的动词链片段）
_ATTRIBUTION_VERB_PREFIXES = {
    "疑惑", "皱眉", "轻叹", "苦笑", "冷笑", "微笑", "傻笑",
    "低声", "高声", "沉声", "轻声", "大声", "细声",
    "缓缓", "缓慢", "急忙", "连忙", "赶紧", "立刻",
    "凝重", "深沉", "平静", "平淡",
    "暗暗", "默默", "悄悄", "偷偷",
    "突然", "忽然", "猛然", "陡然", "蓦地",
    "开口", "继续", "回应", "回答", "答应",
    "听见", "听到", "看见", "看到", "想到", "感到",
    "记得", "知道", "明白", "意识",
}


def is_onomatopoeia(para: str) -> bool:
    """判断段落是否为拟声独立段（豁免极短段计数）。"""
    s = para.strip()
    if not s:
        return False
    return bool(ONOMATOPOEIA_PARA.match(s) or ONOMATOPOEIA_DASH.match(s))


def _split_dialogue_vs_monologue(text: str) -> tuple[int, int]:
    """E6：分离对话与引号化独白字数。

    返回 (dialogue_chars, monologue_chars)。判断方式：
    1. 每个 quoted 段，前后 60 字扫独白标签
    2. 长 ≥ 30 字的引号段额外检查：若段内含"……"省略号 + 第一人称
       "我""自己" + 推演词，视为独白（典型引号化推演）
    3. 段独立成行 + 无 attribution 动词（说/问/答/喊/嘟囔等）跟随，且
       前段是叙述（无"对方""说"）→ 视为独白
    """
    dialogue_chars = 0
    monologue_chars = 0
    # attribution 动词：紧随引号的对话信号
    DIALOGUE_VERB_AFTER = re.compile(
        r"^[，,]?\s*[一-鿿]{0,6}?"
        r"(?:说道?|问道?|答道?|笑道?|喊道?|叫道?|吼道?|嘟囔|嘀咕|喝道|喝问)"
    )

    for m in DIALOGUE_QUOTED.finditer(text):
        content = m.group(0)
        start = m.start()
        end = m.end()
        # 前后 60 字
        after = text[end:end + 60]
        before = text[max(0, start - 60):start]

        is_monologue = False

        # 规则 A：紧邻独白标签
        if any(tag in after for tag in MONOLOGUE_TAGS):
            is_monologue = True
        elif any(tag in before for tag in MONOLOGUE_TAGS):
            is_monologue = True
        else:
            cn_len = count_chinese(content)
            # 规则 B：长引号（≥50 字）+ 无紧随 attribution 动词
            # （超长独立段引号几乎都是引号化推演）
            if cn_len >= 50 and not DIALOGUE_VERB_AFTER.match(after):
                # 排除：前文段尾是显式对话动词（"她说道："）
                if not re.search(
                    r"(说道?|问道?|答道?|笑道?|喊道?|叫道?|嘟囔|嘀咕|开口|沉声|低声|轻声|喝道|喝问|喃喃)[:：]\s*$",
                    before
                ):
                    is_monologue = True

            # 规则 C：中长引号（30-50 字）+ 含"…" + 含"我"/"自己"/"嗯"
            elif (cn_len >= 30 and "…" in content
                  and ("我" in content or "自己" in content or "嗯" in content)):
                if not DIALOGUE_VERB_AFTER.match(after):
                    is_monologue = True

        chinese_count = count_chinese(content)
        if is_monologue:
            monologue_chars += chinese_count
        else:
            dialogue_chars += chinese_count
    return dialogue_chars, monologue_chars


_HIGH_FREQ_VERBS = {
    # 常见单字动词/副词，作为人名前缀通常意味着误抓
    "看", "见", "听", "闻", "想", "知", "懂", "做", "干", "去", "来",
    "走", "跑", "站", "坐", "躺", "睡", "吃", "喝", "笑", "哭", "叫",
    "问", "答", "说", "讲", "谈", "议", "评", "夸", "骂",
    "查", "找", "寻", "搜", "挖", "掘", "调",
    "为", "因", "由", "凭", "据", "依", "靠",
    "将", "把", "让", "给", "替", "代", "向", "对", "和", "与",
    "送", "拿", "取", "递", "给", "传", "递",
    "念", "记", "忆", "思", "考", "推", "测", "估",
    "相", "怀", "疑", "确", "认",
    "除", "非", "如", "若", "假", "倘",
    "好", "坏", "美", "丑", "高", "低", "大", "小", "长", "短",
    "新", "旧", "老", "幼", "深", "浅", "厚", "薄",
    "当", "正", "现", "今", "即", "便", "就",
    # 文中常见动词作为子串前缀
    "担", "充", "任", "做", "扮", "演", "饰", "演",
    "学", "习", "练", "教", "授", "教",
    "身", "肉", "体", "首", "头", "脑",
    "像", "如", "似", "若", "仿",
    "出", "进", "入", "退", "返", "回",
    "握", "抓", "拉", "推", "拉", "扯",
    "上", "下", "前", "后", "左", "右",
    "找", "请", "求", "等", "待", "盼",
    "用", "使", "施", "运",
}


def _is_valid_name(name: str) -> bool:
    """判定人名候选是否合法。

    过滤：
    - 长度 ≠ 2-3（4 字串绝大多数是动词+名字或名字+敬称的误抓）
    - 在 _COMMON_NON_NAMES 中
    - 首字在 PRONOUNS 中
    - 任意 2 字前缀在 _ATTRIBUTION_VERB_PREFIXES 中（如"疑惑问"）
    - 首字是高频单字动词（如"看见海纳斯"被 honorific 抓出"见海纳斯"）
    - 含功能词
    """
    if not name or len(name) < 2 or len(name) > 3:
        return False
    if name in _COMMON_NON_NAMES:
        return False
    if name[0] in PRONOUNS:
        return False
    # 任意 2 字前缀是 attribution 动词
    if name[:2] in _ATTRIBUTION_VERB_PREFIXES:
        return False
    # 首字是高频动词
    if name[0] in _HIGH_FREQ_VERBS:
        return False
    # 名字中含功能词（如"的""了""着"），通常不是人名
    if any(c in {"的", "了", "着", "之", "得", "地", "不", "没", "也", "都", "就", "又", "却", "倒", "只", "便", "竟"} for c in name):
        return False
    return True


def _extract_explicit_speaker(window: str) -> str | None:
    """从窗口文本中抽取显式 attribution 人名。"""
    # 优先 SPEAKER_PREFIX（人名+attribution+引号）
    m = SPEAKER_PREFIX.search(window)
    if m:
        name = m.group(1)
        if _is_valid_name(name):
            return name
    # 然后 DIALOGUE_WITH_TAG（引号+人名+attribution）
    m = DIALOGUE_WITH_TAG.search(window)
    if m:
        name = m.group(1)
        if _is_valid_name(name):
            return name
    # 最后 SPEAKER_PATTERN（通用人名+动词）
    m = SPEAKER_PATTERN.search(window)
    if m:
        name = m.group(1)
        if _is_valid_name(name):
            return name
    return None


def _build_name_registry(text: str) -> set[str]:
    """从全文先建立"人名注册表"——只有注册表里的字串才能算 speaker。

    人名信号来源（任一即可）：
    1. 敬称模式："XX先生""XX小姐""XX队长"等
    2. 显式 attribution 中的"姓.名"模式
    3. 直接 attribution "XX说道：「" — 这里 XX 出现频率 ≥ 2 即注册
    """
    names: set[str] = set()

    # 1. 敬称识别（高置信度）—— 允许 2-5 字（含洋名）
    HONORIFIC = re.compile(
        r"([一-鿿]{2,5})(先生|小姐|女士|夫人|队长|教授|博士|医生|大人|阁下|殿下|陛下|"
        r"老爷|少爷|公主|王子|将军|警官|警长|警督|侦探|绅士|警员|"
        r"叔叔|阿姨|爷爷|奶奶|哥哥|姐姐|弟弟|妹妹|师父|师傅)"
    )
    honorific_names: set[str] = set()
    for m in HONORIFIC.finditer(text):
        name = m.group(1)
        # 从前往后剥离开头的动词/无关字（"调查凡森特" → "凡森特"）
        while len(name) > 2 and (
            name[0] in _HIGH_FREQ_VERBS or
            name[0] in PRONOUNS or
            name[0] in {"的", "了", "着", "之", "得", "地", "不", "没"}
        ):
            name = name[1:]
        # 再检查剥离后的 2-字前缀是否仍是动词组合
        if name[:2] in _ATTRIBUTION_VERB_PREFIXES:
            continue
        if not name or len(name) < 2:
            continue
        if name in _COMMON_NON_NAMES:
            continue
        if name[0] in PRONOUNS or name[0] in _HIGH_FREQ_VERBS:
            continue
        if any(c in {"的", "了", "着", "之", "得", "地", "不", "没"} for c in name):
            continue
        honorific_names.add(name)
        names.add(name)

    # 2. "姓.名" 模式（如"CharC1.史密斯"）
    DOTTED = re.compile(r"([一-鿿]{2,4})[\.·][一-鿿]{2,4}")
    for m in DOTTED.finditer(text):
        name = m.group(1)
        if _is_valid_name(name):
            names.add(name)

    # 3. 紧贴 attribution 引号的人名（高置信度）
    DIRECT_ATTR = re.compile(
        r"(?:^|[，。！？\s])([一-鿿]{2,4})"
        r"(?:说道?|问道?|答道?|笑道?|喊道?|叫道?|吼道?|嘟囔|嘀咕)"
        r"[：:]?\s*[\"\"「『]"
    )
    direct_counts: dict[str, int] = {}
    for m in DIRECT_ATTR.finditer(text):
        name = m.group(1)
        if _is_valid_name(name):
            direct_counts[name] = direct_counts.get(name, 0) + 1
    # 紧贴 attribution 中出现 ≥ 1 次的高置信度名字
    for n in direct_counts:
        names.add(n)

    # 4. 引号后人名 attribution（如 "……" HeroC说道）
    POST_ATTR = re.compile(
        r"[\"\"」』][，,]?\s*([一-鿿]{2,4})"
        r"(?:说道?|问道?|答道?|笑道?|喊道?|叫道?|嘟囔|皱眉|低声道?|沉声道?|轻声道?)"
    )
    for m in POST_ATTR.finditer(text):
        name = m.group(1)
        if _is_valid_name(name):
            names.add(name)

    # 5. 文中高频候选（≥ 3 次的合法 2-3 字串，需要至少 1 次与"speech context"相邻）
    chinese_only = "".join(c for c in text if CHINESE_CHAR.match(c))
    candidate_pool: set[str] = set()
    # 只取 2-3 字（4 字串大多是动作短语，引入噪声）
    for length in (2, 3):
        for i in range(len(chinese_only) - length + 1):
            sub = chinese_only[i:i + length]
            if _is_valid_name(sub):
                candidate_pool.add(sub)

    # "speech context" 标记：人名后紧跟 attribution / 敬称 / 引号 / 表情动词
    SPEECH_CTX = re.compile(
        r"(说道?|问道?|答道?|笑道?|喊道?|嘟囔|嘀咕|皱眉|轻笑|苦笑|冷笑|沉声|低声|轻声|开口|喃喃|"
        r"先生|小姐|女士|队长|警官|警长|博士|教授|大人|阁下|"
        r"擡|抬|点|摇|摸|拿|放|挥|握|按|捏|站|坐|躺|"
        r"思考|心想|暗忖|想到|意识到|觉察|发现|看着|听着|"
        r"凝重|温和|平静|冷淡|惊讶|疑惑|无声|默默|"
        r"[\"\"“”「『])"
    )
    for n in candidate_pool:
        c = text.count(n)
        if c >= 3:
            # 至少 1 次相邻 speech context
            for m in re.finditer(re.escape(n), text):
                end = m.end()
                tail = text[end:end + 6]
                if SPEECH_CTX.match(tail):
                    names.add(n)
                    break

    # 6. 去重：保留最长子串（"CharC1.史密斯"如果只注册"CharC1"够用）
    #   如果 "HeroC" 在注册表，"克莱" 这种前缀子串移除
    pruned = set()
    for n in names:
        # 保留：若不存在严格更长的注册名包含当前名
        if not any(other != n and len(other) > len(n) and n in other for other in names):
            pruned.add(n)
    return pruned


def _find_speaker_in_window(window: str, registry: set[str]) -> str | None:
    """在窗口文本中查找属于 registry 的人名（最近一个）。"""
    # 按位置从右往左找（最近的）
    best_pos = -1
    best_name = None
    for n in registry:
        # 在 window 中找出现位置
        pos = window.rfind(n)
        if pos > best_pos:
            best_pos = pos
            best_name = n
    return best_name


def classify_speakers(text: str, paragraphs: list[str]) -> tuple[set[str], set[str]]:
    """E1+E2+E4：区分 active_speakers / quoted_speakers。

    流程：
    1. 先从全文建立人名注册表（高置信信号）
    2. 遍历每个引号段，从前后 25 字窗口找注册表内的最近人名作为 active
    3. 状态机回合制继承：连续无 attribution 的引号段轮流继承
    4. quoted = 注册表 - active
    """
    registry = _build_name_registry(text)

    active: set[str] = set()
    # 包含 ASCII "、左/右弯双引号 U+201C/201D、单引号 U+2018、中文「『
    quote_starters = ('"', '“', '”', '‘', '「', '『')
    quoted_paras_idx: list[int] = []
    for i, p in enumerate(paragraphs):
        s = p.strip()
        if s and s[0] in quote_starters:
            quoted_paras_idx.append(i)

    last_two: list[str] = []  # 状态机：[A, B]

    for idx_pos, i in enumerate(quoted_paras_idx):
        p = paragraphs[i]
        # 前段尾 + 当前段 + 后段头：扫描注册表人名
        prev_tail = paragraphs[i - 1][-30:] if i > 0 else ""
        next_head = paragraphs[i + 1][:30] if i + 1 < len(paragraphs) else ""
        # 当前段开头若是引号则用其内部+后部
        window = prev_tail + "|" + p + "|" + next_head

        explicit = _find_speaker_in_window(window, registry)

        if explicit:
            active.add(explicit)
            if not last_two or last_two[-1] != explicit:
                last_two.append(explicit)
                if len(last_two) > 2:
                    last_two.pop(0)
        else:
            # E2 回合制继承
            if idx_pos > 0:
                prev_idx = quoted_paras_idx[idx_pos - 1]
                gap = i - prev_idx
                if gap <= 2 and len(last_two) >= 2:
                    prev_speaker = last_two[-1]
                    other = next((s for s in last_two if s != prev_speaker), prev_speaker)
                    active.add(other)
                    last_two = [prev_speaker, other]
                elif gap <= 2 and len(last_two) == 1:
                    active.add(last_two[-1])

    # 兜底：如果引号段中没找到任何 active，但注册表非空，至少把注册表里 attribution 紧贴的人名加入
    if not active:
        DIRECT_ATTR = re.compile(
            r"(?:^|[，。！？\s])([一-鿿]{2,4})"
            r"(?:说道?|问道?|答道?|笑道?|喊道?|嘟囔|皱眉|沉声道?|低声道?|轻声道?)"
            r"[：:]?\s*[\"\"「『]"
        )
        for m in DIRECT_ATTR.finditer(text):
            name = m.group(1)
            if name in registry:
                active.add(name)

    # quoted = 注册表 - active
    quoted = registry - active

    return active, quoted


def split_sentences(text: str) -> list[str]:
    parts = SENTENCE_ENDINGS.split(text)
    return [s.strip() for s in parts if s.strip() and count_chinese(s) > 0]


def split_paragraphs(text: str) -> list[str]:
    lines = text.split("\n")
    return [line.strip() for line in lines if line.strip() and count_chinese(line) > 0]


def calc_dialogue_ratio(text: str) -> float:
    """对话字数 / 总字数（仅算引号内的字数，避免重复计算整行）。

    v2 修复：DIALOGUE_QUOTED 已正确覆盖中英文引号，删除冗余的
    line.startswith 加成（原版会把"...."HeroC说道整行都算进去）。
    """
    all_matches = DIALOGUE_QUOTED.findall(text)
    dialogue_chars = sum(count_chinese(m) for m in all_matches)
    total = count_chinese(text)
    return dialogue_chars / total if total > 0 else 0.0


def calc_stats(values: list[float]) -> dict:
    if not values:
        return {"mean": 0, "std": 0, "min": 0, "max": 0, "median": 0, "count": 0}
    n = len(values)
    mean_val = sum(values) / n
    variance = sum((v - mean_val) ** 2 for v in values) / n if n > 1 else 0
    std_val = math.sqrt(variance)
    sorted_v = sorted(values)
    if n % 2 == 1:
        median_val = sorted_v[n // 2]
    else:
        median_val = (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    return {
        "mean": round(mean_val, 2),
        "std": round(std_val, 2),
        "min": min(values),
        "max": max(values),
        "median": round(median_val, 2),
        "count": n,
    }


def calc_quantiles(values: list[float]) -> dict | None:
    """经验分位数 [p5,p25,p50,p75,p95]（纯 stdlib · 线性插值 · 对齐 numpy 默认 'linear'）。

    2026-05-30 北极星① [L1a 作者分位数 band]：band/baseline 必须从**作者样本经验分布**涌现，
    不是「作者实测 mean ± 固定容差」。固定容差对长段议论体作者（蛊真人/惊悚乐园 段长方差大）
    系统性误判（whack-a-mole 根因）。本函数对一组 per-章值算 5/25/50/75/95 分位数——
    validate_style._apply_style_overrides 用 [p5,p95] 当 band（覆盖作者真实 90% 章节区间），
    取代 mean ± 容差。空集返回 None（调用方据此不 override · 保通用 band）。

    注：纯 stdlib 排序 + 线性插值实现（不引 numpy，守零依赖纪律）。"""
    if not values:
        return None
    s = sorted(float(v) for v in values)
    n = len(s)

    def q(p: float) -> float:
        if n == 1:
            return s[0]
        idx = p * (n - 1)
        lo = int(idx)
        frac = idx - lo
        if lo + 1 < n:
            return s[lo] + frac * (s[lo + 1] - s[lo])
        return s[lo]

    mean_val = sum(s) / n
    var = sum((v - mean_val) ** 2 for v in s) / n if n > 1 else 0.0
    return {
        "p5": round(q(0.05), 4), "p25": round(q(0.25), 4),
        "p50": round(q(0.50), 4), "p75": round(q(0.75), 4),
        "p95": round(q(0.95), 4),
        "mean": round(mean_val, 4), "std": round(math.sqrt(var), 4), "n": n,
    }


def chapter_metrics_lite(text: str) -> dict | None:
    """单章轻量指标（复用 analyze_text 取数原语，但**跳过** O(n²) 的说话人分类）。

    2026-05-30 北极星① [L1a]：per-章分位数统计只需 paragraph_length_chars / punctuation_per_1k
    / function_words_per_1k 三组 per-章值——不需要 _build_name_registry 的 speaker 数据（那是
    analyze_text 里最慢的 O(候选池²) 段）。聚合 686 章蒸馏时跳过它快 ~50x。返回单章值，由
    aggregate_chapter_quantiles 聚合成分位数。无 CJK / 无段落 → None（坏章跳过）。"""
    total = count_chinese(text)
    if total <= 0:
        return None
    per_1000 = 1000.0 / total
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return None
    para_lens = [count_chinese(p) for p in paragraphs]
    para_mean = sum(para_lens) / len(para_lens)
    comma = len(COMMA_PATTERN.findall(text))
    period = max(len(PERIOD_PATTERN.findall(text)), 1)
    ellipsis = len(ELLIPSIS_PATTERN.findall(text))
    exclamation = len(EXCLAMATION_PATTERN.findall(text))
    question = len(QUESTION_PATTERN.findall(text))
    dash = len(DASH_PATTERN.findall(text))
    punc = {
        "comma": comma * per_1000, "period": period * per_1000,
        "comma_period_ratio": comma / period, "ellipsis": ellipsis * per_1000,
        "exclamation": exclamation * per_1000, "question": question * per_1000,
        "dash": dash * per_1000,
    }
    fw = {w: text.count(w) * per_1000 for w in FUNCTION_WORDS}
    return {"paragraph_length_chars": para_mean, "punctuation_per_1k": punc,
            "function_words_per_1k": fw}


def aggregate_chapter_quantiles(chapter_texts: list[str]) -> dict:
    """对一组章节文本聚合 per-章经验分位数（L1a 蒸馏端补 band 数据源 · 北极星①）。

    输出三组分位数桶（每桶各字段都是 calc_quantiles 结果）：
      · paragraph_length_chars : per-章段均长（CJK 字/段）分布 → [p5..p95]
      · punctuation_per_1k      : 各标点 per-1k 密度的 per-章分布
      · function_words_per_1k   : 各功能词 per-1k 频率的 per-章分布
    validate_style 据此把 mean±容差 band 升级为 [p5,p95] 经验 band。坏章自动跳过。"""
    para_vals: list[float] = []
    punc_vals: dict[str, list[float]] = {}
    fw_vals: dict[str, list[float]] = {}
    used = 0
    for text in chapter_texts:
        m = chapter_metrics_lite(text)
        if m is None:
            continue
        used += 1
        para_vals.append(m["paragraph_length_chars"])
        for k, v in m["punctuation_per_1k"].items():
            punc_vals.setdefault(k, []).append(v)
        for w, v in m["function_words_per_1k"].items():
            fw_vals.setdefault(w, []).append(v)
    return {
        "n_chapters_used": used,
        "paragraph_length_chars": calc_quantiles(para_vals),
        "punctuation_per_1k": {k: calc_quantiles(v) for k, v in punc_vals.items()},
        "function_words_per_1k": {w: calc_quantiles(v) for w, v in fw_vals.items()},
    }


def length_distribution(lengths: list[int]) -> dict:
    bins = {"le5": 0, "6to15": 0, "16to30": 0, "31to50": 0, "gt50": 0}
    for length in lengths:
        if length <= 5:
            bins["le5"] += 1
        elif length <= 15:
            bins["6to15"] += 1
        elif length <= 30:
            bins["16to30"] += 1
        elif length <= 50:
            bins["31to50"] += 1
        else:
            bins["gt50"] += 1
    total = len(lengths) or 1
    return {k: round(v / total, 4) for k, v in bins.items()}


# ════════════════════════════════════════════════════════════════════════
# 作者级叙事功能序列提取（advisory · env NARRATIVE_SEQ_MODE 控制）
# ════════════════════════════════════════════════════════════════════════

def narrative_function_raw_scores(text: str) -> dict:
    """单章 7 功能维度原始命中密度（每千 CJK 字命中次数）。

    纯词典计数 / 长度归一——这是 z-score baseline 的输入。单独看意义不大
    （世界观词汇会让 setting 恒高），必须经 score_narrative_function_sequence
    的 z-score 相对显著度才有作者区分意义。"""
    n = max(count_chinese(text), 1)
    out = {}
    for fn, cues in NARRATIVE_FUNCTION_CUES.items():
        hits = sum(text.count(w) for w in cues)
        out[fn] = round(hits / n * 1000.0, 4)
    return out


def _pstdev(values: list[float]) -> float:
    """总体标准差（纯 stdlib · 守零依赖）。空/单值 → 极小正数防除零。"""
    n = len(values)
    if n <= 1:
        return 1e-9
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(var) or 1e-9


def score_narrative_function_sequence(chapter_texts: list[str]) -> dict:
    """从作者多章语料提取**叙事功能序列**（作者专属因果功能链 · advisory）。

    流程（北极星⑤ · 纯启发式黑箱不调 LLM）：
      1) 每章 narrative_function_raw_scores → 7 维原始密度；
      2) 对每个功能维度算全语料 mean/std → 每章该维 z-score（相对显著度）；
         **关键防误判**：z-score 把 always-present 世界观词汇 normalize 掉，
         只留真正 over-index 的结构功能（小验证：原始计数法 90% 章全 setting，
         z-score 法分布均衡 + 命中 face_slap→gain_reward 等真因果链）；
      3) 每章取 z >= 阈值的 top-K 功能作章型标签，主标签拼成序列；
      4) 序列做 bigram/trigram 频次统计 = 作者签名功能链模式。

    返回 advisory dict（永不判决）：
      · mode: "active" | "off"（off 时只回 {mode:"off"}）
      · n_chapters: 实际参与统计的章数（< 2 时 confidence=low，序列无意义）
      · per_chapter_primary: [每章主功能标签]（baseline = 无 over-index）
      · function_distribution: {功能: 占比}（作者整体功能偏好）
      · signature_bigrams / signature_trigrams: [{seq, count, ratio}]（高频因果链）
      · confidence: high(>=30 章) / mid(>=8) / low(<8)——抽取准确率自报短板
      · _note: 写入 skill 的 advisory 使用说明
    """
    if narrative_seq_mode() == "off":
        return {"mode": "off", "_note": "NARRATIVE_SEQ_MODE=off · 叙事功能序列提取已关闭"}

    texts = [t for t in chapter_texts if t and count_chinese(t) > 0]
    n = len(texts)
    if n == 0:
        return {"mode": "active", "n_chapters": 0, "per_chapter_primary": [],
                "function_distribution": {}, "signature_bigrams": [],
                "signature_trigrams": [], "confidence": "low",
                "_note": "无有效章节 · 无法提取叙事功能序列"}

    raw_rows = [narrative_function_raw_scores(t) for t in texts]
    # 每功能维度的语料 baseline（mean/std）
    base_mean, base_std = {}, {}
    for fn in _NARRATIVE_FUNCTION_KEYS:
        col = [r[fn] for r in raw_rows]
        base_mean[fn] = sum(col) / n
        base_std[fn] = _pstdev(col)

    per_chapter_primary: list[str] = []
    per_chapter_labels: list[list[str]] = []
    for r in raw_rows:
        z = {fn: (r[fn] - base_mean[fn]) / base_std[fn] for fn in _NARRATIVE_FUNCTION_KEYS}
        ranked = sorted(z.items(), key=lambda kv: -kv[1])
        over = [fn for fn, zv in ranked if zv >= _NARR_SEQ_Z_THRESHOLD][:_NARR_SEQ_TOP_K]
        per_chapter_labels.append(over)
        per_chapter_primary.append(over[0] if over else "baseline")

    # 功能整体分布（作者偏好画像 · 含 baseline）
    dist_counter = Counter(per_chapter_primary)
    function_distribution = {
        k: round(v / n, 4) for k, v in dist_counter.most_common()
    }

    # 签名功能链：bigram / trigram（剔除 baseline → 只看真功能间的因果转移）
    real_seq = [s for s in per_chapter_primary]  # 保留 baseline 占位以反映真实间隔

    def _ngrams(seq, k):
        grams = Counter()
        for i in range(len(seq) - k + 1):
            window = seq[i:i + k]
            if "baseline" in window:  # 含 baseline 的 n-gram 无功能链意义，跳过
                continue
            grams["→".join(window)] += 1
        total = sum(grams.values()) or 1
        return [
            {"seq": g, "count": c, "ratio": round(c / total, 4)}
            for g, c in grams.most_common(8)
        ]

    confidence = "high" if n >= 30 else "mid" if n >= 8 else "low"

    return {
        "mode": "active",
        "n_chapters": n,
        "z_threshold": _NARR_SEQ_Z_THRESHOLD,
        "per_chapter_primary": per_chapter_primary,
        "function_distribution": function_distribution,
        "signature_bigrams": _ngrams(real_seq, 2),
        "signature_trigrams": _ngrams(real_seq, 3),
        "confidence": confidence,
        "_note": (
            "advisory 软提示（顾问非法官·绝不 hard_gate·不干涉模型创作判断）："
            "signature_bigrams/trigrams 是从作者语料蒸馏的**专属因果叙事功能链**"
            "（如 face_slap→gain_reward = 打脸后获益），比 Save-the-Cat 通用节拍更深、"
            "作者专属。写作时作软提示参考——让连续故事块的功能转移贴近作者签名节奏，"
            "而非默认 LLM 高频模板（中文网文同质化结构层根因）。confidence=low 时"
            "（<8 章）序列不稳定·仅供参考。抽取为纯启发式·准确率存疑·模型自行取舍。"
        ),
    }


# ───────── R7 W2 Batch-E：情感弧 fractal 指纹（Hurst + ApEn · 纯 Python · 零依赖）─────────
# 调研锚（R7-W1）：长篇叙事情感弧呈分形/长程相关结构（Hurst H≠0.5）；ApEn 衡量序列复杂度。
# 北极星⑤：仅作为作者档 ECDF 基线产出，不设通用阈值；样本不足时降级返回 None。
def compute_hurst_rs(series, min_n: int = 10) -> float | None:
    """R/S 重标极差法 Hurst 指数（纯 stdlib·O(n log n)）。

    H≈0.5 随机游走；H>0.5 长程正相关（趋势性）；H<0.5 反持续（振荡）。
    样本不足或方差为 0 → None（不强算·北极星⑤）。
    """
    n = len(series)
    if n < min_n:
        return None
    x = [float(v) for v in series]
    # 多尺度回归：lag ∈ [2 .. n//2] 对数等距取 ≤8 点，平衡稳定性与速度。
    lags = []
    lo, hi = 2, max(4, n // 2)
    k = 6
    step = max(1, (hi - lo) // (k - 1) or 1)
    for i in range(k):
        lg = lo + i * step
        if lg <= hi and lg not in lags:
            lags.append(lg)
    if len(lags) < 3:
        return None
    log_lags, log_rs = [], []
    for lag in lags:
        # 切成 m 个不重叠 lag 长子段，计算每段 R/S 后取均值。
        m = n // lag
        if m < 1:
            continue
        rs_vals = []
        for i in range(m):
            seg = x[i * lag:(i + 1) * lag]
            if len(seg) < 2:
                continue
            mu = sum(seg) / len(seg)
            dev = [v - mu for v in seg]
            cum = []
            acc = 0.0
            for d in dev:
                acc += d
                cum.append(acc)
            R = max(cum) - min(cum)
            var = sum(d * d for d in dev) / len(seg)
            S = math.sqrt(var)
            if S > 1e-12 and R > 0:
                rs_vals.append(R / S)
        if rs_vals:
            mean_rs = sum(rs_vals) / len(rs_vals)
            if mean_rs > 0:
                log_lags.append(math.log(lag))
                log_rs.append(math.log(mean_rs))
    if len(log_lags) < 3:
        return None
    # OLS 斜率 = Hurst 指数。
    mean_x = sum(log_lags) / len(log_lags)
    mean_y = sum(log_rs) / len(log_rs)
    num = sum((lx - mean_x) * (ly - mean_y) for lx, ly in zip(log_lags, log_rs))
    den = sum((lx - mean_x) ** 2 for lx in log_lags)
    if den < 1e-12:
        return None
    h = num / den
    # 钳到 [0, 1]·偶发数值越界视为退化估计。
    return round(max(0.0, min(1.0, h)), 4)


def compute_approx_entropy(series, m: int = 2, r: float | None = None) -> float | None:
    """近似熵 Approximate Entropy（Pincus 1991·纯 stdlib·O(n²) 适用 n≤300 序列）。

    ApEn 越大序列越无序；典型情感弧 0.3-1.5 区间。r 默认 0.2 * std。
    样本不足（n < m+2）或方差为 0 → None。
    """
    x = [float(v) for v in series]
    n = len(x)
    if n < m + 2:
        return None
    mean = sum(x) / n
    var = sum((v - mean) ** 2 for v in x) / n
    std = math.sqrt(var)
    if std < 1e-9:
        return None
    if r is None:
        r = 0.2 * std

    def _phi(mm: int) -> float:
        vecs = [tuple(x[i:i + mm]) for i in range(n - mm + 1)]
        total = 0.0
        for vi in vecs:
            ci = 0
            for vj in vecs:
                # Chebyshev 距离 ≤ r
                if max(abs(a - b) for a, b in zip(vi, vj)) <= r:
                    ci += 1
            if ci > 0:
                total += math.log(ci / len(vecs))
        return total / len(vecs)

    try:
        apen = _phi(m) - _phi(m + 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(max(0.0, apen), 4)


def analyze_text(text: str) -> dict:
    total_chinese = count_chinese(text)
    per_1000 = 1000 / total_chinese if total_chinese > 0 else 0

    paragraphs = split_paragraphs(text)
    sentences = split_sentences(text)

    para_char_lengths = [count_chinese(p) for p in paragraphs]
    sent_char_lengths = [count_chinese(s) for s in sentences]

    para_sent_counts = []
    for p in paragraphs:
        sents_in_para = split_sentences(p)
        para_sent_counts.append(max(len(sents_in_para), 1))

    single_sent_paras = sum(1 for c in para_sent_counts if c == 1)
    single_sent_ratio = single_sent_paras / len(paragraphs) if paragraphs else 0

    # E3：拟声段豁免——计算极短段比例时排除拟声段
    non_onomatopoeia_paras = [p for p in paragraphs if not is_onomatopoeia(p)]
    non_onomatopoeia_lengths = [count_chinese(p) for p in non_onomatopoeia_paras]
    ultra_short_paras = sum(1 for length in non_onomatopoeia_lengths if length <= 5)
    ultra_short_ratio = (
        ultra_short_paras / len(non_onomatopoeia_paras)
        if non_onomatopoeia_paras else 0
    )
    # 保留旧字段含义（含拟声段）作为对照
    ultra_short_paras_raw = sum(1 for length in para_char_lengths if length <= 5)
    ultra_short_ratio_raw = (
        ultra_short_paras_raw / len(paragraphs) if paragraphs else 0
    )

    ultra_long_sents = sum(1 for length in sent_char_lengths if length >= 50)

    dialogue_ratio = calc_dialogue_ratio(text)

    # E6：分离对话 vs 引号化独白
    dialogue_chars, monologue_chars = _split_dialogue_vs_monologue(text)
    total_text_chars = count_chinese(text) or 1
    inner_monologue_ratio = monologue_chars / total_text_chars
    dialogue_only_ratio = dialogue_chars / total_text_chars

    comma_count = len(COMMA_PATTERN.findall(text))
    period_count = max(len(PERIOD_PATTERN.findall(text)), 1)
    ellipsis_count = len(ELLIPSIS_PATTERN.findall(text))
    exclamation_count = len(EXCLAMATION_PATTERN.findall(text))
    question_count = len(QUESTION_PATTERN.findall(text))
    dash_count = len(DASH_PATTERN.findall(text))

    func_word_freq = {}
    for word in FUNCTION_WORDS:
        count = text.count(word)
        func_word_freq[word] = round(count * per_1000, 2)

    banned_hits = {}
    for bw in BANNED_WORDS:
        c = text.count(bw)
        if c > 0:
            banned_hits[bw] = c

    quota_hits = {}
    for qw in QUOTA_WORDS:
        c = text.count(qw)
        if c > 0:
            quota_hits[qw] = c

    ai_tag_hits = {}
    for tag in AI_DIALOGUE_TAGS:
        c = text.count(tag)
        if c > 0:
            ai_tag_hits[tag] = c

    bracket_settings = BRACKET_SETTING.findall(text)

    # E3：拟声段计数用统一 is_onomatopoeia 判定（兼容白名单+破折号）
    onomatopoeia_paras = [p for p in paragraphs if is_onomatopoeia(p)]

    # E1+E2+E4：用 classify_speakers 区分 active / quoted
    active_speakers, quoted_speakers = classify_speakers(text, paragraphs)
    speakers = active_speakers  # 向后兼容：speakers 等价于 active

    # v16: 修辞手法计数（移植自AI_NovelGenerator）
    simile_count = len(re.findall(r'像是?[^，。]{2,15}[一-鿿]|仿佛[^，。]{2,15}|如同[^，。]{2,15}|好似[^，。]{2,15}', text))
    parallelism_count = len(re.findall(r'([一-鿿]{2,4})[，,][一-鿿]{2,4}[，,]\1', text))
    rhetorical_q_count = len(re.findall(r'难道|怎能|岂不|何尝|哪里.*[？?]', text))

    # v16: 词汇丰富度 (TTR) — 计算文体学中区分度最高的特征
    words_2char = re.findall(r'[一-鿿]{2,4}', text)
    unique_words = len(set(words_2char))
    total_words = max(len(words_2char), 1)
    ttr = round(unique_words / total_words, 4)
    # Hapax: 只出现1次的词占比
    word_counts = Counter(words_2char)
    hapax = sum(1 for c in word_counts.values() if c == 1)
    hapax_ratio = round(hapax / total_words, 4) if total_words > 0 else 0

    return {
        "total_chinese_chars": total_chinese,
        "paragraph_count": len(paragraphs),
        "sentence_count": len(sentences),
        "sentence_stats": calc_stats(sent_char_lengths),
        "sentence_length_distribution": length_distribution(sent_char_lengths),
        "paragraph_stats": calc_stats(para_char_lengths),
        "paragraph_length_distribution": length_distribution(para_char_lengths),
        "para_sentence_stats": calc_stats([float(x) for x in para_sent_counts]),
        "single_sentence_para_ratio": round(single_sent_ratio, 4),
        "ultra_short_para_ratio": round(ultra_short_ratio, 4),
        "ultra_short_para_ratio_raw": round(ultra_short_ratio_raw, 4),
        "ultra_long_sentence_count": ultra_long_sents,
        "dialogue_ratio": round(dialogue_ratio, 4),
        "dialogue_only_ratio": round(dialogue_only_ratio, 4),
        "inner_monologue_ratio": round(inner_monologue_ratio, 4),
        "punctuation_density_per_1000": {
            "comma": round(comma_count * per_1000, 2),
            "period": round(period_count * per_1000, 2),
            "comma_period_ratio": round(comma_count / period_count, 2),
            "ellipsis": round(ellipsis_count * per_1000, 2),
            "exclamation": round(exclamation_count * per_1000, 2),
            "question": round(question_count * per_1000, 2),
            "dash": round(dash_count * per_1000, 2),
        },
        "function_word_fingerprint_per_1000": func_word_freq,
        "banned_word_hits": banned_hits,
        "quota_word_hits": quota_hits,
        "ai_dialogue_tag_hits": ai_tag_hits,
        "bracket_setting_count": len(bracket_settings),
        "bracket_settings": bracket_settings,
        "onomatopoeia_para_count": len(onomatopoeia_paras),
        "speaker_count": len(speakers),
        "speakers": sorted(speakers),
        "active_speaker_count": len(active_speakers),
        "active_speakers": sorted(active_speakers),
        "quoted_speaker_count": len(quoted_speakers),
        "quoted_speakers": sorted(quoted_speakers)[:20],
        "vocabulary_richness": {
            "type_token_ratio": ttr,
            "hapax_ratio": hapax_ratio,
            "unique_words": unique_words,
            "total_words": total_words,
        },
        "rhetoric_counts": {
            "simile": simile_count,
            "parallelism": parallelism_count,
            "rhetorical_question": rhetorical_q_count,
        },
        # 作者级叙事功能序列：单章只能给原始密度（per-章 z-score 需全语料 baseline，
        # 由 score_narrative_function_sequence 批量算）。advisory · 不影响任何判决。
        "narrative_function_raw_scores": narrative_function_raw_scores(text),
    }


def cosine_similarity(a: dict, b: dict) -> float:
    keys = set(list(a.keys()) + list(b.keys()))
    va = [a.get(k, 0) for k in keys]
    vb = [b.get(k, 0) for k in keys]
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(x * x for x in vb))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def pct_diff(a: float, b: float) -> float:
    base = max(abs(a), abs(b), 0.01)
    return abs(a - b) / base * 100


def compare_profiles(ref: dict, gen: dict) -> dict:
    dims = []

    def add_dim(name: str, ref_val, gen_val, weight: float = 1.0):
        if isinstance(ref_val, (int, float)) and isinstance(gen_val, (int, float)):
            gap = pct_diff(ref_val, gen_val)
            score = max(0, 100 - gap)
        elif isinstance(ref_val, dict) and isinstance(gen_val, dict):
            sim = cosine_similarity(ref_val, gen_val)
            score = sim * 100
            gap = (1 - sim) * 100
        else:
            gap = 0
            score = 50
        dims.append({
            "name": name,
            "reference": ref_val,
            "generated": gen_val,
            "gap_pct": round(gap, 2),
            "score": round(score, 2),
            "weight": weight,
            "flagged": gap >= 20,
        })

    rs = ref.get("sentence_stats", {})
    gs = gen.get("sentence_stats", {})
    add_dim("句长均值", rs.get("mean", 0), gs.get("mean", 0), 2.0)
    add_dim("句长标准差", rs.get("std", 0), gs.get("std", 0), 1.5)
    add_dim("句长分布", ref.get("sentence_length_distribution", {}),
            gen.get("sentence_length_distribution", {}), 2.0)

    rp = ref.get("paragraph_stats", {})
    gp = gen.get("paragraph_stats", {})
    add_dim("段落均长", rp.get("mean", 0), gp.get("mean", 0), 2.0)
    add_dim("段落长度分布", ref.get("paragraph_length_distribution", {}),
            gen.get("paragraph_length_distribution", {}), 2.0)
    add_dim("极短段占比", ref.get("ultra_short_para_ratio", 0),
            gen.get("ultra_short_para_ratio", 0), 1.5)
    add_dim("单句成段率", ref.get("single_sentence_para_ratio", 0),
            gen.get("single_sentence_para_ratio", 0), 1.0)

    add_dim("对话占比", ref.get("dialogue_ratio", 0),
            gen.get("dialogue_ratio", 0), 2.5)

    rpunc = ref.get("punctuation_density_per_1000", {})
    gpunc = gen.get("punctuation_density_per_1000", {})
    add_dim("逗句比", rpunc.get("comma_period_ratio", 0),
            gpunc.get("comma_period_ratio", 0), 1.0)
    add_dim("省略号密度", rpunc.get("ellipsis", 0), gpunc.get("ellipsis", 0), 1.0)
    add_dim("感叹号密度", rpunc.get("exclamation", 0), gpunc.get("exclamation", 0), 1.0)
    add_dim("问号密度", rpunc.get("question", 0), gpunc.get("question", 0), 0.5)
    add_dim("破折号密度", rpunc.get("dash", 0), gpunc.get("dash", 0), 1.0)

    add_dim("功能词指纹", ref.get("function_word_fingerprint_per_1000", {}),
            gen.get("function_word_fingerprint_per_1000", {}), 3.0)

    add_dim("拟声独立段", ref.get("onomatopoeia_para_count", 0),
            gen.get("onomatopoeia_para_count", 0), 1.0)
    add_dim("方括号设定", ref.get("bracket_setting_count", 0),
            gen.get("bracket_setting_count", 0), 0.5)
    add_dim("群戏人数", ref.get("speaker_count", 0),
            gen.get("speaker_count", 0), 1.0)

    total_weight = sum(d["weight"] for d in dims)
    weighted_score = (
        sum(d["score"] * d["weight"] for d in dims) / total_weight
        if total_weight > 0
        else 0
    )
    flagged_count = sum(1 for d in dims if d["flagged"])

    gen_banned = gen.get("banned_word_hits", {})
    banned_penalty = min(len(gen_banned) * 2, 10)

    gen_ai_tags = gen.get("ai_dialogue_tag_hits", {})
    ai_tag_penalty = min(len(gen_ai_tags) * 3, 15)

    final_score = max(0, weighted_score - banned_penalty - ai_tag_penalty)

    return {
        "dimensions": dims,
        "flagged_count": flagged_count,
        "total_dimensions": len(dims),
        "weighted_score": round(weighted_score, 2),
        "banned_word_penalty": banned_penalty,
        "ai_tag_penalty": ai_tag_penalty,
        "final_programmatic_score": round(final_score, 2),
        "grade": (
            "A" if final_score >= 90 else
            "B" if final_score >= 80 else
            "C" if final_score >= 70 else "D"
        ),
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python style_analyzer.py <文件> [--output out.json] "
              "[--compare <文件B>] [--narrative-seq]")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    output_path = None
    compare_path = None
    batch_mode = False
    narrative_seq = False

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_path = Path(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--compare" and i + 1 < len(sys.argv):
            compare_path = Path(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--batch":
            batch_mode = True
            i += 1
        elif sys.argv[i] == "--narrative-seq":
            narrative_seq = True
            i += 1
        else:
            i += 1

    if narrative_seq and file_path.is_dir():
        # 作者级叙事功能序列蒸馏：按章号排序读全目录，跑跨章功能链提取。
        # 章号自然序对序列语义至关重要（功能链是时间序因果）。
        files = sorted(file_path.glob("第*章.txt")) or sorted(file_path.glob("*.txt"))
        chapter_texts = [f.read_text(encoding="utf-8") for f in files]
        print(f"  叙事功能序列：读 {len(chapter_texts)} 章", file=sys.stderr)
        output = {
            "narrative_function_sequence": score_narrative_function_sequence(chapter_texts),
            "source_dir": str(file_path),
            "chapter_files": [f.name for f in files],
        }
    elif batch_mode and file_path.is_dir():
        results = {}
        for f in sorted(file_path.glob("*.txt")):
            text = f.read_text(encoding="utf-8")
            results[f.name] = analyze_text(text)
            print(f"  {f.name}: {results[f.name]['total_chinese_chars']} chars", file=sys.stderr)
        output = {"batch_results": results, "file_count": len(results)}
        if results:
            all_sent = [r["sentence_stats"]["mean"] for r in results.values()]
            all_para = [r["paragraph_stats"]["mean"] for r in results.values()]
            all_dial = [r["dialogue_ratio"] for r in results.values()]
            output["aggregate"] = {
                "sentence_length_mean": calc_stats(all_sent),
                "paragraph_length_mean": calc_stats(all_para),
                "dialogue_ratio": calc_stats(all_dial),
            }
    elif compare_path:
        text_a = file_path.read_text(encoding="utf-8")
        text_b = compare_path.read_text(encoding="utf-8")
        profile_a = analyze_text(text_a)
        profile_b = analyze_text(text_b)
        comparison = compare_profiles(profile_a, profile_b)
        output = {
            "reference": {"file": str(file_path), "profile": profile_a},
            "generated": {"file": str(compare_path), "profile": profile_b},
            "comparison": comparison,
        }
    else:
        text = file_path.read_text(encoding="utf-8")
        output = {"file": str(file_path), "profile": analyze_text(text)}

    json_str = json.dumps(output, ensure_ascii=False, indent=2)
    if output_path:
        output_path.write_text(json_str, encoding="utf-8")
        print(f"saved to {output_path}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
