// 「AI Agent 进化之路」—— 13 页简约风构建脚本（pptxgenjs）
// 设计系统：近黑/白双底（三明治结构）+ 信号橙强调色 + 底部进度时间线贯穿每页
const pptxgen = require("pptxgenjs");

const p = new pptxgen();
p.layout = "LAYOUT_WIDE"; // 13.33 × 7.5"
p.author = "lzy";
p.title = "AI Agent 进化之路";

// ---------- 调色板（背景 → 主色 → 强调色，全篇仅用这些常量） ----------
const INK = "14171C"; // 深色页背景
const PAPER = "FFFFFF"; // 浅色页背景
const TEXT = "1B1F27"; // 浅底正文
const MUTED = "6B7280"; // 浅底弱化
const MUTED_D = "9AA3AE"; // 深底弱化
const LINE = "E6E8EB"; // 浅底发丝线
const LINE_D = "2B323B"; // 深底发丝线
const ACCENT = "E8590C"; // 信号橙（唯一强调色）
const TINT = "FCEEE6"; // 强调色淡染（横幅）
const CARD = "F7F8FA"; // 卡片浅灰
const WM = "20262E"; // 深底水印数字
const BORDER = "D6DAE0"; // 浅底描边

const F = "微软雅黑";
const FM = "Consolas";
const W = 13.33, H = 7.5;

// ---------- 贯穿母题：底部进度时间线 ----------
// active: -1 无高亮（封面/全景）｜0/1/2 当前阶段｜3 全部点亮（收尾）
function footer(s, active, dark) {
  const lineY = 7.07, x0 = 0.5, dx = [0.5, 4.78, 9.06], xEnd = 12.83;
  s.addShape(p.shapes.LINE, { x: x0, y: lineY, w: xEnd - x0, h: 0, line: { color: dark ? LINE_D : LINE, width: 1.25 } });
  if (active === 3) {
    s.addShape(p.shapes.LINE, { x: x0, y: lineY, w: xEnd - x0, h: 0, line: { color: ACCENT, width: 1.75 } });
  } else if (active >= 0) {
    s.addShape(p.shapes.LINE, { x: x0, y: lineY, w: dx[active] - x0 + 0.05, h: 0, line: { color: ACCENT, width: 1.75 } });
  }
  const stages = ["01 Prompt Engineering", "02 Context Engineering", "03 Harness Engineering"];
  stages.forEach((t, i) => {
    const on = active === 3 || i === active;
    s.addShape(p.shapes.OVAL, { x: dx[i], y: lineY - 0.05, w: 0.1, h: 0.1, fill: { color: on ? ACCENT : (dark ? "3A424C" : "C9CED6") } });
    s.addText(t, { x: dx[i] + 0.2, y: lineY - 0.155, w: 3.6, h: 0.31, fontFace: FM, fontSize: 12, charSpacing: 1, margin: 0, valign: "middle", bold: on, color: on ? (dark ? "FFFFFF" : TEXT) : (dark ? MUTED_D : MUTED) });
  });
}

// ---------- 浅色内容页页头 ----------
function header(s, kicker, title, kMono) {
  s.background = { color: PAPER };
  s.addText(kicker, { x: 0.5, y: 0.42, w: 9, h: 0.3, fontFace: kMono ? FM : F, fontSize: 12.5, bold: true, color: ACCENT, charSpacing: 2, margin: 0 });
  s.addText(title, { x: 0.5, y: 0.72, w: 12.33, h: 0.62, fontFace: F, fontSize: 28, bold: true, color: TEXT, margin: 0 });
}

function hairline(s, x, y, w) {
  s.addShape(p.shapes.LINE, { x, y, w, h: 0, line: { color: LINE, width: 1 } });
}

// ---------- 深色章节页 ----------
function divider(num, kicker, title, subtitle, contents, active, note) {
  const s = p.addSlide();
  s.background = { color: INK };
  s.addText(num, { x: 8.7, y: 1.1, w: 4.13, h: 3.0, fontFace: FM, fontSize: 160, bold: true, color: WM, align: "right", valign: "top", margin: 0 });
  s.addText(kicker, { x: 0.7, y: 2.25, w: 8, h: 0.3, fontFace: FM, fontSize: 13, color: ACCENT, charSpacing: 2, margin: 0 });
  s.addText(title, { x: 0.7, y: 2.6, w: 8.0, h: 0.95, fontFace: F, fontSize: 46, bold: true, color: "FFFFFF", margin: 0 });
  s.addText(subtitle, { x: 0.72, y: 3.78, w: 8.0, h: 0.45, fontFace: F, fontSize: 17, color: MUTED_D, margin: 0 });
  s.addText(contents, { x: 0.72, y: 4.55, w: 8.0, h: 0.35, fontFace: F, fontSize: 14, color: "D5DAE0", charSpacing: 1, margin: 0 });
  footer(s, active, true);
  s.addNotes(note);
  return s;
}

// ============================================================
// S1 封面
// ============================================================
{
  const s = p.addSlide();
  s.background = { color: INK };
  s.addShape(p.shapes.RECTANGLE, { x: 0.5, y: 0.66, w: 0.14, h: 0.14, fill: { color: ACCENT } });
  s.addText("EVOLUTION OF AI AGENTS", { x: 0.78, y: 0.57, w: 7, h: 0.32, fontFace: FM, fontSize: 13, color: MUTED_D, charSpacing: 3, margin: 0 });
  s.addText("2020 — 2026 · 三个阶段 · 一条主线", { x: 0.5, y: 2.0, w: 9, h: 0.32, fontFace: F, fontSize: 13, color: MUTED_D, charSpacing: 1, margin: 0 });
  s.addText("AI Agent 进化之路", { x: 0.5, y: 2.4, w: 12.3, h: 1.15, fontFace: F, fontSize: 58, bold: true, color: "FFFFFF", margin: 0 });
  s.addText("从 Prompt 到 Context 再到 Harness —— 人与大模型协作方式的三次跃迁", { x: 0.5, y: 3.72, w: 11.8, h: 0.42, fontFace: F, fontSize: 17, color: MUTED_D, margin: 0 });
  // 三阶段推进（封面主视觉之一）
  const seq = [
    { t: "Prompt Engineering", x: 1.05, w: 2.75 },
    { t: "→", x: 3.85, w: 0.5, arrow: true },
    { t: "Context Engineering", x: 4.4, w: 3.1 },
    { t: "→", x: 7.55, w: 0.5, arrow: true },
    { t: "Harness Engineering", x: 8.1, w: 3.2 },
  ];
  seq.forEach((it) => {
    s.addText(it.t, { x: it.x, y: 4.85, w: it.w, h: 0.35, fontFace: it.arrow ? F : FM, fontSize: it.arrow ? 16 : 15, bold: !it.arrow, color: it.arrow ? ACCENT : "FFFFFF", align: "center", valign: "middle", margin: 0 });
  });
  footer(s, -1, true);
  s.addNotes("开场：用一条时间线讲清 AI Agent 的三次范式迁移——怎么问、喂什么、怎么做事。");
}

// ============================================================
// S2 全景时间线
// ============================================================
{
  const s = p.addSlide();
  header(s, "TIMELINE · 2020 — 2026", "Agent 进化全景：从文本补全到自主引擎", true);
  // 时代分组标签
  const eras = [
    { t: "01 · PROMPT 时代", cx: 2.61 },
    { t: "02 · CONTEXT 时代", cx: 6.65 },
    { t: "03 · HARNESS 时代", cx: 10.69 },
  ];
  eras.forEach((e) => s.addText(e.t, { x: e.cx - 1.5, y: 1.62, w: 3.0, h: 0.3, fontFace: F, fontSize: 12.5, color: MUTED, charSpacing: 1, align: "center", margin: 0 }));
  // 主时间线
  const lineY = 3.72;
  s.addShape(p.shapes.LINE, { x: 0.9, y: lineY, w: 11.53, h: 0, line: { color: LINE, width: 1.5 } });
  const nodes = [
    { x: 1.6, year: "2020", h1: "GPT-3 发布", h2: "提示词成为模型接口" },
    { x: 3.62, year: "2022", h1: "ChatGPT 上线", h2: "提示词工程走向大众" },
    { x: 5.64, year: "2023", h1: "AutoGPT 引爆", h2: "RAG 与 Function Calling 普及" },
    { x: 7.66, year: "2024", h1: "长上下文成熟", h2: "Context 工程体系化" },
    { x: 9.68, year: "2025", h1: "编码 Agent 落地", h2: "MCP 等协议兴起" },
    { x: 11.7, year: "2026", h1: "个人 Agent 爆发", h2: "OpenClaw 走进日常" },
  ];
  nodes.forEach((n, i) => {
    const last = i === nodes.length - 1;
    s.addShape(p.shapes.OVAL, { x: n.x - 0.065, y: lineY - 0.065, w: 0.13, h: 0.13, fill: { color: last ? ACCENT : TEXT } });
    s.addText(n.year, { x: n.x - 0.7, y: lineY + 0.2, w: 1.4, h: 0.3, fontFace: FM, fontSize: 14, bold: true, color: last ? ACCENT : TEXT, align: "center", margin: 0 });
    const above = i % 2 === 0;
    s.addText([
      { text: n.h1, options: { fontSize: 13, bold: true, color: TEXT, breakLine: true } },
      { text: n.h2, options: { fontSize: 12, color: MUTED } },
    ], { x: n.x - 1.05, y: above ? 2.0 : 4.32, w: 2.1, h: 1.5, fontFace: F, align: "center", valign: above ? "bottom" : "top", paraSpaceAfter: 4, margin: 0 });
  });
  footer(s, -1, false);
  s.addNotes("先给全景：六个节点对应三个阶段，后面逐一展开。2026 是现在进行时。");
}

// ============================================================
// S3 章节页 01
// ============================================================
divider("01", "STAGE 01 · 2020 — 2022", "Prompt Engineering", "提示词工程 —— 把人类意图，写成模型读得懂的文本", "起源 / 核心技巧 / 局限性", 0, "进入第一阶段：提示词工程，一切从「怎么问」开始。");

// ============================================================
// S4 PE 起源
// ============================================================
{
  const s = p.addSlide();
  header(s, "STAGE 01 · 起源", "起源：提示词是第一种「模型编程语言」");
  const rows = [
    { tag: "2020", tagF: FM, h1: "GPT-3 与少样本学习", h2: "论文《Language Models are Few-Shot Learners》：不更新参数，仅凭提示中的示例完成新任务" },
    { tag: "2022", tagF: FM, h1: "ChatGPT 上线", h2: "5 天突破 100 万用户，普通人第一次用自然语言「编程」大模型" },
    { tag: "共识", tagF: F, h1: "同模型，不同命", h2: "提示词写法不同，产出质量天差地别 —— Prompt 成为一门手艺" },
  ];
  rows.forEach((r, i) => {
    const y = 1.8 + i * 1.35;
    s.addText(r.tag, { x: 0.5, y: y + 0.08, w: 0.95, h: 0.35, fontFace: r.tagF, fontSize: 14, bold: true, color: ACCENT, margin: 0 });
    s.addText([
      { text: r.h1, options: { fontSize: 15, bold: true, color: TEXT, breakLine: true } },
      { text: r.h2, options: { fontSize: 12.5, color: MUTED } },
    ], { x: 1.55, y, w: 5.55, h: 1.1, fontFace: F, paraSpaceAfter: 5, margin: 0 });
    if (i < 2) hairline(s, 0.5, y + 1.2, 6.6);
  });
  s.addText("资料：OpenAI GPT-3 论文（2020）；Sam Altman（2022）", { x: 0.5, y: 5.85, w: 6.6, h: 0.3, fontFace: F, fontSize: 12, color: MUTED, margin: 0 });
  // 右侧链路图：Prompt 是唯一接口
  const chain = [
    { t: "人的意图 · 业务目标", hl: false },
    { t: "提示词 Prompt · 手写文本", hl: true },
    { t: "模型 GPT · 概率补全", hl: false },
    { t: "产出 · 一次性的回答", hl: false },
  ];
  chain.forEach((c, i) => {
    const y = 1.8 + i * 1.04;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 7.6, y, w: 4.6, h: 0.62, rectRadius: 0.06, fill: { color: PAPER }, line: { color: c.hl ? ACCENT : BORDER, width: c.hl ? 1.5 : 1 } });
    s.addText(c.t, { x: 7.6, y, w: 4.6, h: 0.62, fontFace: F, fontSize: 13, bold: c.hl, color: c.hl ? ACCENT : TEXT, align: "center", valign: "middle", margin: 0 });
    if (i < 3) s.addShape(p.shapes.LINE, { x: 9.9, y: y + 0.66, w: 0, h: 0.34, line: { color: MUTED_D, width: 1.5, endArrowType: "triangle" } });
  });
  s.addText("Prompt = 人与模型之间唯一的接口", { x: 7.6, y: 6.1, w: 4.6, h: 0.3, fontFace: F, fontSize: 12, color: MUTED, align: "center", margin: 0 });
  footer(s, 0, false);
  s.addNotes("强调历史性突破：不训练参数、只写文本就能调用智能；ChatGPT 把它交给每个人。");
}

// ============================================================
// S5 PE 核心技巧
// ============================================================
{
  const s = p.addSlide();
  header(s, "STAGE 01 · 核心技巧", "五个核心技巧，撑起提示词工程");
  const rows = [
    { n: "01", h1: "少样本示例", h2: "在提示里给出输入输出示例，让模型照猫画虎", en: "Few-shot" },
    { n: "02", h1: "思维链", h2: "「让我们一步步思考」，推理题正确率大幅提升", en: "Chain-of-Thought" },
    { n: "03", h1: "角色设定", h2: "先给模型一个身份，再交代任务与口吻", en: "Role Prompting" },
    { n: "04", h1: "结构化输出", h2: "用 JSON / 字段约束格式，下游程序可直接解析", en: "Structured Output" },
    { n: "05", h1: "指令分层", h2: "系统提示定规矩，用户消息派任务，分隔符划边界", en: "System / User" },
  ];
  rows.forEach((r, i) => {
    const y = 1.75 + i * 0.9;
    s.addText(r.n, { x: 0.5, y: y + 0.25, w: 0.55, h: 0.4, fontFace: FM, fontSize: 15, bold: true, color: ACCENT, valign: "middle", margin: 0 });
    s.addText(r.h1, { x: 1.2, y: y + 0.25, w: 3.0, h: 0.4, fontFace: F, fontSize: 15, bold: true, color: TEXT, valign: "middle", margin: 0 });
    s.addText(r.h2, { x: 4.35, y: y + 0.25, w: 6.0, h: 0.4, fontFace: F, fontSize: 12.5, color: MUTED, valign: "middle", margin: 0 });
    s.addText(r.en, { x: 10.45, y: y + 0.25, w: 2.38, h: 0.4, fontFace: FM, fontSize: 12, color: MUTED_D, align: "right", valign: "middle", margin: 0 });
    if (i < 4) hairline(s, 0.5, y + 0.9, 12.33);
  });
  footer(s, 0, false);
  s.addNotes("记忆线索：给示例、给推理、给身份、给格式、给结构。全部只为「把一次意图说清楚」。");
}

// ============================================================
// S6 PE 局限性
// ============================================================
{
  const s = p.addSlide();
  header(s, "STAGE 01 · 局限性", "天花板：提示词管不住的四件事");
  const cards = [
    { t: "无状态", en: "Stateless", d: "模型不记得上一轮说过什么，每个会话都要从头交代背景" },
    { t: "窗口有限", en: "Limited Window", d: "上下文装不下企业知识，文档一长就被截断或遗忘" },
    { t: "结果脆弱", en: "Fragility", d: "措辞、顺序稍变，输出质量骤降，难以稳定复现" },
    { t: "无法触达", en: "Disconnected", d: "静态文本接不上数据库与实时系统，模型被困在「文本世界」" },
  ];
  cards.forEach((c, i) => {
    const x = i % 2 === 0 ? 0.5 : 6.98, y = i < 2 ? 1.7 : 3.85;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w: 5.85, h: 1.95, rectRadius: 0.08, fill: { color: CARD } });
    s.addText(c.t, { x: x + 0.28, y: y + 0.24, w: 3.4, h: 0.36, fontFace: F, fontSize: 15.5, bold: true, color: TEXT, margin: 0 });
    s.addText(c.en, { x: x + 3.7, y: y + 0.27, w: 1.87, h: 0.3, fontFace: FM, fontSize: 12, color: ACCENT, align: "right", margin: 0 });
    s.addText(c.d, { x: x + 0.28, y: y + 0.72, w: 5.3, h: 1.05, fontFace: F, fontSize: 12.5, color: MUTED, margin: 0 });
  });
  // 收束横幅（通栏淡染，为下一阶段铺垫）
  s.addShape(p.shapes.RECTANGLE, { x: 0, y: 6.08, w: 13.33, h: 0.55, fill: { color: TINT } });
  s.addText("提示词决定「怎么问」，却决定不了「提问时模型知道什么」—— 缺的信息，得有人送进窗口。", { x: 0.75, y: 6.08, w: 12.0, h: 0.55, fontFace: F, fontSize: 14, bold: true, color: TEXT, valign: "middle", margin: 0 });
  footer(s, 0, false);
  s.addNotes("四个局限互相关联，核心是「模型不知道窗口外的事」——引出下一阶段：上下文工程。");
}

// ============================================================
// S7 章节页 02
// ============================================================
divider("02", "STAGE 02 · 2023 — 2025", "Context Engineering", "上下文工程 —— 在有限的窗口里，为模型装配「对的信息」", "为什么 Context 更重要 / RAG / Memory / Tool Use", 1, "进入第二阶段：从打磨「一句话」升级为管理「整个窗口」。");

// ============================================================
// S8 为什么 Context 更重要
// ============================================================
{
  const s = p.addSlide();
  header(s, "STAGE 02 · 为什么是 Context", "模型已经够聪明，稀缺的是「对的信息」");
  const rows = [
    { h1: "上下文决定上限", h2: "同一个模型，喂的信息不同，表现天差地别" },
    { h1: "塞满 ≠ 用好", h2: "Lost in the Middle（2023）：位置与相关性，都影响命中质量" },
    { h1: "装配成为工程", h2: "检索、排序、压缩、取舍，需要系统化方法，而非零散技巧" },
  ];
  rows.forEach((r, i) => {
    const y = 1.75 + i * 0.98;
    s.addText([
      { text: r.h1, options: { fontSize: 14.5, bold: true, color: TEXT, breakLine: true } },
      { text: r.h2, options: { fontSize: 12.5, color: MUTED } },
    ], { x: 0.5, y, w: 6.1, h: 0.9, fontFace: F, paraSpaceAfter: 4, margin: 0 });
    if (i < 2) hairline(s, 0.5, y + 0.9, 6.6);
  });
  s.addText("≈ ×500", { x: 0.5, y: 4.85, w: 2.9, h: 0.95, fontFace: FM, fontSize: 48, bold: true, color: ACCENT, valign: "middle", margin: 0 });
  s.addText([
    { text: "上下文窗口扩容", options: { fontSize: 14, bold: true, color: TEXT, breakLine: true } },
    { text: "2K → 1M tokens（2020 → 2024）", options: { fontSize: 12, color: MUTED } },
  ], { x: 3.55, y: 5.02, w: 3.2, h: 0.75, fontFace: F, paraSpaceAfter: 4, margin: 0 });
  s.addText("资料：OpenAI GPT-3（2020）/ Google Gemini 1.5 Pro（2024）公开规格", { x: 0.5, y: 6.35, w: 7.0, h: 0.28, fontFace: F, fontSize: 12, color: MUTED, margin: 0 });
  // 右侧：上下文窗口装配图
  s.addText("上下文窗口 · CONTEXT WINDOW", { x: 7.5, y: 1.42, w: 5.0, h: 0.28, fontFace: F, fontSize: 12.5, bold: true, color: TEXT, align: "center", margin: 0 });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 7.5, y: 1.75, w: 5.0, h: 4.3, rectRadius: 0.06, fill: { color: PAPER }, line: { color: TEXT, width: 1.25 } });
  const bands = ["系统提示 System Prompt", "对话历史 History", "检索知识 RAG", "工具结果 Tool Output", "长期记忆 Memory"];
  bands.forEach((b, i) => {
    const y = 2.15 + i * 0.75;
    s.addShape(p.shapes.RECTANGLE, { x: 7.85, y, w: 4.3, h: 0.6, fill: { color: i % 2 === 0 ? CARD : PAPER }, line: { color: BORDER, width: 0.75 } });
    s.addText(b, { x: 8.0, y, w: 4.0, h: 0.6, fontFace: F, fontSize: 12.5, color: TEXT, valign: "middle", margin: 0 });
  });
  s.addText("装配进窗口的每一层，都在影响输出质量", { x: 7.5, y: 6.2, w: 5.0, h: 0.3, fontFace: F, fontSize: 12, color: MUTED, align: "center", margin: 0 });
  footer(s, 1, false);
  s.addNotes("关键论点：能力过剩、上下文稀缺；窗口从 2K 到 1M，但「装得下」不等于「装得对」。");
}

// ============================================================
// S9 Context 关键技术
// ============================================================
{
  const s = p.addSlide();
  header(s, "STAGE 02 · 关键技术", "三件套：让模型看到窗口外的世界");
  const cols = [
    { en: "RAG", h1: "检索增强生成", d: "文档向量化入库，回答前先检索相关片段，按需注入窗口 —— 知识实时、答案可溯源。", fix: "解决：知识过期与幻觉" },
    { en: "Memory", h1: "记忆机制", d: "把偏好与关键事实沉淀到外部存储，跨会话按需召回 —— 模型第一次「记得你」。", fix: "解决：会话无状态" },
    { en: "Tool Use", h1: "工具调用", d: "Function Calling 让模型输出结构化调用，触达数据库、API 与真实业务系统。", fix: "解决：文本与现实的墙" },
  ];
  cols.forEach((c, i) => {
    const x = 0.5 + i * 4.28;
    hairline(s, x, 1.75, 3.9);
    s.addText(c.en, { x, y: 1.92, w: 3.9, h: 0.35, fontFace: FM, fontSize: 16, bold: true, color: ACCENT, margin: 0 });
    s.addText(c.h1, { x, y: 2.34, w: 3.9, h: 0.35, fontFace: F, fontSize: 16, bold: true, color: TEXT, margin: 0 });
    s.addText(c.d, { x, y: 2.82, w: 3.9, h: 1.55, fontFace: F, fontSize: 12.5, color: MUTED, margin: 0 });
    s.addShape(p.shapes.RECTANGLE, { x, y: 4.5, w: 0.1, h: 0.1, fill: { color: ACCENT } });
    s.addText(c.fix, { x: x + 0.2, y: 4.4, w: 3.7, h: 0.3, fontFace: F, fontSize: 12.5, bold: true, color: TEXT, margin: 0 });
  });
  hairline(s, 0.5, 5.62, 12.33);
  s.addText("配套能力", { x: 0.5, y: 5.8, w: 1.6, h: 0.35, fontFace: F, fontSize: 13, bold: true, color: TEXT, margin: 0 });
  s.addText("上下文压缩与编排 —— 长对话摘要、多源信息取舍、按任务动态装配", { x: 2.2, y: 5.8, w: 10.6, h: 0.35, fontFace: F, fontSize: 12.5, color: MUTED, margin: 0 });
  footer(s, 1, false);
  s.addNotes("三件套各管一件事：RAG 管知识、Memory 管记忆、Tool Use 管行动；压缩与编排把它们装进窗口。");
}

// ============================================================
// S10 章节页 03
// ============================================================
divider("03", "STAGE 03 · 2025 — 至今", "Harness Engineering", "框架工程 —— 给模型套上「挽具」：循环、工具、权限与护栏", "Agent 框架演进 / 代表产品", 2, "进入第三阶段：把「会聊天的模型」装进能持续做事的执行框架。");

// ============================================================
// S11 Harness 框架演进（二级时间线）
// ============================================================
{
  const s = p.addSlide();
  header(s, "STAGE 03 · 框架演进", "从「会自己跑」到「跑得稳」：Harness 四年演进");
  s.addText("Harness = 模型之外的一切：执行循环 · 工具接入 · 上下文管理 · 权限与护栏", { x: 0.5, y: 1.32, w: 12.33, h: 0.3, fontFace: F, fontSize: 13, color: MUTED, margin: 0 });
  const lineY = 2.9;
  s.addShape(p.shapes.LINE, { x: 0.9, y: lineY, w: 11.53, h: 0, line: { color: LINE, width: 1.5 } });
  const cols = [
    { cx: 1.925, year: "2023", h1: "AutoGPT 开山", d: "首个爆款自主 Agent：目标分解 + 自我循环，也暴露失控与烧 token" },
    { cx: 5.085, year: "2023–24", h1: "框架化", d: "LangChain / LangGraph / MetaGPT：编排、状态机与多智能体分工工程化" },
    { cx: 8.245, year: "2024–25", h1: "生产化", d: "Claude Code / Cursor 把 Agent 带进日常编码：人在环中、权限收放" },
    { cx: 11.405, year: "2025–26", h1: "协议化与个人化", d: "MCP 统一工具接入，ACP 推互操作，OpenClaw 把 Agent 装进个人设备" },
  ];
  cols.forEach((c, i) => {
    const last = i === cols.length - 1;
    s.addText(c.year, { x: c.cx - 1.0, y: 2.4, w: 2.0, h: 0.3, fontFace: FM, fontSize: 14, bold: true, color: last ? ACCENT : TEXT, align: "center", margin: 0 });
    s.addShape(p.shapes.OVAL, { x: c.cx - 0.065, y: lineY - 0.065, w: 0.13, h: 0.13, fill: { color: last ? ACCENT : TEXT } });
    s.addText(c.h1, { x: c.cx - 1.42, y: 3.2, w: 2.85, h: 0.32, fontFace: F, fontSize: 14.5, bold: true, color: TEXT, margin: 0 });
    s.addText(c.d, { x: c.cx - 1.42, y: 3.58, w: 2.85, h: 2.1, fontFace: F, fontSize: 12, color: MUTED, margin: 0 });
  });
  s.addShape(p.shapes.RECTANGLE, { x: 0.5, y: 6.24, w: 0.1, h: 0.1, fill: { color: ACCENT } });
  s.addText("主线上不变的，是同一个循环：目标 → 行动 → 观察 → 修正", { x: 0.72, y: 6.12, w: 11.6, h: 0.35, fontFace: F, fontSize: 13.5, bold: true, color: TEXT, margin: 0 });
  footer(s, 2, false);
  s.addNotes("演进主线：能跑 → 可编排 → 可生产 → 可互操作；不变的是「目标-行动-观察-修正」循环。");
}

// ============================================================
// S12 代表产品
// ============================================================
{
  const s = p.addSlide();
  header(s, "STAGE 03 · 代表产品", "三个代表：从实验到日常");
  const cards = [
    { year: "2023", name: "AutoGPT", role: "开山者", d: "第一次让模型自己给自己下指令：定目标、拆任务、反思循环。引爆 Agent 概念，也暴露失控与成本问题。", fix: "意义：证明自主循环可行" },
    { year: "2025", name: "ACP Agent", role: "连接者", d: "基于 ACP 等开放协议，让不同框架的 Agent 标准化通信、互相调用，从单体智能走向协作生态。", fix: "意义：Agent 之间有了通用语言" },
    { year: "2026", name: "OpenClaw", role: "落地者", d: "开源个人助理：本地运行，接入 WhatsApp、Telegram 等日常渠道，靠技能与记忆长期服务一个人。", fix: "意义：Agent 成为日用工具" },
  ];
  cards.forEach((c, i) => {
    const x = 0.5 + i * 4.28;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 1.7, w: 3.9, h: 3.95, rectRadius: 0.08, fill: { color: CARD } });
    s.addText(c.year, { x: x + 0.28, y: 1.98, w: 2.0, h: 0.28, fontFace: FM, fontSize: 13, bold: true, color: ACCENT, margin: 0 });
    s.addText(c.name, { x: x + 0.28, y: 2.3, w: 3.35, h: 0.45, fontFace: F, fontSize: 20, bold: true, color: TEXT, margin: 0 });
    s.addText(c.role, { x: x + 0.28, y: 2.85, w: 3.35, h: 0.3, fontFace: F, fontSize: 13.5, bold: true, color: MUTED, margin: 0 });
    s.addText(c.d, { x: x + 0.28, y: 3.25, w: 3.34, h: 1.7, fontFace: F, fontSize: 12.5, color: MUTED, margin: 0 });
    s.addShape(p.shapes.RECTANGLE, { x: x + 0.28, y: 5.16, w: 0.1, h: 0.1, fill: { color: ACCENT } });
    s.addText(c.fix, { x: x + 0.48, y: 5.05, w: 3.2, h: 0.32, fontFace: F, fontSize: 12.5, bold: true, color: TEXT, margin: 0 });
  });
  s.addText("共同启示：模型只是引擎，Harness 决定 Agent 能不能上得了路。", { x: 0.5, y: 6.0, w: 12.33, h: 0.4, fontFace: F, fontSize: 14, bold: true, color: TEXT, margin: 0 });
  footer(s, 2, false);
  s.addNotes("三个产品对应三个角色：起点（AutoGPT）、连接（ACP Agent）、落点（OpenClaw）。");
}

// ============================================================
// S13 总结
// ============================================================
{
  const s = p.addSlide();
  s.background = { color: INK };
  s.addText("SUMMARY · 一条主线，三次跃迁", { x: 0.5, y: 0.62, w: 9, h: 0.3, fontFace: F, fontSize: 12.5, bold: true, color: ACCENT, charSpacing: 2, margin: 0 });
  s.addText("从教模型说话，到喂模型知识，再到为模型造引擎", { x: 0.5, y: 0.98, w: 12.33, h: 0.62, fontFace: F, fontSize: 30, bold: true, color: "FFFFFF", margin: 0 });
  const cols = [
    { tag: "01 PROMPT", name: "Prompt Engineering", rows: [["核心问题", "怎么把意图说清楚"], ["关键手段", "示例 · CoT · 角色"], ["代表产物", "提示词模板"]] },
    { tag: "02 CONTEXT", name: "Context Engineering", rows: [["核心问题", "怎么把信息喂对"], ["关键手段", "RAG · Memory · Tool Use"], ["代表产物", "上下文装配管线"]] },
    { tag: "03 HARNESS", name: "Harness Engineering", rows: [["核心问题", "怎么让模型持续做成事"], ["关键手段", "循环 · 工具 · 权限 · 护栏"], ["代表产物", "AutoGPT → OpenClaw"]] },
  ];
  cols.forEach((c, i) => {
    const x = 0.5 + i * 4.28;
    s.addText(c.tag, { x, y: 2.1, w: 3.9, h: 0.3, fontFace: FM, fontSize: 14, bold: true, color: ACCENT, charSpacing: 1, margin: 0 });
    s.addText(c.name, { x, y: 2.46, w: 3.9, h: 0.36, fontFace: F, fontSize: 16, bold: true, color: "FFFFFF", margin: 0 });
    c.rows.forEach((r, j) => {
      const y = 3.0 + j * 0.68;
      s.addText(r[0], { x, y, w: 3.9, h: 0.24, fontFace: F, fontSize: 12, color: MUTED_D, margin: 0 });
      s.addText(r[1], { x, y: y + 0.26, w: 3.9, h: 0.34, fontFace: F, fontSize: 13, color: "FFFFFF", margin: 0 });
    });
    if (i < 2) s.addShape(p.shapes.LINE, { x: x + 4.09, y: 2.2, w: 0, h: 2.75, line: { color: LINE_D, width: 1 } });
  });
  s.addText("下一步：为 Agent 造世界 —— 环境、协作与治理。", { x: 0.5, y: 5.5, w: 12.33, h: 0.4, fontFace: F, fontSize: 17, bold: true, color: "FFFFFF", margin: 0 });
  s.addText("模型能力仍在翻倍，Harness 的形态也远未定型。", { x: 0.5, y: 5.98, w: 12.33, h: 0.32, fontFace: F, fontSize: 12.5, color: MUTED_D, margin: 0 });
  footer(s, 3, true);
  s.addNotes("收尾：三阶段对照一张表；进化仍在继续——下一站是 Agent 的环境、协作与治理。");
}

// ---------- 输出 ----------
p.writeFile({ fileName: "D:/lzy/code/retail-agent/PPT/AI_Agent_进化之路.pptx" }).then((f) => console.log("OK ->", f));
