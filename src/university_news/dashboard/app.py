from __future__ import annotations

import html
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from university_news.search.hybrid import (
    LanceDBRetriever,
    SearchFilters,
    article_id,
    rrf_fuse,
    tokenize,
)
from university_news.search.embeddings import (
    DEFAULT_CONTEXT_LENGTH,
    DEFAULT_MODEL_FILE,
    DEFAULT_MODEL_REPO,
    QwenGGUFEmbedder,
)
from university_news.search.vector_index import (
    DEFAULT_DATABASE,
    DEFAULT_TABLE,
    connect_database,
    load_index_metadata,
)
from university_news.dashboard.data import load_consolidated_articles
from university_news.paths import DEFAULT_DATA_ROOT, compiled_articles_path
from university_news.sources.catalog import REGIONS, region_for_site


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTICLES_PATH = compiled_articles_path(DEFAULT_DATA_ROOT)
RESULTS_PER_PAGE = 12

load_dotenv(PROJECT_ROOT / ".env")

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
    .block-container { max-width: 1180px; padding-top: 1.2rem; padding-bottom: 3rem; }
    html, body, [class*="css"] { font-family: "DM Sans", sans-serif; }
    h1, h2, h3 { font-family: "Newsreader", Georgia, serif !important; color: var(--ink) !important; }
    [data-testid="stHeader"] { background: transparent; }
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
    .hero h1 { font-size: clamp(2.6rem, 6vw, 4.8rem); line-height: .98; letter-spacing: -.045em; margin: 0 0 1rem; }
    .hero p { max-width: 650px; margin: 0 auto; color: var(--muted); font-size: 1.05rem; line-height: 1.65; }
    .section-head {
        display: flex;
        align-items: end;
        justify-content: space-between;
        gap: 1rem;
        margin: 2.8rem 0 1rem;
        padding-bottom: .8rem;
        border-bottom: 1px solid var(--line);
    }
    .section-head h2 { margin: 0; font-size: 2rem; }
    .section-head span { color: var(--muted); font-size: .85rem; white-space: nowrap; }
    .news-card {
        box-sizing: border-box;
        height: 320px;
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
        height: 4.7rem;
        min-height: 4.7rem;
        max-height: 4.7rem;
        overflow: hidden;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 3;
        font-size: 1.42rem;
        line-height: 1.1;
        letter-spacing: -.02em;
        margin: .7rem 0 .65rem;
    }
    .news-card h3 a { color: inherit; text-decoration: none; }
    .news-card p {
        display: -webkit-box;
        height: 6.85rem;
        max-height: 6.85rem;
        overflow: hidden;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 5;
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
    .footer {
        border-top: 1px solid var(--line);
        color: var(--muted);
        margin-top: 3rem;
        padding-top: 1.3rem;
        font-size: .78rem;
    }
    @media (max-width: 700px) {
        .block-container { padding-left: 1rem; padding-right: 1rem; }
        .brand-note { display: none; }
        .hero { text-align: left; }
        .hero h1 { font-size: 3rem; }
        .news-card { height: 320px; margin-bottom: 1.1rem; }
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
    return frame.sort_values("_date", ascending=False, na_position="last")


def article_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


@st.cache_resource(show_spinner="Carregando a busca semântica local…")
def load_semantic_retriever() -> LanceDBRetriever | None:
    if os.environ.get("SEARCH_MODE", "hybrid").lower() == "lexical":
        return None
    database_path = Path(os.environ.get("LANCEDB_PATH", str(DEFAULT_DATABASE)))
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
                    "EMBEDDING_CONTEXT_LENGTH",
                    metadata.get("context_length", DEFAULT_CONTEXT_LENGTH),
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

    retriever = load_semantic_retriever()
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


st.markdown(
    """
    <div class="brandbar">
        <div class="brand"><span class="brand-mark">●</span> Universidades em Foco</div>
        <div class="brand-note">Ciência, cultura e sociedade nas universidades brasileiras</div>
    </div>
    <section class="hero">
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
        results, semantic_enabled = search_articles(
            articles,
            filtered_indices,
            active_query,
            filters=SearchFilters(
                journals=tuple(effective_journals),
                regions=tuple(effective_regions),
                start_date=start_date,
                end_date=end_date,
            ),
            limit=20,
        )
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
