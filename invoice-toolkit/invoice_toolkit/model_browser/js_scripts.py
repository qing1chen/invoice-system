"""
注入到浏览器的 JavaScript 脚本集合

包含：
- 事件监听器追踪（monkey-patch addEventListener）
- DOM 交互元素提取（Shadow DOM 穿透 / 遮挡检测 / 视口排序）
- 标注覆盖层（fixed 定位，不破坏原布局）
"""

# ── 事件监听器追踪：在页面加载前注入，monkey-patch addEventListener ──

INIT_EVENT_TRACKER = """
(() => {
    if (window.__agentEventMap) return;
    window.__agentEventMap = new WeakMap();

    const orig = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(type, listener, options) {
        if (this instanceof Element || this instanceof Document) {
            if (!window.__agentEventMap.has(this)) {
                window.__agentEventMap.set(this, new Set());
            }
            window.__agentEventMap.get(this).add(type);
        }
        return orig.call(this, type, listener, options);
    };

    const origRemove = EventTarget.prototype.removeEventListener;
    EventTarget.prototype.removeEventListener = function(type, listener, options) {
        return origRemove.call(this, type, listener, options);
    };
})();
"""


# ── DOM 交互元素提取器 ──
# 功能：Shadow DOM 穿透 / elementFromPoint 遮挡检测 /
#       事件追踪检测 / 视口感知排序 / 模态弹窗过滤

EXTRACT_DOM = """
() => {
    const interactable = [];
    const seenNodes = new WeakSet();
    const seenKeys = new Set();

    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const scrollY = window.scrollY || document.documentElement.scrollTop || 0;
    const scrollX = window.scrollX || document.documentElement.scrollLeft || 0;
    const docHeight = Math.max(
        document.body?.scrollHeight || 0,
        document.documentElement?.scrollHeight || 0
    );
    const pagesAbove = Math.round((scrollY / vh) * 10) / 10;
    const pagesBelow = Math.round(((docHeight - scrollY - vh) / vh) * 10) / 10;

    const INTERACTIVE_SELECTORS = [
        'a[href]', 'button', 'input', 'textarea', 'select',
        '[role="button"]', '[role="link"]', '[role="tab"]',
        '[role="menuitem"]', '[role="checkbox"]', '[role="radio"]',
        '[role="switch"]', '[role="option"]', '[role="combobox"]',
        '[onclick]', '[tabindex]', '[ng-click]', '[v-on\\\\:click]', '[\\\\@click]',
        '[data-click]', '[data-action]', '[data-href]', '[data-target]',
        'label[for]', 'summary', 'details',
    ];

    // ── 模态弹窗检测 ──
    function findActiveDialog() {
        const candidates = Array.from(document.querySelectorAll(
            '[role="dialog"], .ui-dialog, .modal.show, .modal.in, dialog[open], [aria-modal="true"]'
        ));
        let topmost = null;
        let topZ = -1;
        for (const dlg of candidates) {
            try {
                const style = window.getComputedStyle(dlg);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                if (dlg.getAttribute('aria-hidden') === 'true') continue;
                const rect = dlg.getBoundingClientRect();
                if (rect.width < 50 || rect.height < 50) continue;
                if (rect.bottom < 0 || rect.top > vh) continue;
                const z = parseInt(style.zIndex) || 0;
                if (z >= topZ) { topZ = z; topmost = dlg; }
            } catch (e) {}
        }
        return topmost;
    }

    function detectOverlay() {
        const overlays = document.querySelectorAll(
            '.ui-widget-overlay, .modal-backdrop, .blockUI.blockOverlay, ' +
            '[class*="overlay"][class*="modal"], [class*="mask"][class*="modal"]'
        );
        for (const ov of overlays) {
            try {
                const style = window.getComputedStyle(ov);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const opacity = parseFloat(style.opacity);
                if (!isNaN(opacity) && opacity < 0.05) continue;
                const rect = ov.getBoundingClientRect();
                if (rect.width < 100 || rect.height < 100) continue;
                return true;
            } catch (e) {}
        }
        return false;
    }

    const __activeDialog = findActiveDialog();
    const __overlayPresent = detectOverlay();
    const __isModal = !!(__activeDialog && __overlayPresent);
    let __dialogTitle = '';
    if (__activeDialog) {
        const titleEl = __activeDialog.querySelector(
            '.ui-dialog-title, .modal-title, .modal-header h1, .modal-header h2, ' +
            '.modal-header h3, .modal-header h4, [class*="dialog-title"], [class*="dialogTitle"]'
        );
        if (titleEl) {
            __dialogTitle = (titleEl.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 50);
        } else {
            const labelId = __activeDialog.getAttribute('aria-labelledby');
            if (labelId) {
                const labelEl = document.getElementById(labelId);
                if (labelEl) __dialogTitle = (labelEl.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 50);
            }
        }
    }

    // ── 遮挡检测 ──
    function isOccluded(el, rect) {
        if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) return false;
        try {
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            if (cx < 0 || cx > vw || cy < 0 || cy > vh) return false;
            const top = document.elementFromPoint(cx, cy);
            if (!top) return false;
            if (top === el || el.contains(top) || top.contains(el)) return false;
            return true;
        } catch (e) { return false; }
    }

    // ── 事件监听器检测 ──
    function hasInteractiveEvents(el) {
        if (window.__agentEventMap) {
            const events = window.__agentEventMap.get(el);
            if (events && (events.has('click') || events.has('mousedown') ||
                           events.has('mouseup') || events.has('touchstart') ||
                           events.has('pointerdown'))) {
                return true;
            }
        }
        try {
            if (window.jQuery) {
                const jqEvents = jQuery._data(el, 'events') || {};
                if (jqEvents.click || jqEvents.mousedown || jqEvents.touchstart) return true;
            }
        } catch(e) {}
        if (el.hasAttribute('onclick') || el.hasAttribute('ng-click') ||
            el.hasAttribute('v-on:click') || el.hasAttribute('@click')) return true;
        return false;
    }

    // ── 核心收集 ──
    function addElement(el, source) {
        if (seenNodes.has(el)) return;
        const rect = el.getBoundingClientRect();
        const isFileInput = el.tagName === 'INPUT' &&
            (el.getAttribute('type') || '').toLowerCase() === 'file';

        if (!isFileInput) {
            if (rect.width < 5 || rect.height < 5) return;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return;
            if (style.opacity === '0') return;
            if (isOccluded(el, rect)) return;
        }

        const text = (el.textContent || '').trim().slice(0, 80);

        if (!isFileInput) {
            const kx = Math.round(rect.x / 5) * 5;
            const ky = Math.round(rect.y / 5) * 5;
            const kw = Math.round(rect.width / 5) * 5;
            const key = `${kx}_${ky}_${kw}_${text.slice(0,20)}`;
            if (seenKeys.has(key)) return;
            seenKeys.add(key);

            let ancestor = el.parentElement;
            let depth = 0;
            while (ancestor && depth < 5) {
                if (seenNodes.has(ancestor)) {
                    const ancestorText = (ancestor.textContent || '').trim().slice(0, 80);
                    if (ancestorText === text) return;
                }
                ancestor = ancestor.parentElement;
                depth++;
            }
        }

        seenNodes.add(el);

        const isCheckable = (el.tagName === 'INPUT' &&
            ['checkbox', 'radio'].includes((el.getAttribute('type') || '').toLowerCase()));
        const isSelect = (el.tagName === 'SELECT');
        const inViewport = (rect.top < vh && rect.bottom > 0 && rect.left < vw && rect.right > 0);
        const inActiveDialog = __activeDialog ? __activeDialog.contains(el) : null;

        interactable.push({
            index: interactable.length,
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            name: el.getAttribute('name') || '',
            id: el.id || '',
            text: text,
            placeholder: el.getAttribute('placeholder') || '',
            value: el.value || '',
            aria_label: el.getAttribute('aria-label') || '',
            role: el.getAttribute('role') || '',
            href: el.tagName === 'A' ? (el.getAttribute('href') || '').slice(0, 100) : '',
            accept: el.getAttribute('accept') || '',
            class: (el.className || '').toString().slice(0, 60),
            checked: isCheckable ? el.checked : null,
            disabled: (el.disabled || el.hasAttribute('disabled')) || false,
            readonly: (el.readOnly || el.hasAttribute('readonly')) || false,
            aria_checked: el.getAttribute('aria-checked') || '',
            aria_selected: el.getAttribute('aria-selected') || '',
            selected_option: isSelect ? (el.options[el.selectedIndex]?.text || '') : '',
            inViewport: inViewport,
            inActiveDialog: inActiveDialog,
            rect: {
                x: Math.round(rect.x), y: Math.round(rect.y),
                w: Math.round(rect.width), h: Math.round(rect.height),
            },
            _source: source,
        });
    }

    // ── Shadow DOM 递归遍历 ──
    function scanRoot(root, source) {
        try {
            const allElements = root.querySelectorAll(INTERACTIVE_SELECTORS.join(','));
            for (const el of allElements) addElement(el, source);
        } catch(e) {}

        try {
            const fileInputs = root.querySelectorAll('input[type="file"]');
            for (const el of fileInputs) addElement(el, 'file_input');
        } catch(e) {}

        // 隐式交互元素
        const implicitCandidates = root.querySelectorAll(
            '[onclick], [ng-click], [v-on\\\\:click], [\\\\@click], ' +
            '[data-click], [data-action], [data-href], [data-target], ' +
            '[style*="cursor"], [class*="btn"], [class*="click"], [class*="link"]'
        );
        for (const el of implicitCandidates) {
            if (seenNodes.has(el)) continue;
            const rect = el.getBoundingClientRect();
            if (rect.width < 10 || rect.height < 10) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
            const text = (el.textContent || '').trim();
            if (!text && el.tagName !== 'IMG' && el.tagName !== 'SVG') continue;
            const childInteractive = el.querySelectorAll(
                'a, button, input, [onclick], [role="button"], [tabindex]'
            ).length;
            if (childInteractive > 1) continue;
            if (childInteractive === 1) {
                const child = el.querySelector('a, button, input, [onclick], [role="button"], [tabindex]');
                if (child && (child.textContent || '').trim().slice(0, 50) === text.slice(0, 50)) continue;
            }
            addElement(el, 'implicit');
        }

        // cursor:pointer 叶子节点
        const cursorCandidates = root.querySelectorAll('div, span, li, td, th, img');
        for (const el of cursorCandidates) {
            if (seenNodes.has(el)) continue;
            const rect = el.getBoundingClientRect();
            if (rect.width < 10 || rect.height < 10) continue;
            let isInteractive = false;
            try {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                if (style.cursor === 'pointer') isInteractive = true;
            } catch(e) { continue; }
            if (!isInteractive) isInteractive = hasInteractiveEvents(el);
            if (!isInteractive) continue;
            const text = (el.textContent || '').trim();
            if (!text && el.tagName !== 'IMG') continue;
            const childInteractive = el.querySelectorAll(
                'a, button, input, [onclick], [role="button"]'
            ).length;
            if (childInteractive > 1) continue;
            if (childInteractive === 1) {
                const child = el.querySelector('a, button, input, [onclick], [role="button"]');
                if (child && (child.textContent || '').trim().slice(0, 50) === text.slice(0, 50)) continue;
            }
            addElement(el, 'implicit_cursor');
        }

        // 递归进入 open shadow root
        try {
            const allNodes = root.querySelectorAll('*');
            for (const node of allNodes) {
                if (node.shadowRoot) scanRoot(node.shadowRoot, source + '_shadow');
            }
        } catch(e) {}
    }

    scanRoot(document, 'selector');

    // Uploadifive/Uploadify 插件专项
    const uploadPluginSelectors = [
        '.uploadifive-button input[type="file"]',
        '.uploadify-button input[type="file"]',
        '.upload-btn input[type="file"]',
        '[class*="upload"] input[type="file"]',
        '[id*="upload"] input[type="file"]',
    ];
    for (const sel of uploadPluginSelectors) {
        try {
            const els = document.querySelectorAll(sel);
            for (const el of els) {
                if (!seenNodes.has(el)) {
                    seenNodes.add(el);
                    const rect = el.getBoundingClientRect();
                    const parentRect = el.parentElement ?
                        el.parentElement.getBoundingClientRect() : rect;
                    interactable.push({
                        index: interactable.length,
                        tag: 'input', type: 'file',
                        name: el.getAttribute('name') || '',
                        id: el.id || '',
                        text: '', placeholder: '', value: '',
                        aria_label: '', role: '', href: '',
                        accept: el.getAttribute('accept') || '',
                        class: (el.className || '').toString().slice(0, 60),
                        multiple: el.hasAttribute('multiple') ? 'true' : '',
                        inViewport: (parentRect.top < vh && parentRect.bottom > 0),
                        rect: {
                            x: Math.round(parentRect.x), y: Math.round(parentRect.y),
                            w: Math.round(parentRect.width), h: Math.round(parentRect.height),
                        },
                        _source: 'upload_plugin',
                    });
                }
            }
        } catch(e) {}
    }

    // 视口感知排序
    interactable.sort((a, b) => {
        if (a.inViewport && !b.inViewport) return -1;
        if (!a.inViewport && b.inViewport) return 1;
        return (a.rect.y - b.rect.y) || (a.rect.x - b.rect.x);
    });
    for (let i = 0; i < interactable.length; i++) {
        interactable[i].index = i;
    }

    return {
        url: location.href,
        title: document.title,
        element_count: interactable.length,
        elements: interactable,
        scroll: {
            x: Math.round(scrollX), y: Math.round(scrollY),
            doc_height: Math.round(docHeight),
            viewport_height: vh, viewport_width: vw,
            pages_above: pagesAbove,
            pages_below: Math.max(0, pagesBelow),
        },
        activeDialog: __activeDialog ? {
            present: true,
            isModal: __isModal,
            title: __dialogTitle,
            hasOverlay: __overlayPresent,
        } : null,
    };
}
"""


# ── 标注覆盖层：用 fixed 定位容器，不修改原元素样式 ──

INJECT_LABELS = """
(elements) => {
    let overlay = document.getElementById('___agent_overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = '___agent_overlay';
        overlay.style.cssText = `
            position: fixed; top: 0; left: 0;
            width: 100%; height: 100%;
            pointer-events: none; z-index: 999999;
        `;
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = '';

    for (const el of elements) {
        const label = document.createElement('span');
        label.style.cssText = `
            position: absolute;
            left: ${el.rect.x}px;
            top: ${el.rect.y}px;
            background: #ff6b35; color: #fff; font-size: 10px;
            padding: 1px 4px; border-radius: 3px;
            font-family: monospace; line-height: 1.2;
            white-space: nowrap;
        `;
        label.textContent = el.index;
        overlay.appendChild(label);
    }
}
"""

CLEANUP_LABELS = """
() => {
    const overlay = document.getElementById('___agent_overlay');
    if (overlay) overlay.remove();
}
"""
