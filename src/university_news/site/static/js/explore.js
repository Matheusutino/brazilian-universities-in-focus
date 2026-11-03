// Explore page: search, filters, and pagination over /api/articles.
// The URL holds the whole state, so every search can be shared or bookmarked.
(() => {
  const form = document.getElementById("explore-form");
  const results = document.getElementById("results");
  const title = document.getElementById("results-title");
  const note = document.getElementById("results-note");
  const status = document.getElementById("status-line");
  const chips = document.getElementById("summary-chips");
  const previous = document.getElementById("previous-page");
  const next = document.getElementById("next-page");
  const pageLabel = document.getElementById("page-label");
  const labels = JSON.parse(document.getElementById("labels-data").textContent);
  const FIELDS = ["q", "region", "university", "from", "to", "category", "field", "type", "analyzed"];
  const REGION_SLUGS = { "Norte": "norte", "Nordeste": "nordeste", "Centro-Oeste": "centro-oeste", "Sudeste": "sudeste", "Sul": "sul" };
  let page = 1;
  let controller = null;

  const number = (value) => Number(value).toLocaleString("pt-BR");
  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const label = (group, value) => (labels[group] && labels[group][value]) || value;
  const dateBr = (iso) => (iso ? iso.split("-").reverse().join(".") : "Sem data");
  const truncate = (text, size) => (text && text.length > size ? text.slice(0, size - 1).replace(/\s+\S*$/, "") + "…" : text || "");

  function readUrl() {
    const params = new URLSearchParams(location.search);
    for (const name of FIELDS) {
      const input = form.elements[name];
      if (!input) continue;
      if (input.type === "checkbox") input.checked = params.get(name) === "1";
      else input.value = params.get(name) || "";
    }
    page = Math.max(1, Number(params.get("page")) || 1);
    if ([...params.keys()].some((key) => key !== "q" && key !== "page")) form.querySelector("details").open = true;
  }

  function stateParams() {
    const params = new URLSearchParams();
    for (const name of FIELDS) {
      const input = form.elements[name];
      if (!input) continue;
      const value = input.type === "checkbox" ? (input.checked ? "1" : "") : input.value.trim();
      if (value) params.set(name, value);
    }
    if (page > 1) params.set("page", String(page));
    return params;
  }

  function cardHtml(article, rank) {
    const analysis = article.analysis;
    const tags = analysis
      ? [analysis.category && label("news_category", analysis.category), analysis.fields[0] && label("scientific_fields", analysis.fields[0])]
          .filter(Boolean)
          .map((tag) => `<span class="mini-tag">${escapeHtml(tag)}</span>`)
          .join("")
      : "";
    return `<article class="news-card">
      ${rank ? `<span class="rank">${rank}</span>` : ""}
      <div class="card-meta"><span class="tag tag-${REGION_SLUGS[article.region] || "none"}">${escapeHtml(article.journal)}</span>
        <span class="card-region">${escapeHtml(article.region)}</span><time datetime="${escapeHtml(article.date || "")}">${dateBr(article.date)}</time></div>
      <h3><a href="/noticia/${encodeURIComponent(article.id)}">${escapeHtml(article.title)}</a></h3>
      ${article.subtitle ? `<p>${escapeHtml(truncate(article.subtitle, 240))}</p>` : ""}
      ${tags ? `<div class="card-analysis">${tags}</div>` : ""}
      <div class="card-foot"><span class="byline">${article.author ? "Por " + escapeHtml(truncate(article.author, 55)) : "Fonte oficial da universidade"}</span>
        <a class="card-cta" href="${escapeHtml(article.url)}" target="_blank" rel="noopener noreferrer">Ler na fonte ↗</a></div>
    </article>`;
  }

  function renderChips(params) {
    const items = [];
    if (params.get("region")) items.push(params.get("region"));
    if (params.get("university")) items.push(params.get("university"));
    if (params.get("from") || params.get("to")) {
      const bound = (name) => (params.get(name) ? dateBr(params.get(name)) : "…");
      items.push(`${bound("from")} – ${bound("to")}`);
    }
    if (params.get("category")) items.push(label("news_category", params.get("category")));
    if (params.get("field")) items.push(label("scientific_fields", params.get("field")));
    if (params.get("type")) items.push(label("content_type", params.get("type")));
    if (params.get("analyzed")) items.push("só analisadas");
    chips.innerHTML = items.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("");
  }

  async function fetchPage(params, signal) {
    const response = await fetch(`/api/articles?${params}`, { signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function load({ push = false } = {}) {
    const params = stateParams();
    if (push) history.pushState(null, "", params.toString() ? `?${params}` : location.pathname);
    renderChips(params);
    const query = params.get("q");
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    results.classList.add("loading");
    status.textContent = query ? "Buscando…" : "";
    try {
      if (!query) {
        render(await fetchPage(params, signal), false);
        return;
      }
      // Progressive search: word matches arrive in milliseconds; the hybrid
      // ranking (which embeds the query) replaces them when it is ready.
      const lexicalParams = new URLSearchParams(params);
      lexicalParams.set("mode", "lexical");
      let finished = false;
      const hybrid = fetchPage(params, signal).then((data) => {
        finished = true;
        render(data, true);
      });
      fetchPage(lexicalParams, signal)
        .then((data) => {
          if (!finished) {
            render(data, true);
            status.textContent = "Resultados por palavras · refinando por significado…";
            results.classList.add("loading");
          }
        })
        .catch(() => {});
      await hybrid;
    } catch (error) {
      if (error.name === "AbortError") return;
      status.textContent = "Não foi possível carregar as notícias. Tente novamente em instantes.";
    } finally {
      if (!signal.aborted) results.classList.remove("loading");
    }
  }

  function render(data, ranked) {
    const query = new URLSearchParams(location.search).get("q") || form.elements.q.value;
    title.textContent = ranked ? `Resultados para “${query}”` : "Últimas notícias";
    note.textContent = `${number(data.total)} ${ranked ? "mais relevantes no recorte" : "publicações no recorte"}`;
    status.textContent = ranked
      ? `${data.mode === "hybrid" ? "Busca por significado + palavras" : "Busca por palavras"} · ${data.elapsed_ms} ms`
      : "";
    results.className = ranked ? "card-list" : "card-grid";
    results.innerHTML = data.results.length
      ? data.results.map((article, index) => cardHtml(article, ranked ? (data.page - 1) * data.page_size + index + 1 : null)).join("")
      : `<div class="empty-state">Nenhuma notícia encontrada. Tente um termo mais amplo ou ajuste os filtros.</div>`;
    pageLabel.innerHTML = data.pages ? `Página <strong>${number(data.page)}</strong> de ${number(data.pages)}` : "";
    previous.disabled = data.page <= 1;
    next.disabled = data.page >= data.pages;
    results.classList.remove("loading");
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    page = 1;
    load({ push: true });
  });
  form.addEventListener("reset", () => {
    setTimeout(() => {
      page = 1;
      load({ push: true });
    });
  });
  form.elements.region.addEventListener("change", () => {
    const region = form.elements.region.value;
    for (const option of form.elements.university.options) {
      option.hidden = Boolean(region && option.dataset.region && option.dataset.region !== region);
    }
    if (form.elements.university.selectedOptions[0]?.hidden) form.elements.university.value = "";
  });
  previous.addEventListener("click", () => { page -= 1; load({ push: true }); scrollTo({ top: 0 }); });
  next.addEventListener("click", () => { page += 1; load({ push: true }); scrollTo({ top: 0 }); });
  addEventListener("popstate", () => { readUrl(); load(); });

  readUrl();
  load();
})();
