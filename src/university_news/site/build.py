"""Build static pages from a consistent PostgreSQL metadata snapshot."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .search import ArticleIndex
from .statistics import deduplicate_keywords, site_statistics
from .criteria import SCALES, criteria_for_page, sdg_list
from .labels import LABELS
from .render import STATIC, labels_json, make_environment

PUBLIC_DIRECTORY = "public"
STATS_FILENAME = "stats.json"

AUTHORS = (
    {
        "name": "Matheus Yasuo Ribeiro Utino",
        "initials": "MU",
        "role": "Responsável pelo projeto",
        "affiliation": "ICMC · Universidade de São Paulo",
        "description": (
            "Mestrando em Ciência da Computação no ICMC-USP, com trabalhos em aprendizado "
            "de máquina e análise de dados textuais. É responsável pelo desenvolvimento e "
            "pela pesquisa deste projeto."
        ),
        "links": (
            ("GitHub", "https://github.com/Matheusutino"),
            ("Publicação", "https://scholar.google.com.br/citations?user=FdirnJQAAAAJ&hl=pt-BR"),
        ),
    },
    {
        "name": "Elaine Parros Machado de Sousa",
        "initials": "ES",
        "role": "Coautora do artigo",
        "affiliation": "ICMC · Universidade de São Paulo",
        "description": (
            "Docente do ICMC-USP e pesquisadora na área de bancos de dados, com atuação em "
            "aprendizado de máquina aplicado a séries temporais e fluxos de dados."
        ),
        "links": (
            ("Perfil FAPESP", "https://bv.fapesp.br/pt/pesquisador/37948/elaine-parros-machado-de-sousa/"),
        ),
    },
    {
        "name": "Ricardo Marcondes Marcacini",
        "initials": "RM",
        "role": "Coautor do artigo",
        "affiliation": "ICMC · Universidade de São Paulo",
        "description": (
            "Professor associado do ICMC-USP e coordenador do Labic. Pesquisa aprendizado de "
            "máquina e mineração de textos, com aplicações em dados governamentais e sociais."
        ),
        "links": (
            ("Perfil FAPESP", "https://fapesp.br/17687/ricardo-marcondes-marcacini"),
            ("CeMEAI", "https://cemeai.icmc.usp.br/ricardo-marcondes-marcacini/"),
        ),
    },
)

METHODS = (
    ("Coleta", "As notícias são obtidas exclusivamente dos portais oficiais das universidades acompanhadas."),
    ("Qualidade", "Registros incompletos, duplicados ou fora dos critérios definidos são identificados antes da indexação."),
    (
        "Análise",
        "Modelos de linguagem classificam cada notícia (tema, área, impacto, estilo) segundo um contrato "
        "validado; cada anotação registra o modelo, o prompt e a configuração usados.",
    ),
    ("Busca", "A consulta combina busca textual (BM25) com embeddings locais e busca vetorial aproximada."),
)


@dataclass(frozen=True, slots=True)
class BuildResult:
    directory: str
    articles: int
    annotated: int
    pages: int
    seconds: float


def facets(stats: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Filter options limited to values that occur in the annotations."""
    scope = stats["analysis"]["all"]
    present = {chart["key"]: [row for row in chart["rows"] if row["count"]] for chart in scope["charts"]}
    pick = lambda key: [(str(row["value"]), row["label"]) for row in present.get(key, [])]  # noqa: E731
    return {
        "categories": pick("news_category"),
        "fields": pick("scientific_fields"),
        "types": pick("content_type"),
    }


def build_site(
    output: Path,
    *,
    index: ArticleIndex,
) -> BuildResult:
    started = time.perf_counter()
    records, profiles = index.publication_snapshot()
    articles = pd.DataFrame(records, columns=[
        "id", "url", "title", "subtitle", "author", "date", "journal", "region"
    ])
    annotations = {
        row["url"]: row["annotation"] for row in records if row["annotation"]
    }
    deduplicate_keywords(annotations)
    version = str(int(time.time()))
    stats = site_statistics(articles, annotations)
    latest = [
        {
            "id": row["id"],
            "url": row["url"],
            "title": row["title"],
            "subtitle": row["subtitle"],
            "author": row["author"],
            "date": row["date"],
            "journal": row["journal"],
            "region": row["region"],
            "analysis": None,
        }
        for row in [record for record in records if record["date"]][:6]
    ]

    environment = make_environment()
    context = {"stats": stats, "version": version}
    pages = {
        "index.html": ("home.html", {"page": "home", "latest": latest}),
        "explorar/index.html": (
            "explore.html",
            {"page": "explore", "facets": facets(stats), "labels_json": labels_json()},
        ),
        "analises/index.html": (
            "analysis.html",
            {
                "page": "analysis",
                "profiles": profiles,
                "analysis_json": json.dumps(stats["analysis"], ensure_ascii=False).replace("<", "\\u003c"),
            },
        ),
        "universidades/index.html": ("universities.html", {"page": "universities"}),
        "criterios/index.html": (
            "criteria.html",
            {"page": "criteria", "criteria": criteria_for_page(), "scales": SCALES, "sdgs": sdg_list()},
        ),
        "sobre/index.html": ("about.html", {"page": "about", "authors": AUTHORS, "methods": METHODS}),
        "404.html": ("not_found.html", {"page": ""}),
    }
    staging = output / f".{PUBLIC_DIRECTORY}.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    for relative, (template, extra) in pages.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            environment.get_template(template).render(**context, **extra), encoding="utf-8"
        )
    shutil.copytree(STATIC, staging / "static")
    # Metadata belongs to this release of the pages, not a separate data bundle.
    (staging / STATS_FILENAME).write_text(
        json.dumps({**{key: value for key, value in stats.items() if key != "analysis"}, "version": version}, ensure_ascii=False),
        encoding="utf-8",
    )
    _swap_directory(staging, output / PUBLIC_DIRECTORY)
    return BuildResult(
        directory=str(output),
        articles=len(records),
        annotated=len(annotations),
        pages=len(pages),
        seconds=round(time.perf_counter() - started, 1),
    )


def _swap_directory(staging: Path, target: Path) -> None:
    """Replace the published pages in two renames; readers see old or new."""
    previous = target.with_name(f".{target.name}.old")
    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        target.rename(previous)
    staging.rename(target)
    shutil.rmtree(previous, ignore_errors=True)


__all__ = ["LABELS", "BuildResult", "build_site"]
