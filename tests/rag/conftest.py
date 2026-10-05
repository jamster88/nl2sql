"""Isolation for the tests that write to the real RAG databases.

The context and vector stores hold published data -- the golden pairs and
their embeddings are what `mcfaddja/nl2sql-rag-chunkdb:v3_2` and
`mcfaddja/nl2sql-rag-vectordb:v3_2` ship. These tests must not be able to touch
it, and "must not" has to be structural rather than careful: `rebuild_bm25_index`
truncates, and every function in `ragproc` addresses tables unqualified, so a
scratch *schema* with `public` still on the search_path would let an unqualified
TRUNCATE fall through to the published table whenever the scratch copy did not
exist yet.

So each test gets a throwaway **database** instead, created from template0 and
dropped afterwards. Nothing it can do reaches the real one.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from tests import live_stores

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAG_DIR = REPO_ROOT / "rag"
if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

CHUNK_DB_URL = live_stores.url("chunks", variable="TEST_CHUNK_DB_URL", driver="postgresql")
VECTOR_DB_URL = live_stores.url("vectors", variable="TEST_VECTOR_DB_URL", driver="postgresql")
#: The snippet store, as its owner -- the compose service on its published
#: port. Like the two above, tests only ever touch a throwaway database in it.
SNIPPETS_DB_URL = live_stores.url("snippets", variable="TEST_SNIPPETS_DB_URL", driver="postgresql")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _scratch_database(url: str, connect):
    """Create a throwaway database, yield a connection to it, then drop it."""
    psycopg = pytest.importorskip("psycopg", reason="rag/requirements.txt not installed")
    try:
        admin = psycopg.connect(url, autocommit=True)
    except psycopg.Error as exc:
        live_stores.unreachable("database", url, exc)

    name = f"t_{uuid.uuid4().hex[:16]}"
    try:
        # template0 rather than template1: nothing an operator happened to
        # install into template1 leaks in and changes what is under test.
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
    except psycopg.Error as exc:
        admin.close()
        pytest.skip(f"cannot create a scratch database ({exc}); ragproc may lack CREATEDB")

    conn = None
    scratch_url = _with_database(url, name)
    try:
        conn = connect(scratch_url)
        # Handed out so a test can open its own connection with whichever
        # `connect()` helper is under test.
        conn.scratch_url = scratch_url  # type: ignore[attr-defined]
        yield conn
    finally:
        if conn is not None:
            conn.close()
        # A lingering connection would block the drop; terminate any strays.
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


@pytest.fixture
def chunk_conn():
    from ragproc import golden_pairs as gp

    yield from _scratch_database(CHUNK_DB_URL, gp.connect)


@pytest.fixture
def vector_conn():
    from ragproc import golden_vectors as gv

    # gv.connect() installs the vector extension, which a fresh database needs.
    yield from _scratch_database(VECTOR_DB_URL, gv.connect)


@pytest.fixture
def snippet_store():
    """A throwaway database in the snippet store, and a role name of its own.

    Roles are per cluster, not per database, so the reader role a load
    creates gets a unique name here -- a test must never reset the password
    of the `snippets_reader` the running agent connects as. It is dropped
    after the database, which takes the role's grants with it.
    """
    from ragproc import snippets as sn

    role = f"t_reader_{uuid.uuid4().hex[:10]}"
    scratch = _scratch_database(SNIPPETS_DB_URL, sn.connect)
    conn = next(scratch)
    try:
        yield conn, role
    finally:
        for _ in scratch:  # runs the drop
            pass
        import psycopg

        with psycopg.connect(SNIPPETS_DB_URL, autocommit=True) as admin:
            admin.execute(f'DROP ROLE IF EXISTS "{role}"')


@pytest.fixture(scope="session")
def parsed_pairs():
    from ragproc.golden_pairs import parse_document

    return parse_document(REPO_ROOT / "context_questions" / "translated_questions.md")
