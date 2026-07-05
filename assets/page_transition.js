window.dash_clientside = window.dash_clientside || {};
window.dash_clientside.pageTransition = {
    animatePageChange: function (macroStyle, dashStyle, marketsStyle) {
        function isVis(s) { return !s || s.display !== 'none'; }

        function animIn(el, dx) {
            if (!el) return;
            el.style.transition = 'none';
            el.style.opacity    = '0';
            el.style.transform  = 'translateX(' + dx + 'px)';
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    el.style.transition = 'opacity 0.28s ease, transform 0.28s ease';
                    el.style.opacity    = '1';
                    el.style.transform  = 'translateX(0)';
                });
            });
        }

        var macroNow   = isVis(macroStyle);
        var dashNow    = isVis(dashStyle);
        var marketsNow = isVis(marketsStyle);

        // First fire is the initial page load — record state, skip animation.
        if (!window._pgTransInit) {
            window._pgTransInit  = true;
            window._macroWasVis  = macroNow;
            window._dashWasVis   = dashNow;
            window._marketsWasVis = marketsNow;
            return '';
        }

        // Macro becoming visible → slides in from the right.
        if (macroNow && !window._macroWasVis)
            animIn(document.getElementById('macro-page'), 28);

        // Dashboard becoming visible → slides in from the left.
        if (dashNow && !window._dashWasVis)
            animIn(document.getElementById('dashboard-page'), -28);

        // Markets becoming visible → slides in from the right (right of Macro).
        if (marketsNow && !window._marketsWasVis)
            animIn(document.getElementById('markets-page'), 28);

        window._macroWasVis   = macroNow;
        window._dashWasVis    = dashNow;
        window._marketsWasVis = marketsNow;
        return '';
    }
};
