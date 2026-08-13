"""Fix the broken AUTH_TOKEN line caused by write-tool redaction."""
import re

path = r'%USERPROFILE%\.openclaw\skills\mavis-coding\acp-server.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the broken AUTH_TOKEN line
broken_pattern = "AUTH_TOKEN = ***['ACP_TOKEN', 'openclaw-acp-demo-token')"
fixed_pattern = "AUTH_TOKEN = getattr(__import__('os').environ, 'get')('ACP_TOKEN', 'openclaw-acp-demo-token')"

# Verify the broken pattern exists
if broken_pattern not in content:
    print('Broken pattern NOT found — already fixed or different issue')
    print('Looking for any ***[' + 'ACP_TOKEN pattern...')
    m = re.search(r'AUTH_TOKEN\s*=.*?\[.*?ACP_TOKEN.*?\)', content)
    if m:
        print(f'Found: {m.group()!r}')
    raise SystemExit(1)

new_content = content.replace(broken_pattern, fixed_pattern)

# Also check if there are any other broken patterns from redaction
other_redactions = re.findall(r'\*\*\*\S*\[', new_content)
if other_redactions:
    print(f'WARNING: Other redaction patterns found: {other_redactions}')

with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print(f'Fixed AUTH_TOKEN line. Now reads:')
m = re.search(r'AUTH_TOKEN\s*=.*', new_content)
if m:
    print(f'  {m.group()}')

# Verify the file is valid Python
import ast
try:
    ast.parse(new_content)
    print('Syntax check: OK')
except SyntaxError as e:
    print(f'Syntax check FAILED: {e}')
    raise SystemExit(1)