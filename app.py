import os
import subprocess
import threading
import traceback
import json
from datetime import datetime
from dotenv import load_dotenv

from flask import (Flask, render_template, redirect, url_for, session, request,
                   jsonify, abort)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth

# --- App Initialization & Configuration ---
load_dotenv()
app = Flask(__name__)

# Basic App Config
app.secret_key = os.getenv('SECRET_KEY', 'default-fallback-secret-key')
app.config['SESSION_COOKIE_NAME'] = 'g_session'

# Database Config
db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_host = os.getenv('DB_HOST')
db_name = os.getenv('DB_NAME')
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File Storage Config
USER_BASE_DIR = os.path.join(os.getcwd(), 'user_data')
os.makedirs(USER_BASE_DIR, exist_ok=True)

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
oauth = OAuth(app)

# --- Google OAuth Configuration ---
oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# --- Global Process Management ---
# In a production environment, this should be replaced with a more robust system like Redis or a DB
running_processes = {}


# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(100))
    picture = db.Column(db.String(255))
    first_ip = db.Column(db.String(45), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    main_file = db.Column(db.String(255), default='app.py')
    # requirements_file = db.Column(db.String(255), default='requirements.txt')

    def get_container_path(self):
        path = os.path.join(USER_BASE_DIR, str(self.id))
        os.makedirs(path, exist_ok=True)
        return path


# --- Helper Functions ---
def get_current_user():
    if 'user' in session:
        user_info = session['user']
        user = User.query.filter_by(google_id=user_info.get('sub')).first()
        return user
    return None

def get_log_path(user_id):
    user_dir = os.path.join(USER_BASE_DIR, str(user_id))
    return os.path.join(user_dir, 'console.log')

# --- Authentication Routes ---
@app.route('/login')
def login():
    if get_current_user():
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/auth/login')
def auth_login():
    redirect_uri = url_for('auth_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def auth_callback():
    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
    except Exception:
        return redirect(url_for('login'))

    if not user_info:
        return redirect(url_for('login'))

    user = User.query.filter_by(google_id=user_info['sub']).first()
    client_ip = request.remote_addr

    # Prevent multiple accounts from the same IP
    existing_user_by_ip = User.query.filter_by(first_ip=client_ip).first()
    if existing_user_by_ip and (not user or existing_user_by_ip.id != user.id):
        # Allow if the user logging in IS the user registered with this IP
        return "Error: An account has already been registered from this IP address.", 403

    if user is None:
        # Create new user
        user = User(
            google_id=user_info['sub'],
            email=user_info['email'],
            name=user_info['name'],
            picture=user_info['picture'],
            first_ip=client_ip
        )
        db.session.add(user)
        db.session.commit()

    session['user'] = user_info
    return redirect(url_for('dashboard'))


# --- Core Application Routes ---
@app.before_request
def require_login():
    if request.endpoint and 'static' not in request.endpoint \
            and request.endpoint not in ['login', 'auth_login', 'auth_callback']:
        if not get_current_user():
            return redirect(url_for('login'))

@app.route('/')
def dashboard():
    user = get_current_user()
    return render_template('dashboard.html', user=user)

@app.route('/files')
def files():
    user = get_current_user()
    container_path = user.get_container_path()
    file_list = []
    if os.path.exists(container_path):
        for item in os.listdir(container_path):
             # We can add more logic to differentiate files and folders
            is_dir = os.path.isdir(os.path.join(container_path, item))
            file_list.append({'name': item, 'is_dir': is_dir})

    return render_template('files.html', user=user, files=file_list)

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    user = get_current_user()
    if request.method == 'POST':
        main_file = request.form.get('main_file')
        if main_file:
            user.main_file = secure_filename(main_file)
            db.session.commit()
            return redirect(url_for('profile', success=True))
    return render_template('profile.html', user=user)

# --- API Routes for Bot Control & File Management ---
@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    user = get_current_user()
    if user.id in running_processes and running_processes[user.id].poll() is None:
        return jsonify({'status': 'error', 'message': 'Bot is already running.'}), 400

    container_path = user.get_container_path()
    main_file_path = os.path.join(container_path, user.main_file)
    req_file_path = os.path.join(container_path, 'requirements.txt')
    log_path = get_log_path(user.id)

    # Simplified startup command for this context
    # This is a simplified version of the user's requested script
    command = f"""
    cd {container_path}
    echo "--- System is starting up ---" > {log_path}
    if [ -f {req_file_path} ]; then
        echo "--- Installing requirements from requirements.txt ---" >> {log_path}
        pip install -r {req_file_path} >> {log_path} 2>&1
    fi
    echo "--- Starting bot: python {user.main_file} ---" >> {log_path}
    python -u {user.main_file} >> {log_path} 2>&1
    """

    try:
        proc = subprocess.Popen(['/bin/sh', '-c', command], preexec_fn=os.setsid)
        running_processes[user.id] = proc
        return jsonify({'status': 'success', 'message': 'Bot started successfully.'})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    user = get_current_user()
    proc = running_processes.get(user.id)
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), 15) # SIGTERM
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), 9) # SIGKILL
        running_processes.pop(user.id, None)
        return jsonify({'status': 'success', 'message': 'Bot stopped.'})
    return jsonify({'status': 'info', 'message': 'Bot was not running.'})

@app.route('/api/bot/restart', methods=['POST'])
def bot_restart():
    # A simple implementation: stop then start
    stop_response = bot_stop().get_json()
    if stop_response['status'] not in ['success', 'info']:
        return jsonify(stop_response), 500

    # Give a moment for resources to free up
    import time
    time.sleep(1)

    start_response = bot_start().get_json()
    start_response['message'] = "Bot restart sequence initiated."
    return jsonify(start_response)

@app.route('/api/bot/logs')
def bot_logs():
    user = get_current_user()
    log_path = get_log_path(user.id)
    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            content = f.read()
        return content
    return "No logs found. Start your bot to generate logs."

@app.route('/api/bot/command', methods=['POST'])
def bot_command():
    user = get_current_user()
    data = request.json
    command = data.get('command')
    proc = running_processes.get(user.id)

    if proc and proc.poll() is None:
        try:
            # Writing to stdin of the running process
            proc.stdin.write((command + '\n').encode())
            proc.stdin.flush()
            return jsonify({'status': 'success', 'message': 'Command sent.'})
        except (IOError, AttributeError):
            # Process stdin might not be a pipe
            return jsonify({'status': 'error', 'message': 'Cannot send command to this process.'}), 400
    return jsonify({'status': 'error', 'message': 'Bot is not running.'}), 400

# Placeholder for file management APIs (upload, delete, etc.)
# This would be a substantial addition, similar to the previous example's logic
# but adapted for the new structure.

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
