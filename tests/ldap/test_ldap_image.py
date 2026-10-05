"""The directory image (`ldap/Dockerfile`), read and -- with --run-docker -- run.

Run, it is checked as a standalone directory and as a replica of a second
one, which is what the replica mode exists for: the replica copies the
primary's mirrored groups and their people (nested groups included), passes
a bind through to the primary, refuses every write, and follows an edit made
on the primary.
"""

from __future__ import annotations

import re
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from nl2sql_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = REPO_ROOT / "ldap" / "Dockerfile"
IMAGE = "nl2sql-ldap:pytest"
SOCKET = "ldapi://%2Fvar%2Flib%2Fopenldap%2Frun%2Fldapi"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text()


def test_the_base_image_is_pinned(dockerfile: str):
    assert re.search(r"^FROM alpine:3\.\d+$", dockerfile, re.MULTILINE)


def test_the_overlays_each_mode_needs_are_installed(dockerfile: str):
    for package in (
        "openldap-back-mdb",
        "openldap-overlay-memberof",
        "openldap-overlay-refint",
        "openldap-overlay-ppolicy",
        "openldap-overlay-remoteauth",
        "openldap-passwd-argon2",
        "openldap-passwd-sha2",
    ):
        assert package in dockerfile, package
    # Its Python libraries come from the hash-checked lock, not Alpine.
    lock = (DOCKERFILE.parent / "requirements.lock").read_text()
    assert "ldap3==" in lock and "cryptography==" in lock


def test_root_is_given_up_by_the_entry_point_not_the_image(dockerfile: str):
    """No `USER ldap`: only root can hand over a named volume another
    container created first, so the entry point starts as root and becomes
    `ldap` itself (`service.become`). That it does is checked on a running
    container below."""
    assert not re.search(r"^USER ", dockerfile, re.MULTILINE)


def test_only_the_directory_package_its_lock_and_the_shared_package_are_copied(dockerfile: str):
    assert re.findall(r"^COPY (\S+)", dockerfile, re.MULTILINE) == [
        "ldap/requirements.lock",
        "common/",
        "ldap/nl2sql_ldap/",
    ]


def test_the_image_says_when_it_is_ready_and_what_version_it_is(dockerfile: str):
    assert "HEALTHCHECK" in dockerfile and "python3 -m nl2sql_ldap health" in dockerfile
    assert f'org.opencontainers.image.version="{__version__}"' in dockerfile
    assert 'ENTRYPOINT ["python3", "-m", "nl2sql_ldap"]' in dockerfile


# --- run -----------------------------------------------------------------------


def _docker(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, **kwargs)


@pytest.fixture(scope="module")
def image(docker_daemon_available):
    if not docker_daemon_available:
        pytest.skip("no Docker daemon")
    _docker("build", "-q", "-f", str(DOCKERFILE), "-t", IMAGE, str(REPO_ROOT))
    return IMAGE


@pytest.fixture(scope="module")
def network(image):
    name = f"nl2sql-ldap-test-{uuid.uuid4().hex[:8]}"
    _docker("network", "create", name)
    started: list[str] = []
    yield name, started
    for container in started:
        _docker("rm", "-f", "-v", container, check=False)
    _docker("network", "rm", name, check=False)


def _start(network, name: str, *env: str, alias: str, volume: str | None = None) -> str:
    net, started = network
    container = f"{net}-{name}"
    args = ["run", "-d", "--name", container, "--hostname", alias, "--network", net, "--network-alias", alias]
    if volume:
        args += ["-v", volume]
    for pair in env:
        args += ["-e", pair]
    _docker(*args, IMAGE)
    started.append(container)
    for _ in range(60):
        state = _docker("inspect", "-f", "{{.State.Health.Status}}", container, check=False).stdout.strip()
        if state == "healthy":
            return container
        time.sleep(1)
    pytest.fail(_docker("logs", container, check=False).stdout + _docker("logs", container, check=False).stderr)


def _ldap(container: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    """An OpenLDAP tool in the container, as `ldap`: on the local socket that
    user is the directory's root, and Docker would otherwise run it as root."""
    return _docker(
        "exec", "-i", "-u", "ldap", "-e", "LDAPTLS_CACERT=/etc/nl2sql/ldap-tls/ldap.crt", container, *args,
        check=False, input=stdin,
    )


@pytest.mark.docker
def test_a_standalone_directory_starts_with_its_first_administrator(network):
    ldap = _start(network, "standalone", "LDAP_SERVICE_PASSWORD=svc-password-1", "LDAP_ADMIN_PASSWORD=admin-password-1", alias="nl2sql-ldap")
    base = "dc=nl2sql,dc=local"
    whoami = _ldap(ldap, "ldapwhoami", "-ZZ", "-H", "ldap://nl2sql-ldap", "-x", "-D", f"uid=admin,ou=people,{base}", "-w", "admin-password-1")
    assert whoami.returncode == 0 and "uid=admin" in whoami.stdout
    plain = _ldap(ldap, "ldapwhoami", "-H", "ldap://nl2sql-ldap", "-x", "-D", f"uid=admin,ou=people,{base}", "-w", "admin-password-1")
    assert "Confidentiality required" in plain.stderr, "a password needs an encrypted connection"
    groups = _ldap(ldap, "ldapsearch", "-LLL", "-Y", "EXTERNAL", "-H", SOCKET, "-b", f"uid=admin,ou=people,{base}", "memberOf")
    assert groups.stdout.count("memberOf:") == 4
    short = _ldap(ldap, "ldappasswd", "-ZZ", "-H", "ldap://nl2sql-ldap", "-x", "-D", f"cn=nl2sql-auth,ou=services,{base}",
                  "-w", "svc-password-1", "-s", "short", f"uid=admin,ou=people,{base}")
    assert "quality" in short.stderr + short.stdout, "the password policy's minimum length holds"
    stored = _ldap(ldap, "ldapsearch", "-LLL", "-o", "ldif-wrap=no", "-Y", "EXTERNAL", "-H", SOCKET, "-b", f"uid=admin,ou=people,{base}", "userPassword")
    assert "userPassword:: e0FSR09OMn0" in stored.stdout, "stored as {ARGON2}"


@pytest.mark.docker
def test_a_certificate_volume_another_image_owned_last_is_still_written_and_nothing_runs_as_root(network):
    """What compose does, and 6.0.0's image could not survive. Compose creates
    the directory's and the auth service's containers before it starts either,
    and creating a container on an empty volume copies that image's directory
    -- ownership included -- onto it: the last created wins. The auth image's
    `/etc/nl2sql/ldap-tls` is root's, so the volume was root's when the
    directory started as `ldap`, and it could not write its certificate."""
    net, started = network
    volume = f"{net}-race"
    rootdir = "nl2sql-rootdir:pytest"
    _docker("build", "-q", "-t", rootdir, "-", input="FROM alpine:3.22\nRUN mkdir -p /etc/nl2sql/ldap-tls\n")
    _docker("volume", "create", volume)
    ldap = f"{net}-race-ldap"
    _docker(
        "create", "--name", ldap, "--hostname", "nl2sql-ldap", "--network", net,
        "-e", "LDAP_SERVICE_PASSWORD=svc-password-1", "-e", "LDAP_ADMIN_PASSWORD=admin-password-1",
        "-v", f"{volume}:/etc/nl2sql/ldap-tls", IMAGE,
    )
    started.append(ldap)
    other = f"{net}-race-other"
    _docker("create", "--name", other, "-v", f"{volume}:/etc/nl2sql/ldap-tls:ro", rootdir, "true")
    started.append(other)
    owner = _docker("run", "--rm", "-v", f"{volume}:/v", "alpine:3.22", "stat", "-c", "%u", "/v").stdout.strip()
    assert owner == "0", "the volume is root's when the directory starts, as under compose"
    _docker("start", ldap)
    for _ in range(60):
        if _docker("inspect", "-f", "{{.State.Health.Status}}", ldap, check=False).stdout.strip() == "healthy":
            break
        time.sleep(1)
    else:
        pytest.fail(_docker("logs", ldap, check=False).stderr)
    listing = _docker("exec", ldap, "ls", "-ln", "/etc/nl2sql/ldap-tls").stdout
    assert "ldap.crt" in listing and "ldap.key" in listing
    processes = _docker("exec", ldap, "ps", "-o", "user,args").stdout.splitlines()[1:]
    running = [line for line in processes if "slapd" in line or "nl2sql_ldap" in line]
    assert running and all(line.split()[0] == "ldap" for line in running), processes
    # A command run with `docker exec`, as root, gives root up the same way.
    assert _docker("exec", ldap, "python3", "-m", "nl2sql_ldap", "health", check=False).returncode == 0
    _docker("rm", "-f", ldap, other, check=False)
    _docker("volume", "rm", volume, check=False)


@pytest.mark.docker
def test_a_replica_copies_its_primary_passes_binds_through_and_is_read_only(network):
    net, _ = network
    upstream_base = "dc=corp,dc=example"
    tls_volume = f"{net}-uptls"
    primary = _start(
        network, "primary",
        f"LDAP_BASE_DN={upstream_base}", "LDAP_TLS_HOSTNAMES=primary", "LDAP_SERVICE_PASSWORD=up-svc-password",
        "LDAP_ADMIN_PASSWORD=up-admin-password", "LDAP_GROUPS=NL2SQL Users,NL2SQL Reviewers,Engineering",
        alias="primary", volume=f"{tls_volume}:/etc/nl2sql/ldap-tls",
    )
    edits = (
        f"dn: uid=carol,ou=people,{upstream_base}\nchangetype: add\nobjectClass: inetOrgPerson\nuid: Carol\ncn: Carol Jones\nsn: Jones\n\n"
        f"dn: cn=Engineering,ou=groups,{upstream_base}\nchangetype: modify\nadd: member\nmember: uid=carol,ou=people,{upstream_base}\n\n"
        f"dn: cn=NL2SQL Reviewers,ou=groups,{upstream_base}\nchangetype: modify\nadd: member\nmember: cn=Engineering,ou=groups,{upstream_base}\n"
    )
    assert _ldap(primary, "ldapmodify", "-Y", "EXTERNAL", "-H", SOCKET, stdin=edits).returncode == 0
    assert _ldap(primary, "ldappasswd", "-Y", "EXTERNAL", "-H", SOCKET, "-s", "carol-password-1", f"uid=carol,ou=people,{upstream_base}").returncode == 0

    replica = _start(
        network, "replica",
        "LDAP_MODE=replica", "LDAP_SERVICE_PASSWORD=rep-svc-password",
        "LDAP_UPSTREAM_URI=ldap://primary:389", "LDAP_UPSTREAM_STARTTLS=true", "LDAP_UPSTREAM_CACERT=/upstream-tls/ldap.crt",
        f"LDAP_UPSTREAM_BIND_DN=cn=nl2sql-auth,ou=services,{upstream_base}", "LDAP_UPSTREAM_BIND_PASSWORD=up-svc-password",
        f"LDAP_UPSTREAM_BASE_DN={upstream_base}", "LDAP_UPSTREAM_FLAVOUR=openldap",
        "LDAP_REPLICA_GROUPS=NL2SQL Users=nl2sql-users;NL2SQL Reviewers=nl2sql-reviewers", "LDAP_REPLICA_INTERVAL=3",
        alias="nl2sql-ldap", volume=f"{tls_volume}:/upstream-tls:ro",
    )
    base = "dc=nl2sql,dc=local"
    for _ in range(20):
        found = _ldap(replica, "ldapsearch", "-LLL", "-Y", "EXTERNAL", "-H", SOCKET, "-b", f"ou=people,{base}", "uid", "seeAlso")
        if "uid: carol" in found.stdout:
            break
        time.sleep(1)
    assert f"seeAlso: uid=carol,ou=people,{upstream_base}" in found.stdout, "copied through the nested group"
    reviewers = _ldap(replica, "ldapsearch", "-LLL", "-Y", "EXTERNAL", "-H", SOCKET, "-b", f"cn=nl2sql-reviewers,ou=groups,{base}", "member")
    assert f"member: uid=carol,ou=people,{base}" in reviewers.stdout

    bind = ["ldapwhoami", "-ZZ", "-H", "ldap://nl2sql-ldap", "-x", "-D", f"uid=carol,ou=people,{base}"]
    assert _ldap(replica, *bind, "-w", "carol-password-1").returncode == 0, "passed through to the primary"
    assert "remoteauth_bind failed" in _ldap(replica, *bind, "-w", "wrong").stderr

    write = f"dn: uid=carol,ou=people,{base}\nchangetype: modify\nreplace: mail\nmail: x@y\n"
    refused = _ldap(replica, "ldapmodify", "-ZZ", "-H", "ldap://nl2sql-ldap", "-x", "-D",
                    f"cn=nl2sql-auth,ou=services,{base}", "-w", "rep-svc-password", stdin=write)
    assert "Insufficient access" in refused.stderr, "nothing edits a replica but its copy"

    change = f"dn: uid=carol,ou=people,{upstream_base}\nchangetype: modify\nreplace: mail\nmail: carol@corp.example\n"
    assert _ldap(primary, "ldapmodify", "-Y", "EXTERNAL", "-H", SOCKET, stdin=change).returncode == 0
    for _ in range(20):
        found = _ldap(replica, "ldapsearch", "-LLL", "-Y", "EXTERNAL", "-H", SOCKET, "-b", f"uid=carol,ou=people,{base}", "mail")
        if "carol@corp.example" in found.stdout:
            break
        time.sleep(1)
    assert "mail: carol@corp.example" in found.stdout, "an edit on the primary comes down"
    _docker("volume", "rm", tls_volume, check=False)


@pytest.mark.docker
@pytest.mark.parametrize("mode", ["standalone", "replica"])
def test_slapd_accepts_the_configuration_each_mode_renders(image, mode):
    env = ["-e", "LDAP_MODE=" + mode, "-e", "LDAP_UPSTREAM_URI=ldaps://dc1", "-e", "LDAP_UPSTREAM_BIND_DN=x",
           "-e", "LDAP_UPSTREAM_BIND_PASSWORD=y", "-e", "LDAP_UPSTREAM_BASE_DN=dc=x"]
    script = (
        "python3 -m nl2sql_ldap config > /tmp/slapd.conf && "
        "python3 -c 'from nl2sql_ldap import tls, settings; tls.ensure(settings.DirectorySettings.from_env())' >/dev/null && "
        "slaptest -u -f /tmp/slapd.conf"
    )
    result = _docker("run", "--rm", *env, "--entrypoint", "sh", image, "-c", script, check=False)
    assert result.returncode == 0, result.stderr
