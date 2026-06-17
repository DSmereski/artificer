/**
 * probe_input.ts — Phase A input forwarding verification.
 *
 * Mounts a click-counter button with localStorage persistence.
 * When Lively loads this page as a wallpaper, clicking the desktop
 * area (which forwards to the WebView2) should increment this counter —
 * proving input forwarding is active.
 *
 * the operator's manual check: click the button, see the counter go up.
 */

const LS_KEY = 'hive_click_count';

let clickCount = parseInt(localStorage.getItem(LS_KEY) ?? '0', 10);
if (isNaN(clickCount)) clickCount = 0;

function updateUI(): void {
  const counterEl = document.getElementById('click-counter');
  if (counterEl) {
    counterEl.textContent = String(clickCount);
  }

  const lsEl = document.getElementById('ls-val');
  if (lsEl) {
    lsEl.textContent = String(clickCount);
  }
}

export function initProbeInput(): void {
  // Render initial count from storage
  updateUI();

  const btn = document.getElementById('probe-btn');
  if (!btn) return;

  btn.addEventListener('click', (e) => {
    clickCount += 1;
    localStorage.setItem(LS_KEY, String(clickCount));
    updateUI();

    const lastEl = document.getElementById('last-click');
    if (lastEl) {
      lastEl.textContent = new Date().toLocaleTimeString();
    }

    // Visual flash feedback (brief amber border pulse)
    btn.style.boxShadow = '0 0 24px oklch(0.83 0.15 78 / 0.6)';
    setTimeout(() => {
      btn.style.boxShadow = '';
    }, 200);

    e.stopPropagation();
  });
}
