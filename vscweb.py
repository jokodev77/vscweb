from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit
import requests
import os
import json
import hashlib
import datetime
import secrets
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(16)
socketio = SocketIO(app, cors_allowed_origins="*")

# Create data directory if it doesn't exist
DATA_DIR = 'website_data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# User credentials file
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'w') as f:
        json.dump({}, f)

def save_website_content(url, content):
    """Save website content to a file."""
    website_hash = hashlib.md5(url.encode()).hexdigest()
    filename = os.path.join(DATA_DIR, f'{website_hash}.html')
    
    # Record edit history
    history_file = os.path.join(DATA_DIR, f'{website_hash}_history.json')
    history = []
    if os.path.exists(history_file):
        with open(history_file, 'r') as f:
            history = json.load(f)
    
    history.append({
        'timestamp': datetime.datetime.now().isoformat(),
        'editor': session.get('username', 'anonymous'),
        'url': url
    })
    
    with open(history_file, 'w') as f:
        json.dump(history, f)
    
    # Save actual content
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return website_hash

def get_website_content(url):
    """Get website content either from cache or from the internet."""
    website_hash = hashlib.md5(url.encode()).hexdigest()
    filename = os.path.join(DATA_DIR, f'{website_hash}.html')
    
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            return f.read(), website_hash
    else:
        try:
            response = requests.get(url, timeout=10)
            content = response.text
            save_website_content(url, content)
            return content, website_hash
        except Exception as e:
            return f"<p>Error fetching the website: {str(e)}</p>", website_hash

def is_admin(username):
    """Check if the user is an admin."""
    with open(USERS_FILE, 'r') as f:
        users = json.load(f)
    return username in users and users[username].get('is_admin', False)

@app.route('/')
def index():
    return render_template('index.html', 
                          logged_in=session.get('logged_in', False),
                          username=session.get('username', ''),
                          is_admin=is_admin(session.get('username', '')))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        with open(USERS_FILE, 'r') as f:
            users = json.load(f)
        
        if username in users and check_password_hash(users[username]['password'], password):
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        with open(USERS_FILE, 'r') as f:
            users = json.load(f)
        
        if username in users:
            return render_template('register.html', error='Username already exists')
        
        # First user is automatically an admin
        is_admin_user = len(users) == 0
        
        users[username] = {
            'password': generate_password_hash(password),
            'is_admin': is_admin_user
        }
        
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f)
        
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/view', methods=['GET', 'POST'])
def view():
    if request.method == 'POST':
        url = request.form.get('url')
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        content, website_hash = get_website_content(url)
        
        return render_template('view.html', 
                              content=content, 
                              url=url, 
                              website_hash=website_hash,
                              logged_in=session.get('logged_in', False),
                              username=session.get('username', ''),
                              is_admin=is_admin(session.get('username', '')))
    
    website_hash = request.args.get('hash')
    url = request.args.get('url', '')
    
    if website_hash:
        filename = os.path.join(DATA_DIR, f'{website_hash}.html')
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read()
            
            return render_template('view.html', 
                                  content=content, 
                                  url=url, 
                                  website_hash=website_hash,
                                  logged_in=session.get('logged_in', False),
                                  username=session.get('username', ''),
                                  is_admin=is_admin(session.get('username', '')))
    
    return redirect(url_for('index'))

@app.route('/save', methods=['POST'])
def save():
    if not session.get('logged_in') or not is_admin(session.get('username')):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    
    url = request.form.get('url')
    content = request.form.get('content')
    
    website_hash = save_website_content(url, content)
    
    # Notify all connected clients about the update
    socketio.emit('content_updated', {'website_hash': website_hash, 'content': content})
    
    return jsonify({'status': 'success', 'message': 'Content saved successfully'})

@app.route('/history')
def history():
    website_hash = request.args.get('hash')
    if not website_hash:
        return redirect(url_for('index'))
    
    history_file = os.path.join(DATA_DIR, f'{website_hash}_history.json')
    if not os.path.exists(history_file):
        return render_template('history.html', history=[], website_hash=website_hash)
    
    with open(history_file, 'r') as f:
        history = json.load(f)
    
    return render_template('history.html', 
                          history=history, 
                          website_hash=website_hash,
                          logged_in=session.get('logged_in', False),
                          username=session.get('username', ''),
                          is_admin=is_admin(session.get('username', '')))

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('join_room')
def handle_join_room(data):
    room = data.get('website_hash')
    if room:
        print(f'Client joined room: {room}')

# Create HTML templates
def create_templates():
    os.makedirs('templates', exist_ok=True)
    
    # Base template
    with open('templates/base.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Website Source Editor</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/theme/dracula.min.css">
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #f8f9fa;
        }
        .banner {
            background: linear-gradient(135deg, #2ecc71, #27ae60);
            color: white;
            text-align: center;
            padding: 15px;
            font-size: 24px;
            font-weight: bold;
            text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
            margin-bottom: 20px;
            border-radius: 5px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }
        .editor-container {
            margin: 20px auto;
            max-width: 1200px;
        }
        .CodeMirror {
            height: 70vh;
            border-radius: 5px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }
        .btn-primary {
            background-color: #27ae60;
            border-color: #27ae60;
        }
        .btn-primary:hover {
            background-color: #2ecc71;
            border-color: #2ecc71;
        }
        .card {
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .nav-link {
            color: #27ae60;
        }
        .nav-link:hover {
            color: #2ecc71;
        }
    </style>
    {% block extra_head %}{% endblock %}
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">Website Source Editor</a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav ms-auto">
                    <li class="nav-item">
                        <a class="nav-link" href="/">Home</a>
                    </li>
                    {% if logged_in %}
                        <li class="nav-item">
                            <a class="nav-link" href="/logout">Logout ({{ username }})</a>
                        </li>
                    {% else %}
                        <li class="nav-item">
                            <a class="nav-link" href="/login">Login</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" href="/register">Register</a>
                        </li>
                    {% endif %}
                </ul>
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        <div class="banner">FOR MY LOVE HAPPY ASMARA</div>
        
        {% block content %}{% endblock %}
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    {% block scripts %}{% endblock %}
</body>
</html>''')
    
    # Index template
    with open('templates/index.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
    <div class="card">
        <div class="card-body">
            <h2 class="card-title">View and Edit Website Source</h2>
            <form action="/view" method="post">
                <div class="mb-3">
                    <label for="url" class="form-label">Enter Website URL:</label>
                    <input type="text" class="form-control" id="url" name="url" placeholder="example.com" required>
                </div>
                <button type="submit" class="btn btn-primary">View Source</button>
            </form>
        </div>
    </div>
{% endblock %}''')
    
    # View template
    with open('templates/view.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block extra_head %}
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/addon/hint/show-hint.css">
{% endblock %}

{% block content %}
    <div class="card mb-3">
        <div class="card-body">
            <h2 class="card-title">Viewing Source for: {{ url }}</h2>
            <div class="d-flex justify-content-between align-items-center mb-3">
                <div>
                    <a href="/history?hash={{ website_hash }}" class="btn btn-info btn-sm">View Edit History</a>
                    <button class="btn btn-success btn-sm" id="preview-btn">Preview</button>
                </div>
                <div>
                    <span class="badge bg-secondary" id="status">Connected</span>
                </div>
            </div>
            
            <div class="editor-container">
                <textarea id="editor">{{ content }}</textarea>
            </div>
            
            {% if is_admin %}
            <div class="d-grid gap-2 d-md-flex justify-content-md-end mt-3">
                <button class="btn btn-primary" id="save-btn">Save Changes</button>
            </div>
            {% else %}
            <div class="alert alert-info mt-3">
                You are viewing in read-only mode. Only administrators can edit and save changes.
            </div>
            {% endif %}
        </div>
    </div>
    
    <div class="modal fade" id="previewModal" tabindex="-1">
        <div class="modal-dialog modal-xl">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Preview</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <iframe id="preview-frame" style="width: 100%; height: 70vh; border: none;"></iframe>
                </div>
            </div>
        </div>
    </div>
{% endblock %}

{% block scripts %}
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/mode/htmlmixed/htmlmixed.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/mode/xml/xml.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/mode/javascript/javascript.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/mode/css/css.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/addon/hint/show-hint.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/addon/hint/html-hint.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.4.1/socket.io.min.js"></script>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Initialize CodeMirror
            const editor = CodeMirror.fromTextArea(document.getElementById('editor'), {
                lineNumbers: true,
                mode: 'htmlmixed',
                theme: 'dracula',
                lineWrapping: true,
                extraKeys: {
                    'Ctrl-Space': 'autocomplete'
                },
                readOnly: {% if is_admin %}false{% else %}true{% endif %}
            });
            
            // Socket.io setup
            const socket = io();
            const websiteHash = '{{ website_hash }}';
            
            socket.on('connect', function() {
                document.getElementById('status').textContent = 'Connected';
                document.getElementById('status').className = 'badge bg-success';
                
                // Join room for this specific website
                socket.emit('join_room', { website_hash: websiteHash });
            });
            
            socket.on('disconnect', function() {
                document.getElementById('status').textContent = 'Disconnected';
                document.getElementById('status').className = 'badge bg-danger';
            });
            
            socket.on('content_updated', function(data) {
                if (data.website_hash === websiteHash) {
                    editor.setValue(data.content);
                }
            });
            
            // Save button
            {% if is_admin %}
            document.getElementById('save-btn').addEventListener('click', function() {
                const content = editor.getValue();
                
                fetch('/save', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: new URLSearchParams({
                        url: '{{ url }}',
                        content: content
                    })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        alert('Content saved successfully!');
                    } else {
                        alert('Error: ' + data.message);
                    }
                })
                .catch(error => {
                    alert('Error: ' + error.message);
                });
            });
            {% endif %}
            
            // Preview button
            document.getElementById('preview-btn').addEventListener('click', function() {
                const content = editor.getValue();
                const blob = new Blob([content], { type: 'text/html' });
                const url = URL.createObjectURL(blob);
                
                document.getElementById('preview-frame').src = url;
                
                const previewModal = new bootstrap.Modal(document.getElementById('previewModal'));
                previewModal.show();
            });
        });
    </script>
{% endblock %}''')
    
    # Login template
    with open('templates/login.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
    <div class="card">
        <div class="card-body">
            <h2 class="card-title">Login</h2>
            {% if error %}
                <div class="alert alert-danger">{{ error }}</div>
            {% endif %}
            <form action="/login" method="post">
                <div class="mb-3">
                    <label for="username" class="form-label">Username:</label>
                    <input type="text" class="form-control" id="username" name="username" required>
                </div>
                <div class="mb-3">
                    <label for="password" class="form-label">Password:</label>
                    <input type="password" class="form-control" id="password" name="password" required>
                </div>
                <button type="submit" class="btn btn-primary">Login</button>
            </form>
            <p class="mt-3">Don't have an account? <a href="/register">Register</a></p>
        </div>
    </div>
{% endblock %}''')
    
    # Register template
    with open('templates/register.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
    <div class="card">
        <div class="card-body">
            <h2 class="card-title">Register</h2>
            {% if error %}
                <div class="alert alert-danger">{{ error }}</div>
            {% endif %}
            <form action="/register" method="post">
                <div class="mb-3">
                    <label for="username" class="form-label">Username:</label>
                    <input type="text" class="form-control" id="username" name="username" required>
                </div>
                <div class="mb-3">
                    <label for="password" class="form-label">Password:</label>
                    <input type="password" class="form-control" id="password" name="password" required>
                </div>
                <button type="submit" class="btn btn-primary">Register</button>
            </form>
            <p class="mt-3">Already have an account? <a href="/login">Login</a></p>
        </div>
    </div>
{% endblock %}''')
    
    # History template
    with open('templates/history.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
    <div class="card">
        <div class="card-body">
            <h2 class="card-title">Edit History</h2>
            <a href="/view?hash={{ website_hash }}" class="btn btn-primary mb-3">Back to Editor</a>
            
            <div class="table-responsive">
                <table class="table table-striped">
                    <thead>
                        <tr>
                            <th>Date and Time</th>
                            <th>Editor</th>
                            <th>URL</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for entry in history %}
                            <tr>
                                <td>{{ entry.timestamp }}</td>
                                <td>{{ entry.editor }}</td>
                                <td>{{ entry.url }}</td>
                            </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
{% endblock %}''')

if __name__ == '__main__':
    create_templates()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)