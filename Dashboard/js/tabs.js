// =============================================================
// tabs.js — Tab switching logic
// =============================================================

window.activeTab = 'mission';

window.switchTab = function (tab) {
    window.activeTab = tab;

    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    const buttons = document.querySelectorAll('.tab-btn');
    if (tab === 'mission') buttons[0].classList.add('active');
    else if (tab === 'manual') buttons[1].classList.add('active');
    else if (tab === 'logs') buttons[2].classList.add('active');

    // Update panels
    document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById(`view-${tab}`);
    if (panel) panel.classList.add('active');

    // Leaflet needs invalidateSize() whenever its container becomes
    // visible after being hidden. Fire multiple times to guarantee
    // it catches the correct size after CSS transitions settle.
    if (tab === 'mission' && window.map) {
        [50, 150, 300].forEach(ms => {
            setTimeout(() => {
                window.map.invalidateSize({ animate: false });
                window.map.panTo([10.265624, 123.819612], { animate: false });
            }, ms);
        });
    }
};