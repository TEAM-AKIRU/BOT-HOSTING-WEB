#!/bin/bash

# ==============================================================================
# Bot Hosting Platform Auto-Deploy Script (SQLite Local Storage Version)
# Version 3.0 - Includes fixes for dependencies and database initialization.
# ==============================================================================
# This script installs and configures the application using a local SQLite
# database, removing the need for a separate MySQL server.
# ==============================================================================

set -e # Exit immediately if a command exits with a non-zero status.

# --- Helper Functions ---
print_header() {
    echo ""
    echo "======================================================================"
    echo "=> $1"
    echo "======================================================================"
}

prompt_for_input() {
    local prompt_message=$1
    local variable_name=$2
    while [ -z "${!variable_name}" ]; do
        read -p "$prompt_message: " $variable_name
    done
}

print_header "Starting Bot Hosting Platform Deployment (SQLite Edition)"

# --- Step 1: Pre-flight Checks ---
if [ "$EUID" -ne 0 ]; then
  echo "❌ This script must be run as root. Please use 'sudo ./setup.sh'."
  exit 1
fi

# --- Step 2: Gather User Configuration ---
print_header "Gathering Configuration Details"
prompt_for_input "Enter your domain name (or this server's IP address)" DOMAIN_NAME
prompt_for_input "Enter a long, random SECRET_KEY for the Flask application" FLASK_SECRET_KEY
prompt_for_input "Enter your Google OAuth Client ID" GOOGLE_CLIENT_ID
prompt_for_input "Enter your Google OAuth Client Secret" GOOGLE_CLIENT_SECRET

# --- Step 3: System Dependency Installation ---
print_header "Installing System Dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3-pip python3-venv git nginx curl

# --- Step 4: Application Setup ---
print_header "Setting Up Application Files"
PROJECT_DIR="/var/www/bothost"
REPO_URL="https://github.com/TEAM-AKIRU/BOT-HOSTING-WEB.git"

if [ -d "$PROJECT_DIR/.git" ]; then
    echo "Project directory exists. Pulling latest changes..."
    cd $PROJECT_DIR
    git pull
else
    echo "Cloning repository into $PROJECT_DIR..."
    git clone $REPO_URL $PROJECT_DIR
    cd $PROJECT_DIR
fi

# Create the .env file with user-provided secrets
echo "Creating .env configuration file..."
cat > .env << EOF
# Flask App Configuration
SECRET_KEY='$FLASK_SECRET_KEY'

# Google OAuth Credentials
GOOGLE_CLIENT_ID='$GOOGLE_CLIENT_ID'
GOOGLE_CLIENT_SECRET='$GOOGLE_CLIENT_SECRET'
EOF
echo "✅ .env file created."

# --- Step 5: Python Environment and Database Initialization ---
print_header "Setting Up Python Environment and Local Database"
python3 -m venv venv
source venv/bin/activate

echo "Installing Python dependencies..."
pip install -r requirements.txt
echo "✅ Dependencies installed."

# Run database migrations to create the local app.db file
echo "Initializing local SQLite database..."
export $(grep -v '^#' .env | xargs) # Load .env for flask command

# === FIX APPLIED HERE ===
# 1. Initialize the migrations folder if it doesn't exist.
#    The '|| true' part ensures the script doesn't stop if the folder already exists.
flask db init || true

# 2. Create an initial migration script based on the models in app.py.
flask db migrate -m "Initial database setup" || echo "No new model changes to migrate."

# 3. Apply the migration to the database, creating the tables.
flask db upgrade
# === END OF FIX ===

echo "✅ Local database schema is ready."
deactivate

# --- Step 6: Nginx and Systemd Configuration ---
print_header "Configuring Nginx and Systemd Service"
# Nginx
sed -i "s/YOUR_DOMAIN_OR_IP/$DOMAIN_NAME/g" nginx.conf
cp nginx.conf /etc/nginx/sites-available/bothost
ln -sfn /etc/nginx/sites-available/bothost /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

# Systemd Service
sed -i "s|/var/www/bothost|$PROJECT_DIR|g" app.service
cp app.service /etc/systemd/system/app.service
systemctl daemon-reload
systemctl restart app
systemctl enable app

# --- Final Step: Deployment Summary ---
print_header "✅ Deployment Complete! ✅"
echo "Your bot hosting platform is now running with local storage."
echo ""
echo "Access it at: http://$DOMAIN_NAME"
echo ""
echo "To check application status, run: systemctl status app"
echo "To view live logs, run: journalctl -u app -f"
echo "----------------------------------------------------------------------"
