const DEBOUNCE_MS = 10; // ignore repeat taps on the same button within this window
const lastTapTime = {};

async function addBeer(name) {
    const now = Date.now();
    if (lastTapTime[name] && now - lastTapTime[name] < DEBOUNCE_MS) {
        return; // too soon after the last tap on this button - ignore it
    }
    lastTapTime[name] = now;

    const btn = document.querySelector(`[data-name="${name}"]`);
    btn.disabled = true; // also block the browser's own click queue while the request is in flight

    try {
        const resp = await fetch(`/add/${encodeURIComponent(name)}`, { method: "POST" });

        if (resp.status === 409) {
            // Backend confirms stock ran out (e.g. two people tapped at once) - show
            // the block screen instead of registering the tap.
            fetchStock();
            return;
        }

        const counts = await resp.json();
        applyCounts(counts);
        fetchStock();
        setStatus("All synced", false);
    } catch (err) {
        setStatus("Saved locally - will sync later", true);
    } finally {
        setTimeout(() => { btn.disabled = false; }, 200);
    }
}

function applyCounts(counts) {
    for (const [person, count] of Object.entries(counts)) {
        const el = document.getElementById(`count-${person}`);
        if (el) el.textContent = count;
    }
    updateBadges(counts);
}

function updateBadges(counts) {
    const entries = Object.entries(counts);

    // --- Leader (most beers): only crown if strictly ahead of everyone else ---
    let leader = null;
    let maxCount = 0;
    let maxTie = false;

    for (const [person, count] of entries) {
        if (count > maxCount) {
            maxCount = count;
            leader = person;
            maxTie = false;
        } else if (count === maxCount && maxCount > 0) {
            maxTie = true;
        }
    }

    // --- Last place (fewest beers): only badge if strictly behind everyone else ---
    let straggler = null;
    let minCount = entries.length > 0 ? entries[0][1] : 0;
    let minTie = false;

    for (const [, count] of entries) {
        if (count < minCount) minCount = count;
    }
    for (const [person, count] of entries) {
        if (count === minCount) {
            if (straggler === null) {
                straggler = person;
            } else {
                minTie = true;
            }
        }
    }

    document.querySelectorAll(".crown-badge, .jester-badge").forEach((el) => {
        el.classList.remove("visible");
    });

    if (leader && !maxTie) {
        const crownEl = document.getElementById(`crown-${leader}`);
        if (crownEl) crownEl.classList.add("visible");
    }

    // Don't badge the same person as both leader and straggler (only matters
    // with a single housemate), and skip entirely if everyone is tied.
    if (straggler && !minTie && straggler !== leader) {
        const jesterEl = document.getElementById(`jester-${straggler}`);
        if (jesterEl) jesterEl.classList.add("visible");
    }
}

function setStatus(text, isError) {
    const el = document.getElementById("status");
    el.textContent = text;
    el.className = isError ? "status error" : "status";
}

// --- Stock panel: available / drunk / pace ---

async function fetchStock() {
    try {
        const resp = await fetch("/api/stock");
        const stock = await resp.json();
        applyStock(stock);
    } catch (err) {
        // ignore - panel just keeps its last known values
    }
}

function applyStock(stock) {
    const availEl = document.getElementById("stat-available");
    const drunkEl = document.getElementById("stat-drunk");
    const paceEl = document.getElementById("stat-pace");
    if (availEl) availEl.textContent = stock.available;
    if (drunkEl) drunkEl.textContent = stock.drunk;
    if (paceEl) paceEl.textContent = stock.pace_24h;

    const overlay = document.getElementById("out-of-stock-overlay");
    if (overlay) {
        overlay.classList.toggle("visible", stock.available <= 0);
    }
}

async function restock() {
    const password = prompt("Beheerderswachtwoord:");
    if (password === null) return; // cancelled

    const amountStr = prompt("Aantal nieuw gekochte bieren:");
    if (amountStr === null) return; // cancelled

    const amount = parseInt(amountStr, 10);
    if (isNaN(amount) || amount < 0) {
        alert("Ongeldig aantal.");
        return;
    }

    try {
        const resp = await fetch("/api/restock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password, amount }),
        });

        if (resp.status === 403) {
            alert("Verkeerd wachtwoord.");
            return;
        }
        if (!resp.ok) {
            alert("Er ging iets mis bij het bijwerken van de voorraad.");
            return;
        }

        const stock = await resp.json();
        applyStock(stock);
        alert("Voorraad bijgewerkt!");

    } catch (err) {
        alert("Kon niet verbinden met de server.");
    }
}

// Retry syncing any unsynced taps every 30 seconds (e.g. after PC was off)
async function resync() {
    try {
        const resp = await fetch("/api/resync", { method: "POST" });
        const result = await resp.json();
        if (result.remaining === 0) {
            setStatus("All synced", false);
        } else {
            setStatus(`${result.remaining} taps still waiting to sync`, true);
        }
    } catch (err) {
        // ignore - will try again next interval
    }
    fetchStock();
}
setInterval(resync, 30000);

// Set the badges and stock panel correctly on page load
document.addEventListener("DOMContentLoaded", async () => {
    try {
        const resp = await fetch("/api/counts");
        const counts = await resp.json();
        updateBadges(counts);
    } catch (err) {
        // ignore - badges will just appear after the first tap
    }
    fetchStock();
});

// --- Screensaver ---
// Shows "Du Pont Pilsch Solutions" after a period of no touches, and
// disappears again on the very next single touch/click.
// 90 seconds of inactivity (90 * 1000 milliseconds)
const SCREENSAVER_IDLE_MS = 10000;
let lastInteraction = Date.now();

function isScreensaverActive() {
    return document.getElementById("screensaver").classList.contains("visible");
}

function showScreensaver() {
    document.getElementById("screensaver").classList.add("visible");
}

function hideScreensaver() {
    document.getElementById("screensaver").classList.remove("visible");
    lastInteraction = Date.now();
}

function resetIdleTimer() {
    if (!isScreensaverActive()) {
        lastInteraction = Date.now();
    }
}

// Listen for clicks, touches, mouse movements, and key presses
["click", "mousemove"].forEach((eventType) => {
    document.addEventListener(eventType, resetIdleTimer, { passive: true });
});

setInterval(() => {
    if (!isScreensaverActive() && Date.now() - lastInteraction > SCREENSAVER_IDLE_MS) {
        showScreensaver();
    }
}, 1000);

const screensaverEl = document.getElementById("screensaver");
screensaverEl.addEventListener("click", hideScreensaver);
screensaverEl.addEventListener("touchstart", (e) => {
    e.preventDefault(); // stop touch from firing click on underlying elements
    hideScreensaver();
});