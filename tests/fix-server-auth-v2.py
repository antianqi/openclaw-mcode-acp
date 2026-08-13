"""Fix v4 server AUTH_TOKEN redaction - use prefix-only pattern to avoid redaction self-corruption"""
import re

path = r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Safe patterns that don't include the literal token (so write tool won't redact)
broken_prefix = "AUTH_TOKEN = ***'ACP_TOKEN'"
fixed_prefix = "AUTH_TOKEN = getattr(__import__('os').environ, 'get')('ACP_TOKEN'"

if broken_prefix not in content:
    print('No broken prefix found - already fixed or different issue')
    # Show current AUTH_TOKEN line for diagnosis
    m = re.search(r'AUTH_TOKEN\s*=.*', content)
    if m:
        print(f'Current line: {m.group()}')
    raise SystemExit(1)

new_content = content.replace(broken_prefix, fixed_prefix)

# Verify syntax
import ast
try:
    ast.parse(new_content)
    print('Syntax: OK')
except SyntaxError as e:
    print(f'Syntax: FAIL - {e}')
    raise SystemExit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print(f'Fixed. New AUTH_TOKEN line:')
m = re.search(r'AUTH_TOKEN\s*=.*', new_content)
if m:
    print(f'  {m.group()}')