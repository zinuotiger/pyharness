"""Independent deterministic reviewer. It has no tools or execution authority.

Policy v1 intentionally approves a narrow language, not arbitrary Python.
Unknown operations and failures remain pending for the human channel.
"""
from __future__ import annotations
import ast
import asyncio
import re
from pathlib import PurePosixPath


class AutomaticReviewer:
    identity = 'reviewer:bounded-policy-v1'
    timeout = 2

    async def evaluate(self, request):
        return await asyncio.to_thread(self.check, request)

    def check(self, request):
        scope, args, tool = request['scope'], request['args'], request['tool']
        if scope['mode'] not in ('isolated', 'remote') or tool not in scope['allowed_tools']:
            return {'decision': 'manual', 'reason': '宿主执行或超出助手权限，必须人工确认'}
        context=request.get('context',[])
        intent=context[-1] if isinstance(context,list) and context and isinstance(context[-1],str) else ''
        if not intent.strip() or scope.get('network','none')!='none':
            return {'decision':'manual','reason':'任务上下文缺失或执行范围不是无网络隔离环境，需要人工确认'}
        if tool in ('fs.write_file', 'file.write'):
            path = args.get('path', '')
            parts = path.split('/')
            if (not path or PurePosixPath(path).is_absolute() or '\\' in path or ':' in path or
                    any(p in ('', '.', '..') or p.startswith('.') or p.lower().startswith('credentials') for p in parts)):
                return {'decision': 'deny', 'reason': '文件路径超出可自动审核的私有工作区范围'}
            if args.get('mode') == 'append' or len(str(args.get('content', '')).encode()) > 65536:
                return {'decision': 'manual', 'reason': '追加或大文件写入需人工核对'}
            if path not in intent or not re.search(r'写|创建|生成|保存|\b(write|create|generate|save)\b',intent,re.I):
                return {'decision':'manual','reason':'任务未明确要求写入此文件，需要人工核对调用与任务是否一致'}
            return {'decision': 'approve', 'reason': '任务明确要求写入此相对路径的小文件，范围为无网络隔离工作区；执行前仍重入权限检查'}
        if tool == 'exec.python_run':
            if not re.search(r'打印|输出|计算|算一下|\b(print|calculate|compute)\b',intent,re.I):
                return {'decision':'manual','reason':'任务未明确要求打印或计算，需要人工确认代码用途'}
            try:
                tree = ast.parse(args.get('code', ''))
                # Only print literals/arithmetic. No names except print, imports,
                # attributes, subscripts, comprehensions, mutation or loops.
                allowed = (ast.Module, ast.Expr, ast.Call, ast.Name, ast.Load, ast.Constant,
                    ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)
                nodes = list(ast.walk(tree))
                if len(nodes) <= 100 and nodes and all(isinstance(n, allowed) for n in nodes) and all(
                    (not isinstance(n, ast.Name) or n.id == 'print') and
                    (not isinstance(n, ast.Call) or isinstance(n.func, ast.Name) and n.func.id == 'print' and not n.keywords)
                    for n in nodes):
                    return {'decision': 'approve', 'reason': '隔离环境中仅打印常量或简单算术，无文件、网络或进程访问'}
            except (ValueError, SyntaxError): pass
        return {'decision': 'manual', 'reason': '此操作不在自动审核白名单，需要人工批准'}
