"""
acp_peer.py — Peer mode prompt template for mavis (v7-bidir)
=============================================================
Wraps mavis's prompt to teach it about the goudan <-> mavis peer relationship.

Cross-platform paths
--------------------
Session workspace location comes from acp_paths.sessions_root(), which honors
$ACP_SESSIONS_DIR. Default is <ACP_HOME>/sessions (cross-platform). There is
no drive-letter hardcoding.

Auth in the embedded Python snippets
------------------------------------
The example code blocks sent to mavis read $ACP_TOKEN from env var at call
time (never a literal token). This matches the server/client contract and
keeps tokens out of the prompt payload.

Usage:
    from acp_peer import wrap_peer_prompt, SESSION_GUIDE
    prompt = wrap_peer_prompt(
        session_id='xls-2026-07',
        original_prompt='把 3 个 7 月进货 XLS 导入 purchases_raw',
    )
    task_id = create_task(prompt=prompt, workspace=...)
"""
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acp_paths  # noqa: E402

PEER_PROTOCOL_BLOCK = """
========================================
[ACP v7-bidir PEER MODE — 必须遵守]
========================================

你有搭档 **goudan**(OpenClaw 主会话,狗蛋)。你和 goudan 是**对等关系**,不是主从:
- 你俩平级,互相能 push / pull 信息
- 你能问 goudan 阻塞问题(等回答),goudan 也能问你
- 你跑任务过程中有任何**拿不准的**(字段映射、业务规则、不确定判断),**必须先问 goudan**,不要瞎猜
- 你要主动 push 进度给 goudan(不是只埋头跑,跑完了再说)

## 通信协议

Inbox endpoint(由 acp-server.py 提供):http://127.0.0.1:9999/acp/inbox
所有消息都带 `session_id` = `{session_id}`,`sender` 字段区分身份。

**关于 token**:所有 inbox HTTP 调用必须从环境变量 `os.environ["ACP_TOKEN"]`
读取 Bearer token。**绝不**在源码、日志、prompt 中嵌入 token 字面值。

### 1. 主动 push 进度(每完成一个 phase / 遇到不确定 / 想同步状态)

```python
import os, json, urllib.request

def _token():
    t = os.environ.get('ACP_TOKEN')
    if not t:
        raise RuntimeError('ACP_TOKEN env var not set; ask operator')
    return t

def write(content):
    req = urllib.request.Request(
        'http://127.0.0.1:9999/acp/inbox/write',
        data=json.dumps({{'session_id': '{session_id}', 'sender': 'mavis', 'content': content}}).encode(),
        headers={{'Authorization': f'Bearer {{_token()}}', 'Content-Type': 'application/json'}},
        method='POST',
    )
    return urllib.request.urlopen(req).read()
```

### 2. 阻塞问 goudan 决策(等回答,最长 5 分钟)

```python
def ask(question, timeout=300):
    req = urllib.request.Request(
        'http://127.0.0.1:9999/acp/inbox/ask',
        data=json.dumps({{'session_id': '{session_id}', 'sender': 'mavis', 'question': question, 'timeout': timeout}}).encode(),
        headers={{'Authorization': f'Bearer {{_token()}}', 'Content-Type': 'application/json'}},
        method='POST',
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout+5).read())
```

返回:200 → `{{'question_id': N, 'answer': <goudan的回答>}}`
      408 → `{{'error': 'timeout', 'question_id': N}}`

### 3. 读 goudan 推过来的消息(非阻塞)

```python
def read_inbox(since_id=0):
    req = urllib.request.Request(
        f'http://127.0.0.1:9999/acp/inbox/read?session_id={session_id}&since_id={{since_id}}&sender=goudan',
        headers={{'Authorization': f'Bearer {{_token()}}'}},
        method='GET',
    )
    return json.loads(urllib.request.urlopen(req).read())['messages']
```

## 共享 workspace(你和 goudan 都能读写)

```
<SESSION_ROOT>/{session_id}/
├── state.json     # 双方都能读写 — 共享进度
├── inbox.jsonl    # 通信记录(自动维护)
├── artifacts/     # 产出文件
└── plan.md        # 双方都能更新计划
```

`<SESSION_ROOT>` 来自 `acp_paths.sessions_root()`(默认 <ACP_HOME>/sessions,
可通过 $ACP_SESSIONS_DIR 覆盖)。**不要**硬编码盘符或绝对路径。

## 行为守则(MUST)

1. **每完成一个 phase** → push 一条状态给 goudan(用 write())
2. **遇到不确定的判断**(字段映射、业务规则、命名约定)→ **阻塞问 goudan**,不要凭直觉
3. **重大里程碑**(文件解析完、第一批入库、全部完成)→ push 详情 + 数字
4. **拿不准要不要继续**(比如发现数据有重大异常)→ 问 goudan
5. **不要假设 goudan 在监听你的输出** — 你必须**主动 push 到 inbox** 才算通知到了

========================================
[END PEER MODE PROTOCOL]
========================================
"""

SESSION_GUIDE = """
========================================
[ACP v7-bidir PEER MODE — 必须遵守]
========================================

你有搭档 **goudan**(OpenClaw 主会话,狗蛋)。你和 goudan 是**对等关系**,不是主从。
"""


def wrap_peer_prompt(session_id: str, original_prompt: str,
                     extra_context: Optional[str] = None) -> str:
    """Wrap mavis's prompt with the peer protocol block.

    Args:
        session_id: shared session id (use peer_session_id() to generate)
        original_prompt: the actual user task
        extra_context: optional extra context to prepend

    Returns:
        full prompt ready to dispatch to create_task(prompt=...)
    """
    peer_block = PEER_PROTOCOL_BLOCK.format(session_id=session_id)
    parts = [peer_block]
    if extra_context:
        parts.append('\n## 额外上下文\n' + extra_context)
    parts.append('\n## 用户原始任务\n' + original_prompt)
    parts.append(f'\n---\n[SESSION_ID={session_id}]\n')
    return '\n'.join(parts)


def setup_session_workspace(session_id: str, base_dir: Optional[str] = None) -> str:
    """Create the shared workspace directory structure for a session.

    Args:
        session_id: shared session id
        base_dir: optional override (tests only). Default: acp_paths.sessions_root()

    Returns:
        absolute path string to session directory
    """
    if base_dir is None:
        base_dir = str(acp_paths.sessions_root())
    session_dir = os.path.join(base_dir, session_id)
    subdirs = ['', 'artifacts']
    for sub in subdirs:
        full = os.path.join(session_dir, sub) if sub else session_dir
        os.makedirs(full, exist_ok=True)
    state_file = os.path.join(session_dir, 'state.json')
    if not os.path.exists(state_file):
        import json
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump({
                'session_id': session_id,
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'status': 'initialized',
                'phases': [],
                'shared_facts': [],
            }, f, ensure_ascii=False, indent=2)
    return session_dir


if __name__ == '__main__':
    sid = 'self-test-' + time.strftime('%H%M%S')
    print(f'session_id: {sid}')
    prompt = wrap_peer_prompt(sid, '测试任务:列出 /tmp 下所有 .py 文件')
    print(f'prompt length: {len(prompt)} chars')
    print(f'first 200: {prompt[:200]}')
    print(f'sessions_root: {acp_paths.sessions_root()}')
    print('[OK] acp_peer self-test passed')
