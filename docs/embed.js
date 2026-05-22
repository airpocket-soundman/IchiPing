/* IchiPing — shared shell-iframe integration.
 *
 * Two responsibilities:
 *
 *  (1) Inside the iframe (loaded by index.html shell) — add the `embedded`
 *      class to <html> so style.css can hide our own sidebar and avoid the
 *      double-nav problem.
 *
 *  (2) Standalone (the user opened this page directly) — REDIRECT to the
 *      shell URL `<root>/index.html?p=<this-page-from-root>` so the user
 *      gets the single constant sidebar of the SPA shell. The shell reads
 *      the `p` query parameter and loads this page into its iframe.
 *
 * Together they guarantee: no matter which file the user opens, the
 * resulting view has one fixed sidebar (the shell's) on the left and the
 * requested page in the right pane.
 *
 * Locating the root: embed.js always lives at `<root>/docs/embed.js`, so
 * we can derive the root URL by stripping `/docs/embed.js` from this
 * script's absolute src.
 */
(function () {
  /* --- shared: load highlight.js for code syntax colouring -------------- */
  function applySyntaxHighlight() {
    var apply = function () {
      if (!window.hljs) return;
      try { window.hljs.highlightAll(); } catch (e) { /* idempotent */ }
    };
    if (window.hljs) { apply(); return; }
    // Inject the lib once. CDN, ~30 KB. Theme is defined inline in style.css
    // so we only need the JS engine.
    var s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js";
    s.async = true;
    s.onload = apply;
    s.onerror = function () { /* offline / blocked CDN — fall back to plain pre */ };
    document.head.appendChild(s);
  }
  function whenReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else { fn(); }
  }

  /* --- Case 1: inside iframe → hide our own chrome, syntax-colour, stop --- */
  if (window.self !== window.top) {
    document.documentElement.classList.add("embedded");
    whenReady(applySyntaxHighlight);
    return;
  }

  /* --- Case 2: standalone → redirect to shell --- */

  /* Find the <script> element that loaded this file so we can read its src.
   * Prefer document.currentScript (modern); fall back to scanning. */
  var scriptEl = document.currentScript;
  if (!scriptEl) {
    var scripts = document.getElementsByTagName("script");
    for (var i = 0; i < scripts.length; i++) {
      var srcAttr = scripts[i].getAttribute("src") || "";
      if (/(^|\/)embed\.js(?:[?#]|$)/.test(srcAttr)) {
        scriptEl = scripts[i];
        break;
      }
    }
  }
  if (!scriptEl) return;

  /* scriptEl.src is the resolved absolute URL of embed.js.
   * Strip `/docs/embed.js` to get the project root URL. */
  var embedAbs = scriptEl.src;
  if (!/\/docs\/embed\.js(?:[?#]|$)/.test(embedAbs)) return;
  var root = embedAbs.replace(/\/docs\/embed\.js[?#]?.*$/, "/");

  /* Compute this page's path from root (e.g. "docs/probe_sound.html"). */
  var pageUrl = window.location.href;
  var pageNoHash = pageUrl.replace(/[#?].*$/, "");
  if (pageNoHash.indexOf(root) !== 0) return;        /* sanity */
  var pageFromRoot = pageNoHash.substring(root.length);
  if (!pageFromRoot) return;                          /* already at root */
  if (pageFromRoot === "index.html") return;          /* don't loop into self */

  /* Preserve any hash so anchor links survive the redirect. */
  var hash = window.location.hash || "";
  var shellUrl = root + "index.html?p=" + encodeURIComponent(pageFromRoot) + hash;
  window.location.replace(shellUrl);
})();
