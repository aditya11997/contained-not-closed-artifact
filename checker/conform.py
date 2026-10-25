"""Structural check of a JSON value against a p4.schema TypedDict (stdlib only)."""
import types
import typing


def conform(value, tp, path: str = "$") -> list[str]:
    """Problems with `value` as an instance of `tp` (empty list = conforms)."""
    origin = typing.get_origin(tp)
    if tp is type(None):
        return [] if value is None else [f"{path}: expected null"]
    if origin in (typing.Union, types.UnionType):
        options = [conform(value, arg, path) for arg in typing.get_args(tp)]
        return [] if any(not o for o in options) else [f"{path}: {value!r} matches no option of {tp}"]
    if tp is bool:
        return [] if isinstance(value, bool) else [f"{path}: expected bool"]
    if tp is int:
        return [] if isinstance(value, int) and not isinstance(value, bool) else [f"{path}: expected int"]
    if tp is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        return [] if ok else [f"{path}: expected number"]
    if tp is str:
        return [] if isinstance(value, str) else [f"{path}: expected string"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{path}: expected list"]
        (item,) = typing.get_args(tp)
        return [p for i, v in enumerate(value) for p in conform(v, item, f"{path}[{i}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        _, item = typing.get_args(tp)
        return [p for k, v in value.items() for p in conform(v, item, f"{path}.{k}")]
    if typing.is_typeddict(tp):
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        hints = typing.get_type_hints(tp)
        out = [f"{path}: missing key {k!r}" for k in sorted(tp.__required_keys__ - value.keys())]
        out += [f"{path}: unexpected key {k!r}" for k in sorted(value.keys() - hints.keys())]
        for k in sorted(value.keys() & hints.keys()):
            out += conform(value[k], hints[k], f"{path}.{k}")
        return out
    raise TypeError(f"unsupported schema type {tp!r}")
