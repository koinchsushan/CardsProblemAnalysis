// Main JavaScript Utilities for Flask Card Analysis Application

/**
 * Show loading spinner in an element
 */
function showLoading(element) {
    element.innerHTML = ''
        + '<div class="loading">'
        + '    <div class="spinner"></div>'
        + '    <p>Loading...</p>'
        + '</div>';
    element.style.display = 'block';
}

/**
 * Hide an element
 */
function hideElement(element) {
    element.style.display = 'none';
}

/**
 * Show an element
 */
function showElement(element) {
    element.style.display = 'block';
}

/**
 * Fetch JSON data from API
 */
async function fetchJSON(url) {
    try {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error('HTTP error! status: ' + response.status);
        }
        return await response.json();
    } catch (error) {
        console.error('Fetch error:', error);
        throw error;
    }
}

/**
 * Show error message in element
 */
function showError(element, message) {
    element.innerHTML = ''
        + '<div class="error-message" style="padding: 2rem; text-align: center; color: #f44336;">'
        + '    <p style="font-size: 1.2rem;">\u26A0\uFE0F ' + message + '</p>'
        + '</div>';
    element.style.display = 'block';
}

/**
 * Format trial label for display
 */
function formatTrialLabel(trial) {
    return 'Participant ' + trial.participant + ', Trial ' + trial.trial + ' (' + trial.moves + ' moves)';
}

/**
 * Clear element content
 */
function clearElement(element) {
    element.innerHTML = '';
}

/**
 * Create a DOM element with attributes
 */
function createElement(tag, attributes, content) {
    attributes = attributes || {};
    content = content || '';
    var element = document.createElement(tag);

    for (var key in attributes) {
        if (key === 'className') {
            element.className = attributes[key];
        } else {
            element.setAttribute(key, attributes[key]);
        }
    }

    if (content) {
        element.innerHTML = content;
    }

    return element;
}

function initBackToTopButton() {
    var button = document.getElementById('back-to-top');
    if (!button) { return; }

    var reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    var scrollThreshold = 240;

    var updateVisibility = function() {
        button.hidden = window.scrollY < scrollThreshold;
    };

    var scrollToTop = function() {
        window.scrollTo({
            top: 0,
            behavior: reducedMotionQuery.matches ? 'auto' : 'smooth'
        });
    };

    button.addEventListener('click', scrollToTop);
    window.addEventListener('scroll', updateVisibility, { passive: true });
    updateVisibility();
}

if (typeof window !== 'undefined') {
    document.addEventListener('DOMContentLoaded', function() {
        initBackToTopButton();
        initNavbarHoverDropdowns();
        initThemeToggle();
    });
}

/**
 * Navbar dropdowns: open on hover, close on mouse leave, close on link click.
 * Uses CSS transitions for smooth open/close animations.
 */
function initNavbarHoverDropdowns() {
    var HOVER_DELAY = 200;
    var CLOSE_DELAY = 150;
    var ANIM_OUT = 140;  // match CSS transition duration
    var timers = {};

    var dropdowns = document.querySelectorAll('.nav-menu__dropdown details, .nav-menu__upload details');

    /** Close a details element with animation: add closing class to trigger
     *  CSS transition, keep 'open' so the panel stays visible during the
     *  animation, then remove 'open' + class after the transition completes. */
    function smoothClose(details) {
        var closingClass = details.classList.contains('nav-dropdown')
            ? 'nav-dropdown--closing'
            : 'nav-upload-widget--closing';

        clearTimeout(details._closeTimer);
        if (!details.hasAttribute('open')) {
            details.classList.remove(closingClass);
            return;
        }

        details.classList.add(closingClass);
        // Keep `open` during the short CSS fade, then fully close it.
        details._closeTimer = setTimeout(function() {
            details.removeAttribute('open');
            details.classList.remove(closingClass);
        }, ANIM_OUT);
    }

    function closeAll() {
        dropdowns.forEach(function(details) {
            clearTimeout(timers[details._bcpIdx]);
            smoothClose(details);
        });
    }

    function closeAllExcept(keep) {
        dropdowns.forEach(function(d) {
            if (d !== keep) {
                clearTimeout(timers[d._bcpIdx]);
                smoothClose(d);
            }
        });
    }

    dropdowns.forEach(function(details, idx) {
        details._bcpIdx = idx;

        // If native <details> toggling reopens a menu during a fade-out,
        // cancel the old close timer so it cannot unexpectedly close again.
        details.addEventListener('toggle', function() {
            if (details.hasAttribute('open')) {
                clearTimeout(details._closeTimer);
                details.classList.remove(details.classList.contains('nav-dropdown')
                    ? 'nav-dropdown--closing'
                    : 'nav-upload-widget--closing');
            }
        });

        // Hover to open — close all others first, then animate this one in
        details.addEventListener('mouseenter', function() {
            clearTimeout(timers[idx]);
            clearTimeout(details._closeTimer);

            // Cancel any pending close on this dropdown
            var closingClass = details.classList.contains('nav-dropdown')
                ? 'nav-dropdown--closing'
                : 'nav-upload-widget--closing';
            details.classList.remove(closingClass);

            timers[idx] = setTimeout(function() {
                closeAllExcept(details);
                details.setAttribute('open', '');
            }, HOVER_DELAY);
        });

        // Mouse leave closes THIS dropdown smoothly after a short delay
        details.addEventListener('mouseleave', function() {
            clearTimeout(timers[idx]);
            timers[idx] = setTimeout(function() {
                smoothClose(details);
            }, CLOSE_DELAY);
        });

        // Click on a link inside the panel closes the dropdown smoothly.
        var links = details.querySelectorAll('.nav-dropdown-panel a, .nav-upload-panel a');
        links.forEach(function(link) {
            link.addEventListener('click', function() {
                clearTimeout(timers[idx]);
                closeAll();
            });
        });
    });

    // Secondary-nav links and ordinary page links should never leave a
    // primary dropdown visually open while navigation is starting.
    document.querySelectorAll('.sub-nav a, .nav-menu > li > a, .brand-link').forEach(function(link) {
        link.addEventListener('click', function() {
            closeAll();
        });
    });

    document.querySelectorAll('.nav-upload-panel button').forEach(function(button) {
        button.addEventListener('click', function() {
            closeAll();
        });
    });

    // Escape, outside clicks, and focus moving away from the navbar all close
    // the menus without requiring an extra click from the user.
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            closeAll();
            var openToggle = document.querySelector('.nav-dropdown[open] summary, .nav-upload-widget[open] summary');
            if (openToggle) openToggle.focus();
        }
    });

    document.addEventListener('click', function(event) {
        if (!event.target.closest('.navbar')) closeAll();
    });

    document.addEventListener('focusin', function(event) {
        if (!event.target.closest('.navbar')) closeAll();
    });
}

/**
 * Theme toggle — dark/light mode with localStorage persistence.
 * Respects prefers-color-scheme on first visit, then remembers user choice.
 */
function initThemeToggle() {
    var STORAGE_KEY = 'app-theme';
    var toggle = document.getElementById('themeToggle');
    var html = document.documentElement;
    if (!toggle || !html) { return; }

    function resolveTheme() {
        var saved = localStorage.getItem(STORAGE_KEY);
        if (saved === 'dark' || saved === 'light') { return saved; }
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return 'dark';
        }
        return 'light';
    }

    function applyTheme(theme) {
        html.setAttribute('data-theme', theme);
        toggle.textContent = theme === 'dark' ? '\uD83C\uDF19' : '\u2600\uFE0F';
        toggle.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    }

    function broadcastTheme(theme) {
        document.querySelectorAll('iframe').forEach(function(iframe) {
            try {
                if (iframe.contentWindow) {
                    iframe.contentWindow.postMessage({ type: 'card-theme', value: theme }, '*');
                }
            } catch (error) { /* iframe may be unavailable during navigation */ }
        });
    }

    function setTheme(theme) {
        localStorage.setItem(STORAGE_KEY, theme);
        applyTheme(theme);
        broadcastTheme(theme);
    }

    var initialTheme = resolveTheme();
    applyTheme(initialTheme);
    broadcastTheme(initialTheme);

    document.querySelectorAll('iframe').forEach(function(iframe) {
        iframe.addEventListener('load', function() {
            broadcastTheme(html.getAttribute('data-theme') || initialTheme);
        });
    });

    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
            if (!localStorage.getItem(STORAGE_KEY)) {
                var systemTheme = e.matches ? 'dark' : 'light';
                applyTheme(systemTheme);
                broadcastTheme(systemTheme);
            }
        });
    }

    toggle.addEventListener('click', function() {
        var current = html.getAttribute('data-theme');
        setTheme(current === 'dark' ? 'light' : 'dark');
    });
}

// Export functions if using modules (optional)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        showLoading: showLoading,
        hideElement: hideElement,
        showElement: showElement,
        fetchJSON: fetchJSON,
        showError: showError,
        formatTrialLabel: formatTrialLabel,
        clearElement: clearElement,
        createElement: createElement,
        initBackToTopButton: initBackToTopButton
    };
}
