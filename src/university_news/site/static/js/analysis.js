// Analysis page: switch the scope (all, a region, or a university) and redraw
// the charts from the statistics embedded at build time. No server request.
(() => {
  const source = document.getElementById("analysis-data");
  if (!source) return;
  const scopes = JSON.parse(source.textContent);
  const regionSelect = document.getElementById("scope-region");
  const universitySelect = document.getElementById("scope-university");
  const showTables = document.getElementById("show-tables");
  const charts = document.getElementById("charts");
  const stats = document.getElementById("scope-stats");
  const keywords = document.getElementById("keywords");

  const number = (value) => Number(value).toLocaleString("pt-BR");
  const percent = (value, digits = 0) => `${(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: digits, minimumFractionDigits: digits })}%`;
  const coverage = (part, whole) => {
    if (!whole) return "—";
    const share = part / whole;
    const digits = share >= 0.01 || share === 0 ? 1 : share >= 0.0001 ? 2 : 3;
    return percent(share, digits);
  };
  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

  function chartHtml(chart) {
    const rows = chart.ordered ? chart.rows : chart.rows.filter((row) => row.count);
    const largest = Math.max(0, ...rows.map((row) => row.count));
    const wide = chart.key === "sdgs" ? " wide" : "";
    if (!largest) {
      return `<section class="chart${wide}"><h3 class="chart-title">${escapeHtml(chart.title)}</h3><p class="chart-note">${escapeHtml(chart.note)}</p><p class="chart-note">Sem dados no recorte.</p></section>`;
    }
    const bars = rows.map((row) => `
      <div class="bar-row${row.count ? "" : " zero"}" role="listitem" tabindex="0" aria-label="${escapeHtml(row.label)}: ${row.count} notícias (${percent(row.share)})">
        <span class="bar-label">${escapeHtml(row.label)}</span>
        <span class="bar-track"><span class="bar" style="--w:${((row.count / largest) * 100).toFixed(1)}%"></span></span>
        <span class="bar-value">${number(row.count)}</span>
        <span class="tip">${escapeHtml(row.label)} · ${number(row.count)} notícias · ${percent(row.share)}</span>
      </div>`).join("");
    const table = rows.map((row) => `<tr><td>${escapeHtml(row.label)}</td><td>${number(row.count)}</td></tr>`).join("");
    return `<section class="chart${wide}" data-chart="${chart.key}">
      <h3 class="chart-title">${escapeHtml(chart.title)}</h3><p class="chart-note">${escapeHtml(chart.note)} · <a href="/criterios/#${chart.key}">o que significa?</a></p>
      <div class="bars" role="list">${bars}</div>
      <div class="table-wrap${showTables.checked ? "" : " hidden"}" data-table><table class="data-table">
        <thead><tr><th>Categoria</th><th>Notícias</th></tr></thead><tbody>${table}</tbody></table></div>
    </section>`;
  }

  function render() {
    const university = universitySelect.value;
    const region = regionSelect.value;
    const scope = scopes[university ? `university:${university}` : region ? `region:${region}` : "all"];
    stats.innerHTML = `<div class="stat-strip" style="--stat-columns:4">
      ${[
        [number(scope.annotated), "notícias analisadas"],
        [coverage(scope.annotated, scope.articles), "do acervo no recorte"],
        [percent(scope.research_share), "relatam pesquisa"],
        [scope.models.length, "modelos usados"],
      ].map(([value, name]) => `<div class="stat"><div class="stat-value">${value}</div><div class="stat-label">${name}</div></div>`).join("")}
    </div>`;
    charts.innerHTML = scope.annotated
      ? scope.charts.map(chartHtml).join("")
      : `<div class="empty-state chart wide">Nenhuma notícia analisada neste recorte.</div>`;
    keywords.innerHTML = scope.keywords.map((row) => `<span class="chip">${escapeHtml(row.label)} <strong>${row.count}</strong></span>`).join("");
  }

  regionSelect.addEventListener("change", () => {
    const region = regionSelect.value;
    for (const option of universitySelect.options) {
      option.hidden = Boolean(region && option.dataset.region && option.dataset.region !== region);
    }
    if (universitySelect.selectedOptions[0]?.hidden) universitySelect.value = "";
    render();
  });
  universitySelect.addEventListener("change", render);
  showTables.addEventListener("change", () => {
    for (const table of document.querySelectorAll("[data-table]")) table.classList.toggle("hidden", !showTables.checked);
  });
})();
