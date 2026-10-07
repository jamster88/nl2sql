"""What every page's template shares (V6-37), checked once.

Until 6.3 each page had a template of its own, and each page's project tests
checked it. Since, the review, curation and console pages share
`pages/service.conf.template`, and every page the site rules in
`shared/site.conf` -- so this is where those are held, once. What only one
page does stays with that page's project tests: the paths its dev server
proxies, the web interface's event stream, the directory page's two ports.
Every template verifying its upstream, carrying no token of its own and
resolving its upstream per request are tests/docker/test_script_coverage.py's,
which reads every template there is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROXY = Path(__file__).resolve().parent.parent.parent / "proxy"
SITE = (PROXY / "shared" / "site.conf").read_text()


def _template(name: str) -> str:
    return (PROXY / "pages" / f"{name}.conf.template").read_text()


@pytest.mark.parametrize("name", ["gui", "service", "directory"])
def test_every_page_is_served_from_any_path_and_its_assets_are_immutable(name: str):
    """A deep link reloaded lands on the page; a hashed asset is cached for
    good, and the page that names them never is."""
    assert "include /etc/nginx/nl2sql/shared/site.conf;" in _template(name)


def test_the_site_rules_serve_the_page_for_any_path_and_cache_only_the_hashed_assets():
    assert "try_files $uri $uri/ /index.html;" in SITE
    assert re.search(r"location /assets/ \{[^}]*immutable", SITE, re.S)
    assert re.search(r"location = /index\.html \{[^}]*no-cache", SITE, re.S)


def test_mlflows_front_door_serves_no_page_of_its_own():
    assert "site.conf" not in _template("mlflow")


@pytest.mark.parametrize("name", ["gui", "service", "directory", "mlflow"])
def test_each_page_waits_for_its_service_as_long_as_its_own_setting_says(name: str):
    """A question, a promotion, a query and an import each take a different
    while: the read timeout is each page's own setting (`.env`'s
    `*_READ_TIMEOUT`), never a literal here. That each default outlasts its
    service is the compose tests' to check."""
    template = _template(name)
    assert "proxy_read_timeout ${UPSTREAM_READ_TIMEOUT};" in template
    assert not re.search(r"proxy_read_timeout \d", template)


@pytest.mark.parametrize("name", ["gui", "service"])
def test_the_pages_with_a_service_token_add_it_on_this_side_of_the_hop(name: str):
    """The browser never holds it: the start-up script works the header out
    from the token file, with sign-in off, and the template adds it."""
    assert 'proxy_set_header Authorization "${UPSTREAM_AUTH_HEADER}";' in _template(name)
