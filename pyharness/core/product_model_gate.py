"""Optional application-owned admission, immediately before each adapter attempt."""
import inspect


async def check_model_attempt(ctx, model, method):
    runtime = getattr(ctx, 'platform_runtime', None)
    check = getattr(runtime, 'before_model_attempt', None)
    if callable(check):
        result = check(model, method, ctx)
        if inspect.isawaitable(result):await result
