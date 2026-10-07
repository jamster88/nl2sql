"""The nl2sql directory: an OpenLDAP that the retail database signs people in against.

Standalone, it is where people and groups are kept -- loaded from a file on
first start, edited through the auth service's web interface. As a replica,
it is a read-only copy of another directory (Active Directory, OpenLDAP,
anything that answers an LDAP search), whose binds it passes through to that
primary. Either way the retail database sees the same tree, and the auth
service turns its groups into Postgres roles.

`directory`, `layout` and `records` are also the auth service's: its web
interface edits the directory through the same operations this container
seeds it with.
"""
