// Patterns Page JavaScript - Pattern Analysis and Trial Selection

document.addEventListener('DOMContentLoaded', function() {
    
    // Tab switching functionality
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    
    tabBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const tab = this.dataset.tab;
            
            // Update active tab button
            tabBtns.forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            
            // Update active tab content
            tabContents.forEach(content => {
                content.classList.remove('active');
            });
            document.getElementById(`${tab}-tab`).classList.add('active');
        });
    });
    
    // Load patterns for both success and failure tabs
    loadPatterns('success');
    loadPatterns('failure');
    
    // Set up interactive selectors for both tabs
    setupInteractiveSelector('success');
    setupInteractiveSelector('failure');
    
    // Show More button handlers
    let successShowingAll = false;
    let failureShowingAll = false;
    
    document.getElementById('success-show-more-btn').addEventListener('click', function() {
        successShowingAll = !successShowingAll;
        loadPatterns('success', successShowingAll ? 0 : 5);
    });
    
    document.getElementById('failure-show-more-btn').addEventListener('click', function() {
        failureShowingAll = !failureShowingAll;
        loadPatterns('failure', failureShowingAll ? 0 : 5);
    });
});

/**
 * Load and display pattern visualizations
 */
async function loadPatterns(type, limit = 5) {
    const gridId = `${type}-patterns-grid`;
    const grid = document.getElementById(gridId);
    const showMoreBtn = document.getElementById(`${type}-show-more-btn`);
    
    try {
        const url = `/api/analyze-patterns/${type}${limit === 0 ? '?limit=0' : ''}`;
        const data = await fetchJSON(url);
        const patterns = data.patterns;
        const totalUnique = data.total_unique;
        
        if (patterns.length === 0) {
            grid.innerHTML = '<p style="text-align: center; padding: 2rem;">No patterns found</p>';
            return;
        }
        
        grid.innerHTML = '';
        
        // Create pattern cards
        patterns.forEach(pattern => {
            const card = document.createElement('div');
            card.className = 'pattern-card';
            card.innerHTML = `
                <img src="/api/pattern-image/${type}/${pattern.id}" 
                     alt="Pattern ${pattern.id + 1}"
                     loading="lazy">
                <div class="pattern-info">
                    <h4>Pattern #${pattern.id + 1}</h4>
                    <p>Frequency: ${pattern.count} trials</p>
                    <p>${pattern.cards} cards</p>
                </div>
            `;
            grid.appendChild(card);
        });
        
        // Update header with count
        const header = grid.closest('.patterns-section').querySelector('h3');
        if (limit === 0) {
            header.textContent = `All ${patterns.length} Unique ${type.charAt(0).toUpperCase() + type.slice(1)} Patterns`;
            showMoreBtn.textContent = 'Show Top 5 Only';
            showMoreBtn.style.display = 'inline-block';
        } else {
            header.textContent = `Top 5 Most Frequent ${type.charAt(0).toUpperCase() + type.slice(1)} Patterns`;
            // Show button only if there are more patterns
            if (totalUnique > 5) {
                showMoreBtn.textContent = `Show All ${totalUnique} Patterns`;
                showMoreBtn.style.display = 'inline-block';
            } else {
                showMoreBtn.style.display = 'none';
            }
        }
        
        // Populate pattern selector dropdown
        const selectId = `${type}-pattern-select`;
        const select = document.getElementById(selectId);
        
        select.innerHTML = '<option value="">-- Select a Pattern --</option>';
        patterns.forEach(pattern => {
            const option = document.createElement('option');
            option.value = pattern.id;
            option.textContent = `Pattern #${pattern.id + 1} (Frequency: ${pattern.count})`;
            select.appendChild(option);
        });
        
    } catch (error) {
        console.error(`Error loading ${type} patterns:`, error);
        showError(grid, `Failed to load ${type} patterns`);
    }
}

/**
 * Set up interactive trial selector for a pattern type
 */
function setupInteractiveSelector(type) {
    const patternSelect = document.getElementById(`${type}-pattern-select`);
    const trialSelect = document.getElementById(`${type}-trial-select`);
    const animateBtn = document.getElementById(`${type}-animate-btn`);
    const finalBtn = document.getElementById(`${type}-final-btn`);
    const visualization = document.getElementById(`${type}-visualization`);
    
    let currentPatternId = null;
    let currentTrials = [];
    
    /**
     * Handle pattern selection change
     */
    patternSelect.addEventListener('change', async function() {
        const patternId = this.value;
        
        if (!patternId) {
            trialSelect.disabled = true;
            trialSelect.innerHTML = '<option value="">-- Select Pattern First --</option>';
            animateBtn.disabled = true;
            finalBtn.disabled = true;
            hideElement(visualization);
            return;
        }
        
        currentPatternId = patternId;
        
        // Fetch trials for this pattern
        try {
            const trials = await fetchJSON(`/api/pattern-trials/${type}/${patternId}`);
            currentTrials = trials;
            
            trialSelect.innerHTML = '<option value="">-- Select Trial --</option>';
            trials.forEach((trial, index) => {
                const option = document.createElement('option');
                option.value = index;
                option.textContent = formatTrialLabel(trial);
                trialSelect.appendChild(option);
            });
            
            trialSelect.disabled = false;
            
        } catch (error) {
            console.error('Error fetching trials for pattern:', error);
            showError(visualization, 'Failed to load trials for this pattern');
            showElement(visualization);
        }
    });
    
    /**
     * Handle trial selection change
     */
    trialSelect.addEventListener('change', function() {
        const trialIndex = this.value;
        
        if (!trialIndex && trialIndex !== 0) {
            animateBtn.disabled = true;
            finalBtn.disabled = true;
            return;
        }
        
        animateBtn.disabled = false;
        finalBtn.disabled = false;
    });
    
    /**
     * Show Animation button click handler
     */
    animateBtn.addEventListener('click', async function() {
        const trialIndex = trialSelect.value;
        if (!trialIndex && trialIndex !== 0) return;
        if (currentTrials.length === 0) return;
        
        const trial = currentTrials[trialIndex];
        
        showLoading(visualization);
        showElement(visualization);
        
        try {
            // UPDATED: Use frame-based animation player
            visualization.innerHTML = `
                <h4 style="color: #667eea; margin-bottom: 1rem;">
                    Animation - Pattern #${parseInt(currentPatternId) + 1}
                </h4>
                <p style="margin-bottom: 1rem; color: #666;">
                    Participant ${trial.participant}, Trial ${trial.trial} | 
                    Condition: ${trial.condition} | Moves: ${trial.moves}
                </p>
                <div id="pattern-animation-player-container"></div>
            `;
            
            // Initialize frame-based animation player
            const player = new AnimationPlayer(
                trial.participant,
                trial.trial,
                'pattern-animation-player-container'
            );
            
            // Store reference for cleanup
            window.currentPatternAnimationPlayer = player;
            
        } catch (error) {
            console.error('Error showing animation:', error);
            showError(visualization, 'Failed to load animation');
        }
    });
    
    /**
     * Show Final State button click handler
     */
    finalBtn.addEventListener('click', function() {
        const trialIndex = trialSelect.value;
        if (!trialIndex && trialIndex !== 0) return;
        if (currentTrials.length === 0) return;
        
        const trial = currentTrials[trialIndex];
        
        visualization.innerHTML = `
            <h4 style="color: #667eea; margin-bottom: 1rem;">
                Final State - Pattern #${parseInt(currentPatternId) + 1}
            </h4>
            <p style="margin-bottom: 1rem; color: #666;">
                Participant ${trial.participant}, Trial ${trial.trial} | 
                Condition: ${trial.condition} | Moves: ${trial.moves}
            </p>
            <div style="text-align: center; margin-top: 1rem;">
                <img src="/api/trial-image/${trial.participant}/${trial.trial}" 
                     alt="Trial visualization" 
                     style="max-width: 100%; height: auto; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            </div>
        `;
        showElement(visualization);
    });
}
