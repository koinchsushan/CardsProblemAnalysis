// Explorer Page JavaScript - Trial Selection and Visualization

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
            console.error('Error fetching trials:', error);
            showError(visualizationContainer, 'Failed to load trials for this participant');
            showElement(visualizationContainer);
        }
    }
    
    /**
     * Handle participant selection change
     */
    participantSelect.addEventListener('change', async function() {
        const participant = this.value;
        
        if (!participant) {
            // Reset to initial state
            trialSelect.disabled = true;
            trialSelect.innerHTML = '<option value="">-- Select Participant First --</option>';
            hideElement(trialInfo);
            showAnimationBtn.disabled = true;
            showFinalBtn.disabled = true;
            return;
        }
        
        currentParticipant = participant;
        await loadTrialsForParticipant(participant);
    });
    
    /**
     * Handle trial selection change
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
            showError(visualizationContainer, 'Failed to load trial information');
            showElement(visualizationContainer);
        }
    });
    
    /**
     * Show Animation button click handler
     */
    showAnimationBtn.addEventListener('click', async function() {
        if (!currentParticipant || !currentTrial) return;
        
        // Hide welcome message and show loading
        hideElement(welcomeMessage);
        showLoading(loadingDiv);
        hideElement(visualizationContainer);
        
        try {
            const data = await fetchJSON(`/api/generate-animation/${currentParticipant}/${currentTrial}`);
            
            hideElement(loadingDiv);
            
            // Display animation in iframe - CSS injected in HTML handles scaling
            visualizationContainer.innerHTML = `
                <div style="text-align: center;">
                    <h3 style="color: #667eea; margin-bottom: 1rem;">
                        Animation - Participant ${currentParticipant}, Trial ${currentTrial}
                    </h3>
                    <iframe src="${data.file}" 
                            style="width: 100%; max-width: 650px; height: 650px; border: 1px solid #ddd; border-radius: 8px;"
                            frameborder="0">
                    </iframe>
                </div>
            `;
            showElement(visualizationContainer);
            
            // Scroll to visualization
            visualizationContainer.scrollIntoView({ 
                behavior: 'smooth', 
                block: 'nearest' 
            });
            
        } catch (error) {
            console.error('Error generating animation:', error);
            hideElement(loadingDiv);
            showError(visualizationContainer, 'Failed to generate animation. Please try again.');
            showElement(visualizationContainer);
        }
    });
    
    /**
     * Show Final State button click handler
     */
    showFinalBtn.addEventListener('click', function() {
        if (!currentParticipant || !currentTrial) return;
        
        hideElement(welcomeMessage);
        hideElement(loadingDiv);
        
        // Create image display
        visualizationContainer.innerHTML = `
            <div style="text-align: center;">
                <h3 style="color: #667eea; margin-bottom: 1rem;">
                    Final State - Participant ${currentParticipant}, Trial ${currentTrial}
                </h3>
                <img src="/api/trial-image/${currentParticipant}/${currentTrial}" 
                     alt="Trial final state visualization" 
                     style="max-width: 100%; height: auto; margin-top: 1rem; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            </div>
        `;
        showElement(visualizationContainer);
        
        // Scroll to visualization
        visualizationContainer.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'nearest' 
        });
    });
});
