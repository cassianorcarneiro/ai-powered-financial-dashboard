/* =============================================================================
   AI POWERED FINANCIAL DASHBOARD
   Keeps each chart sized correctly: width matched to its container, height
   corrected back to the fixed value charts.py uses whenever it comes out wrong.

   Measurement (via the browser console, not guessed) showed that Cartesian
   charts (bar: x/y axes) can render with a computed height of 0 on the very
   first paint, before any JS in this file runs — while pie charts (no axes)
   render correctly every time. This happens with `autosize` off and no
   Plotly.Plots.resize() call anywhere, so it isn't something a resize handler
   is causing; whatever produces it happens inside Plotly's own first render
   for a Cartesian subplot. Rather than prevent that first-paint state (not
   fully understood), this corrects it: any chart found at height 0 is
   explicitly relaid out back to the known-correct height.

   FIGURE_HEIGHT below must match charts.py's constant of the same name.
   ============================================================================= */

(function () {
  "use strict";

  var FIGURE_HEIGHT = 400;

  function syncSizes() {
    document.querySelectorAll(".js-plotly-plot").forEach(function (gd) {
      if (!window.Plotly || !gd.data || !gd.parentElement) return;

      var width = gd.parentElement.clientWidth;
      var update = {};

      if (width > 0 && width !== gd._fullLayout?.width) {
        update.width = width;
      }
      // The actual bug this file exists for: a Cartesian chart's rendered
      // height collapsing to 0 independent of anything this script does.
      // Forcing it back to the fixed height is a correction, not a guess at
      // the underlying cause.
      if (gd.clientHeight === 0) {
        update.height = FIGURE_HEIGHT;
      }

      if (Object.keys(update).length > 0) {
        try {
          window.Plotly.relayout(gd, update);
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
    pending = setTimeout(syncSizes, 150);
  }

  window.addEventListener("load", scheduleSync);
  window.addEventListener("resize", scheduleSync);

  var root = document.getElementById("react-entry-point") || document.body;
  new MutationObserver(scheduleSync).observe(root, {
    childList: true,
    subtree: true,
  });

  // A single chart collapsing to height 0 doesn't always touch the DOM again
  // afterward (no further mutation to trigger the MutationObserver above), so
  // a plain interval catches it independent of any other event firing. Runs
  // only twice, shortly after load, rather than forever.
  var attempts = 0;
  var poll = setInterval(function () {
    syncSizes();
    attempts += 1;
    if (attempts >= 2) clearInterval(poll);
  }, 500);
})();
