"""
acp_cli.py — CLI wrapper for ACP (callable from shell)
=======================================================
One-liner access to ACP from any shell. Useful for cron / scripts / OpenClaw exec tool.

Usage:
    python acp_cli.py health
    python acp_cli.py create --prompt "..." --workspace "..."
    python acp_cli.py get --id task_xxx
    python acp_cli.py wait --id task_xxx --timeout 60
    python acp_cli.py stream --id task_xxx
    python acp_cli.py cancel --id task_xxx
    python acp_cli.py history [--status SUCCEEDED] [--limit 10]
    python acp_cli.py stats
"""
import sys
import json
import argparse
import time
from pathlib import Path

# Make acp_tools importable
sys.path.insert(0, str(Path(__file__).parent))
import acp_tools as t


def cmd_health(args):
    h = t.health()
    print(json.dumps(h, ensure_ascii=False, indent=2))


def cmd_create(args):
    task_id = t.create_task(args.prompt, args.workspace, files=args.files, timeout=args.timeout)
    print(task_id)


def cmd_get(args):
    d = t.get_task(args.id)
    print(json.dumps(d, ensure_ascii=False, indent=2))


def cmd_wait(args):
    result = t.wait_task(args.id, timeout=args.timeout, poll_interval=args.poll)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_stream(args):
    for event in t.stream_task(args.id, max_idle_sec=args.idle):
        print(json.dumps(event, ensure_ascii=False))


def cmd_run(args):
    def on_event(evt_type, data):
        print(f'[{evt_type}] {json.dumps(data, ensure_ascii=False)[:200]}', file=sys.stderr)
    result = t.run_and_stream(
        prompt=args.prompt, workspace=args.workspace,
        files=args.files, timeout=args.timeout, on_event=on_event,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_cancel(args):
    result = t.cancel_task(args.id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_history(args):
    h = t.history(status=args.status, workspace=args.workspace, limit=args.limit)
    print(json.dumps(h, ensure_ascii=False, indent=2))


def cmd_stats(args):
    s = t.stats()
    print(json.dumps(s, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description='ACP CLI wrapper for OpenClaw')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('health', help='Health check').set_defaults(func=cmd_health)

    pc = sub.add_parser('create', help='Create + enqueue a task')
    pc.add_argument('--prompt', required=True)
    pc.add_argument('--workspace', required=True)
    pc.add_argument('--files', nargs='*')
    pc.add_argument('--timeout', default='5m')
    pc.set_defaults(func=cmd_create)

    pg = sub.add_parser('get', help='Get task state')
    pg.add_argument('--id', required=True)
    pg.set_defaults(func=cmd_get)

    pw = sub.add_parser('wait', help='Poll task until terminal state')
    pw.add_argument('--id', required=True)
    pw.add_argument('--timeout', type=float, default=120)
    pw.add_argument('--poll', type=float, default=2.0)
    pw.set_defaults(func=cmd_wait)

    ps = sub.add_parser('stream', help='SSE stream of task events')
    ps.add_argument('--id', required=True)
    ps.add_argument('--idle', type=float, default=30)
    ps.set_defaults(func=cmd_stream)

    pr = sub.add_parser('run', help='Create + stream + return final')
    pr.add_argument('--prompt', required=True)
    pr.add_argument('--workspace', required=True)
    pr.add_argument('--files', nargs='*')
    pr.add_argument('--timeout', default='5m')
    pr.set_defaults(func=cmd_run)

    pcan = sub.add_parser('cancel', help='Cancel task')
    pcan.add_argument('--id', required=True)
    pcan.set_defaults(func=cmd_cancel)

    ph = sub.add_parser('history', help='SQLite task history')
    ph.add_argument('--status')
    ph.add_argument('--workspace')
    ph.add_argument('--limit', type=int, default=20)
    ph.set_defaults(func=cmd_history)

    sub.add_parser('stats', help='Task counts by status').set_defaults(func=cmd_stats)

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()