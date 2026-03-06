/**
 * AnimationPlayer - Client-Side Frame-by-Frame Animation
 * Production-safe: Fetches frames on-demand instead of loading entire animation
 * 
 * Usage:
 *   const player = new AnimationPlayer(participantId, trialId, 'container-id');
 */

class AnimationPlayer {
    constructor(participant, trial, containerId) {
        this.participant = participant;
        this.trial = trial;
        this.container = document.getElementById(containerId);
        
        this.currentFrame = 0;
        this.totalFrames = 0;
        this.isPlaying = false;
        this.isLoading = true;
        this.frameCache = new Map();
        
        this.frameRate = 2; // Frames per second
        this.playInterval = null;
        
        this.init();
    }
    
    async init() {
        try {
            // Fetch animation metadata
            const response = await fetch(`/api/animation-info/${this.participant}/${this.trial}`);
            
            if (!response.ok) {
                throw new Error('Failed to load animation info');
            }
            
            const info = await response.json();
            
            this.totalFrames = info.total_frames;
            this.condition = info.condition;
            this.success = info.success;
            this.totalMoves = info.total_moves;
            
            this.buildUI();
            
            // Load and display first frame
            await this.loadFrame(0);
            this.displayFrame(0);
            
            // Preload next frames in background
            this.preloadFrames(1, 3);
            
            this.isLoading = false;
            
        } catch (error) {
            console.error('Animation init error:', error);
            this.container.innerHTML = `
                <div style="padding: 2rem; text-align: center; color: #e74c3c;">
                    <h3>Failed to load animation</h3>
                    <p>${error.message}</p>
                </div>
            `;
        }
    }
    
    buildUI() {
        this.container.innerHTML = `
            <div class="animation-player">
                <div class="animation-header" style="text-align: center; margin-bottom: 0.75rem;">
                    <h3 style="color: #667eea; margin: 0 0 0.5rem 0; font-size: 1.25rem;">Animation - Participant ${this.participant}, Trial ${this.trial}</h3>
                    <p style="margin: 0; font-size: 0.9rem; color: #666;">
                        <strong>Condition:</strong> ${this.condition} | 
                        <strong>Moves:</strong> ${this.totalMoves} | 
                        ${this.success ? '<span style="color: green; font-weight: bold;">✓ Success</span>' : '<span style="color: red; font-weight: bold;">✗ Failed</span>'}
                    </p>
                </div>
                
                <div class="animation-display" style="position: relative; max-width: 600px; margin: 0 auto;">
                    <img id="animation-frame-img" 
                         alt="Animation frame" 
                         style="width: 100%; height: 500px; object-fit: contain; border: 1px solid #ddd; border-radius: 8px; background: #f5f5f5; display: block;">

                </div>
                
                <div class="animation-controls" style="max-width: 600px; margin: 1rem auto 0; padding: 0.75rem; background: #f9f9f9; border-radius: 8px;">
                    <div style="display: flex; gap: 0.4rem; margin-bottom: 0.75rem; justify-content: center; flex-wrap: wrap;">
                        <button id="play-pause-btn" class="btn" style="padding: 0.4rem 0.8rem; font-size: 0.85rem; min-width: 70px;">▶ Play</button>
                        <button id="stop-btn" class="btn" style="padding: 0.4rem 0.8rem; font-size: 0.85rem; min-width: 60px;">⏹ Stop</button>
                        <button id="prev-btn" class="btn" style="padding: 0.4rem 0.8rem; font-size: 0.85rem; min-width: 60px;">⏮ Prev</button>
                        <button id="next-btn" class="btn" style="padding: 0.4rem 0.8rem; font-size: 0.85rem; min-width: 60px;">⏭ Next</button>
                    </div>
                    
                    <div class="frame-slider" style="width: 100%; margin-bottom: 0.75rem;">
                        <input type="range" 
                               id="frame-slider" 
                               min="0" 
                               max="${this.totalFrames - 1}" 
                               value="0" 
                               style="width: 100%; height: 6px; cursor: pointer;">
                        <div id="frame-label" style="text-align: center; color: #666; margin-top: 0.4rem; font-size: 0.85rem;">
                            Frame: 0 / ${this.totalFrames - 1}
                        </div>
                    </div>
                    
                    <div class="speed-control" style="display: flex; align-items: center; gap: 0.4rem; justify-content: center;">
                        <label style="font-size: 0.85rem; margin: 0;">Speed:</label>
                        <select id="speed-select" class="form-control" style="padding: 0.3rem 0.5rem; font-size: 0.85rem; border: 1px solid #ddd; border-radius: 4px;">
                            <option value="0.5">0.5x</option>
                            <option value="1">1x</option>
                            <option value="2" selected>2x</option>
                            <option value="4">4x</option>
                        </select>
                    </div>
                </div>
            </div>
        `;
        
        this.attachEventListeners();
    }
    
    attachEventListeners() {
        document.getElementById('play-pause-btn').addEventListener('click', () => {
            this.togglePlayPause();
        });
        
        document.getElementById('stop-btn').addEventListener('click', () => {
            this.stop();
        });
        
        document.getElementById('prev-btn').addEventListener('click', () => {
            this.previousFrame();
        });
        
        document.getElementById('next-btn').addEventListener('click', () => {
            this.nextFrame();
        });
        
        document.getElementById('frame-slider').addEventListener('input', (e) => {
            const frame = parseInt(e.target.value);
            this.goToFrame(frame);
        });
        
        document.getElementById('speed-select').addEventListener('change', (e) => {
            this.frameRate = parseFloat(e.target.value);
            if (this.isPlaying) {
                this.stop();
                this.play();
            }
        });
    }
    
    async loadFrame(frameIndex) {
        if (this.frameCache.has(frameIndex)) {
            return this.frameCache.get(frameIndex);
        }
        
        try {
            const url = `/api/animation-frame/${this.participant}/${this.trial}/${frameIndex}`;
            const response = await fetch(url);
            
            if (!response.ok) {
                throw new Error(`Failed to load frame ${frameIndex}`);
            }
            
            const blob = await response.blob();
            const imageUrl = URL.createObjectURL(blob);
            
            this.frameCache.set(frameIndex, imageUrl);
            return imageUrl;
            
        } catch (error) {
            console.error(`Error loading frame ${frameIndex}:`, error);
            throw error;
        }
    }
    
    async displayFrame(frameIndex) {
        try {
            const imageUrl = await this.loadFrame(frameIndex);
            
            const img = document.getElementById('animation-frame-img');
            if (img) {
                img.src = imageUrl;
            }
            
            this.currentFrame = frameIndex;
            this.updateUI();
            
        } catch (error) {
            console.error('Display frame error:', error);
        }
    }
    
    async preloadFrames(startFrame, count) {
        const promises = [];
        for (let i = 0; i < count; i++) {
            const frame = startFrame + i;
            if (frame < this.totalFrames && !this.frameCache.has(frame)) {
                promises.push(this.loadFrame(frame).catch(err => {
                    console.warn(`Failed to preload frame ${frame}:`, err);
                }));
            }
        }
        await Promise.all(promises);
    }
    
    updateUI() {
        const slider = document.getElementById('frame-slider');
        if (slider) slider.value = this.currentFrame;
        
        const label = document.getElementById('frame-label');
        if (label) {
            label.textContent = `Frame: ${this.currentFrame} / ${this.totalFrames - 1}`;
        }
    }
    
    play() {
        if (this.isPlaying) return;
        
        this.isPlaying = true;
        const playPauseBtn = document.getElementById('play-pause-btn');
        if (playPauseBtn) playPauseBtn.textContent = '⏸ Pause';
        
        const intervalMs = 1000 / this.frameRate;
        
        this.playInterval = setInterval(async () => {
            const nextFrame = this.currentFrame + 1;
            
            if (nextFrame >= this.totalFrames) {
                this.stop();
                return;
            }
            
            if (nextFrame + 2 < this.totalFrames) {
                this.preloadFrames(nextFrame + 2, 2);
            }
            
            await this.displayFrame(nextFrame);
            
        }, intervalMs);
    }
    
    pause() {
        this.isPlaying = false;
        if (this.playInterval) {
            clearInterval(this.playInterval);
            this.playInterval = null;
        }
        
        const playPauseBtn = document.getElementById('play-pause-btn');
        if (playPauseBtn) playPauseBtn.textContent = '▶ Play';
    }
    
    togglePlayPause() {
        if (this.isPlaying) {
            this.pause();
        } else {
            this.play();
        }
    }
    
    stop() {
        this.pause();
        this.goToFrame(0);
    }
    
    async goToFrame(frameIndex) {
        if (frameIndex < 0 || frameIndex >= this.totalFrames) return;
        
        if (this.isPlaying) {
            this.pause();
        }
        
        await this.displayFrame(frameIndex);
    }
    
    async previousFrame() {
        const prevFrame = Math.max(0, this.currentFrame - 1);
        await this.goToFrame(prevFrame);
    }
    
    async nextFrame() {
        const nextFrame = Math.min(this.totalFrames - 1, this.currentFrame + 1);
        await this.goToFrame(nextFrame);
    }
    
    destroy() {
        if (this.playInterval) {
            clearInterval(this.playInterval);
        }
        
        this.frameCache.forEach(url => {
            URL.revokeObjectURL(url);
        });
        
        this.frameCache.clear();
    }
}

// Make available globally
window.AnimationPlayer = AnimationPlayer;
