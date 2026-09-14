/* =============================================================================
   AI POWERED FINANCIAL DASHBOARD
   Forces Plotly to re-measure its charts after the DOM has actually settled.

   Bar charts (Cartesian: x/y axes, ticks, gridlines) occasionally render blank
   if their container is measured at zero or transitional width — during the
   page's first paint, or while Bootstrap reflows columns across a breakpoint.
   Pie charts survive the same moment because they don't depend on axis tick
   layout. `config.responsive: true` on each dcc.Graph (layout.py) is supposed
   to cover this on its own via Plotly's internal ResizeObserver, but a bad
   first measurement can leave a Cartesian plot in a state that a later resize
   event doesn't recover from — only an explicit, later re-measurement does,
   which is why reloading the page (a fresh first paint) fixes it.

   This does not touch chart data or the Dash callback graph — it only asks
   already-rendered Plotly figures to re-measure their container.
   ============================================================================= */

(function () {
  "use strict";

  function resizeAllPlots() {
    document.querySelectorAll(".js-plotly-plot").forEach(function (gd) {
      // `gd.data` only exists once Plotly has actually initialized that div;
      // calling resize before that throws.
      if (window.Plotly && gd.data) {
        try {
          window.Plotly.Plots.resize(gd);
        } catch (e) {
          /* Not ready yet; the next scheduled pass will catch it. */
        }
      }
    });
  }

  // Coalesces bursts of triggers (a window resize firing repeatedly while the
  // user drags, or several DOM mutations from one Dash re-render) into a
  // single resize pass after things settle.
  var pending = null;
  function scheduleResize() {
    clearTimeout(pending);
    pending = setTimeout(resizeAllPlots, 150);
  }

  // Covers opening the dashboard: Plotly's first measurement can race Dash's
  // own layout hydration, so re-measure once the page has fully loaded.
  window.addEventListener("load", scheduleResize);

  // Covers crossing a Bootstrap breakpoint: charts.py pins figure height but
  // leaves width to the container, so a column width change needs a fresh
  // measurement to redraw correctly.
  window.addEventListener("resize", scheduleResize);

  // Covers a chart appearing after a Dash callback re-render — unlocking the
  // dashboard, the first data refresh — cases responsive's own ResizeObserver
  // does not reliably catch if that render's first measurement was at zero
  // width. Scoped to the app's own root so it doesn't react to unrelated
  // browser-extension DOM changes.
  var root = document.getElementById("react-entry-point") || document.body;
  new MutationObserver(scheduleResize).observe(root, {
    childList: true,
    subtree: true,
  });
})();
