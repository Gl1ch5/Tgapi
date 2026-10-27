"""Reflection over Telethon's generated TL schema.

Exposes every MTProto function as JSON-callable, builds a catalog for the docs/explorer
and converts JSON <-> TL objects.
"""
import base64
import datetime as dt
import inspect
import re
import zlib
from functools import lru_cache

from telethon.tl import TLObject
from telethon.tl.alltlobjects import LAYER, tlobjects
from telethon.tl.tlobject import TLRequest

_FN_PREFIX = "telethon.tl.functions"
_TY_PREFIX = "telethon.tl.types"


def _official_name(cls) -> str:
    """SendMessageRequest in telethon.tl.functions.messages -> messages.sendMessage"""
    name = cls.__name__
    if name.endswith("Request"):
        name = name[: -len("Request")]
    name = name[0].lower() + name[1:]
    ns = cls.__module__[len(_FN_PREFIX):].lstrip(".")
    return f"{ns}.{name}" if ns else name


def _type_name(cls) -> str:
    ns = cls.__module__[len(_TY_PREFIX):].lstrip(".")
    return f"{ns}.{cls.__name__}" if ns else cls.__name__


_functions: dict[str, type] = {}
_constructors: dict[str, type] = {}
_by_subclass: dict[int, list[type]] = {}

for _cls in tlobjects.values():
    if _cls.__module__.startswith(_FN_PREFIX):
        _functions[_official_name(_cls).lower()] = _cls
    elif _cls.__module__.startswith(_TY_PREFIX):
        _constructors[_type_name(_cls).lower()] = _cls
        _by_subclass.setdefault(_cls.SUBCLASS_OF_ID, []).append(_cls)

# Telethon-style aliases: "messages.SendMessageRequest", "messages.SendMessage"
_aliases: dict[str, type] = {}
for _cls in _functions.values():
    ns = _cls.__module__[len(_FN_PREFIX):].lstrip(".")
    base = _cls.__name__
    for n in (base, base[: -len("Request")] if base.endswith("Request") else base):
        _aliases[(f"{ns}.{n}" if ns else n).lower()] = _cls


def find_function(name: str):
    key = name.strip().lower()
    return _functions.get(key) or _aliases.get(key)


def find_constructor(name: str):
    key = name.strip().lower()
    if key.startswith("types."):
        key = key[6:]
    return _constructors.get(key)


# ---------- annotations ----------

_ann_re = re.compile(r"ForwardRef\('([^']+)'\)")


def _ann_str(ann) -> str:
    if isinstance(ann, str):
        return ann
    s = str(ann).replace("typing.", "")
    s = _ann_re.sub(r"\1", s)
    s = s.replace("datetime.datetime", "date").replace("<class '", "").replace("'>", "")
    return s


def parse_ann(ann) -> tuple[str, bool, bool]:
    """-> (base type, optional, is_list)"""
    s = _ann_str(ann).strip("'")
    optional = False
    is_list = False
    if s.startswith("Optional[") and s.endswith("]"):
        optional, s = True, s[9:-1]
    if s.startswith("List[") and s.endswith("]"):
        is_list, s = True, s[5:-1]
    s = s.strip("'")
    if s.startswith("Type"):
        s = s[4:]
    if s == "datetime":
        s = "date"
    return s, optional, is_list


def params_of(cls) -> list[dict]:
    if "__init__" not in cls.__dict__:
        return []
    sig = inspect.signature(cls.__init__)
    out = []
    for p in list(sig.parameters.values())[1:]:
        base, optional, is_list = parse_ann(p.annotation)
        out.append({
            "name": p.name,
            "type": f"Vector<{base}>" if is_list else base,
            "required": not optional and p.default is inspect.Parameter.empty,
            "flag": optional,
        })
    return out


def constructors_of(type_name: str) -> list[type]:
    return _by_subclass.get(zlib.crc32(type_name.encode("ascii")), [])


@lru_cache(maxsize=1)
def catalog() -> dict:
    methods = []
    for key, cls in sorted(_functions.items()):
        name = _official_name(cls)
        ns = name.split(".")[0] if "." in name else "core"
        methods.append({
            "name": name,
            "namespace": ns,
            "params": params_of(cls),
            "result": _result_type(cls),
            "doc": f"https://core.telegram.org/method/{name}",
        })
    return {"layer": LAYER, "count": len(methods), "methods": methods}


_camel = re.compile(r"[A-Z][a-z0-9]*")


@lru_cache(maxsize=1)
def _type_names() -> dict[int, str]:
    """Recover abstract TL type names (not stored by Telethon) via crc32 == SUBCLASS_OF_ID."""
    names: dict[int, str] = {}
    candidates: set[str] = set()
    for cls in tlobjects.values():
        if cls.__module__.startswith(_TY_PREFIX):
            ns = cls.__module__[len(_TY_PREFIX):].lstrip(".")
            words = _camel.findall(cls.__name__)
            for i in range(len(words), 0, -1):
                n = "".join(words[:i])
                candidates.add(f"{ns}.{n}" if ns else n)
        for p in list(inspect.signature(cls.__init__).parameters.values())[1:]:
            candidates.add(parse_ann(p.annotation)[0])
    candidates |= {"Bool", "int", "long", "string", "bytes", "true", "Object", "X"}
    candidates |= {f"Vector<{c}>" for c in list(candidates)}
    for c in candidates:
        names.setdefault(zlib.crc32(c.encode("ascii", "ignore")), c)
    return names


def _result_type(cls) -> str | None:
    return _type_names().get(cls.SUBCLASS_OF_ID)


def describe_type(name: str) -> dict:
    """Describe a constructor (e.g. inputPeerUser / InputPeerUser) or an abstract type (InputPeer)."""
    cls = find_constructor(name)
    if cls:
        return {"kind": "constructor", "name": _type_name(cls), "params": params_of(cls)}
    base = name[4:] if name.startswith("Type") else name
    base = base[0].upper() + base[1:] if base else base
    ctors = constructors_of(base)
    if not ctors and "." in base:
        ns, n = base.split(".", 1)
        ctors = constructors_of(f"{ns}.{n[0].upper()}{n[1:]}")
    if ctors:
        return {
            "kind": "type",
            "name": base,
            "constructors": [{"name": _type_name(c), "params": params_of(c)} for c in ctors],
        }
    return {}


# ---------- JSON -> TL ----------

class TLBuildError(ValueError):
    pass


_PRIMITIVES = {"int", "long", "str", "bool", "bytes", "float", "double", "date", "string", "int128", "int256"}


def _convert(value, base: str, is_list: bool, path: str):
    if value is None:
        return None
    if is_list:
        if not isinstance(value, list):
            raise TLBuildError(f"{path}: expected a list")
        return [_convert(v, base, False, f"{path}[{i}]") for i, v in enumerate(value)]
    if base == "bytes":
        if isinstance(value, dict) and "base64" in value:
            return base64.b64decode(value["base64"])
        if isinstance(value, str):
            if value.startswith("base64:"):
                return base64.b64decode(value[7:])
            return value.encode()
        if isinstance(value, list):
            return bytes(value)
        raise TLBuildError(f"{path}: expected bytes (string, 'base64:...' or byte list)")
    if base == "date":
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
        if isinstance(value, str):
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        raise TLBuildError(f"{path}: expected unix time or ISO date")
    if base == "bool":
        return bool(value)
    if base in ("int", "long", "int128", "int256"):
        if isinstance(value, str) and value.lstrip("-").isdigit():
            return int(value)
        return value
    if base == "float" or base == "double":
        return float(value)
    if base in _PRIMITIVES:
        return value
    # TL object: {"_": "inputPeerUser", ...}. Strings/ints are kept as-is so that
    # Telethon's resolve() can turn "@username", phone or id into the InputPeer/InputUser/...
    if isinstance(value, dict):
        return build_object(value, path)
    return value


def build_object(data: dict, path: str = "$"):
    name = data.get("_")
    if not name:
        raise TLBuildError(f"{path}: object needs a '_' key with the constructor name")
    cls = find_constructor(name) or find_function(name)
    if not cls:
        raise TLBuildError(f"{path}: unknown constructor '{name}'")
    return _instantiate(cls, {k: v for k, v in data.items() if k != "_"}, path)


def _instantiate(cls, params: dict, path: str):
    if "__init__" not in cls.__dict__:
        if params:
            raise TLBuildError(f"{path}: {cls.__name__} takes no params")
        return cls()
    sig = inspect.signature(cls.__init__)
    known = list(sig.parameters.values())[1:]
    names = {p.name for p in known}
    extra = set(params) - names
    if extra:
        raise TLBuildError(f"{path}: unknown params {sorted(extra)} for {cls.__name__}; expected {sorted(names)}")
    kwargs = {}
    for p in known:
        base, optional, is_list = parse_ann(p.annotation)
        if p.name in params:
            kwargs[p.name] = _convert(params[p.name], base, is_list, f"{path}.{p.name}")
        elif not optional and p.default is inspect.Parameter.empty:
            raise TLBuildError(f"{path}: missing required param '{p.name}' ({base})")
    return cls(**kwargs)


def build_request(method: str, params: dict | None) -> TLRequest:
    cls = find_function(method)
    if not cls:
        raise TLBuildError(f"unknown method '{method}'")
    return _instantiate(cls, params or {}, method)


# ---------- TL -> JSON ----------

def to_json(obj):
    if isinstance(obj, TLObject):
        obj = obj.to_dict()
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode()
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    return str(obj)
