import os
import subprocess
import threading
import json
import shutil
from datetime import datetime
from dotenv import load_dotenv

from flask import (Flask, render_template, redirect, url_for, session, request,
                   jsonify, abort, send_from_directory, flash)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth

# --- App Initialization & Configuration ---
load_dotenv()
app = Flask(__name__)

# Basic App Config
app.secret_key = os.getenv('SECRET_KEY', 'default-fallback-secret-key-for-dev')
app.config['SESSION_COOKIE_NAME'] = 'g_session'

# Base directory for all user-specific data (bots, logs, database)
DATA_BASE_DIR = os.path.join(app.root_path, 'user_data')
os.makedirs(DATA_BASE_DIR, exist_ok=True)

# Use SQLite for local file-based storage.
# The database will be a single file named 'app.db' in the DATA_BASE_DIR.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(DATA_BASE_DIR, 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
oauth = OAuth(app)

# Global dictionary to track running bot processes (keyed by user.id)
# In production, a more persistent solution like Redis could be used.
running_processes = {}


# --- Google OAuth Configuration ---
oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)


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

    def get_container_path(self):
        """Returns the dedicated directory path for this user's files."""
        path = os.path.join(DATA_BASE_DIR, 'files', str(self.id))
        os.makedirs(path, exist_ok=True)
        return path

    def get_log_path(self):
        """Returns the path to the console log file for this user."""
        log_dir = os.path.join(DATA_BASE_DIR, 'logs')
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"{self.id}.log")


# --- Helper Functions & Decorators ---
def get_current_user():
    """Retrieves the current user from the database based on the session."""
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None

def login_required(f):
    """Decorator to protect routes that require a logged-in user."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# --- Authentication Routes ---
@app.route('/login')
def login():
    if get_current_user():
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/auth/login')
def auth_login():
    """Redirects the user to Google's OAuth consent screen."""
    redirect_uri = url_for('auth_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def auth_callback():
    """Handles the callback from Google after user authentication."""
    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
    except Exception:
        flash("Authentication failed. Please try again.", "error")
        return redirect(url_for('login'))

    if not user_info:
        return redirect(url_for('login'))

    user = User.query.filter_by(google_id=user_info['sub']).first()
    client_ip = request.remote_addr

    # Prevent multiple accounts from the same IP address.
    existing_user_by_ip = User.query.filter_by(first_ip=client_ip).first()
    if existing_user_by_ip and (not user or existing_user_by_ip.id != user.id):
        return "Error: An account has already been registered from this IP address.", 403

    if user is None:
        user = User(
            google_id=user_info['sub'],
            email=user_info['email'],
            name=user_info['name'],
            picture=user_info['picture'],
            first_ip=client_ip
        )
        db.session.add(user)
        db.session.commit()

    session['user_id'] = user.id
    return redirect(url_for('dashboard'))


# --- Core Application Routes ---
@app.route('/')
@login_required
def dashboard():
    user = get_current_user()
    return render_template('dashboard.html', user=user)

@app.route('/files')
@login_required
def files():
    user = get_current_user()
    container_path = user.get_container_path()
    file_list = []
    try:
        if os.path.exists(container_path):
            for item in sorted(os.listdir(container_path)):
                is_dir = os.path.isdir(os.path.join(container_path, item))
                file_list.append({'name': item, 'is_dir': is_dir})
    except OSError:
        flash("Could not read files directory.", "error")

    return render_template('files.html', user=user, files=file_list)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = get_current_user()
    if request.method == 'POST':
        main_file = request.form.get('main_file')
        if main_file:
            user.main_file = secure_filename(main_file)
            db.session.commit()
            flash("Settings saved successfully!", "success")
        return redirect(url_for('profile'))
    return render_template('profile.html', user=user)

# --- API Routes for Bot Control ---
def run_bot_process(command, user_id):
    """Thread target to run the user's bot in a subprocess."""
    log_path = User.query.get(user_id).get_log_path()
    try:
        with open(log_path, 'a') as log_file:
            # Use os.setsid to create a new process group. This allows us to kill the entire group.
            proc = subprocess.Popen(
                ['/bin/sh', '-c', command],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid
            )
            running_processes[user_id] = proc
            proc.wait() # Wait for the process to complete
    except Exception as e:
        with open(log_path, 'a') as log_file:
            log_file.write(f"\n--- CRITICAL ERROR: Failed to start process ---\n{e}\n")
    finally:
        running_processes.pop(user_id, None)


@app.route('/api/bot/start', methods=['POST'])
@login_required
def bot_start():
    user = get_current_user()
    if user.id in running_processes and running_processes[user.id].poll() is None:
        return jsonify({'status': 'error', 'message': 'Bot is already running.'}), 400

    container_path = user.get_container_path()
    req_file_path = os.path.join(container_path, 'requirements.txt')
    log_path = user.get_log_path()

    command = f"""
    cd "{container_path}"
    echo "--- System is starting up at $(date) ---" > "{log_path}"
    if [ -f "{req_file_path}" ]; then
        echo "--- Installing requirements from requirements.txt ---" >> "{log_path}"
        pip install -r "{req_file_path}" >> "{log_path}" 2>&1
    fi
    echo "--- Starting bot: python3 {user.main_file} ---" >> "{log_path}"
    exec python3 -u "{user.main_file}"
    """

    thread = threading.Thread(target=run_bot_process, args=(command, user.id))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'success', 'message': 'Bot start sequence initiated.'})


@app.route('/api/bot/stop', methods=['POST'])
@login_required
def bot_stop():
    user = get_current_user()
    proc = running_processes.get(user.id)
    if proc and proc.poll() is None:
        try:
            # Kill the entire process group started with os.setsid
            os.killpg(os.getpgid(proc.pid), 15) # SIGTERM
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                os.killpg(os.getpgid(proc.pid), 9) # SIGKILL
            except ProcessLookupError:
                pass # Process already gone
        running_processes.pop(user.id, None)
        return jsonify({'status': 'success', 'message': 'Bot stopped.'})
    return jsonify({'status': 'info', 'message': 'Bot was not running.'})


@app.route('/api/bot/restart', methods=['POST'])
@login_required
def bot_restart():
    bot_stop()
    import time
    time.sleep(1) # Give a moment for resources to free up
    return bot_start()


@app.route('/api/bot/logs')
@login_required
def bot_logs():
    user = get_current_user()
    log_path = user.get_log_path()
    try:
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                return f.read()
        return "No logs found. Start your bot to generate logs."
    except Exception as e:
        return f"Error reading logs: {e}"

# Placeholder for future command injection into stdin
@app.route('/api/bot/command', methods=['POST'])
@login_required
def bot_command():
    return jsonify({'status': 'info', 'message': 'Direct command input is not yet implemented.'}), 501


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
