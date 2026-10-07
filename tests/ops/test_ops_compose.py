"""The dbprep service as compose declares it, against what `nl2sql_ops` reads.

The check every other service has, both ways: nothing compose sets on it is
something it does not read, and everything it reads can be set -- but for
what the stack itself fixes, named below with the reason. And the paths it
is given: each database's socket where its settings look for it, and the
documents where the snippet check reads them.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from nl2sql_ops.settings import DEFAULT_SOCKETS, OpsSettings
from tests.settings_names import read_names, settable_names

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = (REPO_ROOT / "common" / "nl2sql_ops" / "settings.py").read_text()
DBPREP = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]["dbprep"]

#: Read, and deliberately not set: the socket directory and the documents are
#: where compose mounts them, and the directory's host, port and StartTLS are
#: the stack's own `ldap` service's -- 6.2's launch.sh fixed the host the same
#: way. Each is for running the one-shot outside compose.
FIXED_BY_THE_STACK = {
    "NL2SQL_SOCKETS_DIR", "NL2SQL_GOLDEN_DOCUMENT", "NL2SQL_SNIPPETS_DOCUMENT",
    "NL2SQL_LDAP_HOST", "NL2SQL_LDAP_PORT", "NL2SQL_LDAP_TLS",
}


def test_every_variable_compose_sets_on_dbprep_is_one_it_reads():
    unread = sorted(set(DBPREP["environment"]) - read_names(SOURCE))
    assert unread == [], f"compose sets {unread} on dbprep, which nothing in it reads"


def test_every_setting_dbprep_reads_can_be_set_through_compose_but_what_the_stack_fixes():
    assert FIXED_BY_THE_STACK <= read_names(SOURCE), "a fixed setting is no longer read: drop it from the list"
    missing = sorted(settable_names(SOURCE) - FIXED_BY_THE_STACK - set(DBPREP["environment"]))
    assert missing == [], f"nl2sql_ops reads these, but compose never passes them: {missing}"


def test_each_password_it_reads_is_a_secret_compose_mounts():
    files = {value.removeprefix("/run/secrets/") for key, value in DBPREP["environment"].items() if key.endswith("_FILE")}
    assert files == set(DBPREP["secrets"])


def test_each_database_socket_is_mounted_where_its_settings_look():
    """A socket mounted under another name is a database dbprep reports as
    not running, and prepares nothing in."""
    settings = OpsSettings.from_env()
    wanted = {"retail", "stores", *(login.socket for login in (*settings.knowledge, settings.mlflow))}
    mounted = {target.removeprefix(DEFAULT_SOCKETS + "/") for _, target, *_ in
               (volume.split(":") for volume in DBPREP["volumes"]) if target.startswith(DEFAULT_SOCKETS)}
    assert mounted == wanted


def test_the_documents_are_mounted_where_the_snippet_check_reads_them():
    settings = OpsSettings.from_env()
    [mount] = [volume for volume in DBPREP["volumes"] if volume.startswith("./context_questions:")]
    target = mount.split(":")[1]
    assert settings.golden_document.parent == settings.snippets_document.parent == Path(target)
    assert mount.endswith(":ro"), "it reads the documents; it writes none of them"
