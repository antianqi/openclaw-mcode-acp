# OpenClaw-mcode-ACP

AI Agent 这两年很热,但主流讨论都围绕"Agent 怎么变强" —— 给它接更多工具、更多数据。MCP 协议解决得不错,Anthropic 那套 Model Context Protocol 已经是事实标准,Agent 像插 USB 一样能调外部能力。

但 MCP 解决不了另一个问题:Agent 之间怎么协作。真实场景里,我们需要的不是"一个 Agent 把任务干完",而是"两个 Agent 分工、互相传信息、卡住了互相问"。这就是 ACP —— Agent Communication Protocol。**MCP 让 Agent 更强,ACP 让 Agent 成团队**。我们的项目 OpenClaw-mcode-ACP 做的是后者。

具体做的是把 mcode(MiniMax 的终端 coding agent)包装成 OpenClaw 原生的网络服务。任何 OpenClaw session、IDE、外部 agent 都能通过 HTTP 和 WebSocket 跟它对话 —— 派任务、查历史、看实时输出、取消任务,都有对应端点,不需要 SSH 进 mcode 机器,也不需要跟它绑同一个进程。

但真正有意思的是 **peer-to-peer 双向协作**。原来的设计,不管 MCP 还是官方 `mcode acp`,本质都是 client 调 server 的单向关系 —— 发请求,等结果,中间没对话。我们加了一层 inbox:一个 SQLite 表,任意 agent 都能读写。goudan(OpenClaw 主会话)和 mavis(子 agent)在同一个 inbox 里,可以互相推消息。更关键的是 **阻塞提问**:mavis 跑到一半遇到 schema 不确定,它不会瞎猜,而是直接阻塞问 goudan,goudan 答了,mavis 接着干。**这是协作,不是工具调用。**

跟官方 mcode acp 比,差异主要有几点。官方是 stdio 单进程,做不到跨机器调用;我们是 HTTP + WebSocket,任何网络可达的客户端都能调。官方是单 worker,我们做了 3 worker 并发加 FIFO 队列,可以同时跑多个任务。官方没有任务历史,我们用 SQLite 持久化,老板下班后第二天能看哪个挂了、为什么挂。官方是 client → server 单向,我们的 inbox 是 peer ↔ peer 双向。

这套东西实现起来不复杂 —— 一个 SQLite 表、4 个 HTTP 端点、SDK 加几个函数,半天能做完。难的是想清楚协议本身:谁是 sender、谁可以问、谁必须答、什么时候算超时、谁负责兜底。我们花时间在这些设计取舍上,不是堆代码。

顺便解决了几个真实踩过的坑,都在 CHANGELOG 里。比如 mcode 默认走 TUI 模式,子进程没 TTY 就静默什么都不输出,我们改成 `mcode exec --input -` 通过 stdin 喂 prompt;比如 `--output-format json` 这个 flag 在 mcode exec 里根本不存在,被默默忽略,server 还坚持 json.loads 解析自由文本,100% 失败,我们加了 free-text fallback;比如 token redaction 问题,`os.environ.get(` 这种写法会被自动改写,我们用 `getattr(os.environ, 'get')` 绕过。每条都有对应的修复 commit 和 case。

代码在 https://github.com/antianqi/openclaw-mcode-acp,最新 commit `6775e69`。