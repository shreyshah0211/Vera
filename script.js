// Helper function to escape HTML for user messages
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Page Navigation
const API_BASE = '';

function showLandingPage() {
    document.getElementById('landingPage').classList.remove('hidden');
    document.getElementById('newAgentPage').classList.add('hidden');
    document.getElementById('chatPage').classList.add('hidden');
    document.body.classList.remove('chat-active');
}

function showNewAgentPage() {
    document.getElementById('landingPage').classList.add('hidden');
    document.getElementById('newAgentPage').classList.remove('hidden');
    document.getElementById('chatPage').classList.add('hidden');
    document.body.classList.remove('chat-active');
}

function showChatPage() {
    document.getElementById('landingPage').classList.add('hidden');
    document.getElementById('newAgentPage').classList.add('hidden');
    document.getElementById('chatPage').classList.remove('hidden');
    document.body.classList.add('chat-active');

    const chatContainer = document.querySelector('.chat-container');
    const emptyState = document.getElementById('emptyState');
    const sidebar = document.querySelector('.sidebar');

    if (agents.length === 0) {
        // Show empty state, hide chat interface and sidebar
        if (chatContainer) {
            chatContainer.style.display = 'none';
        }
        if (sidebar) {
            sidebar.style.display = 'none';
        }
        if (emptyState) {
            emptyState.classList.add('show');
        }
    } else {
        // Hide empty state, show chat interface and sidebar
        if (emptyState) {
            emptyState.classList.remove('show');
        }
        if (sidebar) {
            sidebar.style.display = 'block';
        }
        if (chatContainer) {
            chatContainer.style.display = 'flex';
        }
        if (!currentAgent) {
            loadChat(agents[0].id);
        }
    }

    updateCallLogs();
}

// Calls page removed per latest requirements

// Waveform Visualization
function initWaveform() {
    const canvas = document.getElementById('waveform');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;

    // Set canvas size
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;

    // Animation variables
    let animationId;
    let phase = 0;

    function drawWaveform() {
        ctx.clearRect(0, 0, width, height);

        // Draw multiple wave layers
        drawWave(phase, 0.8, 40, 'rgba(255, 255, 255, 0.3)');
        drawWave(phase + Math.PI / 2, 0.6, 30, 'rgba(255, 255, 255, 0.2)');
        drawWave(phase + Math.PI, 0.4, 25, 'rgba(255, 255, 255, 0.15)');

        phase += 0.03;
        animationId = requestAnimationFrame(drawWaveform);
    }

    function drawWave(phaseOffset, amplitude, frequency, color) {
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 3;

        for (let x = 0; x < width; x++) {
            const y = height / 2 + Math.sin((x / width) * frequency + phaseOffset) * amplitude;
            if (x === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        }

        ctx.stroke();
    }

    drawWaveform();
}

// Agent Data Storage
let agents = [];
let currentAgent = null;

// Create Agent
function createAgent() {
    const userName = document.getElementById('userName').value.trim();
    const receiverName = document.getElementById('receiverName').value.trim();
    const phoneNumber = document.getElementById('phoneNumber').value.trim();
    const callDetails = document.getElementById('callDetails').value.trim();

    // Validate all fields are filled
    if (!userName) {
        alert('Please enter your name');
        return;
    }

    if (!receiverName) {
        alert('Please enter the receiver name');
        return;
    }

    if (!phoneNumber) {
        alert('Please enter a phone number');
        return;
    }

    if (!callDetails) {
        alert('Please enter call details');
        return;
    }

    // Validate phone number format (US format: 10 digits or with country code)
    const phoneRegex = /^[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,5}[-\s\.]?[0-9]{1,5}$/;
    if (!phoneRegex.test(phoneNumber)) {
        alert('Please enter a valid phone number (e.g., 123-456-7890 or +1-123-456-7890)');
        return;
    }

    // Create assistant name as "Receiver Name - Date"
    const currentDate = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    const assistantName = `${receiverName} - ${currentDate}`;

    const agentData = {
        id: Date.now(),
        name: assistantName,
        userName: userName,
        receiver: receiverName,
        phone: phoneNumber,
        callDetails: callDetails,
        messages: [],
        createdAt: new Date().toISOString()
    };

    agents.unshift(agentData);
    currentAgent = agentData;

    // Clear form
    document.getElementById('userName').value = '';
    document.getElementById('receiverName').value = '';
    document.getElementById('phoneNumber').value = '';
    document.getElementById('callDetails').value = '';

    // Add initial user message with call details
    const initialUserMessage = `I need to call ${receiverName} at ${phoneNumber}. ${callDetails}`;
    addMessage('user', initialUserMessage);

    // Get AI response to the initial message
    fetchInitialResponse(agentData, initialUserMessage).then(() => {
        updateCallLogs();
        showChatPage();
        loadChat(agentData.id);
    });

}

// Chat Functions
async function sendMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();

    if (!message || !currentAgent) return;

    // Check if user typed CONFIRM to finalize the call
    if (message.toUpperCase() === 'CONFIRM') {
        await finalizeCall(message);
        return;
    }

    addMessage('user', message);
    input.value = '';

    // Show typing indicator
    const typingDiv = document.createElement('div');
    typingDiv.className = 'message ai typing-indicator';
    typingDiv.id = 'typingIndicator';
    typingDiv.innerHTML = `
        <div class="message-avatar">V</div>
        <div class="message-content">Thinking...</div>
    `;
    const messagesContainer = document.getElementById('chatMessages');
    messagesContainer.appendChild(typingDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    // Get AI response from backend
    try {
        const response = await fetch(`${API_BASE}/api/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                message: message,
                history: currentAgent.messages,
                callDetails: {
                    receiver: currentAgent.receiver,
                    phone: currentAgent.phone,
                    callDetails: currentAgent.callDetails
                }
            })
        });

        const data = await response.json();

        // Remove typing indicator
        const indicator = document.getElementById('typingIndicator');
        if (indicator) {
            indicator.remove();
        }

        if (data.success) {
            addMessage('ai', data.response);
        } else {
            addMessage('ai', 'Sorry, I encountered an error. Please try again.');
        }
    } catch (error) {
        console.error('Error:', error);
        // Remove typing indicator
        const indicator = document.getElementById('typingIndicator');
        if (indicator) {
            indicator.remove();
        }
        addMessage('ai', 'Sorry, I could not connect to the server. Please make sure the backend is running.');
    }
}

async function finalizeCall(confirmMessage) {
    const input = document.getElementById('chatInput');

    addMessage('user', confirmMessage);
    input.value = '';

    // Show processing indicator
    const typingDiv = document.createElement('div');
    typingDiv.className = 'message ai typing-indicator';
    typingDiv.id = 'typingIndicator';
    typingDiv.innerHTML = `
        <div class="message-avatar">V</div>
        <div class="message-content">Finalizing call details...</div>
    `;
    const messagesContainer = document.getElementById('chatMessages');
    messagesContainer.appendChild(typingDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    try {
        const response = await fetch(`${API_BASE}/api/finalize-call`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                assistantId: currentAgent.id,
                assistantName: currentAgent.name,
                history: currentAgent.messages,
                callDetails: {
                    userName: currentAgent.userName,
                    receiver: currentAgent.receiver,
                    phone: currentAgent.phone,
                    callDetails: currentAgent.callDetails
                }
            })
        });

        const data = await response.json();

        // Remove typing indicator
        const indicator = document.getElementById('typingIndicator');
        if (indicator) {
            indicator.remove();
        }

        if (data.success) {
            // Add success message
            const successMessage = `✅ **Call details saved successfully!**\n\n*File saved: ${data.filename}*\n\nVera is now ready to make this call for you!`;
            addMessage('ai', successMessage);

            // Mark assistant as finalized
            currentAgent.finalized = true;
            currentAgent.callDataFile = data.filename;

            // Hide input and Send button, show Call button
            const chatInput = document.getElementById('chatInput');
            const sendButton = document.getElementById('sendButton');
            const callButton = document.getElementById('callButton');

            if (chatInput) {
                chatInput.style.display = 'none';
            }
            if (sendButton) {
                sendButton.style.display = 'none';
            }
            if (callButton) {
                callButton.classList.remove('hidden');
            }
        } else {
            addMessage('ai', 'Sorry, there was an error finalizing the call. Please try again.');
        }
    } catch (error) {
        console.error('Error:', error);
        const indicator = document.getElementById('typingIndicator');
        if (indicator) {
            indicator.remove();
        }
        addMessage('ai', 'Sorry, I could not connect to the server. Please make sure the backend is running.');
    }
}

function addMessage(type, content) {
    if (!currentAgent) return;

    const messageData = {
        type,
        content,
        timestamp: new Date().toISOString()
    };

    currentAgent.messages.push(messageData);

    const messagesContainer = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}`;

    if (type === 'user') {
        messageDiv.innerHTML = `
            <div class="message-content">${escapeHtml(content)}</div>
        `;
    } else {
        // Render markdown for AI messages
        const renderedContent = marked.parse(content);
        messageDiv.innerHTML = `
            <div class="message-avatar">V</div>
            <div class="message-content markdown-content">${renderedContent}</div>
        `;
    }

    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

async function fetchInitialResponse(agent, userMessage) {
    try {
        const response = await fetch(`${API_BASE}/api/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                message: userMessage,
                history: agent.messages,
                callDetails: {
                    receiver: agent.receiver,
                    phone: agent.phone,
                    callDetails: agent.callDetails
                }
            })
        });

        const data = await response.json();

        if (data.success) {
            addMessage('ai', data.response);
        } else {
            addMessage('ai', `Hello! I'm Vera, your personal calling assistant. Let me help you prepare for this call to ${agent.receiver}.`);
        }
    } catch (error) {
        console.error('Error fetching initial response:', error);
        addMessage('ai', `Hello! I'm Vera, your personal calling assistant. Let me help you prepare for this call to ${agent.receiver}.`);
    }
}

function deleteAgent(agentId) {
    if (confirm('Are you sure you want to delete this assistant?')) {
        agents = agents.filter(a => a.id !== agentId);

        // If we deleted the current agent, clear the chat
        if (currentAgent && currentAgent.id === agentId) {
            currentAgent = null;

            // Clear the chat messages
            const messagesContainer = document.getElementById('chatMessages');
            if (messagesContainer) {
                messagesContainer.innerHTML = '';
            }

            // If no agents left, show empty state
            if (agents.length === 0) {
                const chatContainer = document.querySelector('.chat-container');
                const emptyState = document.getElementById('emptyState');

                if (chatContainer) {
                    chatContainer.style.display = 'none';
                }
                if (emptyState) {
                    emptyState.classList.add('show');
                }
                updateCallLogs();
                return;
            }
            // Otherwise load the first agent
            loadChat(agents[0].id);
        } else {
            // Just update the logs if we didn't delete the current agent
            updateCallLogs();
        }
    }
}

function updateCallLogs() {
    const logsContainer = document.getElementById('callLogs');
    logsContainer.innerHTML = '';

    agents.forEach(agent => {
        const logItem = document.createElement('div');
        logItem.className = 'call-log-item';
        if (currentAgent && agent.id === currentAgent.id) {
            logItem.classList.add('active');
        }

        const date = new Date(agent.createdAt);
        const timeStr = formatTimeAgo(date);

        logItem.innerHTML = `
            <div class="call-log-content" onclick="loadChat(${agent.id})">
                <div class="call-log-name">${agent.name}</div>
                <div class="call-log-time">${timeStr}</div>
            </div>
            <button class="delete-btn" onclick="event.stopPropagation(); deleteAgent(${agent.id})" title="Delete assistant">×</button>
        `;

        logsContainer.appendChild(logItem);
    });
}

function loadChat(agentId) {
    const agent = agents.find(a => a.id === agentId);
    if (!agent) return;

    currentAgent = agent;
    document.getElementById('currentAgentName').textContent = agent.name;

    // Show chat container, hide empty state
    const chatContainer = document.querySelector('.chat-container');
    const emptyState = document.getElementById('emptyState');

    if (chatContainer) {
        chatContainer.style.display = 'flex';
    }
    if (emptyState) {
        emptyState.classList.remove('show');
    }

    const messagesContainer = document.getElementById('chatMessages');
    messagesContainer.innerHTML = '';

    agent.messages.forEach(msg => {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${msg.type}`;

        if (msg.type === 'user') {
            messageDiv.innerHTML = `
                <div class="message-content">${escapeHtml(msg.content)}</div>
            `;
        } else {
            // Render markdown for AI messages
            const renderedContent = marked.parse(msg.content);
            messageDiv.innerHTML = `
                <div class="message-avatar">V</div>
                <div class="message-content markdown-content">${renderedContent}</div>
            `;
        }

        messagesContainer.appendChild(messageDiv);
    });

    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    updateCallLogs();

    // Update input and button state based on finalized status
    const chatInput = document.getElementById('chatInput');
    const sendButton = document.getElementById('sendButton');
    const callButton = document.getElementById('callButton');

    if (agent.finalized) {
        // Show only Call button
        if (chatInput) chatInput.style.display = 'none';
        if (sendButton) sendButton.style.display = 'none';
        if (callButton) callButton.classList.remove('hidden');
    } else {
        // Show input and Send button
        if (chatInput) {
            chatInput.style.display = 'flex';
            chatInput.disabled = false;
            chatInput.placeholder = 'Ask about your call...';
        }
        if (sendButton) sendButton.style.display = 'block';
        if (callButton) callButton.classList.add('hidden');
    }
}

function initiateCall() {
    if (!currentAgent || !currentAgent.callDataFile) {
        alert('No call data available');
        return;
    }

    // Send only the filename to the backend; backend will read JSON server-side
    // include assistant id so webhook summary can be routed back to chat
    fetch(`${API_BASE}/api/calls/outbound`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            call_data_file: currentAgent.callDataFile,
            assistant_id: currentAgent.id
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert(`Call initiation failed: ${data.message || data.error}`);
        } else {
            // Grey-out call button while in progress
            const callButton = document.getElementById('callButton');
            if (callButton) {
                callButton.disabled = true;
                callButton.textContent = 'Call in progress...';
                callButton.style.opacity = '0.6';
            }
            ensureSSE();
        }
    })
    .catch(error => {
        console.error('Error initiating call:', error);
        alert(`Sorry, I could not initiate the call. Please make sure the backend is running.\n\nError: ${error.message}`);
    });
}

// SSE state
let eventSource = null;

function ensureSSE() {
    if (eventSource) return;
    eventSource = new EventSource(`/api/calls/stream`);
    eventSource.onmessage = (e) => {
        try {
            const payload = JSON.parse(e.data);
            if (payload && payload.data) {
                handleCallEvent(payload.event, payload.data);
            }
        } catch (err) { /* noop */ }
    };
    eventSource.onerror = () => {
        // Keep the connection alive; browser will retry
    };
}

function handleCallEvent(event, data) {
    if (event === 'call_updated') {
        // No-op for finished; UI will be updated on 'call_summary'
    } else if (event === 'call_summary') {
        // Summary prepared by backend; route it to the correct assistant
        const summary = data.summary;
        const assistantId = data.assistant_id;
        if (!summary) return;
        const target = agents.find(a => a.id === assistantId);
        if (target) {
            // Append to that agent's history
            target.messages.push({
                type: 'ai',
                content: `### Call Summary\n\n${summary}`,
                timestamp: new Date().toISOString()
            });
            // If the current agent is the target, also render
            if (currentAgent && currentAgent.id === target.id) {
                addMessage('ai', `### Call Summary\n\n${summary}`);
                const callButton = document.getElementById('callButton');
                if (callButton) {
                    callButton.disabled = true; // keep disabled; do not allow re-calling
                    callButton.textContent = 'Call completed';
                    callButton.style.opacity = '0.3';
                }
            } else {
                // Not current: update logs badge/state if needed later
                updateCallLogs();
            }
        }
    }
}

function formatTimeAgo(date) {
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);

    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
}

// Enter key to send message
document.addEventListener('DOMContentLoaded', () => {
    initWaveform();

    const chatInput = document.getElementById('chatInput');
    if (chatInput) {
        chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });
    }
});

// Handle window resize for waveform
window.addEventListener('resize', () => {
    if (!document.getElementById('landingPage').classList.contains('hidden')) {
        initWaveform();
    }
});
