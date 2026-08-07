from __future__ import annotations

import html
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pyarrow.dataset  # noqa: F401
import streamlit as st
from dotenv import load_dotenv

# Load PyArrow's dataset extension on the initial Streamlit script thread,
# before DuckDB scans the corpus. Deferring this native import until a later
# search rerun can crash while LanceDB is being imported.
# Pandas 3 defaults newly inferred strings to PyArrow. Those arrays can crash
# when Streamlit reuses cached frames from a different script-runner thread.
# Object strings are small enough for this projected dashboard dataset and are
# stable across reruns.
pd.set_option("future.infer_string", False)

from university_news.search.hybrid import (
    LanceDBRetriever,
    SearchFilters,
    article_id,
    rrf_fuse,
    tokenize,
)
from university_news.search.embeddings import (
    DEFAULT_MODEL_FILE,
    DEFAULT_MODEL_REPO,
    DEFAULT_QUERY_CONTEXT_LENGTH,
    QwenGGUFEmbedder,
)
from university_news.search.vector_index import (
    DEFAULT_DATABASE,
    DEFAULT_TABLE,
    connect_database,
    load_index_metadata,
)
from university_news.dashboard.data import load_consolidated_articles
from university_news.paths import (
    DEFAULT_DATA_ROOT,
    compiled_articles_path,
    compiled_dashboard_articles_path,
)
from university_news.sources.catalog import REGIONS, region_for_site


PROJECT_ROOT = Path(__file__).resolve().parents[3]
JSON_ARTICLES_PATH = compiled_articles_path(DEFAULT_DATA_ROOT)
PARQUET_ARTICLES_PATH = compiled_dashboard_articles_path(DEFAULT_DATA_ROOT)
RESULTS_PER_PAGE = 12

load_dotenv(PROJECT_ROOT / ".env")


def dashboard_articles_path() -> Path:
    """Prefer the compact artifact when it is at least as new as the JSON."""
    if PARQUET_ARTICLES_PATH.is_file() and (
        not JSON_ARTICLES_PATH.is_file()
        or PARQUET_ARTICLES_PATH.stat().st_mtime_ns
        >= JSON_ARTICLES_PATH.stat().st_mtime_ns
    ):
        return PARQUET_ARTICLES_PATH
    return JSON_ARTICLES_PATH


ARTICLES_PATH = dashboard_articles_path()

st.set_page_config(
    page_title="Universidades em Foco — Notícias universitárias brasileiras",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="collapsed",
)


st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Newsreader:opsz,wght@6..72,500;6..72,600&display=swap');

    :root {
        --ink: #18201d;
        --muted: #68736e;
        --paper: #f7f8f5;
        --card: #ffffff;
        --line: #dfe4df;
        --green: #176b52;
        --lime: #d9f25f;
    }

    .stApp { background: var(--paper); color: var(--ink); }
    .block-container { max-width: 1180px; padding-top: 3.7rem; padding-bottom: 3rem; }
    html, body, [class*="css"] { font-family: "DM Sans", sans-serif; }
    h1, h2, h3 { font-family: "Newsreader", Georgia, serif !important; color: var(--ink) !important; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stAppDeployButton"],
    [data-testid="stMainMenu"] { display: none; }
    [data-testid="stTopNavLink"],
    [data-testid="stSidebarNavLink"],
    [data-testid="stTopNavLink"] p,
    [data-testid="stSidebarNavLink"] p,
    [data-testid="stTopNavLink"] [data-testid="stIconMaterial"],
    [data-testid="stSidebarNavLink"] [data-testid="stIconMaterial"] {
        color: var(--ink) !important;
    }
    [data-testid="stTopNavLink"][aria-current="page"],
    [data-testid="stSidebarNavLink"][aria-current="page"] {
        background: #e9ede9 !important;
    }
    [data-testid="stMetric"] {
        background: transparent;
        border-left: 1px solid var(--line);
        padding: .25rem 1rem;
    }
    [data-testid="stMetricValue"] { font-family: "Newsreader", Georgia, serif; }
    div[data-baseweb="input"] > div,
    div[data-baseweb="select"] > div {
        background: white;
        border-color: var(--line);
        border-radius: 10px;
    }
    [data-testid="stDateInput"] div[data-baseweb="input"] {
        background: white !important;
        border-color: var(--line) !important;
        border-radius: 10px;
        color-scheme: light;
    }
    [data-testid="stDateInput"] input {
        background: transparent !important;
        color: var(--ink) !important;
        -webkit-text-fill-color: var(--ink) !important;
        opacity: 1 !important;
    }
    [data-testid="stDateInput"] input::placeholder {
        color: var(--muted) !important;
        -webkit-text-fill-color: var(--muted) !important;
        opacity: 1;
    }
    [data-testid="stDateInput"] svg {
        color: var(--muted) !important;
        fill: var(--muted) !important;
    }
    .stButton > button, .stFormSubmitButton > button {
        border: 0;
        border-radius: 10px;
        background: var(--green);
        color: white;
        font-weight: 700;
        min-height: 2.8rem;
    }
    .stButton > button:hover, .stFormSubmitButton > button:hover {
        background: #10553f;
        color: white;
        border: 0;
    }
    button[kind="secondary"] { background: white !important; color: var(--ink) !important; border: 1px solid var(--line) !important; }
    [data-testid="stExpander"] {
        background: white;
        border: 1px solid var(--line);
        border-radius: 14px;
        margin-top: .8rem;
    }
    [data-testid="stExpander"] summary { font-weight: 700; color: var(--ink); }
    .filter-summary {
        color: var(--muted);
        font-size: .82rem;
        margin: .45rem 0 0;
    }
    .brandbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: .2rem 0 1.1rem;
        border-bottom: 1px solid var(--line);
        margin-bottom: 2.4rem;
    }
    .brand { display: flex; align-items: center; gap: .7rem; font-weight: 800; letter-spacing: -.02em; }
    .brand-mark {
        display: grid;
        place-items: center;
        width: 2rem;
        height: 2rem;
        border-radius: 50%;
        color: #163d30;
        background: var(--lime);
        font-size: .9rem;
    }
    .brand-note { color: var(--muted); font-size: .78rem; }
    .eyebrow {
        color: var(--green);
        font-size: .76rem;
        font-weight: 800;
        letter-spacing: .12em;
        text-transform: uppercase;
        margin-bottom: .7rem;
    }
    .hero { max-width: 790px; margin: 0 auto 1.8rem; text-align: center; }
    .hero h1 { padding: 0; font-size: clamp(2.6rem, 6vw, 4.8rem); line-height: .98; letter-spacing: -.045em; margin: 0 0 1rem; }
    .hero p { max-width: 650px; margin: 0 auto; color: var(--muted); font-size: 1.05rem; line-height: 1.65; }
    .explore-hero h1 { font-size: clamp(2.6rem, 5vw, 4.2rem); }
    .section-head {
        display: flex;
        align-items: end;
        justify-content: space-between;
        gap: 1rem;
        margin: 1.5rem 0 1rem;
        padding-bottom: .8rem;
        border-bottom: 1px solid var(--line);
    }
    .section-head h2 { margin: 0; font-size: 2rem; }
    .section-head span { color: var(--muted); font-size: .85rem; white-space: nowrap; }
    .news-card {
        box-sizing: border-box;
        height: 340px;
        overflow: hidden;
        margin-bottom: 1.6rem;
        padding: 1.25rem;
        border: 1px solid var(--line);
        border-radius: 16px;
        background: var(--card);
        box-shadow: 0 8px 28px rgba(28, 48, 39, .035);
        display: flex;
        flex-direction: column;
    }
    .news-card:hover { border-color: #b7c4bc; transform: translateY(-2px); transition: .18s ease; }
    .news-card .meta { color: var(--green); font-size: .72rem; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; }
    .news-card h3 {
        display: -webkit-box;
        flex: 0 0 5.25rem;
        height: 5.25rem;
        min-height: 5.25rem;
        max-height: 5.25rem;
        overflow: hidden;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 3;
        font-size: 1.42rem;
        line-height: 1.2;
        letter-spacing: -.02em;
        margin: .7rem 0 .65rem;
    }
    .news-card h3 a { color: inherit; text-decoration: none; }
    .news-card p {
        display: -webkit-box;
        flex: 0 0 5.55rem;
        height: 5.55rem;
        min-height: 5.55rem;
        max-height: 5.55rem;
        overflow: hidden;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 4;
        color: var(--muted);
        font-size: .88rem;
        line-height: 1.55;
        margin: 0 0 1rem;
    }
    .news-card .source {
        overflow: hidden;
        color: #8a938f;
        font-size: .76rem;
        margin-top: auto;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .result-card {
        background: white;
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 1.2rem 1.35rem;
        margin-bottom: .8rem;
    }
    .result-card h3 { font-size: 1.45rem; line-height: 1.15; margin: .45rem 0; }
    .result-card h3 a { color: var(--ink); text-decoration: none; }
    .result-card p { color: var(--muted); margin: .35rem 0 .7rem; line-height: 1.55; }
    .result-card small { color: var(--green); font-weight: 700; }
    .empty-state {
        padding: 3rem 1rem;
        border: 1px dashed #bdc7c0;
        border-radius: 16px;
        color: var(--muted);
        text-align: center;
    }
    .page-intro {
        max-width: 760px;
        margin: 1.2rem 0 .6rem;
    }
    .page-intro h1 {
        padding: 0;
        font-size: clamp(2.7rem, 5vw, 4.4rem);
        line-height: 1;
        letter-spacing: -.04em;
        margin: 0 0 1rem;
    }
    .page-intro p {
        color: var(--muted);
        font-size: 1.05rem;
        line-height: 1.7;
    }
    .feature-card, .author-card {
        box-sizing: border-box;
        padding: 1.4rem;
        border: 1px solid var(--line);
        border-radius: 16px;
        background: white;
    }
    .feature-card {
        height: 148px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }
    [data-testid="stPageLink-NavLink"] {
        min-height: 2.8rem;
        box-sizing: border-box;
        justify-content: center;
        border: 1px solid var(--line);
        border-radius: 10px;
        background: white;
        font-weight: 700;
    }
    [data-testid="stPageLink-NavLink"],
    [data-testid="stPageLink-NavLink"] p,
    [data-testid="stPageLink-NavLink"] [data-testid="stIconMaterial"] {
        color: var(--ink) !important;
    }
    [data-testid="stPageLink-NavLink"]:hover {
        border-color: #b7c4bc;
        color: var(--green);
    }
    .feature-number {
        color: var(--green);
        font-family: "Newsreader", Georgia, serif;
        font-size: 2.3rem;
        font-weight: 600;
        line-height: 1;
        margin-bottom: .55rem;
    }
    .feature-label {
        color: var(--muted);
        font-size: .82rem;
        line-height: 1.45;
    }
    .author-role {
        color: var(--green);
        font-size: .72rem;
        font-weight: 800;
        letter-spacing: .08em;
        text-transform: uppercase;
    }
    .author-card h3 { font-size: 1.65rem; margin: .65rem 0 .2rem; }
    .author-card .affiliation { color: #8a938f; font-size: .78rem; margin-bottom: .85rem; }
    .author-card p { color: var(--muted); line-height: 1.6; min-height: 7.6rem; }
    .author-card a { color: var(--green); font-weight: 700; text-decoration: none; }
    .method-card {
        padding: 1.3rem 1.45rem;
        border-left: 3px solid var(--lime);
        background: white;
        color: var(--muted);
        line-height: 1.65;
    }
    .footer {
        border-top: 1px solid var(--line);
        color: var(--muted);
        margin-top: 3rem;
        padding-top: 1.3rem;
        font-size: .78rem;
    }
    @media (max-width: 700px) {
        .block-container { padding: 4rem 1rem 2.5rem; }
        .brand-note { display: none; }
        .hero { text-align: left; }
        .hero h1 { font-size: 3rem; }
        .explore-hero h1 { font-size: 2.7rem; }
        .page-intro h1 { font-size: 3rem; }
        .feature-card { margin-bottom: .75rem; }
        .news-card {
            height: auto;
            min-height: 260px;
            margin-bottom: 1.1rem;
        }
        .news-card h3,
        .news-card p {
            flex-basis: auto;
            height: auto;
            min-height: 0;
        }
        .news-card .source { margin-top: 1.25rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner="Preparando o acervo…")
def load_articles(path: str, signature: tuple[int, int]) -> pd.DataFrame:
    """Keep a single corpus copy across Streamlit reruns."""
    del signature
    frame = load_consolidated_articles(Path(path))
    if frame.empty:
        return frame
    frame["journal"] = frame["journal"].astype(str).str.upper()
    frame["_date"] = pd.to_datetime(frame.get("date"), errors="coerce")
    frame["_search_text"] = (
        frame.get("title", pd.Series("", index=frame.index)).fillna("").astype(str)
        + " "
        + frame.get("subtitle", pd.Series("", index=frame.index)).fillna("").astype(str)
    ).str.lower()
    frame["_article_id"] = [
        article_id({"url": url}) if isinstance(url, str) and url else ""
        for url in frame.get("url", pd.Series("", index=frame.index))
    ]
    # Pandas may infer Arrow-backed strings for the transformations above.
    # Streamlit shares this cached frame across rerun threads, so keep textual
    # columns as ordinary Python objects.
    for column in frame.columns.difference(["_date"]):
        frame[column] = frame[column].astype(object)
    return frame.sort_values("_date", ascending=False, na_position="last")


def article_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def vector_index_signature(database_path: Path) -> tuple[int, int] | None:
    metadata_path = database_path / "index_metadata.json"
    try:
        stat = metadata_path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


@st.cache_resource(show_spinner="Carregando a busca semântica local…")
def load_semantic_retriever(
    database_path_value: str,
    index_signature: tuple[int, int] | None,
) -> LanceDBRetriever | None:
    del index_signature
    if os.environ.get("SEARCH_MODE", "hybrid").lower() == "lexical":
        return None
    database_path = Path(database_path_value)
    metadata = load_index_metadata(database_path)
    table_name = os.environ.get(
        "LANCEDB_TABLE",
        str(metadata.get("table", DEFAULT_TABLE)),
    )
    try:
        database = connect_database(database_path)
        table = database.open_table(table_name)
        if table.count_rows() == 0:
            return None
        model_path_value = os.environ.get("EMBEDDING_MODEL_PATH") or metadata.get(
            "model_path"
        )
        embedder = QwenGGUFEmbedder(
            model_path=Path(model_path_value) if model_path_value else None,
            model_repo=os.environ.get(
                "EMBEDDING_MODEL_REPO",
                str(metadata.get("model_repo", DEFAULT_MODEL_REPO)),
            ),
            model_file=os.environ.get(
                "EMBEDDING_MODEL_FILE",
                str(metadata.get("model_file", DEFAULT_MODEL_FILE)),
            ),
            context_length=int(
                os.environ.get(
                    "EMBEDDING_QUERY_CONTEXT_LENGTH",
                    DEFAULT_QUERY_CONTEXT_LENGTH,
                )
            ),
            gpu_layers=int(os.environ.get("EMBEDDING_GPU_LAYERS", "-1")),
        )
        if table.schema.field("vector").type.list_size != embedder.dimension:
            return None
        return LanceDBRetriever(table, embedder)
    except Exception:
        return None


def search_articles(
    articles: pd.DataFrame,
    filtered_indices: pd.Index,
    query: str,
    *,
    filters: SearchFilters = SearchFilters(),
    limit: int = 20,
) -> tuple[list[dict[str, object]], bool]:
    """Combine local LanceDB results with the dashboard's lexical ranking."""
    tokens = [token for token in dict.fromkeys(tokenize(query)) if len(token) > 1][
        :8
    ]
    if not tokens:
        return [], False

    text = articles.loc[filtered_indices, "_search_text"]
    lexical_scores = pd.Series(0.0, index=text.index)
    for token in tokens:
        lexical_scores += text.str.count(rf"\b{re.escape(token)}\b")
    normalized_query = " ".join(query.lower().split())
    if normalized_query:
        lexical_scores += text.str.contains(
            re.escape(normalized_query), regex=True, na=False
        ).astype(float) * 3

    lexical_indices = lexical_scores[lexical_scores > 0].nlargest(50).index
    lexical_ranking = articles.loc[lexical_indices, "_article_id"].tolist()

    database_path = Path(os.environ.get("LANCEDB_PATH", str(DEFAULT_DATABASE)))
    retriever = load_semantic_retriever(
        str(database_path),
        vector_index_signature(database_path),
    )
    semantic_ranking = retriever.search(query, 50, filters) if retriever else []
    fused = rrf_fuse(semantic_ranking, lexical_ranking)
    ordered_ids = [
        identifier
        for identifier, _ in sorted(
            fused.items(), key=lambda item: item[1], reverse=True
        )
    ]
    if not ordered_ids:
        return [], retriever is not None

    allowed_ids = set(articles.loc[filtered_indices, "_article_id"])
    ordered_ids = [
        identifier for identifier in ordered_ids if identifier in allowed_ids
    ][:limit]
    matched = (
        articles[articles["_article_id"].isin(ordered_ids)]
        .set_index("_article_id")
        .to_dict("index")
    )
    return [matched[identifier] for identifier in ordered_ids if identifier in matched], (
        retriever is not None
    )


def present(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, (str, bytes)) and hasattr(value, "__len__"):
        return len(value) > 0  # type: ignore[arg-type]
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(str(value).strip())


def clean_text(value: object, limit: int = 190) -> str:
    if not present(value):
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def display_date(value: object) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed.strftime("%d.%m.%Y") if pd.notna(parsed) else "Sem data"


def safe_url(value: object) -> str:
    if not isinstance(value, str):
        return "#"
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} else "#"


def reset_filters() -> None:
    for key in ("filter_regions", "filter_journals", "filter_period"):
        st.session_state.pop(key, None)
    st.session_state["news_page"] = 1


def reset_journal_filter() -> None:
    st.session_state.pop("filter_journals", None)
    st.session_state["news_page"] = 1


def render_card(article: pd.Series | dict[str, object], *, result: bool = False) -> None:
    title = html.escape(clean_text(article.get("title"), 115) or "Notícia sem título")
    subtitle = clean_text(article.get("subtitle"))
    journal = html.escape(str(article.get("journal") or "Universidade"))
    published = display_date(article.get("date"))
    url = html.escape(safe_url(article.get("url")), quote=True)
    author = clean_text(article.get("author"), 55)
    source = f"Por {html.escape(author)}" if author else "Fonte oficial da universidade"
    class_name = "result-card" if result else "news-card"
    st.markdown(
        f"""
        <article class="{class_name}">
            <div class="meta">{journal} &nbsp;·&nbsp; {published}</div>
            <h3><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a></h3>
            <p>{html.escape(subtitle)}</p>
            <div class="source">{source}</div>
        </article>
        """,
        unsafe_allow_html=True,
    )


def render_brand() -> None:
    st.markdown(
        """
        <div class="brandbar">
            <div class="brand"><span class="brand-mark">●</span> Universidades em Foco</div>
            <div class="brand-note">Ciência, cultura e sociedade nas universidades brasileiras</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_articles() -> pd.DataFrame:
    if not ARTICLES_PATH.is_file():
        st.error(f"Arquivo consolidado não encontrado em `{ARTICLES_PATH}`.")
        st.stop()
    try:
        articles = load_articles(str(ARTICLES_PATH), article_signature(ARTICLES_PATH))
    except Exception as exc:
        st.error(f"Não foi possível ler o acervo: {exc}")
        st.stop()
    if articles.empty or "journal" not in articles:
        st.error("O acervo não contém artigos com o campo `journal`.")
        st.stop()
    return articles


def render_home() -> None:
    render_brand()
    articles = page_articles()
    valid_dates = articles["_date"].dropna()
    earliest_year = int(valid_dates.min().year) if not valid_dates.empty else None
    latest_year = int(valid_dates.max().year) if not valid_dates.empty else None
    coverage = (
        f"{earliest_year}–{latest_year}"
        if earliest_year and latest_year
        else "Acervo histórico"
    )
    st.markdown(
        """
        <section class="hero">
            <div class="eyebrow">Notícias universitárias brasileiras</div>
            <h1>Conhecimento público.<br>Busca inteligente.</h1>
            <p>Uma visão integrada da ciência, da cultura e da sociedade produzida
            pelas universidades públicas do Brasil.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    metrics = (
        (f"{len(articles):,.0f}".replace(",", "."), "notícias no acervo"),
        (str(articles["journal"].nunique()), "universidades acompanhadas"),
        (str(len(REGIONS)), "regiões brasileiras"),
        (coverage, "cobertura temporal"),
    )
    columns = st.columns(4)
    for column, (value, label) in zip(columns, metrics):
        with column:
            st.markdown(
                f"""
                <div class="feature-card">
                    <div class="feature-number">{html.escape(value)}</div>
                    <div class="feature-label">{html.escape(label)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:.65rem'></div>", unsafe_allow_html=True)
    action_col, universities_col, spacer = st.columns([1.25, 1.5, 3])
    with action_col:
        st.page_link(
            EXPLORE_PAGE,
            label="Explorar notícias",
            icon=":material/search:",
            width="stretch",
        )
    with universities_col:
        st.page_link(
            UNIVERSITIES_PAGE,
            label="Conhecer universidades",
            icon=":material/account_balance:",
            width="stretch",
        )

    st.markdown(
        '<div class="section-head"><h2>Publicações recentes</h2><span>fontes oficiais</span></div>',
        unsafe_allow_html=True,
    )
    recent = articles.head(6)
    for row_start in range(0, len(recent), 3):
        card_columns = st.columns(3)
        for column, (_, article) in zip(
            card_columns,
            recent.iloc[row_start : row_start + 3].iterrows(),
        ):
            with column:
                render_card(article)


def render_universities() -> None:
    render_brand()
    articles = page_articles()
    summary = (
        articles.assign(
            region=articles["journal"].map(region_for_site),
        )
        .groupby(["journal", "region"], as_index=False)
        .agg(
            news=("_article_id", "size"),
            earliest=("_date", "min"),
            latest=("_date", "max"),
        )
        .sort_values(["region", "journal"])
    )
    st.markdown(
        """
        <section class="page-intro">
            <div class="eyebrow">Cobertura do acervo</div>
            <h1>Universidades</h1>
            <p>Veja quanto conteúdo foi reunido de cada instituição, o período
            disponível e as publicações mais recentes.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    metric_columns = st.columns(3)
    metric_columns[0].metric("Universidades", len(summary))
    metric_columns[1].metric("Regiões", summary["region"].nunique())
    metric_columns[2].metric(
        "Notícias",
        f"{int(summary['news'].sum()):,}".replace(",", "."),
    )

    selected_region = st.selectbox(
        "Filtrar por região",
        ["Todas as regiões", *REGIONS],
        key="universities_region",
    )
    visible_summary = (
        summary
        if selected_region == "Todas as regiões"
        else summary[summary["region"] == selected_region]
    ).copy()
    visible_summary["earliest"] = visible_summary["earliest"].dt.strftime("%d/%m/%Y")
    visible_summary["latest"] = visible_summary["latest"].dt.strftime("%d/%m/%Y")
    st.dataframe(
        visible_summary.rename(
            columns={
                "journal": "Universidade",
                "region": "Região",
                "news": "Notícias",
                "earliest": "Primeira publicação",
                "latest": "Mais recente",
            }
        ),
        hide_index=True,
        width="stretch",
    )

    journals = visible_summary["journal"].tolist()
    if journals:
        selected_journal = st.selectbox(
            "Ver publicações recentes de",
            journals,
            key="university_highlight",
        )
        selected_articles = articles[articles["journal"] == selected_journal].head(6)
        st.markdown(
            f'<div class="section-head"><h2>{html.escape(selected_journal)}</h2><span>publicações recentes</span></div>',
            unsafe_allow_html=True,
        )
        for row_start in range(0, len(selected_articles), 3):
            card_columns = st.columns(3)
            for column, (_, article) in zip(
                card_columns,
                selected_articles.iloc[row_start : row_start + 3].iterrows(),
            ):
                with column:
                    render_card(article)


def render_about() -> None:
    render_brand()
    st.markdown(
        """
        <section class="page-intro">
            <div class="eyebrow">Sobre o projeto</div>
            <h1>Universidades em Foco</h1>
            <p>O projeto reúne notícias publicadas por universidades brasileiras
            e oferece uma busca local que combina palavras-chave e similaridade
            semântica. A proposta é facilitar estudos sobre comunicação
            universitária, ciência e sociedade.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="section-head"><h2>Equipe e autoria</h2><span>pesquisa e desenvolvimento</span></div>',
        unsafe_allow_html=True,
    )
    author_columns = st.columns(3)
    authors = (
        {
            "name": "Matheus Yasuo Ribeiro Utino",
            "role": "Responsável pelo projeto",
            "affiliation": "ICMC · Universidade de São Paulo",
            "description": (
                "Mestrando em Ciência da Computação no ICMC-USP, com trabalhos "
                "em aprendizado de máquina e análise de dados textuais. É "
                "responsável pelo desenvolvimento e pela pesquisa deste projeto."
            ),
            "links": (
                '<a href="https://github.com/Matheusutino" target="_blank">GitHub</a>'
                ' &nbsp;·&nbsp; '
                '<a href="https://www.nature.com/articles/s41599-026-07448-w" target="_blank">Publicação</a>'
            ),
        },
        {
            "name": "Elaine Parros Machado de Sousa",
            "role": "Coautora do artigo",
            "affiliation": "ICMC · Universidade de São Paulo",
            "description": (
                "Docente do ICMC-USP e pesquisadora na área de bancos de dados, "
                "com atuação em aprendizado de máquina aplicado a séries "
                "temporais e fluxos de dados."
            ),
            "links": (
                '<a href="https://bv.fapesp.br/pt/pesquisador/37948/elaine-parros-machado-de-sousa/" '
                'target="_blank">Perfil FAPESP</a>'
            ),
        },
        {
            "name": "Ricardo Marcondes Marcacini",
            "role": "Coautor do artigo",
            "affiliation": "ICMC · Universidade de São Paulo",
            "description": (
                "Professor associado do ICMC-USP e coordenador do Labic. "
                "Pesquisa aprendizado de máquina e mineração de textos, com "
                "aplicações em dados governamentais e sociais."
            ),
            "links": (
                '<a href="https://fapesp.br/17687/ricardo-marcondes-marcacini" '
                'target="_blank">Perfil FAPESP</a> &nbsp;·&nbsp; '
                '<a href="https://cemeai.icmc.usp.br/ricardo-marcondes-marcacini/" '
                'target="_blank">CeMEAI</a>'
            ),
        },
    )
    for column, author in zip(author_columns, authors):
        with column:
            st.markdown(
                f"""
                <article class="author-card">
                    <div class="author-role">{author["role"]}</div>
                    <h3>{author["name"]}</h3>
                    <div class="affiliation">{author["affiliation"]}</div>
                    <p>{author["description"]}</p>
                    <div>{author["links"]}</div>
                </article>
                """,
                unsafe_allow_html=True,
            )

    st.markdown(
        '<div class="section-head"><h2>Como funciona</h2><span>transparência metodológica</span></div>',
        unsafe_allow_html=True,
    )
    method_columns = st.columns(3)
    methods = (
        (
            "1 · Coleta",
            "As notícias são obtidas exclusivamente dos portais oficiais das universidades acompanhadas.",
        ),
        (
            "2 · Qualidade",
            "Registros incompletos, duplicados ou fora dos critérios definidos são identificados antes da indexação.",
        ),
        (
            "3 · Busca",
            "A consulta combina correspondência textual com embeddings locais e índice vetorial LanceDB.",
        ),
    )
    for column, (title, description) in zip(method_columns, methods):
        with column:
            st.markdown(
                f'<div class="method-card"><strong>{title}</strong><br>{description}</div>',
                unsafe_allow_html=True,
            )
    st.caption(
        "Os conteúdos pertencem aos respectivos portais de origem. "
        "A plataforma organiza metadados e direciona o leitor para a publicação oficial."
    )


def render_explore() -> None:
    st.markdown(
        """
        <div class="brandbar">
            <div class="brand"><span class="brand-mark">●</span> Universidades em Foco</div>
            <div class="brand-note">Ciência, cultura e sociedade nas universidades brasileiras</div>
        </div>
        <section class="hero explore-hero">
            <div class="eyebrow">Conhecimento público, em um só lugar</div>
            <h1>O Brasil que pesquisa.<br>O Brasil que transforma.</h1>
            <p>Explore notícias publicadas por universidades de todas as regiões do país e encontre temas no acervo com uma busca inteligente.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    if not ARTICLES_PATH.is_file():
        st.error(f"Arquivo consolidado não encontrado em `{ARTICLES_PATH}`.")
        st.stop()

    try:
        articles = load_articles(str(ARTICLES_PATH), article_signature(ARTICLES_PATH))
    except Exception as exc:
        st.error(f"Não foi possível ler o acervo: {exc}")
        st.stop()

    if articles.empty or "journal" not in articles:
        st.error("O acervo não contém artigos com o campo `journal`.")
        st.stop()

    # Warm the cached model and LanceDB table before the user submits the first
    # query. With the compact Parquet corpus, the whole cold start remains faster
    # than the former JSON-only dashboard.
    search_database_path = Path(
        os.environ.get("LANCEDB_PATH", str(DEFAULT_DATABASE))
    )
    search_index_signature = vector_index_signature(search_database_path)
    load_semantic_retriever(
        str(search_database_path),
        search_index_signature,
    )

    available_journals = sorted(articles["journal"].dropna().unique().tolist())

    with st.form("main-search", border=False):
        search_col, button_col = st.columns([8, 1.5], vertical_alignment="bottom")
        with search_col:
            query = st.text_input(
                "Busque no acervo",
                value=st.session_state.get("active_query", ""),
                placeholder="Ex.: mudanças climáticas, saúde pública, inteligência artificial…",
                label_visibility="collapsed",
            )
        with button_col:
            submitted = st.form_submit_button("Buscar", type="primary", use_container_width=True)

    if submitted:
        st.session_state["active_query"] = query.strip()
        st.session_state["run_search"] = bool(query.strip())

    valid_dates = articles["_date"].dropna()
    minimum = valid_dates.min().date() if not valid_dates.empty else None
    maximum = valid_dates.max().date() if not valid_dates.empty else None

    with st.expander("Filtros de leitura", expanded=False):
        region_col, journal_col, date_col = st.columns([1.1, 2, 1.35])
        with region_col:
            selected_region = st.selectbox(
                "Regiões",
                ["Todas as regiões", *REGIONS],
                index=0,
                key="filter_regions",
                on_change=reset_journal_filter,
            )
            selected_regions = (
                [] if selected_region == "Todas as regiões" else [selected_region]
            )
        journals_for_region = [
            journal
            for journal in available_journals
            if not selected_regions or region_for_site(journal) in selected_regions
        ]
        with journal_col:
            selected_journals = st.multiselect(
                "Universidades",
                ["Todas as universidades", *journals_for_region],
                default=["Todas as universidades"],
                key="filter_journals",
            )
        with date_col:
            selected_range = (
                st.date_input(
                    "Período",
                    value=(minimum, maximum),
                    min_value=minimum,
                    max_value=maximum,
                    format="DD/MM/YYYY",
                    key="filter_period",
                )
                if minimum and maximum
                else None
            )
        action_col, note_col = st.columns([1, 4], vertical_alignment="center")
        with action_col:
            st.button(
                "Limpar filtros",
                type="secondary",
                use_container_width=True,
                on_click=reset_filters,
            )
        with note_col:
            st.caption("Use “Todas” para ver o acervo inteiro. Os filtros também valem para a busca.")

    effective_regions = selected_regions or list(REGIONS)
    effective_journals = (
        journals_for_region
        if not selected_journals or "Todas as universidades" in selected_journals
        else selected_journals
    )

    start_date, end_date = minimum, maximum
    if isinstance(selected_range, tuple) and len(selected_range) == 2:
        start_date, end_date = selected_range

    article_regions = articles["journal"].map(region_for_site)
    filter_mask = articles["journal"].isin(effective_journals) & article_regions.isin(
        effective_regions
    )
    if start_date and end_date:
        filter_mask &= articles["_date"].between(
            pd.Timestamp(start_date),
            pd.Timestamp(end_date) + pd.Timedelta(days=1),
            inclusive="left",
        )
    filtered_indices = articles.index[filter_mask]
    filtered_count = len(filtered_indices)

    region_summary = (
        "todas as regiões"
        if not selected_regions or len(selected_regions) == len(REGIONS)
        else f"{len(selected_regions)} regiões"
    )
    st.markdown(
        f'<div class="filter-summary">{region_summary} · {len(effective_journals)} universidades · {filtered_count:,.0f} notícias</div>'.replace(
            ",", "."
        ),
        unsafe_allow_html=True,
    )

    active_query = st.session_state.get("active_query", "")
    if st.session_state.get("run_search") and active_query:
        st.markdown(
            f'<div class="section-head"><h2>Resultados para “{html.escape(active_query)}”</h2><span>busca no acervo</span></div>',
            unsafe_allow_html=True,
        )
        try:
            active_filters = SearchFilters(
                journals=tuple(effective_journals),
                regions=tuple(effective_regions),
                start_date=start_date,
                end_date=end_date,
            )
            search_cache_key = (
                article_signature(ARTICLES_PATH),
                search_index_signature,
                os.environ.get("SEARCH_MODE", "hybrid").lower(),
                active_query,
                active_filters,
                20,
            )
            if st.session_state.get("search_cache_key") == search_cache_key:
                results = st.session_state["search_cache_results"]
                semantic_enabled = st.session_state["search_cache_semantic_enabled"]
            else:
                results, semantic_enabled = search_articles(
                    articles,
                    filtered_indices,
                    active_query,
                    filters=active_filters,
                    limit=20,
                )
                st.session_state["search_cache_key"] = search_cache_key
                st.session_state["search_cache_results"] = results
                st.session_state["search_cache_semantic_enabled"] = semantic_enabled
            mode = (
                "significado + palavras"
                if semantic_enabled
                else "títulos e resumos · modo local"
            )
            st.caption(
                f"{len(results)} notícias mais relevantes no recorte · {mode}"
            )
            for result in results:
                render_card(result, result=True)
            if not results:
                st.markdown(
                    '<div class="empty-state">Nenhuma notícia encontrada. Tente um termo mais amplo ou ajuste os filtros.</div>',
                    unsafe_allow_html=True,
                )
        except Exception:
            st.warning(
                "A busca está temporariamente indisponível. "
                "As últimas notícias continuam acessíveis abaixo."
            )
        if st.button("Limpar busca", type="secondary"):
            st.session_state["active_query"] = ""
            st.session_state["run_search"] = False
            st.session_state.pop("search_cache_key", None)
            st.session_state.pop("search_cache_results", None)
            st.session_state.pop("search_cache_semantic_enabled", None)
            st.rerun()

    st.markdown(
        f'<div class="section-head"><h2>Últimas notícias</h2><span>{filtered_count:,.0f} publicações no recorte</span></div>'.replace(",", "."),
        unsafe_allow_html=True,
    )

    if not filtered_count:
        st.markdown(
            '<div class="empty-state">Não há notícias neste período. Ajuste os filtros para ampliar o recorte.</div>',
            unsafe_allow_html=True,
        )
    else:
        pages = max(1, (filtered_count + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
        filter_signature = (
            tuple(effective_regions),
            tuple(effective_journals),
            start_date,
            end_date,
        )
        if st.session_state.get("news_filter_signature") != filter_signature:
            st.session_state["news_filter_signature"] = filter_signature
            st.session_state["news_page"] = 1

        current_page = min(max(int(st.session_state.get("news_page", 1)), 1), pages)
        st.session_state["news_page"] = current_page
        start = (current_page - 1) * RESULTS_PER_PAGE
        visible_indices = filtered_indices[start : start + RESULTS_PER_PAGE]
        visible = articles.loc[visible_indices]
        for row_start in range(0, len(visible), 3):
            columns = st.columns(3)
            for column, (_, article) in zip(columns, visible.iloc[row_start : row_start + 3].iterrows()):
                with column:
                    render_card(article)

        previous_col, page_col, next_col = st.columns([1, 2, 1], vertical_alignment="center")
        with previous_col:
            if st.button(
                "← Anterior",
                disabled=current_page == 1,
                use_container_width=True,
            ):
                st.session_state["news_page"] = current_page - 1
                st.rerun()
        with page_col:
            st.markdown(
                f"<div style='text-align:center;color:var(--muted);padding:.8rem'>Página <strong>{current_page}</strong> de {pages}</div>",
                unsafe_allow_html=True,
            )
        with next_col:
            if st.button(
                "Próxima →",
                disabled=current_page == pages,
                use_container_width=True,
            ):
                st.session_state["news_page"] = current_page + 1
                st.rerun()

    total_articles = f"{len(articles):,}".replace(",", ".")
    st.markdown(
        f"""
        <div class="footer">
            Universidades em Foco reúne {total_articles} notícias de {len(available_journals)} universidades.
            Conteúdo pertencente aos respectivos portais de origem.
        </div>
        """,
        unsafe_allow_html=True,
    )


DEFAULT_PAGE = os.environ.get("DASHBOARD_DEFAULT_PAGE", "home").lower()
if DEFAULT_PAGE not in {"home", "explore", "universities", "about"}:
    DEFAULT_PAGE = "home"

HOME_PAGE = st.Page(
    render_home,
    title="Home",
    icon=":material/home:",
    default=DEFAULT_PAGE == "home",
)
EXPLORE_PAGE = st.Page(
    render_explore,
    title="Explore",
    icon=":material/search:",
    url_path="explore",
    default=DEFAULT_PAGE == "explore",
)
UNIVERSITIES_PAGE = st.Page(
    render_universities,
    title="Universities",
    icon=":material/account_balance:",
    url_path="universities",
    default=DEFAULT_PAGE == "universities",
)
ABOUT_PAGE = st.Page(
    render_about,
    title="About",
    icon=":material/info:",
    url_path="about",
    default=DEFAULT_PAGE == "about",
)

navigation = st.navigation(
    [HOME_PAGE, EXPLORE_PAGE, UNIVERSITIES_PAGE, ABOUT_PAGE],
    position="top",
)
navigation.run()
