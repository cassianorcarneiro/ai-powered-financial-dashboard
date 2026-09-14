/* =============================================================================
   AI POWERED FINANCIAL DASHBOARD
   Keeps each chart's width matched to its container, without touching height.

   Earlier version of this file called Plotly.Plots.resize(gd), which measures
   the container and can recompute BOTH width and height from it. Direct
   measurement showed that recomputation returning height 0 for bar charts
   (Cartesian: x/y axes) while leaving pie charts (no axes) correctly sized,
   even though every figure sets an explicit `height` in charts.py. autosize is
   now off in charts.py so nothing else can touch height either; this file
   calls Plotly.relayout with only a `width` key, which changes exclusively
   that property and leaves layout.height untouched.
   ============================================================================= */

(function () {
  "use strict";

  function syncWidths() {
    document.querySelectorAll(".js-plotly-plot").forEach(function (gd) {
      if (!window.Plotly || !gd.data || !gd.parentElement) return;
      var width = gd.parentElement.clientWidth;
      if (width > 0 && width !== gd._fullLayout?.width) {
        try {
          window.Plotly.relayout(gd, { width: width });
        } catch (e) {
          /* Not ready yet; the next scheduled pass will catch it. */
        }
      }
    });
  }

  // Coalesces bursts of triggers (a window resize firing repeatedly while the
  // user drags, or several DOM mutations from one Dash re-render) into a
  // single pass after things settle.
  var pending = null;
  function scheduleSync() {
    clearTimeout(pending);
    pending = setTimeout(syncWidths, 150);
  }

  // Covers opening the dashboard: Plotly's first measurement can race Dash's
  // own layout hydration, so re-measure once the page has fully loaded.
  window.addEventListener("load", scheduleSync);

  // Covers crossing a Bootstrap breakpoint: a column width change needs a
  // fresh measurement to redraw at the right width.
  window.addEventListener("resize", scheduleSync);

  // Covers a chart appearing after a Dash callback re-render — unlocking the
  // dashboard, the first data refresh. Scoped to the app's own root so it
  // doesn't react to unrelated browser-extension DOM changes.
  var root = document.getElementById("react-entry-point") || document.body;
  new MutationObserver(scheduleSync).observe(root, {
    childList: true,
    subtree: true,
  });
})();
