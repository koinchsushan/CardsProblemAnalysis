async function fetchJSON(url) {
    const response = await fetch(url);
    if (!response.ok) {
        let message = `Request failed with status ${response.status}`;
        try {
            const data = await response.json();
            if (data.error) message = data.error;
        } catch (err) {}
        throw new Error(message);
    }
    return await response.json();
}

function fillSelect(selectEl, options, fallbackText = "No options") {
    selectEl.innerHTML = "";

    if (!options || options.length === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = fallbackText;
        selectEl.appendChild(option);
        return;
    }

    options.forEach(value => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        selectEl.appendChild(option);
    });
}

function showError(message) {
    const errorBox = document.getElementById("errorBox");
    errorBox.style.display = "block";
    errorBox.textContent = message;
}

function clearError() {
    const errorBox = document.getElementById("errorBox");
    errorBox.style.display = "none";
    errorBox.textContent = "";
}

function resetGrid() {
    const cells = document.querySelectorAll(".grid-cell");
    cells.forEach(cell => {
        cell.classList.remove("blank", "other");
        cell.classList.add("empty");
        cell.querySelector(".cell-index").textContent = "";
        cell.querySelector(".cell-symbol").textContent = "";
    });
}

function renderSummary(data) {
    document.getElementById("summaryOutcome").textContent = data.outcome || "-";
    document.getElementById("summarySelectedCondition").textContent = data.selected_condition || "-";
    document.getElementById("summaryActualCondition").textContent = data.actual_condition || "-";
    document.getElementById("summaryParticipant").textContent = data.participant || "-";
    document.getElementById("summaryTrial").textContent = data.trial || "-";
    document.getElementById("summaryPattern").textContent = data.pattern || "-";
    document.getElementById("summarySR").textContent =
        Number.isFinite(Number(data.SR)) ? Number(data.SR).toFixed(2) : "-";
    document.getElementById("summaryN").textContent = data.N ?? "-";
}

function renderLegend(data) {
    const rawLegendText = document.getElementById("rawLegendText");
    if (data.legend && data.legend.length > 0) {
        rawLegendText.textContent = data.legend.join(", ");
    } else {
        rawLegendText.textContent = "-";
    }
}

function renderGrid(data) {
    resetGrid();

    if (!data.grid_map) return;

    Object.values(data.grid_map).forEach(item => {
        const cell = document.querySelector(`.grid-cell[data-pos="${item.pos}"]`);
        if (!cell) return;

        cell.classList.remove("empty");
        if (item.value_type === "blank") {
            cell.classList.add("blank");
        } else {
            cell.classList.add("other");
        }

        cell.querySelector(".cell-index").textContent = item.index || "";
        cell.querySelector(".cell-symbol").textContent = item.sym || "";
    });
}

async function loadPlotData() {
    clearError();

    const condition = document.getElementById("conditionSelect").value;
    const pattern = document.getElementById("patternSelect").value;
    const participant = document.getElementById("participantSelect").value;
    const trial = document.getElementById("trialSelect").value;

    if (!condition || !pattern || !participant || !trial) {
        resetGrid();
        return;
    }

    const params = new URLSearchParams({ condition, pattern, participant, trial });

    try {
        const data = await fetchJSON(`/api/blank-patterns/plot-data?${params.toString()}`);
        renderSummary(data);
        renderGrid(data);
        renderLegend(data);
    } catch (error) {
        resetGrid();
        showError(error.message);
    }
}

async function onConditionChange() {
    clearError();

    const condition = document.getElementById("conditionSelect").value;
    const patternSelect = document.getElementById("patternSelect");
    const participantSelect = document.getElementById("participantSelect");
    const trialSelect = document.getElementById("trialSelect");

    try {
        const data = await fetchJSON(`/api/blank-patterns/patterns?condition=${encodeURIComponent(condition)}`);

        fillSelect(patternSelect, data.patterns, "No patterns");
        fillSelect(participantSelect, [], "No participants");
        fillSelect(trialSelect, [], "No trials");

        await onPatternChange();
    } catch (error) {
        showError(error.message);
    }
}

async function onPatternChange() {
    clearError();

    const condition = document.getElementById("conditionSelect").value;
    const pattern = document.getElementById("patternSelect").value;
    const participantSelect = document.getElementById("participantSelect");
    const trialSelect = document.getElementById("trialSelect");

    if (!pattern) {
        fillSelect(participantSelect, [], "No participants");
        fillSelect(trialSelect, [], "No trials");
        resetGrid();
        return;
    }

    try {
        const data = await fetchJSON(
            `/api/blank-patterns/participants?condition=${encodeURIComponent(condition)}&pattern=${encodeURIComponent(pattern)}`
        );

        fillSelect(participantSelect, data.participants, "No participants");
        fillSelect(trialSelect, [], "No trials");

        await onParticipantChange();
    } catch (error) {
        showError(error.message);
    }
}

async function onParticipantChange() {
    clearError();

    const condition = document.getElementById("conditionSelect").value;
    const pattern = document.getElementById("patternSelect").value;
    const participant = document.getElementById("participantSelect").value;
    const trialSelect = document.getElementById("trialSelect");

    if (!participant) {
        fillSelect(trialSelect, [], "No trials");
        resetGrid();
        return;
    }

    try {
        const data = await fetchJSON(
            `/api/blank-patterns/trials?condition=${encodeURIComponent(condition)}&pattern=${encodeURIComponent(pattern)}&participant=${encodeURIComponent(participant)}`
        );

        fillSelect(trialSelect, data.trials, "No trials");
        await loadPlotData();
    } catch (error) {
        showError(error.message);
    }
}

async function initBlankPatterns() {
    const conditionSelect = document.getElementById("conditionSelect");
    const patternSelect = document.getElementById("patternSelect");
    const participantSelect = document.getElementById("participantSelect");
    const trialSelect = document.getElementById("trialSelect");

    if (!conditionSelect || !patternSelect || !participantSelect || !trialSelect) {
        return;
    }

    try {
        const data = await fetchJSON("/api/blank-patterns/options");

        fillSelect(conditionSelect, data.conditions, "No conditions");
        fillSelect(patternSelect, data.patterns, "No patterns");
        fillSelect(participantSelect, data.participants, "No participants");
        fillSelect(trialSelect, data.trials, "No trials");

        conditionSelect.addEventListener("change", onConditionChange);
        patternSelect.addEventListener("change", onPatternChange);
        participantSelect.addEventListener("change", onParticipantChange);
        trialSelect.addEventListener("change", loadPlotData);

        await loadPlotData();
    } catch (error) {
        showError(error.message);
    }
}

document.addEventListener("DOMContentLoaded", initBlankPatterns);