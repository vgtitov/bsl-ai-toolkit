# /// script
# dependencies = ["mcp"]
# ///
"""MCP-сервер чтения кода 1С — точный контекст по РЕАЛЬНОМУ коду, чтобы AI не гадал.

Поверх локальных клонов конфигураций/расширений 1С (EDT- или Designer-XML формат), поиск через ripgrep
(с чистым Python-фолбэком). АВТО-ПОДХВАТ всех репозиториев в каталоге исходников: склонировал репо в
ONEC_SRC_DIR — search/find/read видят его без правок.

Универсальность через КОНФИГ (необязательный TOML, путь в ONEC_LAYERS_CONFIG): группы/роли слоёв,
человекочитаемые ярлыки (pin), scope-группы для поиска и режимы/профили контуров. Без конфига —
чистое auto-discovery: каждый слой тегируется именем репозитория [repo], scope 'all'. Схема и примеры
локализации — в config/layers.example.toml. Локализация = свой конфиг, КОД МЕНЯТЬ НЕ НУЖНО.

Запуск: uv run onec_mcp.py   (или python onec_mcp.py)
Каталог исходников: ONEC_SRC_DIR (по умолчанию ~/erp-src). Транспорт: MCP_TRANSPORT (stdio|sse|streamable-http),
MCP_HOST/MCP_PORT. Активный профиль из конфига: ONEC_PROFILE. Кроссплатформенно.
"""
import os, glob, re, shutil, subprocess
from mcp.server.fastmcp import FastMCP

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover — на 3.10 и ниже конфиг просто отключается
    tomllib = None

# Транспорт: stdio (локально, по умолчанию) или http/sse (контейнер/центральный сервис).
mcp = FastMCP("onec-code",
              host=os.environ.get("MCP_HOST", "0.0.0.0"),
              port=int(os.environ.get("MCP_PORT", "8000")))
HOME = os.path.expanduser("~")
SRC = (os.environ.get("ONEC_SRC_DIR") or os.path.join(HOME, "erp-src")).replace("\\", "/")
PROFILE = os.environ.get("ONEC_PROFILE", "").strip()


def _load_config():
    """Прочитать конфиг слоёв (TOML) из ONEC_LAYERS_CONFIG. Нет файла/пути/tomllib — пустой конфиг
    (поведение по умолчанию = чистое auto-discovery)."""
    path = os.environ.get("ONEC_LAYERS_CONFIG", "").strip()
    if not path or tomllib is None or not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


CFG = _load_config()
_OPTS = CFG.get("options", {}) if isinstance(CFG.get("options"), dict) else {}
# Репозитории-исключения (тестовые песочницы и т.п.) — из конфига или env, дефолт "sandbox".
IGNORE = {r.strip() for r in
          os.environ.get("ONEC_SRC_IGNORE", ",".join(_OPTS.get("ignore", ["sandbox"]))).split(",") if r.strip()}
_CLASSIFY = CFG.get("classify", []) if isinstance(CFG.get("classify"), list) else []
_CLASSIFY_DEFAULT = CFG.get("classify_default", {}) if isinstance(CFG.get("classify_default"), dict) else {}
_PINS = CFG.get("pin", []) if isinstance(CFG.get("pin"), list) else []
_SCOPES_CFG = CFG.get("scopes", {}) if isinstance(CFG.get("scopes"), dict) else {}
_PROFILES = CFG.get("profiles", {}) if isinstance(CFG.get("profiles"), dict) else {}


def _repo_of(path):
    return os.path.relpath(path, SRC).replace("\\", "/").split("/")[0]


def _profile_filter(repo):
    """Активный профиль (ONEC_PROFILE) ограничивает набор репозиториев include/exclude. Нет профиля — всё."""
    if not PROFILE or PROFILE not in _PROFILES:
        return True
    p = _PROFILES[PROFILE]
    inc, exc = p.get("include"), p.get("exclude")
    if inc is not None and repo not in inc:
        return False
    if exc and repo in exc:
        return False
    return True


def _discover_roots():
    """Найти корни конфигураций/расширений в SRC по подпапке CommonModules.
    Оба layout: EDT `<repo>/src/<имя>/src` и Designer-XML `<repo>/src/<имя>`.
    Вызывается ОДИН РАЗ при старте — новые клоны видны после перезапуска."""
    roots, seen = [], set()
    for pat in (f"{SRC}/*/src/*/src", f"{SRC}/*/src/*", f"{SRC}/*/src", f"{SRC}/*"):
        for p in sorted(glob.glob(pat)):
            p = p.replace("\\", "/")
            if p in seen or not os.path.isdir(p):
                continue
            repo = _repo_of(p)
            if repo in IGNORE or not _profile_filter(repo):
                continue
            if os.path.isdir(os.path.join(p, "CommonModules")):
                roots.append(p)
                seen.add(p)
    # Явно приколотые слои (pin) — добавить, даже если auto-discovery их не нашёл.
    for pin in _PINS:
        rel = (pin.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        cand = f"{SRC}/{rel}"
        if cand not in seen and os.path.isdir(cand) and _profile_filter(_repo_of(cand)):
            roots.append(cand)
            seen.add(cand)
    return sorted(roots, key=len, reverse=True)  # длинные пути первыми — корректная замена корня на тег


def _match_rule(rule, repo):
    g, rx = rule.get("glob"), rule.get("regex")
    if g is not None:
        import fnmatch
        return fnmatch.fnmatch(repo, g)
    if rx is not None:
        return bool(re.search(rx, repo))
    return False


def _classify(root):
    """Вернуть (role, tag-строка) для корня по конфигу. Без конфига — role 'layer', ярлык [repo]."""
    repo = _repo_of(root)
    rel = os.path.relpath(root, SRC).replace("\\", "/")
    for pin in _PINS:
        if (pin.get("path") or "").replace("\\", "/").strip("/") == rel:
            return pin.get("role", "layer"), f"[{pin.get('label', repo)}]"
    for rule in _CLASSIFY:
        if _match_rule(rule, repo):
            tag = rule.get("tag")
            return rule.get("role", "layer"), (f"[{tag}:{repo}]" if tag else f"[{repo}]")
    tag = _CLASSIFY_DEFAULT.get("tag")
    return _CLASSIFY_DEFAULT.get("role", "layer"), (f"[{tag}:{repo}]" if tag else f"[{repo}]")


_ROOTS = _discover_roots()
_ROLE_OF = {}
_TAG_OF = {}
for _r in _ROOTS:
    _role, _tag = _classify(_r)
    _ROLE_OF[_r] = _role
    _TAG_OF[_r] = _tag
# (корень, тег), длинные пути первыми — для корректной замены в выводе поиска.
_ROOT_TAGS = sorted([(r, _TAG_OF[r]) for r in _ROOTS], key=lambda rt: len(rt[0]), reverse=True)
_ALL_ROLES = sorted(set(_ROLE_OF.values()))
# scope-группы: из конфига + гарантированная 'all' = все роли.
SCOPES = {name: list(roles) for name, roles in _SCOPES_CFG.items() if isinstance(roles, list)}
SCOPES.setdefault("all", _ALL_ROLES)
DEFAULT_SCOPE = _OPTS.get("default_scope", "all")
if DEFAULT_SCOPE not in SCOPES and DEFAULT_SCOPE not in _ALL_ROLES:
    DEFAULT_SCOPE = "all"


def _roles_for_scope(scope):
    """Роли, входящие в scope-группу/имя роли. Для КОНКРЕТНОГО слоя роли не при чём (см. _match_roots)."""
    if scope in SCOPES:
        return SCOPES[scope]
    if scope in _ALL_ROLES:
        return [scope]
    return _ALL_ROLES


# Синонимы scope из конфига: алиас (нормализованный) → regex по имени репозитория.
# Локализация задаёт здесь имена контуров/ядер компании ([aliases] в layers.toml) —
# движок остаётся generic, спец-имён в коде нет.
_ALIASES_CFG = CFG.get("aliases", {}) if isinstance(CFG.get("aliases"), dict) else {}


def _norm(x):
    return re.sub(r"[\[\]\s]", "", (x or "").lower())


_ALIASES = { _norm(k): str(v) for k, v in _ALIASES_CFG.items() if isinstance(v, str) and v }


def _match_roots(scope):
    """Сузить scope до КОНКРЕТНОГО слоя (а не роли/группы): подстрока имени репозитория
    или тега/ярлыка слоя; фолбэк — алиас из конфига ([aliases]: синоним → regex по репо).
    Возвращает совпавшие корни без дублей."""
    sn = _norm(scope)
    if not sn:
        return []
    hits = [r for r in _ROOTS if sn in _norm(_repo_of(r)) or sn in _norm(_TAG_OF.get(r, ""))]
    if hits:
        return list(dict.fromkeys(hits))
    rx = _ALIASES.get(sn)
    if rx:
        try:
            hits = [r for r in _ROOTS if re.search(rx, _repo_of(r).lower())]
        except re.error:
            hits = []
    return list(dict.fromkeys(hits))


def _scope_known(scope):
    return scope in SCOPES or scope in _ALL_ROLES or bool(_match_roots(scope))


def _dirs(scope):
    """Каталоги для поиска. scope: имя scope-группы/роли (из конфига) — по ролям;
    ИНАЧЕ конкретный слой по репо/тегу/алиасу (см. _match_roots)."""
    if scope in SCOPES or scope in _ALL_ROLES:
        roles = _roles_for_scope(scope)
        return [r for r in _ROOTS if _ROLE_OF.get(r) in roles]
    return _match_roots(scope)


def _retag(text):
    for root, tag in _ROOT_TAGS:
        text = text.replace(root, tag)
    return text


def _find_rg():
    exe = shutil.which("rg")
    if exe:
        return exe
    for c in (r"C:\Program Files\ripgrep\rg.exe",
              os.path.join(HOME, ".local", "bin", "rg.exe"),
              os.path.join(HOME, "scoop", "shims", "rg.exe")):
        if os.path.isfile(c):
            return c
    for c in glob.glob(os.path.join(HOME, "AppData", "Local", "Microsoft", "WinGet",
                                    "Packages", "BurntSushi.ripgrep*", "**", "rg.exe"), recursive=True):
        if os.path.isfile(c):
            return c
    return None


RG = _find_rg()


def _grep(query, dirs, timeout=180):
    if not dirs:
        return []
    if RG:
        try:
            r = subprocess.run([RG, "-n", "--no-heading", "-i", "-S", "--path-separator", "/", query, *dirs],
                               capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
            return r.stdout.splitlines()
        except Exception as e:
            return [f"[ошибка поиска rg: {e}]"]
    ql, out = query.lower(), []
    for base in dirs:
        for root, _, files in os.walk(base):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) > 4_000_000:
                        continue
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if ql in line.lower():
                                out.append(f"{fp.replace(chr(92), '/')}:{i}:{line.rstrip()}")
                                if len(out) >= 5000:
                                    return out
                except Exception:
                    pass
    return out


def _scope_hint():
    """Подсказка о доступных scope: scope-группы (из конфига) + конкретные слои (теги)."""
    names = [s for s in SCOPES if s != "all"]
    groups = ", ".join(["all", *names])
    layers = ", ".join(sorted({t for t in _TAG_OF.values()}))
    return f" scope-группы: {groups}; ЛИБО конкретный слой (тег/репо/алиас): {layers}."


@mcp.tool()
def search_1c(query: str, scope: str = "", max_results: int = 40) -> str:
    """Поиск по РЕАЛЬНОМУ коду 1С во всех склонированных конфигурациях/расширениях.
    Используй, чтобы проверить, как устроена типовая или есть ли уже готовое в расширении, а не гадать.
    query — текст/имя метода/объекта (регистр игнорируется). scope ограничивает слои: scope-группа
    (из конфига) ЛИБО КОНКРЕТНЫЙ слой по тегу/имени репозитория/алиасу — чтобы не фильтровать
    вручную. '' = по умолчанию."""
    if not _ROOTS:
        return f"код не найден локально ({SRC}). Склонируй репозитории в ONEC_SRC_DIR."
    sc = scope or DEFAULT_SCOPE
    if not _scope_known(sc):
        return f"неизвестный scope '{sc}'.{_scope_hint()}"
    raw = _grep(query, _dirs(sc))
    if not raw:
        return f"по '{query}' ничего не найдено (scope={sc})."
    head = [_retag(l) for l in raw[:max_results]]
    extra = len(raw) - max_results
    return "\n".join(head) + (f"\n... ещё {extra} совпадений" if extra > 0 else "")


@mcp.tool()
def find_object(name: str, scope: str = "") -> str:
    """Найти объект метаданных (Документ, Справочник, Регистр, ОбщийМодуль, Перечисление и т.п.) по имени
    или части имени. Возвращает пути в каждом слое (тег слоя). scope: scope-группа ЛИБО конкретный
    слой по тегу/репо/алиасу. '' = по умолчанию."""
    if not _ROOTS:
        return f"код не найден локально ({SRC})."
    sc = scope or DEFAULT_SCOPE
    if not _scope_known(sc):
        return f"неизвестный scope '{sc}'.{_scope_hint()}"
    found, nl = [], name.lower()
    for base in _dirs(sc):
        base_depth = base.rstrip("/").count("/")
        for root, dnames, _ in os.walk(base):
            if root.replace("\\", "/").rstrip("/").count("/") - base_depth >= 3:
                dnames[:] = []
            for d in dnames:
                if nl in d.lower():
                    found.append(f"{_TAG_OF.get(base, '[' + _repo_of(base) + ']')} {os.path.relpath(os.path.join(root, d), base)}")
    return "\n".join(sorted(set(found))[:60]) or f"объект '{name}' не найден (scope={sc})."


@mcp.tool()
def read_module(path: str, start: int = 1, end: int = 250) -> str:
    """Прочитать BSL-модуль или метаданные по пути (как из search_1c/find_object), строки start..end.
    Путь можно дать относительно тега слоя или абсолютный."""
    p = path.strip()
    for root, tag in _ROOT_TAGS:
        p = p.replace(tag, root)
    p = p.strip()
    if not os.path.isabs(p):
        for base in _ROOTS:
            cand = os.path.join(base, p)
            if os.path.exists(cand):
                p = cand
                break
    if not os.path.isfile(p):
        return f"файл не найден: {path}"
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        chunk = lines[max(0, start - 1):end]
        return f"{os.path.basename(p)} (строки {start}-{min(end, len(lines))} из {len(lines)}):\n" + "".join(chunk)
    except Exception as e:
        return f"ошибка чтения: {e}"


@mcp.tool()
def list_modules(scope: str = "") -> str:
    """Список общих модулей по слоям — что уже реализовано, чтобы переиспользовать, а не изобретать.
    scope: scope-группа ЛИБО конкретный слой по тегу/репо/алиасу ('' = по умолчанию)."""
    sc = scope or DEFAULT_SCOPE
    if not _scope_known(sc):
        return f"неизвестный scope '{sc}'.{_scope_hint()}"
    blocks = []
    for root in _dirs(sc):
        d = os.path.join(root, "CommonModules")
        if not os.path.isdir(d):
            continue
        mods = sorted(os.listdir(d))
        blocks.append(f"Общие модули — {_TAG_OF.get(root, '[' + _repo_of(root) + ']')}:\n" + "\n".join(f"- {m}" for m in mods))
    return "\n\n".join(blocks) if blocks else "модули не найдены."


if __name__ == "__main__":
    # stdio для локального Claude Code; sse/streamable-http — для запуска в Docker как центрального сервиса.
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))
