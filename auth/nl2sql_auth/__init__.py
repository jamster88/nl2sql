"""The nl2sql auth service: sign-in, the session, the directory's web interface.

Signing in is a connection to the retail database as the person, which
Postgres checks against the directory; the session is a token signed with a
key only this service holds (`nl2sql_identity` verifies it everywhere else).
The role sync makes the directory's people roles in that database, and the
web interface edits the people of a standalone directory.
"""

__version__ = "6.0.0"
