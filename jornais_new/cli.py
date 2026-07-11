import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from .config import SITES
from .pipeline import NewsPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jornais-new",
        description="Coleta e extrai noticias de portais universitarios.",
    )
    parser.add_argument("site", choices=sorted(SITES), help="Site alvo")
    parser.add_argument(
        "--stage",
        choices=("discovery", "fetching", "extraction", "all"),
        default="all",
        help="Estagio a executar (padrao: all)",
    )
    parser.add_argument(
        "--extractor",
        choices=("trafilatura", "llm", "hybrid"),
        default="trafilatura",
        help="Extrator do estagio extraction (padrao: trafilatura)",
    )
    parser.add_argument(
        "--start-page",
        "--start-offset",
        dest="start_page",
        type=int,
        help="Primeira pagina ou offset da listagem",
    )
    parser.add_argument(
        "--end-page",
        "--end-offset",
        dest="end_page",
        type=int,
        help="Ultima pagina ou offset da listagem",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Diretorio de saida (padrao: data/<site>)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        choices=range(1, 11),
        metavar="1..10",
        help="Numero maximo de requisicoes simultaneas (padrao: 10)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Refaz o estagio selecionado"
    )
    parser.add_argument("--verbose", action="store_true", help="Ativa logs detalhados")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.stage not in {"discovery", "all"} and (
        args.start_page is not None or args.end_page is not None
    ):
        parser.error(
            "--start-page e --end-page so podem ser usados com discovery ou all"
        )
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    site = SITES[args.site]
    output = args.output or Path("data") / site.name
    pipeline = NewsPipeline(site, output, concurrency=args.concurrency)
    try:
        stats = asyncio.run(
            pipeline.run(
                stage=args.stage,
                extractor=args.extractor,
                start_page=args.start_page,
                end_page=args.end_page,
                force=args.force,
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        logging.error("Falha estrutural no pipeline: %s", exc)
        return 1

    print(json.dumps(pipeline.stats_as_dict(stats), ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(run())
