"""Real-LEO TLE acquisition layer (Phase 9.3).

Resolves a ``--tle`` specification to a local TLE file path:

  - ``path/to/file.tle``      used verbatim (must exist)
  - ``celestrak:<GROUP>``     fetched from the Celestrak GP API, cached
                              locally with a TTL; on network failure falls
                              back to the bundled offline catalog
  - ``url:https://...``       fetched from an arbitrary TLE URL, cached

Celestrak reference (free, no account):
    https://celestrak.org/NORAD/elements/gp.php?GROUP=<group>&FORMAT=tle

Network egress honours the standard ``HTTP_PROXY`` / ``HTTPS_PROXY``
environment variables (urllib default behaviour); an explicit proxy may
also be passed. A browser-like User-Agent is sent because Celestrak
rejects the default urllib agent.
"""

import os
import sys
import time
import urllib.request
import urllib.error

CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
DEFAULT_CACHE_DIR = os.path.join("data", "tle_cache")
DEFAULT_FALLBACK = os.path.join("data", "starlink_sample.tle")
DEFAULT_TTL_S = 24.0 * 3600.0
_USER_AGENT = "dayilixiang-satviz/4.0 (real-data integration; contact: local)"


class TleSourceError(RuntimeError):
    """Raised when a TLE source cannot be resolved to a usable file."""


def _log(msg):
    # Diagnostics only; keep ASCII for the GBK Windows console.
    print(f"[tle_source] {msg}", file=sys.stderr)


def _cache_path(cache_dir, key):
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    return os.path.join(cache_dir, safe + ".tle")


def _cache_is_fresh(path, ttl_s):
    if not os.path.exists(path):
        return False
    if ttl_s <= 0:
        return False  # ttl 0 == always refetch
    age = time.time() - os.path.getmtime(path)
    return age < ttl_s


def _download(url, dest, proxy=None, timeout=120.0):
    """Stream ``url`` to ``dest`` (atomic via temp file). Honours env proxies
    unless an explicit ``proxy`` is given. Returns bytes written."""
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener()  # uses env HTTP(S)_PROXY
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    tmp = dest + ".part"
    total = 0
    with opener.open(req, timeout=timeout) as resp, \
            open(tmp, "wb") as out:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    os.replace(tmp, dest)
    return total


def _looks_like_tle(path):
    """Cheap sanity check: at least one '1 ' / '2 ' element-line pair."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\r\n") for ln in fh if ln.strip()]
    except OSError:
        return False
    for i in range(len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            return True
    return False


def resolve_tle(spec, cache_dir=DEFAULT_CACHE_DIR, ttl_s=DEFAULT_TTL_S,
                fallback=DEFAULT_FALLBACK, proxy=None, timeout=120.0):
    """Resolve a --tle spec to a local TLE file path.

    Returns the path to a readable TLE file. Never raises for the
    ``celestrak:`` / ``url:`` forms when a fallback exists: a failed fetch
    degrades to the offline catalog (with a warning) so accelerated
    simulation still runs disconnected.
    """
    if spec.startswith("celestrak:"):
        group = spec.split(":", 1)[1].strip()
        if not group:
            raise TleSourceError("celestrak: spec needs a GROUP, e.g. "
                                 "--tle celestrak:starlink")
        url = CELESTRAK_GP_URL.format(group=group)
        cache_key = f"celestrak_{group}"
    elif spec.startswith("url:"):
        url = spec.split(":", 1)[1].strip()
        if not url.startswith("http"):
            raise TleSourceError(f"unsupported url spec: {spec!r}")
        cache_key = "url_" + url
    else:
        # Plain local path.
        if not os.path.exists(spec):
            raise TleSourceError(f"TLE file not found: {spec!r}")
        return spec

    os.makedirs(cache_dir, exist_ok=True)
    dest = _cache_path(cache_dir, cache_key)

    if _cache_is_fresh(dest, ttl_s) and _looks_like_tle(dest):
        _log(f"cache hit: {dest}")
        return dest

    try:
        _log(f"fetching {url}")
        nbytes = _download(url, dest, proxy=proxy, timeout=timeout)
        if not _looks_like_tle(dest):
            raise TleSourceError("downloaded payload is not a valid TLE")
        _log(f"cached {nbytes} bytes -> {dest}")
        return dest
    except (urllib.error.URLError, OSError, TleSourceError) as exc:
        _log(f"fetch failed ({exc}); falling back")
        if fallback and os.path.exists(fallback):
            _log(f"using offline fallback {fallback}")
            return fallback
        raise TleSourceError(
            f"could not fetch {url} and no fallback available: {exc}") from exc
