from dataclasses import dataclass


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
    "JornaisNew/0.1"
)


@dataclass(frozen=True, slots=True)
class SiteConfig:
    name: str
    base_url: str
    listing_url: str
    paginated_url: str
    listing_selector: str
    start_page: int = 1
    end_page: int = 1
    page_step: int = 1
    rss_url: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    content_selector: str | None = None
    title_selector: str | None = None
    subtitle_selector: str | None = None
    author_selector: str | None = None
    date_selector: str | None = None
    tags_selector: str | None = None

    def page_url(self, page: int) -> str:
        if page < self.start_page:
            raise ValueError(f"A posicao deve ser maior ou igual a {self.start_page}")
        if page == self.start_page:
            return self.listing_url
        return self.paginated_url.format(page=page)

    def page_values(self, start: int, end: int) -> range:
        if start < self.start_page or end > self.end_page or end < start:
            raise ValueError(
                f"Faixa invalida para {self.name}: {self.start_page}..{self.end_page}"
            )
        if (start - self.start_page) % self.page_step != 0 or (
            end - self.start_page
        ) % self.page_step != 0:
            raise ValueError(
                f"Inicio e fim devem respeitar incrementos de {self.page_step}"
            )
        return range(start, end + 1, self.page_step)


USP = SiteConfig(
    name="usp",
    base_url="https://jornal.usp.br/",
    listing_url="https://jornal.usp.br/todas-as-noticias/",
    paginated_url="https://jornal.usp.br/todas-as-noticias/page/{page}/",
    listing_selector=".elementor-widget-archive-posts .elementor-post__title a",
    start_page=1,
    end_page=3579,
    rss_url=None,
    content_selector="main#main",
)

UFAL = SiteConfig(
    name="ufal",
    base_url="https://noticias.ufal.br/",
    listing_url="https://noticias.ufal.br/",
    paginated_url="https://noticias.ufal.br/?b_start:int={page}",
    listing_selector="#content-core a.titulo",
    start_page=0,
    end_page=23880,
    page_step=30,
    rss_url=None,
    content_selector="article#content",
    title_selector="article#content h1",
    author_selector="article#content strong.autor",
    date_selector="article#content time.data",
    tags_selector="article#content .keywords-viewlet a",
)

UNESP = SiteConfig(
    name="unesp",
    base_url="https://jornal.unesp.br/",
    listing_url="https://jornal.unesp.br/",
    paginated_url="https://jornal.unesp.br/page/{page}/",
    listing_selector=(
        "header.article-banner a.hover-opacity, header.article-subbanner > a[href], "
        ".col-12.col-lg-9.pr-lg-5 a.excerpt-title-link"
    ),
    start_page=1,
    end_page=136,
    page_step=1,
    rss_url=None,
    content_selector=(
        ".article-header, article.type-post, "
        ".col-12.col-lg-9.pr-lg-5 .article-date, "
        ".col-12.col-lg-9.pr-lg-5 .widget_tag_cloud"
    ),
    title_selector=".article-header h2",
    subtitle_selector=".article-header .linha-fina",
    author_selector="article.type-post > p:first-of-type",
    date_selector=".article-date p:first-of-type",
    tags_selector=(
        ".col-12.col-lg-9.pr-lg-5 "
        ".widget_tag_cloud nav[role='navigation'] a.tag-cloud-link"
    ),
)

UNB = SiteConfig(
    name="unb",
    base_url="https://noticias.unb.br/",
    listing_url="https://noticias.unb.br/",
    paginated_url="https://noticias.unb.br/?start={page}",
    listing_selector=".items-row .page-header h2 a[href]",
    start_page=0,
    end_page=2420,
    page_step=10,
    rss_url=None,
    content_selector=(
        ".item-page .chapeu, .item-page .page-header, "
        ".item-page > p:first-of-type, .item-page .article-info, "
        ".item-page [itemprop='articleBody'], .item-page .tags"
    ),
    title_selector=".item-page .page-header h2",
    subtitle_selector=".item-page > p:first-of-type",
    author_selector=".item-page .article-info .createdby",
    date_selector=".item-page .article-info .published time",
    tags_selector=".item-page .tags a.label",
)

UFMS = SiteConfig(
    name="ufms",
    base_url="https://www.ufms.br/",
    listing_url="https://www.ufms.br/category/noticias/",
    paginated_url="https://www.ufms.br/category/noticias/page/{page}/",
    listing_selector=".col-md-9 h2 a[href], .col-md-12 h2 a[href]",
    start_page=1,
    end_page=626,
    page_step=1,
    rss_url=None,
    content_selector=".post-inner-content",
    title_selector=".post-inner-content h1.entry-title",
    date_selector=".post-inner-content time.entry-date",
    tags_selector=".post-inner-content .cat-links a, .post-inner-content .tags-links a",
)

UFRGS = SiteConfig(
    name="ufrgs",
    base_url="https://www.ufrgs.br/",
    listing_url="https://www.ufrgs.br/site/noticias/",
    paginated_url="https://www.ufrgs.br/site/noticias/page/{page}/",
    listing_selector=".col-xl-9 a.title[href]",
    start_page=1,
    end_page=2667,
    page_step=1,
    rss_url=None,
    content_selector=(
        ".box-text-blog > h1, .box-text-blog > small.date, "
        ".box-text-blog > img.full-img-article, "
        ".box-text-blog > div:not(.breadcrumbs)"
    ),
    title_selector=".box-text-blog > h1",
    date_selector=".box-text-blog > small.date",
)

UFPA = SiteConfig(
    name="ufpa",
    base_url="https://ufpa.br/",
    listing_url="https://ufpa.br/?post_type=post&sf_paged=1",
    paginated_url="https://ufpa.br/?post_type=post&sf_paged={page}",
    listing_selector=(
        ".elementor-loop-container.elementor-grid "
        ".e-loop-item h1.elementor-heading-title a[href]"
    ),
    start_page=1,
    end_page=809,
    page_step=1,
    rss_url=None,
    content_selector=(
        ".elementor-location-single .elementor-element-27a903d, "
        ".elementor-location-single .elementor-element-8e06085, "
        ".elementor-location-single .elementor-element-a06f44d, "
        ".elementor-location-single .elementor-element-d4ffd3d, "
        ".elementor-location-single .elementor-element-d30b770, "
        ".elementor-location-single .elementor-element-664a8e87, "
        ".elementor-location-single .elementor-element-3627d6a"
    ),
    title_selector=(
        ".elementor-location-single .elementor-element-a06f44d "
        "h1.elementor-heading-title"
    ),
    author_selector=".elementor-location-single .elementor-element-3627d6a",
    date_selector=".elementor-location-single .elementor-element-d4ffd3d time",
    tags_selector=(
        ".elementor-location-single .elementor-element-27a903d "
        ".elementor-post-info__terms-list-item, "
        ".elementor-location-single .elementor-element-8e06085 "
        ".elementor-post-info__terms-list-item"
    ),
)

UFPR = SiteConfig(
    name="ufpr",
    base_url="https://ufpr.br/",
    listing_url="https://ufpr.br/todas-as-noticias/",
    paginated_url="https://ufpr.br/todas-as-noticias/page/{page}/",
    listing_selector=".blog-posts__container .blog-posts__text h2 a[href]",
    start_page=1,
    end_page=2170,
    page_step=1,
    rss_url=None,
    content_selector=".single__post__text",
    title_selector=".single__post__title h1",
    author_selector=(
        ".single__post__conteudo__info__icons:has(i.fa-user)"
    ),
    date_selector=(
        ".single__post__conteudo__info__icons:has(i.fa-calendar-week)"
    ),
    tags_selector=(
        ".single__post__conteudo__info__icons:has(i.fa-tags)"
    ),
)

UFRJ = SiteConfig(
    name="ufrj",
    base_url="https://conexao.ufrj.br/",
    listing_url="https://conexao.ufrj.br/todas-as-noticias/",
    paginated_url="https://conexao.ufrj.br/todas-as-noticias/page/{page}/",
    listing_selector="#site-content article h2.entry-title a[href]",
    start_page=1,
    end_page=1789,
    page_step=1,
    rss_url=None,
    content_selector="article .entry-content",
    title_selector="article .entry-title",
    subtitle_selector="article .intro-text",
    author_selector="article .post-author .meta-text a",
    date_selector="article .post-date .meta-text",
    tags_selector="article .entry-categories-inner a",
)

UERJ = SiteConfig(
    name="uerj",
    base_url="https://www.uerj.br/",
    listing_url="https://www.uerj.br/todas-as-noticias/",
    paginated_url="https://www.uerj.br/todas-as-noticias/page/{page}/",
    listing_selector="main#main a.ultimas-noticias__itemwrapper[href]",
    start_page=1,
    end_page=139,
    page_step=1,
    rss_url=None,
    content_selector="main#main .entry-content",
    title_selector="main#main .entry-title",
    author_selector="main#main .entry-autor",
    date_selector="main#main .entry-data",
)

UFABC = SiteConfig(
    name="ufabc",
    base_url="https://www.ufabc.edu.br/",
    listing_url="https://www.ufabc.edu.br/noticias",
    paginated_url="https://www.ufabc.edu.br/noticias?start={page}",
    listing_selector="form#adminForm .tileItem h2.tileHeadline a[href]",
    start_page=0,
    end_page=2080,
    page_step=10,
    rss_url=None,
    content_selector=".item-page",
    title_selector=".item-page .documentFirstHeading",
    author_selector=".item-page > p:last-of-type",
    date_selector=".item-page .documentPublished",
)

UFMG = SiteConfig(
    name="ufmg",
    base_url="https://www.ufmg.br/",
    listing_url=(
        "https://www.ufmg.br/comunicacao/noticias/?secao=todas-as-noticias"
    ),
    paginated_url=(
        "https://www.ufmg.br/comunicacao/noticias/?secao=todas-as-noticias&pagina={page}"
    ),
    listing_selector="main#primary > div.space-y-10.py-4 > a[href]",
    start_page=1,
    end_page=259,
    page_step=1,
    rss_url=None,
    content_selector="main#primary > div.space-y-8",
    title_selector="main#primary > div.space-y-4 h1:not(.sr-only)",
    subtitle_selector="main#primary > div.space-y-4 > p",
    author_selector="main#primary > div.space-y-4 > div.text-neutral-300 p",
    date_selector="main#primary > div.space-y-4 > div.text-neutral-300 time",
    tags_selector=(
        "main#primary > section a[href*='tag='], "
        "main#primary > section a.inline-block[href*='categoria=']"
    ),
)

UFLA = SiteConfig(
    name="ufla",
    base_url="https://ufla.br/",
    listing_url="https://ufla.br/noticias?start=0",
    paginated_url="https://ufla.br/noticias?start={page}",
    listing_selector=".blog .item h3 a[href]",
    start_page=0,
    end_page=4032,
    page_step=12,
    rss_url=None,
    content_selector="main#conteudo-principal .item-page",
    title_selector="main#conteudo-principal .item-page .documentFirstHeading",
    author_selector="main#conteudo-principal .item-page .documentAuthor strong",
    date_selector="main#conteudo-principal .item-page .documentPublished",
    tags_selector="main#conteudo-principal .item-page .documentCategory",
)

UNICAMP = SiteConfig(
    name="unicamp",
    base_url="https://jornal.unicamp.br/",
    listing_url="https://jornal.unicamp.br/noticias/",
    paginated_url="https://jornal.unicamp.br/noticias/pagina/{page}/",
    listing_selector="main#conteudo-principal a.post-cards__single-post__link[href]",
    start_page=1,
    end_page=59,
    page_step=1,
    rss_url=None,
    content_selector="main#conteudo-principal .single-post__post-content",
    title_selector="main#conteudo-principal .single-post__post-title__text",
    subtitle_selector="main#conteudo-principal .single-post__excerpt__text",
    author_selector=(
        "main#conteudo-principal .single-post__meta__author__name"
    ),
    date_selector="main#conteudo-principal .single-post__meta__date__date",
    tags_selector="main#conteudo-principal .single-post__tax__single",
)

UNIFESP = SiteConfig(
    name="unifesp",
    base_url="https://portal.unifesp.br/",
    listing_url="https://portal.unifesp.br/destaques?start=0",
    paginated_url="https://portal.unifesp.br/destaques?start={page}",
    listing_selector=(
        ".blog-items .blog-item .item-content .page-header h2 a[href]"
    ),
    start_page=0,
    end_page=432,
    page_step=4,
    rss_url=None,
    content_selector="main#g-main .com-content-article__body",
    title_selector="main#g-main .page-header h1",
    subtitle_selector="main#g-main .unifesp-article-introtext",
    date_selector="main#g-main .article-info .published time",
    tags_selector="main#g-main .article-info .category-name a",
)

UEM = SiteConfig(
    name="uem",
    base_url="https://www.noticias.uem.br/",
    listing_url="https://www.noticias.uem.br/index.php?limitstart=0",
    paginated_url="https://www.noticias.uem.br/index.php?limitstart={page}",
    listing_selector=(
        "#t3-content h2 a[href*='option=com_content'][href*='view=article']"
    ),
    start_page=0,
    end_page=345,
    page_step=15,
    rss_url=None,
    content_selector="#t3-content article.article-content",
    title_selector="#t3-content .article-title",
    subtitle_selector="#t3-content blockquote.article-intro",
    author_selector="#t3-content .article-info .createdby span[itemprop='name']",
    date_selector="#t3-content .article-info .published time",
    tags_selector="#t3-content .tags a.label",
)

FURG = SiteConfig(
    name="furg",
    base_url="https://www.furg.br/",
    listing_url="https://www.furg.br/noticias?start=0",
    paginated_url="https://www.furg.br/noticias?start={page}",
    listing_selector=(
        ".pagina.pagina__titulo--sem_linha.category-list "
        "h2 a[href*='/noticias/']"
    ),
    start_page=0,
    end_page=25780,
    page_step=20,
    rss_url=None,
    content_selector="article.pagina .pagina__conteudo",
    title_selector="article.pagina .noticia__titulo",
    subtitle_selector="article.pagina .noticia__linha-apoio",
    author_selector="article.pagina .noticia__meta-autor span[itemprop='name']",
    date_selector="article.pagina .noticia__meta-dados time",
    tags_selector="article.pagina .tags__item",
)

UFC = SiteConfig(
    name="ufc",
    base_url="https://agencia.ufc.br/",
    listing_url="https://agencia.ufc.br/",
    paginated_url="https://agencia.ufc.br/page/{page}/",
    listing_selector=(
        ".main .slide-item > a[href], .main .card .content .title a[href]"
    ),
    start_page=1,
    end_page=56,
    page_step=1,
    rss_url=None,
    content_selector=".article .post-text",
    title_selector=".article .header h1",
    subtitle_selector=".article .header > p",
    author_selector=".article .info .author",
    date_selector=".article .info .date",
    tags_selector=".article .header > a",
)

SITES: dict[str, SiteConfig] = {
    site.name: site
    for site in (
        USP,
        UFAL,
        UNESP,
        UNB,
        UFMS,
        UFRGS,
        UFPA,
        UFPR,
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
    )
}
