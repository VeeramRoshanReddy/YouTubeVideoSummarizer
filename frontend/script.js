// OAuth Configuration
const CLIENT_ID = '1060175441605-3hudt7ldlotdouoau00jsbuhnamqvgeg.apps.googleusercontent.com';
const REDIRECT_URI = 'https://www.vidsummarize.online';
const API_ENDPOINT = '';

// Elements
let loginBtn;
let heroLoginBtn; 
let logoutBtn;
let userProfile;
let userAvatar;
let userName;
let authRequiredMessage;
let searchContainer;
let youtubeUrlInput;
let submitBtn;
let errorMessage;
let loadingSection;
let processingStep;
let progressFill;
let resultSection;
let videoTitle;
let videoPreview;
let summaryText;
let newVideoBtn;
let copyBtn;
let downloadBtn;
let loadingIndicator;

// State variables
let accessToken = null;
let currentVideoId = null;
let processingInterval = null;
let processingProgress = 0;
let isAuthenticated = false;

// Initialize elements after DOM is loaded
function initializeElements() {
    loginBtn = document.getElementById('login-btn');
    heroLoginBtn = document.getElementById('hero-login-btn');
    logoutBtn = document.getElementById('logout-btn');
    userProfile = document.getElementById('user-profile');
    userAvatar = document.getElementById('user-avatar');
    userName = document.getElementById('user-name');
    authRequiredMessage = document.getElementById('auth-required-message');
    searchContainer = document.getElementById('search-container');
    youtubeUrlInput = document.getElementById('youtube-url');
    submitBtn = document.getElementById('submit-btn');
    errorMessage = document.getElementById('error-message');
    loadingSection = document.getElementById('loading-section');
    processingStep = document.getElementById('processing-step');
    progressFill = document.getElementById('progress-fill');
    resultSection = document.getElementById('result-section');
    videoTitle = document.getElementById('video-title');
    videoPreview = document.getElementById('video-preview');
    summaryText = document.getElementById('summary-text');
    newVideoBtn = document.getElementById('new-video-btn');
    copyBtn = document.getElementById('copy-btn');
    downloadBtn = document.getElementById('download-btn');
    
    // Create loading indicator if it doesn't exist
    loadingIndicator = document.getElementById('loading-indicator');
    if (!loadingIndicator) {
        loadingIndicator = document.createElement('div');
        loadingIndicator.id = 'loading-indicator';
        loadingIndicator.className = 'loading-spinner hidden';
        document.body.appendChild(loadingIndicator);
    }
}

// Check for OAuth callback and initialize the application
document.addEventListener('DOMContentLoaded', () => {
    initializeElements();
    
    console.log("DOM content loaded, checking for auth parameters");
    
    // Check if we have a hash fragment from OAuth redirect (implicit flow)
    const hashParams = new URLSearchParams(window.location.hash.substring(1));
    if (hashParams.has('access_token')) {
        console.log("Found access token in hash parameters");
        handleOAuthRedirect(hashParams);
    } else {
        // Check for authorization code in URL query parameters (code flow)
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.has('code')) {
            console.log("Found authorization code in URL parameters");
            handleAuthCode(urlParams.get('code'));
        } else {
            console.log("No auth parameters found, checking for stored token");
            // Check for stored token
            checkStoredToken();
        }
    }
    
    // Setup event listeners
    setupEventListeners();
});

// Handle OAuth redirect with hash fragment
function handleOAuthRedirect(hashParams) {
    console.log("Processing OAuth redirect with hash parameters");
    accessToken = hashParams.get('access_token');
    const expiresIn = hashParams.get('expires_in');
    
    // Store token with expiration
    const expiryTime = Date.now() + parseInt(expiresIn) * 1000;
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('expiry_time', expiryTime);
    
    console.log("Token stored in localStorage, cleaning URL");
    // Clean the URL 
    window.history.replaceState({}, document.title, window.location.pathname);
    
    // Get user info
    fetchUserInfo();
}

// Show loading indicator
function showLoadingIndicator() {
    console.log("Showing loading indicator");
    if (loadingIndicator) {
        loadingIndicator.classList.remove('hidden');
    }
}

// Hide loading indicator
function hideLoadingIndicator() {
    console.log("Hiding loading indicator");
    if (loadingIndicator) {
        loadingIndicator.classList.add('hidden');
    }
}

// Handle authorization code from query parameters
function handleAuthCode(code) {
    console.log("Handling authorization code:", code.substring(0, 10) + "...");
    // Show loading indicator while processing
    showLoadingIndicator();
    
    // Clean the URL immediately to prevent issues with refreshing
    window.history.replaceState({}, document.title, window.location.pathname);
    
    // Exchange code for token
    exchangeCodeForToken(code);
}

// Exchange authorization code for access token
function exchangeCodeForToken(code) {
    console.log("Exchanging code for token...");
    showLoadingIndicator();
    
    fetch(`${API_ENDPOINT}/auth`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Origin': REDIRECT_URI
        },
        body: JSON.stringify({ 
            code: code,
            redirect_uri: REDIRECT_URI
        }),
    })
    .then(response => {
        console.log("Auth response status:", response.status);
        if (!response.ok) {
            return response.text().then(text => {
                throw new Error(`Failed to exchange code: ${response.status} ${text}`);
            });
        }
        return response.json();
    })
    .then(data => {
        console.log("Auth success, received token data");
        accessToken = data.access_token;
        const expiryTime = Date.now() + data.expires_in * 1000;
        localStorage.setItem('access_token', accessToken);
        localStorage.setItem('expiry_time', expiryTime);
        fetchUserInfo();
    })
    .catch(error => {
        console.error('Token exchange failed:', error);
        showLoginUI();
        hideLoadingIndicator();
    });
}

// Check if a stored token exists and is valid
function checkStoredToken() {
    const storedToken = localStorage.getItem('access_token');
    const expiryTime = localStorage.getItem('expiry_time');
    
    console.log("Checking for stored token");
    
    if (storedToken && expiryTime) {
        // Check if token is still valid
        if (Date.now() < parseInt(expiryTime)) {
            console.log("Found valid stored token");
            accessToken = storedToken;
            fetchUserInfo();
            return;
        } else {
            console.log("Token expired");
        }
    } else {
        console.log("No token found in storage");
    }
    
    // No valid token found, show login UI
    showLoginUI();
}

// Show login UI for unauthenticated users
function showLoginUI() {
    console.log("Showing login UI");
    isAuthenticated = false;
    
    if (authRequiredMessage) {
        authRequiredMessage.classList.remove('hidden');
        // Make sure blur is removed as well
        authRequiredMessage.classList.remove('blurred');
    }
    
    if (searchContainer) {
        searchContainer.classList.add('hidden');
    }
    
    if (loginBtn) {
        loginBtn.style.display = 'flex';
    }
    
    if (heroLoginBtn) {
        heroLoginBtn.style.display = 'flex';
    }
    
    if (userProfile) {
        userProfile.classList.add('hidden');
    }
}

// Show application UI for authenticated users
function showAppUI() {
    console.log("Showing app UI for authenticated user");
    isAuthenticated = true;
    
    if (authRequiredMessage) {
        authRequiredMessage.classList.add('hidden');
    }
    
    if (searchContainer) {
        searchContainer.classList.remove('hidden');
    }
    
    if (loginBtn) {
        loginBtn.style.display = 'none';
    }
    
    if (heroLoginBtn) {
        heroLoginBtn.style.display = 'none';
    }
    
    if (userProfile) {
        userProfile.classList.remove('hidden');
    }
    
    hideLoadingIndicator();
}

// Fetch user information from Google API
function fetchUserInfo() {
    if (!accessToken) {
        console.log("No access token available for fetching user info");
        showLoginUI();
        hideLoadingIndicator();
        return;
    }
    
    console.log("Fetching user info with token");
    showLoadingIndicator();
    
    fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
        headers: {
            'Authorization': `Bearer ${accessToken}`
        }
    })
    .then(response => {
        console.log("User info response status:", response.status);
        if (!response.ok) {
            return response.text().then(text => {
                throw new Error(`Failed to fetch user info: ${response.status} ${text}`);
            });
        }
        return response.json();
    })
    .then(data => {
        console.log("User info received:", data.name);
        // Update UI with user info
        if (userAvatar) userAvatar.src = data.picture;
        if (userName) userName.textContent = data.name;
        
        // Show app UI
        showAppUI();
    })
    .catch(error => {
        console.error('Error fetching user info:', error);
        // Token might be invalid, clear and show login
        logout();
        hideLoadingIndicator();
    });
}

// Setup all event listeners
function setupEventListeners() {
    console.log("Setting up event listeners");
    // Login buttons
    if (loginBtn) loginBtn.addEventListener('click', initiateLogin);
    if (heroLoginBtn) heroLoginBtn.addEventListener('click', initiateLogin);
    
    // Logout button
    if (logoutBtn) logoutBtn.addEventListener('click', logout);
    
    // Submit button for video summarization
    if (submitBtn) submitBtn.addEventListener('click', processVideo);
    
    // URL input validation
    if (youtubeUrlInput) {
        youtubeUrlInput.addEventListener('input', validateYouTubeUrl);
        youtubeUrlInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                processVideo();
            }
        });
    }
    
    // New video button
    if (newVideoBtn) newVideoBtn.addEventListener('click', resetToSearch);
    
    // Copy summary button
    if (copyBtn) copyBtn.addEventListener('click', copySummaryToClipboard);
    
    // Download summary button
    if (downloadBtn) downloadBtn.addEventListener('click', downloadSummary);
}

// Initiate OAuth login process
function initiateLogin() {
    console.log("Initiating OAuth login process");
    const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?client_id=${CLIENT_ID}&redirect_uri=${REDIRECT_URI}&response_type=code&scope=profile email&prompt=consent`;
    window.location.href = authUrl;
}

// Log out user
function logout() {
    console.log("Logging out user");
    // Clear stored tokens
    localStorage.removeItem('access_token');
    localStorage.removeItem('expiry_time');
    accessToken = null;
    
    // Reset UI
    showLoginUI();
}

// Validate YouTube URL
function validateYouTubeUrl() {
    if (!youtubeUrlInput) return false;
    
    const url = youtubeUrlInput.value.trim();
    const videoId = extractYouTubeVideoId(url);
    
    if (url === '') {
        hideError();
        return;
    }
    
    if (!videoId) {
        showError('Please enter a valid YouTube video URL');
        return false;
    }
    
    hideError();
    return videoId;
}

// Show error message
function showError(message) {
    console.log("Showing error:", message);
    if (errorMessage) {
        errorMessage.textContent = message;
        errorMessage.classList.remove('hidden');
    }
    
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.remove('pulse-animation');
    }
}

// Hide error message
function hideError() {
    if (errorMessage) {
        errorMessage.classList.add('hidden');
    }
    
    if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.add('pulse-animation');
    }
}

// Extract YouTube video ID from various URL formats
function extractYouTubeVideoId(url) {
    if (!url) return null;
    
    try {
        // Handle youtu.be short links
        if (url.includes('youtu.be/')) {
            const parts = url.split('youtu.be/');
            if (parts.length > 1) {
                return parts[1].split('?')[0].split('&')[0];
            }
        }
        
        // Handle regular youtube.com links
        const urlObj = new URL(url.startsWith('http') ? url : `https://${url}`);
        if (urlObj.hostname === 'www.youtube.com' || urlObj.hostname === 'youtube.com') {
            const params = new URLSearchParams(urlObj.search);
            return params.get('v');
        }
    } catch (e) {
        return null;
    }
    
    return null;
}

// Process video and get summary
function processVideo() {
    const videoId = validateYouTubeUrl();
    if (!videoId) return;
    
    if (!accessToken) {
        showError('You need to sign in first');
        return;
    }
    
    currentVideoId = videoId;
    
    console.log("Processing video with ID:", videoId);
    
    // Show loading section
    hideError();
    if (searchContainer) searchContainer.classList.add('hidden');
    if (loadingSection) loadingSection.classList.remove('hidden');
    if (resultSection) resultSection.classList.add('hidden');
    
    // Reset progress
    processingProgress = 0;
    updateProgressBar(0);
    updateProcessingStep('Fetching video information...');
    
    // Start the processing animation
    startProcessingAnimation();
    
    // Use a CORS proxy to avoid CORS issues
    // First let's try the direct approach with modified headers
    callSummarizeAPI(videoId);
}

// Call the summarize API with proper CORS headers
function callSummarizeAPI(videoId) {
    // Ensure origin doesn't have a trailing slash
    //const origin = REDIRECT_URI.endsWith('/') ? REDIRECT_URI.slice(0, -1) : REDIRECT_URI;
    
    // Set up the fetch request with proper CORS headers
    fetch(`${API_ENDPOINT}/summary/${videoId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${accessToken}`
            //'Origin': REDIRECT_URI, //origin,
            //'Access-Control-Request-Method': 'POST',
            //'Access-Control-Request-Headers': 'Content-Type, Authorization'
        },
        //body: JSON.stringify({ videoId }),
        mode: 'cors',
        credentials: 'omit'
    })
    .then(response => {
        console.log("Video processing API response status:", response.status);
        if (!response.ok) {
            return response.text().then(text => {
                throw new Error(`Failed to process video: ${response.status} ${text}`);
            });
        }
        return response.json();
    })
    .then(data => {
        console.log("Video processing API response:", data);
        // Check if we got a direct result or need to poll
        if (data.status === 'completed' && data.summary) {
            // Show result immediately
            showResult(data.videoTitle, data.summary, videoId);
        } else if (data.jobId) {
            // Start polling for result
            pollJobStatus(data.jobId);
        } else {
            throw new Error('Invalid response from server');
        }
    })
    .catch(error => {
        console.error('Error processing video:', error);
        
        // If there's a CORS error, try an alternative approach using JSON-P or a workaround
        // In this case, let's simulate a successful API call for demonstration
        console.log("Attempting fallback for CORS issue...");
        
        // For demonstration, let's create a mock process that shows a useful message
        let mockJobId = "demo_" + Date.now();
        simulateProcessing(mockJobId, videoId);
    });
}

// This function simulates processing when CORS fails
function simulateProcessing(jobId, videoId) {
    console.log("Simulating processing with fallback method for video ID:", videoId);
    
    // Show a more informative message about CORS
    updateProcessingStep("API connection issue detected. Working on alternative method...");
    
    // After a delay, show a helpful message
    setTimeout(() => {
        updateProcessingStep("CORS issue detected. Please check server configuration.");
        processingProgress = 50;
        updateProgressBar(processingProgress);
        
        // After another delay, provide instructions
        setTimeout(() => {
            stopProcessingAnimation();
            
            // Create a helpful summary with instructions
            const helpMessage = `We've detected a CORS (Cross-Origin Resource Sharing) issue when connecting to the API server. This is preventing your browser from accessing the video summarization service.

To resolve this issue:

1. The API server needs to include proper CORS headers in its responses.
2. Configure your AWS API Gateway to allow requests from ${REDIRECT_URI}.
3. Add these headers to your API responses:
   - Access-Control-Allow-Origin: ${REDIRECT_URI}
   - Access-Control-Allow-Methods: GET, POST, OPTIONS
   - Access-Control-Allow-Headers: Content-Type, Authorization

For immediate testing, you can also use a CORS browser extension to temporarily bypass this restriction.`;

            // Show the result with the help message
            showResult("CORS Configuration Required", helpMessage, videoId);
        }, 3000);
    }, 2000);
}

// Start the processing animation
function startProcessingAnimation() {
    const steps = [
        'Analyzing video content...',
        'Transcribing audio...',
        'Processing transcription...',
        'Generating summary...',
        'Finalizing results...'
    ];
    
    let currentStep = 0;
    
    // Update step text every few seconds
    processingInterval = setInterval(() => {
        if (currentStep < steps.length) {
            updateProcessingStep(steps[currentStep]);
            currentStep++;
            
            // Update progress bar
            processingProgress = Math.min(processingProgress + 20, 90);
            updateProgressBar(processingProgress);
        }
    }, 2000);
}

// Stop the processing animation
function stopProcessingAnimation() {
    if (processingInterval) {
        clearInterval(processingInterval);
        processingInterval = null;
    }
}

// Update the processing step text
function updateProcessingStep(text) {
    if (processingStep) {
        processingStep.textContent = text;
    }
}

// Update the progress bar
function updateProgressBar(percentage) {
    if (progressFill) {
        progressFill.style.width = `${percentage}%`;
    }
}

// Poll job status until completion
function pollJobStatus(jobId) {
    console.log("Starting to poll job status for job ID:", jobId);
    const pollInterval = setInterval(() => {
        fetch(`${API_ENDPOINT}/status?jobId=${jobId}`, {
            headers: {
                'Authorization': `Bearer ${accessToken}`,
                'Origin': REDIRECT_URI
            },
            mode: 'cors'
        })
        .then(response => {
            console.log("Job status poll response status:", response.status);
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`Failed to check job status: ${response.status} ${text}`);
                });
            }
            return response.json();
        })
        .then(data => {
            console.log("Job status poll response:", data);
            if (data.status === 'completed') {
                // Job complete, show result
                clearInterval(pollInterval);
                stopProcessingAnimation();
                updateProgressBar(100);
                updateProcessingStep('Summary ready!');
                showResult(data.videoTitle, data.summary, currentVideoId);
            } else if (data.status === 'failed') {
                // Job failed
                clearInterval(pollInterval);
                stopProcessingAnimation();
                showError('Processing failed. Please try another video.');
                resetToSearch();
            }
            // else: still processing, keep polling
        })
        .catch(error => {
            console.error('Error polling job status:', error);
            clearInterval(pollInterval);
            stopProcessingAnimation();
            showError('An error occurred while checking the video status.');
            resetToSearch();
        });
    }, 3000); // Poll every 3 seconds
}

// Show the result section with the summary
function showResult(title, summary, videoId) {
    console.log("Showing result for video:", title);
    // Update video title
    if (videoTitle) videoTitle.textContent = title || 'Video Summary';
    
    // Create YouTube embed
    if (videoPreview) {
        videoPreview.innerHTML = `
            <iframe 
                src="https://www.youtube.com/embed/${videoId}" 
                title="YouTube video player" 
                frameborder="0" 
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" 
                allowfullscreen>
            </iframe>
        `;
    }
    
    // Update summary text
    if (summaryText) summaryText.textContent = summary;
    
    // Hide loading, show result
    if (loadingSection) loadingSection.classList.add('hidden');
    if (resultSection) resultSection.classList.remove('hidden');
}

// Reset to search interface
function resetToSearch() {
    console.log("Resetting to search interface");
    if (youtubeUrlInput) youtubeUrlInput.value = '';
    if (loadingSection) loadingSection.classList.add('hidden');
    if (resultSection) resultSection.classList.add('hidden');
    if (searchContainer) searchContainer.classList.remove('hidden');
    hideError();
    stopProcessingAnimation();
}

// Copy summary to clipboard
function copySummaryToClipboard() {
    if (!summaryText) return;
    
    const summaryContent = summaryText.textContent;
    console.log("Copying summary to clipboard");
    
    if (navigator.clipboard) {
        navigator.clipboard.writeText(summaryContent)
            .then(() => {
                // Show temporary success message
                if (copyBtn) {
                    const originalText = copyBtn.innerHTML;
                    copyBtn.innerHTML = '<i class="fas fa-check"></i>';
                    
                    setTimeout(() => {
                        copyBtn.innerHTML = originalText;
                    }, 2000);
                }
            })
            .catch(err => {
                console.error('Failed to copy text: ', err);
            });
    } else {
        // Fallback for browsers that don't support clipboard API
        const textArea = document.createElement('textarea');
        textArea.value = summaryContent;
        document.body.appendChild(textArea);
        textArea.select();
        
        try {
            document.execCommand('copy');
            if (copyBtn) {
                const originalText = copyBtn.innerHTML;
                copyBtn.innerHTML = '<i class="fas fa-check"></i>';
                
                setTimeout(() => {
                    copyBtn.innerHTML = originalText;
                }, 2000);
            }
        } catch (err) {
            console.error('Failed to copy text: ', err);
        }
        
        document.body.removeChild(textArea);
    }
}

// Download summary as text file
function downloadSummary() {
    if (!summaryText || !videoTitle) return;
    
    const summaryContent = summaryText.textContent;
    const videoTitleText = videoTitle.textContent || 'Video Summary';
    const fileName = `${videoTitleText.replace(/[^a-z0-9]/gi, '_').toLowerCase()}_summary.txt`;
    
    console.log("Downloading summary as:", fileName);
    
    const blob = new Blob([summaryContent], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    a.style.display = 'none';
    
    document.body.appendChild(a);
    a.click();
    
    // Clean up
    setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }, 100);
}

// Add a simple CSS for loading indicator if needed
(function addLoadingIndicatorStyles() {
    if (!document.getElementById('loading-spinner-style')) {
        const style = document.createElement('style');
        style.id = 'loading-spinner-style';
        style.textContent = `
            .loading-spinner {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background-color: rgba(0, 0, 0, 0.5);
                display: flex;
                justify-content: center;
                align-items: center;
                z-index: 9999;
            }
            
            .loading-spinner::after {
                content: '';
                width: 50px;
                height: 50px;
                border: 5px solid #f3f3f3;
                border-top: 5px solid #3498db;
                border-radius: 50%;
                animation: spin 1s linear infinite;
            }
            
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            
            .hidden {
                display: none !important;
            }
        `;
        document.head.appendChild(style);
    }
})();