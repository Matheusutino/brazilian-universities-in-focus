from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.data import REGIONS, build_site_summary, discover_sites, load_artifact, region_for_site


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIRECTORY = PROJECT_ROOT / "data"

st.set_page_config(
    page_title="Jornais universitários",
    page_icon="📰",
    layout="wide",
)


@st.cache_data(show_spinner="Carregando arquivos JSONL…")
def load_data(
    data_directory: str, sites: tuple[str, ...], signature: tuple[tuple[str, int, int], ...]
) -> dict[str, pd.DataFrame]:
    del signature
    root = Path(data_directory)
    selected = list(sites)
    return {
        artifact: load_artifact(root, artifact, selected)
        for artifact in ("urls", "manifest", "articles", "errors")
    }


def data_signature(root: Path, sites: list[str]) -> tuple[tuple[str, int, int], ...]:
    values: list[tuple[str, int, int]] = []
    for site in sites:
        for name in ("urls", "manifest", "articles", "errors"):
            path = root / site / f"{name}.jsonl"
            if path.is_file():
                stat = path.stat()
                values.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(values)


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


st.title("Jornais universitários")
st.caption("Acompanhamento da coleta e exploração dos artigos extraídos.")

available_sites = discover_sites(DATA_DIRECTORY)
if not available_sites:
    st.error(f"Nenhum artefato encontrado em `{DATA_DIRECTORY}`.")
    st.stop()

with st.sidebar:
    st.header("Filtros")
    selected_regions = st.multiselect(
        "Regiões",
        list(REGIONS),
        default=list(REGIONS),
    )
    region_sites = [
        site for site in available_sites if region_for_site(site) in selected_regions
    ]
    selected_sites = st.multiselect(
        "Universidades",
        available_sites,
        default=region_sites,
        format_func=str.upper,
    )
    if st.button("Atualizar dados", use_container_width=True):
        st.cache_data.clear()

if not selected_sites:
    st.info("Selecione pelo menos uma universidade.")
    st.stop()

try:
    data = load_data(
        str(DATA_DIRECTORY),
        tuple(selected_sites),
        data_signature(DATA_DIRECTORY, selected_sites),
    )
except Exception as exc:
    st.error(f"Não foi possível ler os artefatos: {exc}")
    st.stop()

urls = data["urls"]
manifest = data["manifest"]
articles = data["articles"]
errors = data["errors"]
summary = build_site_summary(urls, manifest, articles, errors, selected_sites)

total_urls = int(summary["urls"].sum())
total_downloads = int(summary["downloads"].sum())
total_articles = int(summary["artigos"].sum())
total_errors = int(summary["erros"].sum())

metric_columns = st.columns(4)
metric_columns[0].metric("URLs descobertas", f"{total_urls:,}".replace(",", "."))
metric_columns[1].metric("HTMLs baixados", f"{total_downloads:,}".replace(",", "."))
metric_columns[2].metric("Artigos extraídos", f"{total_articles:,}".replace(",", "."))
metric_columns[3].metric("Erros registrados", f"{total_errors:,}".replace(",", "."))

overview_tab, articles_tab, quality_tab, errors_tab = st.tabs(
    ["Visão geral", "Artigos", "Qualidade", "Erros"]
)

with overview_tab:
    st.subheader("Progresso por região e universidade")
    st.dataframe(
        summary,
        width="stretch",
        hide_index=True,
        column_config={
            "site": "Universidade",
            "região": "Região",
            "urls": st.column_config.NumberColumn("URLs", format="%d"),
            "downloads": st.column_config.NumberColumn("Downloads", format="%d"),
            "artigos": st.column_config.NumberColumn("Artigos", format="%d"),
            "erros": st.column_config.NumberColumn("Erros", format="%d"),
            "conclusão": st.column_config.ProgressColumn(
                "Conclusão", min_value=0.0, max_value=1.0, format="percent"
            ),
        },
    )
    regional_summary = (
        summary.groupby("região", as_index=True)[["urls", "downloads", "artigos", "erros"]]
        .sum()
        .reindex(REGIONS, fill_value=0)
    )
    st.subheader("Totais por região")
    st.dataframe(regional_summary, width="stretch")
    st.bar_chart(regional_summary[["urls", "downloads", "artigos"]], x_label="Região", y_label="Registros")

with articles_tab:
    if articles.empty:
        st.info("Nenhum artigo extraído para os sites selecionados.")
    else:
        filtered = articles.copy()
        search = st.text_input(
            "Pesquisar em título, subtítulo, autor ou texto",
            placeholder="Digite uma expressão…",
        ).strip()
        if search:
            searchable = pd.Series(False, index=filtered.index)
            for column in ("title", "subtitle", "author", "text"):
                if column in filtered:
                    searchable |= filtered[column].fillna("").astype(str).str.contains(
                        search, case=False, regex=False
                    )
            filtered = filtered[searchable]

        if "date" in filtered:
            parsed_dates = pd.to_datetime(filtered["date"], errors="coerce")
            valid_dates = parsed_dates.dropna()
            if not valid_dates.empty:
                minimum = valid_dates.min().date()
                maximum = valid_dates.max().date()
                selected_range = st.date_input(
                    "Período",
                    value=(minimum, maximum),
                    min_value=minimum,
                    max_value=maximum,
                )
                if isinstance(selected_range, tuple) and len(selected_range) == 2:
                    start, end = selected_range
                    filtered = filtered[
                        parsed_dates.between(
                            pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1),
                            inclusive="left",
                        )
                    ]

        st.caption(f"{len(filtered):,} artigos encontrados".replace(",", "."))
        visible_columns = [
            column
            for column in ("site", "date", "title", "author", "tags", "url")
            if column in filtered
        ]
        display = filtered[visible_columns].copy()
        if "site" in display:
            display["site"] = display["site"].str.upper()
        st.dataframe(
            display,
            width="stretch",
            hide_index=True,
            column_config={
                "site": "Universidade",
                "date": "Data",
                "title": "Título",
                "author": "Autor",
                "tags": st.column_config.ListColumn("Tags"),
                "url": st.column_config.LinkColumn("URL", display_text="Abrir"),
            },
        )

        if not filtered.empty:
            choices = filtered.index.tolist()
            selected_index = st.selectbox(
                "Visualizar artigo",
                choices,
                format_func=lambda index: str(
                    filtered.at[index, "title"] or filtered.at[index, "url"]
                ),
            )
            article = filtered.loc[selected_index]
            st.subheader(str(article.get("title") or "Sem título"))
            metadata = " · ".join(
                str(value)
                for value in (
                    str(article.get("site", "")).upper(),
                    article.get("date"),
                    article.get("author"),
                )
                if present(value)
            )
            if metadata:
                st.caption(metadata)
            if present(article.get("subtitle")):
                st.markdown(f"*{article['subtitle']}*")
            # O Markdown do ``st.write`` colapsa quebras simples de linha.
            # Duas terminações de espaço forçam a quebra sem alterar o JSONL.
            article_text = str(article.get("text") or "")
            st.markdown(article_text.replace("\n", "  \n"))
            if present(article.get("url")):
                st.link_button("Abrir notícia original", str(article["url"]))
            images = article.get("images")
            if hasattr(images, "tolist"):
                images = images.tolist()
            if isinstance(images, (list, tuple)) and images:
                st.image(list(images)[:6], width=320)

with quality_tab:
    if articles.empty:
        st.info("Nenhum artigo disponível para avaliar.")
    else:
        quality_fields = ("title", "subtitle", "author", "date", "text", "tags", "images")
        rows = []
        for site, group in articles.groupby("site"):
            row: dict[str, object] = {"site": str(site).upper(), "artigos": len(group)}
            for field in quality_fields:
                row[f"sem {field}"] = (
                    int((~group[field].map(present)).sum()) if field in group else len(group)
                )
            rows.append(row)
        quality = pd.DataFrame(rows)
        st.subheader("Campos ausentes")
        st.dataframe(quality, width="stretch", hide_index=True)

        if "tags" in articles:
            tag_rows = articles[["site", "tags"]].explode("tags").dropna()
            if not tag_rows.empty:
                top_tags = (
                    tag_rows.groupby("tags")
                    .size()
                    .sort_values(ascending=False)
                    .head(20)
                    .rename("artigos")
                )
                st.subheader("Tags mais frequentes")
                st.bar_chart(top_tags)

with errors_tab:
    if errors.empty:
        st.success("Não há erros registrados para os sites selecionados.")
    else:
        error_view = errors.copy()
        error_view["site"] = error_view["site"].str.upper()
        columns = [
            column
            for column in ("site", "stage", "error", "message", "url", "page")
            if column in error_view
        ]
        st.dataframe(
            error_view[columns],
            width="stretch",
            hide_index=True,
            column_config={
                "site": "Universidade",
                "stage": "Estágio",
                "error": "Erro",
                "message": "Mensagem",
                "url": st.column_config.LinkColumn("URL", display_text="Abrir"),
                "page": "Página",
            },
        )
