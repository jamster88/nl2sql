"""The environment variables a settings module reads, by name, as compose can set them.

Each service's settings module reads its environment through the shared
helpers in `nl2sql_common.env`, and the compose tests check both directions
against it: every variable compose sets is one the service reads, and every
setting the service reads can be set through compose. Since 6.3 a password or
a token reaches a service as a file (V6-38), so a name is read more than one
way:

* `_secret("NAME")` reads `NAME`, or the file `NAME_FILE` names -- and compose
  sets only the file;
* `_env_url("X_URL")` reads `X_URL` (or `X_URL_FILE`) with the password
  replaced by the file `X_PASSWORD_FILE` names -- and compose sets the URL,
  with no password in it, beside the file.
"""

from __future__ import annotations

import re
from pathlib import Path

_CALL = re.compile(r'\b_?(env(?:_str|_bool|_int|_float|_tuple|_url)?|secret|os\.getenv)\(\s*"([A-Z_][A-Z0-9_]*)"')


def _stem(url_name: str) -> str:
    return url_name[: -len("_URL")] if url_name.endswith("_URL") else url_name


def read_names(source: str) -> set[str]:
    """Every name `source` reads, under each spelling it accepts: what compose
    may set without setting something nothing reads."""
    names: set[str] = set()
    for helper, name in _CALL.findall(source):
        names.add(name)
        if helper in ("secret", "env_url"):
            names.add(f"{name}_FILE")
        if helper == "env_url":
            names |= {f"{_stem(name)}_PASSWORD", f"{_stem(name)}_PASSWORD_FILE"}
    return names


def settable_names(source: str) -> set[str]:
    """What compose has to set for each setting `source` reads to be settable:
    a plain setting or a URL by its name, a secret by its file -- never the
    secret itself, which would put it in the container's environment."""
    names: set[str] = set()
    for helper, name in _CALL.findall(source):
        names.add(f"{name}_FILE" if helper == "secret" else name)
    return names


#: Worked out by the proxy's start-up script rather than set by anyone, and
#: the two places its tests point it elsewhere.
PROXY_UNSET = {"PAGE_ROOT", "PROXY_LISTEN_TLS", "UPSTREAM_AUTH_HEADER", "NL2SQL_PROXY_OUT", "NL2SQL_PROXY_TEMPLATES"}

#: What a page does not read: only the directory page asks LDAP_MODE, and
#: the directory page and MLflow's front door hold no service token.
PROXY_NOT_READ_BY = {
    "gui": {"LDAP_MODE"}, "review": {"LDAP_MODE"}, "curate": {"LDAP_MODE"}, "console": {"LDAP_MODE"},
    "directory": {"UPSTREAM_TOKEN_FILE"}, "mlflow": {"LDAP_MODE", "UPSTREAM_TOKEN_FILE"},
}


def proxy_names(page: str) -> set[str]:
    """Every setting the proxy image reads for `page` (V6-37): its start-up
    script's and its templates', less what it works out for itself."""
    proxy = Path(__file__).resolve().parent.parent / "proxy"
    sources = "".join(path.read_text() for path in [
        proxy / "10-nl2sql-proxy.envsh", *sorted(proxy.glob("**/*.template")),
    ])
    names = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", sources)) - PROXY_UNSET
    return names - PROXY_NOT_READ_BY[page]
