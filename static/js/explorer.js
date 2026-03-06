// Explorer Page JavaScript - Trial Selection and Visualization
// UPDATED: Uses frame-based animation (no worker timeouts)

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
            
            // Scroll to visualization
            visualizationContainer.scrollIntoView({ 
                behavior: 'smooth', 
                block: 'nearest' 
            });
            
        } catch (error) {
            console.error('Error loading final state:', error);
            hideElement(loadingDiv);
            showError(visualizationContainer, 'Failed to load final state image.');
            showElement(visualizationContainer);
        }
    });
});
