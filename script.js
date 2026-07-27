async function addBeer(name) {
    const btn = document.querySelector(`[data-name="${name}"]`);
    btn.disabled = true; // avoid double taps registering twice

    try {
        const resp = await fetch(`/add/${encodeURIComponent(name)}`, { method: "POST" });
        const counts = await resp.json();
        for (const [person, count] of Object.entries(counts)) {
            const el = document.getElementById(`count-${person}`);
            if (el) el.textContent = count;
        }
        setStatus("All synced", false);
    } catch (err) {
        setStatus("Saved locally - will sync later", true);
    } finally {
        setTimeout(() => { btn.disabled = false; }, 200);
    }
}

function setStatus(text, isError) {
    const el = document.getElementById("status");
    el.textContent = text;
    el.className = isError ? "status error" : "status";
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
}
setInterval(resync, 30000);
