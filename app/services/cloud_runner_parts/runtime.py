"""Runtime binding for cloud runner mixins without reverse imports."""

from __future__ import annotations

from functools import wraps

_RUNTIME_API = None

def bind_runtime(api):
    global _RUNTIME_API
    _RUNTIME_API = api

def runtime_bound(names):
    names = tuple(names)
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            api = _RUNTIME_API
            if api is None:
                raise RuntimeError("cloud runner runtime is not bound")
            namespace = fn.__globals__
            for name in names:
                namespace[name] = getattr(api, name)
            return fn(*args, **kwargs)
        return wrapped
    return decorate
