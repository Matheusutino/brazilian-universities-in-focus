# PostgreSQL remains the transactional store. pg_search supplies only BM25;
# article embeddings and ANN search remain in LanceDB.
FROM postgres:16-bookworm

ARG PG_SEARCH_VERSION=0.26.4
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl postgresql-16-pgvector \
    && curl --fail --location --retry 3 \
      "https://github.com/paradedb/paradedb/releases/download/v${PG_SEARCH_VERSION}/postgresql-16-pg-search_${PG_SEARCH_VERSION}-1PARADEDB-bookworm_amd64.deb" \
      --output /tmp/pg-search.deb \
    && apt-get install -y --no-install-recommends /tmp/pg-search.deb \
    && rm /tmp/pg-search.deb \
    && rm -r /var/lib/apt/lists/*
