import httpx

from jornais_new.config import (
    UFAL,
    UFMS,
    UFPA,
    UFPR,
    UFRGS,
    UFRJ,
    UERJ,
    UFABC,
    UFMG,
    UFLA,
    UNICAMP,
    UNIFESP,
    UEM,
    FURG,
    UFC,
    UNB,
    UNESP,
    USP,
)
from jornais_new.discovery import UrlDiscovery


def test_usp_page_urls() -> None:
    assert USP.page_url(1) == "https://jornal.usp.br/todas-as-noticias/"
    assert USP.page_url(2) == "https://jornal.usp.br/todas-as-noticias/page/2/"
    assert USP.page_url(3579) == "https://jornal.usp.br/todas-as-noticias/page/3579/"


def test_ufpr_page_urls_and_listing_selector() -> None:
    assert UFPR.page_url(1) == "https://ufpr.br/todas-as-noticias/"
    assert UFPR.page_url(2) == "https://ufpr.br/todas-as-noticias/page/2/"
    assert UFPR.page_url(2170) == "https://ufpr.br/todas-as-noticias/page/2170/"

    html = """
    <div class="blog-posts__container">
      <article><div class="blog-posts__text"><h2><a href="/materia-1/">Matéria 1</a></h2></div></article>
      <article><div class="blog-posts__text"><h2><a href="/materia-2/#comentarios">Matéria 2</a></h2></div></article>
      <div class="pagination"><a href="/todas-as-noticias/page/3/">3</a></div>
    </div>
    <aside><a href="/fora-do-arquivo/">Fora</a></aside>
    """
    assert UrlDiscovery(UFPR).parse_urls(html, UFPR.page_url(2)) == {
        "https://ufpr.br/materia-1/",
        "https://ufpr.br/materia-2/",
    }


def test_ufrj_page_urls_and_listing_selector() -> None:
    assert UFRJ.page_url(1) == "https://conexao.ufrj.br/todas-as-noticias/"
    assert UFRJ.page_url(2) == "https://conexao.ufrj.br/todas-as-noticias/page/2/"
    assert UFRJ.page_url(1789) == "https://conexao.ufrj.br/todas-as-noticias/page/1789/"

    html = """
    <main id="site-content">
      <article>
        <h2 class="entry-title"><a href="/2026/07/materia-1/">Matéria 1</a></h2>
      </article>
      <article>
        <h2 class="entry-title"><a href="/2002/01/materia-2/#comentarios">Matéria 2</a></h2>
      </article>
      <a href="/todas-as-noticias/page/3/">Próxima</a>
    </main>
    """
    assert UrlDiscovery(UFRJ).parse_urls(html, UFRJ.page_url(2)) == {
        "https://conexao.ufrj.br/2026/07/materia-1/",
        "https://conexao.ufrj.br/2002/01/materia-2/",
    }


def test_uerj_page_urls_and_listing_selector() -> None:
    assert UERJ.page_url(1) == "https://www.uerj.br/todas-as-noticias/"
    assert UERJ.page_url(2) == "https://www.uerj.br/todas-as-noticias/page/2/"
    assert UERJ.page_url(139) == "https://www.uerj.br/todas-as-noticias/page/139/"

    html = """
    <main id="main">
      <div class="col-md-6">
        <a class="ultimas-noticias__itemwrapper" href="/noticia/materia-1/">
          <article class="ultimas-noticias__item"><h1 class="titulo">Matéria 1</h1></article>
        </a>
      </div>
      <a class="ultimas-noticias__itemwrapper" href="/noticia/materia-2/#comentarios">
        <article class="ultimas-noticias__item"><h1 class="titulo">Matéria 2</h1></article>
      </a>
      <a href="/todas-as-noticias/page/3/">Próxima</a>
    </main>
    """
    assert UrlDiscovery(UERJ).parse_urls(html, UERJ.page_url(2)) == {
        "https://www.uerj.br/noticia/materia-1/",
        "https://www.uerj.br/noticia/materia-2/",
    }


def test_ufabc_offset_urls_and_listing_selector() -> None:
    assert UFABC.page_url(0) == "https://www.ufabc.edu.br/noticias"
    assert UFABC.page_url(10) == "https://www.ufabc.edu.br/noticias?start=10"
    assert UFABC.page_url(2080) == "https://www.ufabc.edu.br/noticias?start=2080"
    assert list(UFABC.page_values(0, 30)) == [0, 10, 20, 30]

    html = """
    <form id="adminForm">
      <div class="tileItem"><div class="tileContent">
        <h2 class="tileHeadline"><a href="/noticias/materia-1">Matéria 1</a></h2>
      </div></div>
      <div class="tileItem"><div class="tileContent">
        <h2 class="tileHeadline"><a href="/noticias/materia-2#comentarios">Matéria 2</a></h2>
      </div></div>
      <a href="/noticias?start=20">Próxima</a>
    </form>
    """
    assert UrlDiscovery(UFABC).parse_urls(html, UFABC.page_url(10)) == {
        "https://www.ufabc.edu.br/noticias/materia-1",
        "https://www.ufabc.edu.br/noticias/materia-2",
    }


def test_ufmg_page_urls_and_listing_selector() -> None:
    assert UFMG.page_url(1) == (
        "https://www.ufmg.br/comunicacao/noticias/?secao=todas-as-noticias"
    )
    assert UFMG.page_url(2) == (
        "https://www.ufmg.br/comunicacao/noticias/?secao=todas-as-noticias&pagina=2"
    )
    assert UFMG.page_url(259).endswith("&pagina=259")

    html = """
    <main id="primary">
      <div class="space-y-10 py-4"><a href="/comunicacao/noticias/categoria/materia-1/">Matéria 1</a></div>
      <div class="space-y-10 py-4"><a href="/comunicacao/noticias/categoria/materia-2/">Matéria 2</a></div>
      <div class="mx-auto"><a class="page-numbers" href="?pagina=3">3</a></div>
    </main>
    """
    assert UrlDiscovery(UFMG).parse_urls(html, UFMG.page_url(2)) == {
        "https://www.ufmg.br/comunicacao/noticias/categoria/materia-1/",
        "https://www.ufmg.br/comunicacao/noticias/categoria/materia-2/",
    }


def test_ufla_offset_urls_and_listing_selector() -> None:
    assert UFLA.page_url(0) == "https://ufla.br/noticias?start=0"
    assert UFLA.page_url(12) == "https://ufla.br/noticias?start=12"
    assert UFLA.page_url(4032) == "https://ufla.br/noticias?start=4032"
    assert list(UFLA.page_values(0, 36)) == [0, 12, 24, 36]

    html = """
    <div class="blog">
      <div class="item"><h3><a href="/noticias/ensino/123-materia-1">Matéria 1</a></h3></div>
      <div class="item"><h3><a href="/noticias/pesquisa/124-materia-2#comentarios">Matéria 2</a></h3></div>
      <div class="pagination"><a href="/noticias?start=24">24</a></div>
    </div>
    """
    assert UrlDiscovery(UFLA).parse_urls(html, UFLA.page_url(12)) == {
        "https://ufla.br/noticias/ensino/123-materia-1",
        "https://ufla.br/noticias/pesquisa/124-materia-2",
    }


def test_unicamp_page_urls_and_listing_selector() -> None:
    assert UNICAMP.page_url(1) == "https://jornal.unicamp.br/noticias/"
    assert UNICAMP.page_url(2) == "https://jornal.unicamp.br/noticias/pagina/2/"
    assert UNICAMP.page_url(59) == "https://jornal.unicamp.br/noticias/pagina/59/"

    html = """
    <main id="conteudo-principal">
      <a class="post-cards__single-post__link" href="/noticias/2026/01/materia-1/">Matéria 1</a>
      <a class="post-cards__single-post__link" href="/noticias/2026/01/materia-2/#comentarios">Matéria 2</a>
      <a href="/noticias/pagina/3/">Próxima</a>
    </main>
    """
    assert UrlDiscovery(UNICAMP).parse_urls(html, UNICAMP.page_url(2)) == {
        "https://jornal.unicamp.br/noticias/2026/01/materia-1/",
        "https://jornal.unicamp.br/noticias/2026/01/materia-2/",
    }


def test_unifesp_offset_urls_and_listing_selector() -> None:
    assert UNIFESP.page_url(0) == "https://portal.unifesp.br/destaques?start=0"
    assert UNIFESP.page_url(4) == "https://portal.unifesp.br/destaques?start=4"
    assert UNIFESP.page_url(432) == "https://portal.unifesp.br/destaques?start=432"
    assert list(UNIFESP.page_values(0, 12)) == [0, 4, 8, 12]

    html = """
    <div class="blog-items">
      <div class="blog-item"><div class="item-content"><div class="page-header">
        <h2><a href="/destaques/materia-1">Matéria 1</a></h2>
      </div></div></div>
      <div class="blog-item"><div class="item-content"><div class="page-header">
        <h2><a href="/destaques/materia-2#comentarios">Matéria 2</a></h2>
      </div></div></div>
      <a href="/destaques?start=8">Próxima</a>
    </div>
    """
    assert UrlDiscovery(UNIFESP).parse_urls(html, UNIFESP.page_url(4)) == {
        "https://portal.unifesp.br/destaques/materia-1",
        "https://portal.unifesp.br/destaques/materia-2",
    }


def test_uem_offset_urls_and_listing_selector() -> None:
    assert UEM.page_url(0) == "https://www.noticias.uem.br/index.php?limitstart=0"
    assert UEM.page_url(15) == "https://www.noticias.uem.br/index.php?limitstart=15"
    assert UEM.page_url(345) == "https://www.noticias.uem.br/index.php?limitstart=345"
    assert list(UEM.page_values(0, 45)) == [0, 15, 30, 45]

    html = """
    <div id="t3-content">
      <h2><a href="/index.php?option=com_content&view=article&id=1:materia-1&catid=986">Matéria 1</a></h2>
      <h2><a href="/index.php?option=com_content&view=article&id=2:materia-2&catid=986#comentarios">Matéria 2</a></h2>
      <a href="/index.php?option=com_tags&view=tag&id=1-tag">Tag</a>
    </div>
    """
    assert UrlDiscovery(UEM).parse_urls(html, UEM.page_url(15)) == {
        "https://www.noticias.uem.br/index.php?option=com_content&view=article&id=1:materia-1&catid=986",
        "https://www.noticias.uem.br/index.php?option=com_content&view=article&id=2:materia-2&catid=986",
    }


def test_furg_offset_urls_and_listing_selector() -> None:
    assert FURG.page_url(0) == "https://www.furg.br/noticias?start=0"
    assert FURG.page_url(20) == "https://www.furg.br/noticias?start=20"
    assert FURG.page_url(25780) == "https://www.furg.br/noticias?start=25780"
    assert list(FURG.page_values(0, 60)) == [0, 20, 40, 60]

    html = """
    <div class="pagina pagina__titulo--sem_linha category-list" id="t3-content">
      <h2><a href="/noticias/noticias-eventos/materia-1">Matéria 1</a></h2>
      <h2><a href="/noticias/noticias-arquivo/materia-2#comentarios">Matéria 2</a></h2>
      <a href="/component/tags/tag/assunto">Assunto</a>
    </div>
    """
    assert UrlDiscovery(FURG).parse_urls(html, FURG.page_url(20)) == {
        "https://www.furg.br/noticias/noticias-eventos/materia-1",
        "https://www.furg.br/noticias/noticias-arquivo/materia-2",
    }


def test_ufc_page_urls_and_listing_selector() -> None:
    assert UFC.page_url(1) == "https://agencia.ufc.br/"
    assert UFC.page_url(2) == "https://agencia.ufc.br/page/2/"
    assert UFC.page_url(56) == "https://agencia.ufc.br/page/56/"

    html = """
    <div class="main">
      <div class="slide-item"><a href="/materia-destaque/"><img></a></div>
      <div class="card"><div class="content"><p class="title">
        <a href="/materia-1/">Matéria 1</a>
      </p></div></div>
      <div class="card"><div class="content"><p class="category">
        <a href="/category/saude/">Saúde</a>
      </p></div></div>
    </div>
    """
    assert UrlDiscovery(UFC).parse_urls(html, UFC.page_url(2)) == {
        "https://agencia.ufc.br/materia-destaque/",
        "https://agencia.ufc.br/materia-1/",
    }


def test_ufal_offset_urls_and_range() -> None:
    assert UFAL.page_url(0) == "https://noticias.ufal.br/"
    assert UFAL.page_url(30) == "https://noticias.ufal.br/?b_start:int=30"
    assert UFAL.page_url(23880) == "https://noticias.ufal.br/?b_start:int=23880"
    assert list(UFAL.page_values(0, 60)) == [0, 30, 60]
    assert len(UFAL.page_values(0, 23880)) == 797


async def test_ufal_discovery_uses_offset_step() -> None:
    offsets: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("b_start:int", "0"))
        offsets.append(offset)
        return httpx.Response(
            200,
            request=request,
            text=(
                '<div id="content-core">'
                f'<a class="titulo" href="/ufal/noticias/2026/6/noticia-{offset}">N</a>'
                '<a href="/?b_start:int=30">Paginação</a>'
                "</div>"
            ),
        )

    discovery = UrlDiscovery(
        UFAL,
        transport=httpx.MockTransport(handler),
        retry_multiplier=0,
    )
    result = await discovery.discover(0, 60)

    assert sorted(offsets) == [0, 30, 60]
    assert len(result.urls) == 3
    assert all("noticia-" in url for url in result.urls)


def test_unesp_page_urls_and_listing_selectors() -> None:
    assert UNESP.page_url(1) == "https://jornal.unesp.br/"
    assert UNESP.page_url(2) == "https://jornal.unesp.br/page/2/"
    assert UNESP.page_url(136) == "https://jornal.unesp.br/page/136/"

    html = """
    <header class="article-banner">
      <a class="badge" href="/category/reportagens/">Categoria destaque</a>
      <a class="hover-opacity" href="/2026/06/26/destaque/">Destaque</a>
    </header>
    <div class="col-12 col-lg-9 pr-lg-5">
      <a class="badge" href="/category/reportagens/">Categoria</a>
      <a class="excerpt-title-link" href="/2026/06/25/materia/">Matéria</a>
      <a href="/page/2/">Próxima</a>
    </div>
    """

    assert UrlDiscovery(UNESP).parse_urls(html, UNESP.page_url(1)) == {
        "https://jornal.unesp.br/2026/06/26/destaque/",
        "https://jornal.unesp.br/2026/06/25/materia/",
    }


def test_unb_offset_urls_and_listing_selectors() -> None:
    assert UNB.page_url(0) == "https://noticias.unb.br/"
    assert UNB.page_url(10) == "https://noticias.unb.br/?start=10"
    assert UNB.page_url(2420) == "https://noticias.unb.br/?start=2420"
    assert list(UNB.page_values(0, 20)) == [0, 10, 20]

    html = """
    <div class="items-row">
      <div class="item-image"><a href="/institucional/1-imagem"><img></a></div>
      <div class="page-header"><h2><a href="/institucional/1-materia">Matéria</a></h2></div>
      <div class="tags"><a href="/component/tags/tag/ods-4">ODS 4</a></div>
      <a href="/">Compartilhar</a>
    </div>
    """

    assert UrlDiscovery(UNB).parse_urls(html, UNB.page_url(0)) == {
        "https://noticias.unb.br/institucional/1-materia"
    }


def test_ufms_page_urls_and_listing_selectors() -> None:
    assert UFMS.page_url(1) == "https://www.ufms.br/category/noticias/"
    assert UFMS.page_url(2) == "https://www.ufms.br/category/noticias/page/2/"
    assert UFMS.page_url(626) == "https://www.ufms.br/category/noticias/page/626/"

    html = """
    <div class="col-md-9">
      <article>
        <h2><a href="https://www.ufms.br/materia-1/">Matéria 1</a></h2>
        <a href="https://www.ufms.br/category/noticias/">Categoria</a>
      </article>
    </div>
    <div class="col-md-12">
      <h2><a href="/materia-2/#comentarios">Matéria 2</a></h2>
    </div>
    <aside><h2><a href="https://www.ufms.br/link-fora/">Fora</a></h2></aside>
    """

    assert UrlDiscovery(UFMS).parse_urls(html, UFMS.page_url(1)) == {
        "https://www.ufms.br/materia-1/",
        "https://www.ufms.br/materia-2/",
    }


def test_ufrgs_page_urls_and_listing_selectors() -> None:
    assert UFRGS.page_url(1) == "https://www.ufrgs.br/site/noticias/"
    assert UFRGS.page_url(2) == "https://www.ufrgs.br/site/noticias/page/2/"
    assert UFRGS.page_url(2667) == "https://www.ufrgs.br/site/noticias/page/2667/"

    html = """
    <div class="col-xl-9">
      <a class="image" href="/site/noticias/materia-1/"><img></a>
      <a class="title" href="/site/noticias/materia-1/#texto">Matéria 1</a>
      <a class="title" href="https://www.ufrgs.br/site/noticias/materia-2/">Matéria 2</a>
      <a href="/site/noticias/page/2/">Próxima</a>
    </div>
    <aside><a class="title" href="/site/noticias/link-fora/">Fora</a></aside>
    """

    assert UrlDiscovery(UFRGS).parse_urls(html, UFRGS.page_url(1)) == {
        "https://www.ufrgs.br/site/noticias/materia-1/",
        "https://www.ufrgs.br/site/noticias/materia-2/",
    }


def test_ufpa_page_urls_and_listing_selectors() -> None:
    assert UFPA.page_url(1) == "https://ufpa.br/?post_type=post&sf_paged=1"
    assert UFPA.page_url(2) == "https://ufpa.br/?post_type=post&sf_paged=2"
    assert UFPA.page_url(809) == "https://ufpa.br/?post_type=post&sf_paged=809"

    html = """
    <div class="elementor-loop-container elementor-grid">
      <div class="e-loop-item">
        <a href="https://ufpa.br/materia-1/"><img></a>
        <a href="/?post_type=post&_sft_category=noticias">Notícias</a>
        <h1 class="elementor-heading-title">
          <a href="https://ufpa.br/materia-1/#texto">Matéria 1</a>
        </h1>
      </div>
      <div class="e-loop-item">
        <h1 class="elementor-heading-title"><a href="/materia-2/">Matéria 2</a></h1>
      </div>
    </div>
    <aside><h1 class="elementor-heading-title"><a href="/fora/">Fora</a></h1></aside>
    """

    assert UrlDiscovery(UFPA).parse_urls(html, UFPA.page_url(1)) == {
        "https://ufpa.br/materia-1/",
        "https://ufpa.br/materia-2/",
    }


def test_parse_urls_deduplicates_and_filters() -> None:
    html = """
    <div class="elementor-widget-archive-posts">
      <h3 class="elementor-post__title"><a href="/ciencias/materia/#top">A</a></h3>
      <h3 class="elementor-post__title"><a href="https://jornal.usp.br/ciencias/materia/">A mobile</a></h3>
      <h3 class="elementor-post__title"><a href="https://example.com/fora/">Fora</a></h3>
    </div>
    <div class="elementor-widget-container">
      <a href="https://jornal.usp.br/menu/">Link fora da listagem</a>
    </div>
    """
    discovery = UrlDiscovery(USP)

    assert discovery.parse_urls(html, USP.page_url(2)) == {
        "https://jornal.usp.br/ciencias/materia/"
    }


async def test_discovery_retries_transient_status_but_not_404() -> None:
    attempts: dict[str, int] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        attempts[key] = attempts.get(key, 0) + 1
        if key.endswith("/page/2/") and attempts[key] == 1:
            return httpx.Response(500, request=request)
        if key.endswith("/page/3/"):
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            request=request,
            text=(
                '<div class="elementor-widget-archive-posts">'
                '<h3 class="elementor-post__title">'
                f'<a href="https://jornal.usp.br/noticia-{key.strip("/").replace("/", "-")}/">N</a>'
                "</h3></div>"
            ),
        )

    discovery = UrlDiscovery(
        USP,
        transport=httpx.MockTransport(handler),
        retry_multiplier=0,
    )
    result = await discovery.discover(1, 3)

    assert len(result.urls) == 2
    assert len(result.errors) == 1
    assert attempts["/todas-as-noticias/page/2/"] == 2
    assert attempts["/todas-as-noticias/page/3/"] == 1
