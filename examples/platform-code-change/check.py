"""Run inside the copied sandbox; expected to fail until add.py is fixed."""
from add import add

assert add(2, 3) == 5
assert add(-2, 4) == 2
print('2 checks passed')
