// Explorer Page JavaScript - Trial Selection and Visualization
// UPDATED: Uses frame-based animation (no worker timeouts)

/**
 * Render a card grid as HTML (matching the behavioural analysis card style).
 * @param {Array} cells - Array of {row, col, value, suit}
 */
function renderCardGrid(cells, participant, trial, condition, success, totalMoves) {
    const gridState = {};
    cells.forEach(function(c) {
        gridState[c.row + '-' + c.col] = c;
    });

    var boardHtml = '<div style="display:grid;grid-template-columns:28px repeat(8,1fr);grid-template-rows:28px repeat(8,1fr);gap:3px;max-width:520px;margin:0 auto;">';

    // Corner + column headers
    boardHtml += '<div style="display:flex;align-items:center;justify-content:center;font-size:11px;color:rgba(255,255,255,0.5);font-weight:600;"></div>';
    for (var c = 0; c < 8; c++) {
        boardHtml += '<div style="display:flex;align-items:center;justify-content:center;font-size:11px;color:rgba(255,255,255,0.6);font-weight:600;">' + (c+1) + '</div>';
    }

    for (var r = 0; r < 8; r++) {
        // Row header
        boardHtml += '<div style="display:flex;align-items:center;justify-content:center;font-size:11px;color:rgba(255,255,255,0.6);font-weight:600;">' + String.fromCharCode(65+r) + '</div>';
        for (var c = 0; c < 8; c++) {
            var key = r + '-' + c;
            var cell = gridState[key];
            if (cell) {
                var val = cell.value;
                var isBlank = val === 'B';
                // Use the card's actual suit glyph + red/black colour (from the
                // API), matching the animation — not a rank-derived suit.
                var symbol = isBlank ? '?' : (cell.symbol || '');
                // Blank cards get a clean, light card-like face so they sit
                // harmoniously beside the real cards instead of a heavy grey block.
                var bg = isBlank ? '#EEF2F7' : '#FFFFFF';
                var borderCol = isBlank ? '#CBD5E1' : '#D0D0D0';
                var textColor = isBlank ? '#94A3B8' : (cell.red ? '#DC2626' : '#111827');
                boardHtml += '<div style="border-radius:6px;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;background:' + bg + ';border:1px solid ' + borderCol + ';box-shadow:0 2px 8px rgba(0,0,0,0.20);color:' + textColor + ';font-weight:700;aspect-ratio:3/4;min-width:0;">';
                if (!isBlank) {
                    boardHtml += '<span style="position:absolute;top:3px;left:4px;font-size:11px;font-weight:700;line-height:1;">' + val + '</span>';
                }
                boardHtml += '<span style="font-size:' + (isBlank ? '17px' : '20px') + ';font-weight:' + (isBlank ? '500' : '700') + ';line-height:1;">' + symbol + '</span>';
                boardHtml += '</div>';
            } else {
                boardHtml += '<div style="border-radius:6px;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.08);border:1px dashed rgba(255,255,255,0.18);aspect-ratio:3/4;min-width:0;"></div>';
            }
        }
    }
    boardHtml += '</div>';

    return '<div style="text-align:center;">' +
        '<h3 style="margin:0 0 12px;color:var(--app-text-primary,#0f172a);font-size:1.1rem;font-weight:700;">Final State - Participant ' + participant + ', Trial ' + trial + '</h3>' +
        '<p style="margin:0 0 10px;font-size:0.85rem;color:var(--app-text-secondary,#475569);">' +
        '<strong>Condition:</strong> ' + condition + ' | <strong>Moves:</strong> ' + totalMoves + ' | ' +
        (success ? '<span style="color:var(--app-success,#059669);font-weight:600;">Success</span>' : '<span style="color:var(--app-fail,#dc2626);font-weight:600;">Failed</span>') +
        '</p>' +
        '<div style="background:linear-gradient(135deg,#06483f,#0b5a4f);border-radius:14px;padding:14px;border:1px solid rgba(255,255,255,0.08);box-shadow:0 4px 24px rgba(0,0,0,0.25);">' +
        boardHtml +
        '</div>' +
        '</div>';
}


document.addEventListener('DOMContentLoaded', function() {
    const conditionSelect = document.getElementById('condition-select');
    const participantSelect = document.getElementById('participant-select');
    const trialSelect = document.getElementById('trial-select');
    const trialInfo = document.getElementById('trial-info');
    const showAnimationBtn = document.getElementById('show-animation-btn');
    const showFinalBtn = document.getElementById('show-final-btn');
    const visualizationContainer = document.getElementById('visualization-container');
    const welcomeMessage = document.getElementById('welcome-message');
    const loadingDiv = document.getElementById('loading');
    
    let currentParticipant = null;
    let currentTrial = null;
    let currentCondition = '';
    let currentAnimationPlayer = null; // Store animation player instance
    
    /**
     * Handle condition selection change
     */
    conditionSelect.addEventListener('change', function() {
        currentCondition = this.value;
        
        // If participant already selected, reload trials with new filter
        if (currentParticipant) {
            loadTrialsForParticipant(currentParticipant);
        }
    });
    
    /**
     * Load trials for a participant with optional condition filter
     */
    async function loadTrialsForParticipant(participant) {
        try {
            const url = `/api/get-trials/${participant}${currentCondition ? '?condition=' + encodeURIComponent(currentCondition) : ''}`;
            const trials = await fetchJSON(url);
            
            trialSelect.innerHTML = '<option value="">-- Select Trial --</option>';
            
            if (trials.length === 0) {
                trialSelect.innerHTML = '<option value="">-- No trials for this condition --</option>';
                trialSelect.disabled = true;
                hideElement(trialInfo);
                showAnimationBtn.disabled = true;
                showFinalBtn.disabled = true;
                return;
            }
            
            trials.forEach(trial => {
                const option = document.createElement('option');
                option.value = trial;
                option.textContent = `Trial ${trial}`;
                trialSelect.appendChild(option);
            });
            
            trialSelect.disabled = false;
            
        } catch (error) {
            console.error('Error loading trials:', error);
            alert('Failed to load trials for this participant.');
        }
    }
    
    /**
     * Handle participant selection
     */
    participantSelect.addEventListener('change', async function() {
        currentParticipant = this.value ? parseInt(this.value) : null;
        
        if (!currentParticipant) {
            trialSelect.innerHTML = '<option value="">-- Select Trial --</option>';
            trialSelect.disabled = true;
            hideElement(trialInfo);
            showAnimationBtn.disabled = true;
            showFinalBtn.disabled = true;
            return;
        }
        
        await loadTrialsForParticipant(currentParticipant);
    });
    
    /**
     * Handle trial selection
     */
    trialSelect.addEventListener('change', async function() {
        const trial = this.value;
        
        if (!trial) {
            hideElement(trialInfo);
            showAnimationBtn.disabled = true;
            showFinalBtn.disabled = true;
            return;
        }
        
        currentTrial = trial;
        
        // Fetch trial information
        try {
            const info = await fetchJSON(`/api/trial-info/${currentParticipant}/${trial}`);
            
            // Update trial info display
            document.getElementById('info-condition').textContent = info.condition;
            document.getElementById('info-moves').textContent = info.total_moves;
            document.getElementById('info-result').innerHTML = info.success 
                ? '<span style="color: green; font-weight: bold;">✓ Success</span>' 
                : '<span style="color: red; font-weight: bold;">✗ Failed</span>';
            
            showElement(trialInfo);
            showAnimationBtn.disabled = false;
            showFinalBtn.disabled = false;
            
        } catch (error) {
            console.error('Error fetching trial info:', error);
            hideElement(trialInfo);
        }
    });
    
    /**
     * Show Animation Button - UPDATED: Frame-based animation
     */
    showAnimationBtn.addEventListener('click', async function() {
        if (!currentParticipant || currentTrial === null) return;
        
        // Cleanup old animation player if exists
        if (currentAnimationPlayer) {
            currentAnimationPlayer.destroy();
            currentAnimationPlayer = null;
        }
        
        // Hide welcome message and show loading
        hideElement(welcomeMessage);
        showLoading(loadingDiv);
        hideElement(visualizationContainer);
        
        try {
            // Hide loading and show container
            hideElement(loadingDiv);
            showElement(visualizationContainer);
            
            // Create container for animation player
            visualizationContainer.innerHTML = '<div id="animation-player-container"></div>';
            
            // Initialize frame-based animation player
            currentAnimationPlayer = new AnimationPlayer(
                currentParticipant,
                currentTrial,
                'animation-player-container'
            );
            
            // Scroll to visualization
            visualizationContainer.scrollIntoView({ 
                behavior: 'smooth', 
                block: 'nearest' 
            });
            
        } catch (error) {
            console.error('Error showing animation:', error);
            hideElement(loadingDiv);
            showError(visualizationContainer, 'Failed to load animation. Please try again.');
            showElement(visualizationContainer);
        }
    });
    
    /**
     * Show Final State Button
     */
    showFinalBtn.addEventListener('click', async function() {
        if (!currentParticipant || currentTrial === null) return;
        
        // Cleanup animation player if active
        if (currentAnimationPlayer) {
            currentAnimationPlayer.destroy();
            currentAnimationPlayer = null;
        }
        
        // Hide welcome message and show loading
        hideElement(welcomeMessage);
        showLoading(loadingDiv);
        hideElement(visualizationContainer);
        
        try {
            // Fetch grid data for HTML card rendering
            const gridData = await fetchJSON(`/api/trial-grid/${currentParticipant}/${currentTrial}`);
            
            hideElement(loadingDiv);
            
            visualizationContainer.innerHTML = renderCardGrid(
                gridData.cells,
                gridData.participant,
                gridData.trial,
                gridData.condition,
                gridData.success,
                gridData.total_moves
            );
            showElement(visualizationContainer);
            
            // Scroll to visualization
            visualizationContainer.scrollIntoView({ 
                behavior: 'smooth', 
                block: 'nearest' 
            });
            
        } catch (error) {
            console.error('Error loading final state:', error);
            // Fallback to server-rendered image if grid data fails
            try {
                const imageUrl = `/api/trial-image/${currentParticipant}/${currentTrial}`;
                hideElement(loadingDiv);
                visualizationContainer.innerHTML = `
                    <div style="text-align: center;">
                        <h3 style="color: #667eea; margin-bottom: 1rem;">
                            Final State - Participant ${currentParticipant}, Trial ${currentTrial}
                        </h3>
                        <img src="${imageUrl}" 
                             alt="Trial final state" 
                             style="max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 8px;">
                    </div>
                `;
                showElement(visualizationContainer);
            } catch (fallbackError) {
                hideElement(loadingDiv);
                showError(visualizationContainer, 'Failed to load final state.');
                showElement(visualizationContainer);
            }
        }
    });
});
