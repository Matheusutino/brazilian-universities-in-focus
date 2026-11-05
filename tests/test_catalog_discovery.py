import httpx

from university_news.sources.catalog import (
    UFAL,
    UFAM,
    SITES,
    region_for_site,
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
    UFT,
    UNB,
    UNESP,
    USP,
)
from university_news.pipeline.discovery import UrlDiscovery


async def test_ufam_offset_discovery_and_registration() -> None:
    assert SITES["ufam"] is UFAM
    assert region_for_site("ufam") == "Norte"
    assert UFAM.page_url(0) == "https://www.ufam.edu.br/noticias.html"
    assert UFAM.page_url(2150).endswith("?start=2150")
    assert len(UFAM.page_values(0, 2150)) == 216
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("start", "0"))
        offsets.append(offset)
        return httpx.Response(200, text=f"""
          <form id="adminForm"><div class="tileItem">
            <h2 class="tileHeadline">
              <a href="/noticias/{offset}-pesquisa.html">Pesquisa</a>
              <a href="/noticias/{offset}-pesquisa.html#texto">Repetida</a>
              <a href="https://outro.edu.br/noticias/fora.html">Externa</a>
            </h2>
          </div><a href="/noticias.html?start=20">Próxima</a></form>
          <footer><a href="/noticias/fora.html">Fora da listagem</a></footer>
        """)

    result = await UrlDiscovery(
        UFAM, transport=httpx.MockTransport(handler)
    ).discover(0, 20)

    assert sorted(offsets) == [0, 10, 20]
    assert result.errors == []
    assert result.urls == {
        f"https://www.ufam.edu.br/noticias/{offset}-pesquisa.html"
        for offset in (0, 10, 20)
    }


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


def test_uft_page_urls_and_listing_selector() -> None:
    assert UFT.page_url(1) == "https://www.uft.edu.br/noticias"
    assert UFT.page_url(2) == "https://www.uft.edu.br/noticias?page=2"
    assert UFT.page_url(43) == "https://www.uft.edu.br/noticias?page=43"

    html = """
    <div id="page-document" class="ui container">
      <div class="listing news_items">
        <div class="listing-item">
          <a class="listing-item-main-link" href="/noticias/materia-1">
            <h4 class="listing-title">Matéria 1</h4>
          </a>
        </div>
        <div class="listing-item">
          <a class="listing-item-main-link" href="/noticias/materia-2#top">
            <h4 class="listing-title">Matéria 2</h4>
          </a>
        </div>
      </div>
      <div class="pagination-wrapper"><a href="/noticias?page=3">3</a></div>
    </div>
    """
    assert UrlDiscovery(UFT).parse_urls(html, UFT.page_url(2)) == {
        "https://www.uft.edu.br/noticias/materia-1",
        "https://www.uft.edu.br/noticias/materia-2",
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
      <ul class="py-4">
        <li><a class="flex" href="/comunicacao/noticias/categoria/materia-1/">Matéria 1</a></li>
        <li><a class="flex" href="/comunicacao/noticias/categoria/materia-2/">Matéria 2</a></li>
      </ul>
      <nav><ul><li><a class="page-numbers" href="?pagina=3">3</a></li></ul></nav>
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
    assert UFC.page_url(1) == "https://ufcinforma.ufc.br/pt/marcador/noticias"
    assert UFC.page_url(2) == (
        "https://ufcinforma.ufc.br/pt/marcador/noticias?pagina=2"
    )
    assert UFC.page_url(17).endswith("?pagina=17")

    html = """
    <main id="content">
      <a href="/pt/noticias/materia-destaque"><img></a>
      <a href="https://ufcinforma.ufc.br/pt/noticias/materia-1">Matéria 1</a>
      <a href="/pt/noticias/materia-1#texto">Matéria 1 repetida</a>
      <a href="/pt/marcador/noticias?pagina=3">Próxima</a>
    </main>
    <footer><a href="/pt/noticias/fora-da-listagem">Fora</a></footer>
    """
    assert UrlDiscovery(UFC).parse_urls(html, UFC.page_url(2)) == {
        "https://ufcinforma.ufc.br/pt/noticias/materia-destaque",
        "https://ufcinforma.ufc.br/pt/noticias/materia-1",
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


def test_ufal_2026_listing_without_title_class() -> None:
    html = """
    <div id="content-core">
      <a href="https://noticias.ufal.br/estudante/noticias/2026/11/selecao-de-vagas">Leia mais</a>
      <a href="https://noticias.ufal.br/transparencia/noticias/2026/11/circuito-de-cinema">Leia mais</a>
      <a href="https://sistemas.ufal.br/cas/login">Entrar</a>
      <a href="/?b_start:int=30">Próxima</a>
    </div>
    """

    assert UrlDiscovery(UFAL).parse_urls(html, UFAL.page_url(0)) == {
        "https://noticias.ufal.br/estudante/noticias/2026/11/selecao-de-vagas",
        "https://noticias.ufal.br/transparencia/noticias/2026/11/circuito-de-cinema",
    }


def test_tls_context_completes_chains_with_bundled_intermediates() -> None:
    from university_news.shared.http_client import EXTRA_CERTIFICATES, tls_context

    subjects = {
        dict(item[0] for item in cert["subject"]).get("commonName")
        for cert in tls_context().get_ca_certs()
    }

    assert any(EXTRA_CERTIFICATES.glob("*.pem"))
    assert "RNP ICPEdu GR46 OV TLS CA 2025" in subjects


def test_usp_lists_through_the_wordpress_api() -> None:
    from datetime import date

    from university_news.sources.catalog import USP

    url = USP.wordpress_page_url("posts", 3, after=date(2026, 7, 10))

    assert url.startswith("https://jornal.usp.br/wp-json/wp/v2/posts?")
    assert "per_page=100" in url and "page=3" in url
    assert "after=2026-07-10T00%3A00%3A00" in url
    assert USP.wordpress_types == ("posts", "podcast")


async def test_wordpress_discovery_reads_links_and_detects_the_last_page() -> None:
    from university_news.sources.catalog import USP

    async def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        if page == 3:
            return httpx.Response(400, request=request, json={"code": "rest_post_invalid_page_number"})
        return httpx.Response(
            200,
            request=request,
            headers={"X-WP-TotalPages": "2"},
            json=[
                {"link": f"https://jornal.usp.br/ciencias/noticia-{page}/", "date": "2026-07-11T10:00:00"},
                {"link": "https://outro-site.br/fora/", "date": "2026-07-11T10:00:00"},
            ],
        )

    discovery = UrlDiscovery(USP, transport=httpx.MockTransport(handler), retry_multiplier=0)
    first = await discovery.discover_wordpress("posts", 1)
    last = await discovery.discover_wordpress("posts", 2)
    beyond = await discovery.discover_wordpress("posts", 3)

    assert first.urls == {"https://jornal.usp.br/ciencias/noticia-1/"}
    assert not first.exhausted
    assert last.exhausted
    assert beyond.exhausted and beyond.errors == []
