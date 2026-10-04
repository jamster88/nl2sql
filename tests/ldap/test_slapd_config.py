"""slapd's configuration, as each mode renders it.

Whether slapd accepts it is checked live (tests/ldap/test_ldap_image.py runs
`slaptest` on both); this pins what each mode is supposed to say.
"""

from __future__ import annotations

from nl2sql_ldap import slapd
from nl2sql_ldap.settings import REPLICA, DirectorySettings, UpstreamSettings

from .conftest import BASE

ROOT = "gidNumber=101+uidNumber=100,cn=peercred,cn=external,cn=auth"


def _replica(**upstream) -> DirectorySettings:
    return DirectorySettings(
        mode=REPLICA,
        upstream=UpstreamSettings(uri="ldaps://dc1.corp:636", bind_dn="x", bind_password="y", base_dn="z", **upstream),
    )


def _blocks(text: str) -> list[str]:
    """Each `access to` rule of the data's database with its `by` lines."""
    rules: list[str] = []
    for line in text.split("database mdb", 1)[1].splitlines():
        if line.startswith("access to"):
            rules.append(line)
        elif line.startswith("  by") and rules:
            rules[-1] += "\n" + line
    return rules


def test_root_is_this_containers_user_on_the_socket_and_has_no_password():
    text = slapd.render(DirectorySettings(), root=ROOT)
    assert f'rootdn "{ROOT}"' in text
    assert "rootpw" not in text
    assert f'access to * by dn.exact="{ROOT}" manage by * none' in text


def test_the_root_defaults_to_the_running_users_peer_credential(monkeypatch):
    monkeypatch.setattr(slapd.os, "getuid", lambda: 7)
    monkeypatch.setattr(slapd.os, "getgid", lambda: 8)
    assert slapd.peercred_dn() == "gidNumber=8+uidNumber=7,cn=peercred,cn=external,cn=auth"
    assert slapd.peercred_dn(uid=1, gid=2) == "gidNumber=2+uidNumber=1,cn=peercred,cn=external,cn=auth"
    assert 'rootdn "gidNumber=8+uidNumber=7,' in slapd.render(DirectorySettings())


def test_standalone_hashes_with_argon2_and_locks_out(monkeypatch):
    text = slapd.render(DirectorySettings(), root=ROOT)
    assert "password-hash {ARGON2}" in text
    for line in ("moduleload ppolicy.so", "overlay ppolicy", "ppolicy_hash_cleartext", "ppolicy_use_lockout"):
        assert line in text
    assert f'ppolicy_default "cn=default,ou=policies,{BASE}"' in text
    assert "remoteauth" not in text


def test_standalone_lets_the_auth_service_and_owners_set_passwords_but_nobody_read_them():
    rules = _blocks(slapd.render(DirectorySettings(), root=ROOT))
    password = rules[0]
    assert password.startswith("access to attrs=userPassword")
    assert f'by dn.exact="cn=nl2sql-auth,ou=services,{BASE}" =w' in password
    assert "by self =xw" in password, "changing your own checks the old one: auth, then write"
    assert "by anonymous auth" in password and password.endswith("by * none")
    assert " read" not in password
    people = next(rule for rule in rules if "ou=people" in rule)
    assert f'by dn.exact="cn=nl2sql-auth,ou=services,{BASE}" write' in people
    assert "by users read" in people
    assert any("pwdAccountLockedTime" in rule for rule in rules)


def test_a_replica_is_read_only_to_every_account():
    text = slapd.render(_replica(), root=ROOT)
    rules = _blocks(text)
    assert all(" write" not in rule and "=w" not in rule for rule in rules)
    assert "ppolicy" not in text and "pwdAccountLockedTime" not in text, "the policy's attributes do not exist here"


def test_a_replica_passes_binds_through_to_its_primary():
    text = slapd.render(_replica(cacert="/ca.pem"), root=ROOT)
    for line in (
        "moduleload remoteauth.so",
        "overlay remoteauth",
        "remoteauth_dn_attribute seeAlso",
        "remoteauth_domain_attribute associatedDomain",
        "remoteauth_default_domain upstream",
        "remoteauth_mapping upstream ldaps://dc1.corp:636",
        "remoteauth_default_realm ldaps://dc1.corp:636",
        "remoteauth_store off",
        "remoteauth_tls tls_reqcert=demand tls_cacert=/ca.pem",
    ):
        assert line in text


def test_a_replica_can_start_tls_or_not_check_the_primary():
    text = slapd.render(_replica(starttls=True, verify=False), root=ROOT)
    assert "remoteauth_tls starttls=yes tls_reqcert=never" in text


def test_passwords_need_an_encrypted_connection_unless_told_otherwise():
    assert "security simple_bind=64" in slapd.render(DirectorySettings(), root=ROOT)
    assert "security" not in slapd.render(DirectorySettings(require_tls=False), root=ROOT)


def test_the_certificate_and_the_listeners():
    text = slapd.render(DirectorySettings(tls_cert_file="/a.crt", tls_key_file="/a.key"), root=ROOT)
    assert "TLSCertificateFile /a.crt" in text and "TLSCertificateKeyFile /a.key" in text
    assert "TLSProtocolMin 3.3" in text
    assert slapd.listen_urls() == "ldap:/// ldaps:/// ldapi://%2Fvar%2Flib%2Fopenldap%2Frun%2Fldapi"


def test_the_overlays_that_keep_groups_consistent_are_in_both_modes():
    for settings in (DirectorySettings(), _replica()):
        text = slapd.render(settings, root=ROOT)
        assert "overlay memberof" in text and "overlay refint" in text
        assert "refint_attributes member" in text
