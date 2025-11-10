# Vera - Setup Instructions

## Prerequisites
- Python 3.8 or higher
- OpenAI API key

## Setup Steps

### 1. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 2. Set Your OpenAI API Key

**Option A: Environment Variable (Recommended)**
```bash
# Windows
set OPENAI_API_KEY=your-api-key-here

# Mac/Linux
export OPENAI_API_KEY=your-api-key-here
```

**Option B: Direct in Code**
Open `server.py` and replace `YOUR_API_KEY_HERE` with your actual API key:
```python
client = OpenAI(api_key="sk-your-actual-key-here")
```

### 3. Start the Backend Server
```bash
python server.py
```

The server will start on `http://localhost:5001`

### 4. Open the Frontend
Open `index.html` in your web browser (just double-click the file or use a local server)

## Usage

1. Click "Create New Assistant"
2. Fill in:
   - Receiver Name (who you're calling)
   - Phone Number
   - Call Details (purpose of the call)
3. Chat with Vera to prepare for your call
4. Vera will ask intelligent questions based on your call context to help you prepare

## Features

- **Intelligent Conversations**: Vera uses GPT-4o-mini to analyze your call details and ask relevant questions
- **Context-Aware**: Remembers the entire conversation history
- **Multiple Assistants**: Create separate assistants for different calls
- **Clean UI**: Minimal, elegant design inspired by modern web apps

## Troubleshooting

**Error: "Could not connect to the server"**
- Make sure the backend server is running (`python server.py`)
- Check that it's running on port 5000

**Error: "API key not found"**
- Make sure you've set your OpenAI API key (see Step 2)

**Conversations not working**
- Check the browser console (F12) for errors
- Check the terminal running `server.py` for backend errors
