"""Explicit deployment of the BM25 index (ParadeDB pg_search 0.26.4)."""

from psycopg import Connection


BM25_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS site_documents_bm25 ON site_documents
USING paradedb (
    id,
    (title::pdb.simple('ascii_folding=true')),
    (subtitle::pdb.simple('ascii_folding=true')),
    (summary::pdb.simple('ascii_folding=true')),
    (keywords::pdb.simple('ascii_folding=true')),
    site, date, category, content_type, fields
);
"""


def initialize_search(connection: Connection) -> None:
    """Install on an explicitly selected database; never called by API readers."""
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('university-news-search-init'))")
        connection.execute("CREATE EXTENSION IF NOT EXISTS pg_search CASCADE")
        version = connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'pg_search'"
        ).fetchone()
        if tuple(int(part) for part in version["extversion"].split(".")[:3]) < (0, 26, 4):
            raise RuntimeError("The website requires pg_search >= 0.26.4")
        connection.execute(BM25_INDEX_SQL)
