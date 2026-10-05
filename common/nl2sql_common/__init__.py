"""What every nl2sql service shares that is not identity.

Each module stands alone and imports nothing heavier than the standard
library -- except `envelope`, which is pydantic models, and is imported only
by the services that answer HTTP. The directory imports `env` and
`privileges` without pydantic in its image.

- `env`: settings from the environment, the one way every service reads them.
- `urls`: a connection URL with its password taken out, for printing.
- `values`: a database value as JSON can carry it.
- `envelope`: the error body, `/healthz` and `/readyz`, shared by every API.
- `vectors`: the embedder every retriever and loader is given, and pgvector's
  text form of a vector.
- `privileges`: dropping root once a process has taken what it writes.
- `errors`: the failures a service tells apart, so it can catch what it means.

Installed as a package (`pip install ./common`): `pyproject.toml` beside this
directory names the version every image records (V6-66).
"""

__version__ = "6.2.0"
