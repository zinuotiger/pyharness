import inspect, re
import uvicorn
print("=== run ===")
print(inspect.getsource(uvicorn.Server.run))
s = inspect.getsource(uvicorn.Server.serve)
print("=== serve head ===")
print(s[:1500])
print("=== self.X = assignments in serve ===")
for m in re.finditer(r"self\.(\w+)\s*=", s):
    print(m.group(1))
