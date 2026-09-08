import subprocess, sys
cmds = [
    r'''grep -n "add_parser(" cli.py | head -20''',
    r'''grep -rn "def .*compact\|compact" core/session.py | head -5''',
    r'''grep -n "fork" cli.py | head -5''',
    r'''grep -n "def .*plan\|plan_mode" core/commands.py | head -5''',
]
for c in cmds:
    print('###', c)
    r = subprocess.run(['bash', '-c', c], capture_output=True, text=True, cwd=r'C:\Users\LENOVO\Desktop\mini-harness\pyharness')
    print(r.stdout[:900] or r.stderr[:300])
    print()
