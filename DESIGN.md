# Design

jobfeed Web UI 视觉系统。方向:**石墨与钴(Graphite & Cobalt)** —— "制图室的精密:灰白图纸上,一道钴蓝墨线。"

Register: product(见 PRODUCT.md)。设计服务任务,工具消失在任务里。本文件是视觉唯一事实源;实现期迁往 rewrite repo 并落成 Tailwind tokens / CSS variables。

## Theme

- **浅色单主题(v1)**。物理场景:白天书桌、笔记本、日常 triage 会话。暗色为未来增强 —— token 全部走语义命名 CSS variables,为第二主题留结构,不提前实现。
- 零暖味:背景为纯中性灰白(chroma 0),不做 cream/sand 暖底。情绪由钴蓝、verdict 三色与排版承载,不进背景。
- 色彩策略:**Restrained** —— 中性层 + 唯一 accent(钴蓝)≤10% 表面积。verdict 三色是仅有的其他色彩词汇。

## Colors

| Token | Hex | 用途 |
|---|---|---|
| `bg` | `#f7f7f8` | 应用底/侧栏(第二中性层) |
| `surface` | `#ffffff` | 内容面板、行、卡 |
| `border` | `#e6e6ea` | 标准边框 |
| `border-strong` | `#d8d8de` | 强边框(输入框等) |
| `hairline` | `#f0f0f2` | 行分隔线 |
| `ink` | `#141417` | 主文字(近黑) |
| `ink-2` | `#3c3c42` | 次级文字 |
| `mute` | `#5d5d66` | 弱化文字(bg 上 ≥5.6:1) |
| `faint` | `#9a9aa2` | 最弱(仅大字号/非关键) |
| `accent` | `#2742d6` | 钴蓝。**只给**:选中态、主按钮、当前导航、焦点 |
| `accent-hover` | `#1f37b8` | accent 悬停 |
| `accent-bg` | `#edf0fd` | 选中行背景 |
| `accent-border` | `#c6cdf7` | 选中行 inset ring |
| `apply` / `apply-bg` | `#136a38` / `#e2f2e8` | verdict: apply |
| `consider` / `consider-bg` | `#7c5500` / `#f7eed6` | verdict: consider;attention 条同族 |
| `skip` / `skip-bg` | `#5c5c64` / `#ececef` | verdict: skip / below_threshold |
| `danger` / `danger-bg` | `#b42318` / `#fdebe9` | 破坏性操作(删除公司等) |

规则:
- 正文对比 ≥4.5:1,大字 ≥3:1。`faint` 不得用于正文。
- accent 不做装饰色;非激活态不得满饱和。
- 彩底上不用灰字 —— 用同色相深字(verdict pill 即范式:tinted bg + 同族深色字)。

## Typography

- **唯一家族:Geist**(400/500/600/700)+ **Geist Mono**(400/500)。无 display 字体。
- Mono 的领地:分数、日期/相对时间、计数、run id、键盘提示、slug。开 `tabular-nums`。
- 固定 rem 阶(product register:不做 fluid clamp),比率 ~1.2:

| 步 | px | 用途 |
|---|---|---|
| h1 | 21 / 600 | 页标题 |
| h2 | 18 / 600 | 区块标题 |
| h3 | 15 / 600 | 面板/Drawer 标题 |
| body | 14 / 400 | 正文、详情 |
| body-sm | 13 / 400–500 | 行标题(舒适密度) |
| compact | 12.5 / 400–500 | 行(紧凑密度)、表格 |
| label | 12 / 500 | 标签、导航 |
| micro | 11 / 400–500 | 计数、时间戳、pill |

- 标题 letter-spacing −0.01em(下限 −0.04em);prose 行长 ≤72ch;表格不受限。
- h1–h3 用 `text-wrap: balance`。

## Density

两档,**默认紧凑**,视图菜单切换(持久化到 localStorage;快捷键实现期定):

- **紧凑(默认)**:单行 32px —— 题目 + 公司·地点(弱化)同行 + 相对时间(mono)+ 分数(mono)+ verdict pill。一屏 ~18 条。
- **舒适**:双行 46px —— 题目一行;公司·地点·时间次行。一屏 ~12 条。

## Layout

- 应用壳:左侧栏(`bg`,148–200px,导航 + 计数)+ 内容区(`surface`)。
- Triage / Pipeline:**分屏** —— 列表 + 右侧常驻详情 pane(~380px),键盘流前提,不用 drawer。
- Library:全宽表 + 行点击 **drawer**(查阅场景,非决策场景)。
- 行选中态:`accent-bg` 背景 + `accent-border` inset ring + 标题转 accent。**禁止侧边彩条。**
- 间距 4px 网格;圆角:控件 6px、面板/卡 8–10px、pill 99px。阴影仅一层(`0 1px 2–3px rgba(20,20,23,.04–.07)`),无大阴影堆叠。
- z-index 语义阶:dropdown < sticky < drawer-backdrop < drawer < dialog < toast < tooltip。

## Components

基底 **shadcn/ui**(Radix primitives + Tailwind,vendored 进代码),按本 token 重设 —— 不用 shadcn 默认灰紫味。每个交互组件必须有完整状态:default / hover / focus / active / disabled / loading / error。

清单:侧栏导航(计数徽标)、顶栏(标题 + meta + 键盘提示)、job 行(两密度)、verdict pill、分数(mono)、详情 pane(状态操作、twin "also seen on"、Stage A/B 分、Stage B 内容块、notes、followup picker、手动贴 JD 卡)、主/次按钮(主=钴蓝填充,次=描边)、bulk 操作条(选中数 + 级联结果反馈)、tabs、drawer、dialog(仅破坏性确认 —— modal 是最后手段)、toast、空状态(教界面,非"空空如也")、skeleton(不用内容区 spinner)、表格(Sources/Runs)、attention 条(consider 同族琥珀)。

图表(Insights):**recharts** 统一 —— KPI 卡、Sankey 漏斗、Pipeline donut、Daily timeline、By-resume 表。图表色从本 palette 取,不用库默认。

## Motion

- 150–250ms,ease-out(quart/quint);无 bounce/elastic;无页面加载编排。
- 动效只传达状态:选中迁移、drawer 开合、toast、行决策后移除(高度塌缩 ~180ms)。
- 全局尊重 `prefers-reduced-motion`:crossfade 或瞬切替代。

## Bans(impeccable absolute + product register)

侧边彩条 borders;渐变文字;玻璃态;hero-metric 模板;同构卡片网格;每节 eyebrow;装饰性动效;display 字体进 UI;自定义滚动条/非标控件;非激活态满饱和色;modal 当第一反应;cream/warm 底;内容区 spinner。
