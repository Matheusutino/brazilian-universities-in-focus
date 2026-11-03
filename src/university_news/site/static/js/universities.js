// Universities page: filter the cards by region.
(() => {
  const buttons = document.querySelectorAll("#region-filter button");
  const cards = document.querySelectorAll(".uni-card");
  for (const button of buttons) {
    button.addEventListener("click", () => {
      const region = button.dataset.region;
      for (const other of buttons) other.setAttribute("aria-pressed", String(other === button));
      for (const card of cards) card.hidden = Boolean(region && card.dataset.region !== region);
    });
  }
})();
