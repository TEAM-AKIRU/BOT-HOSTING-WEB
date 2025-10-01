#!/bin/bash

# ==============================================================================
# Bot Hosting Platform Interactive Auto-Deploy Script
# ==============================================================================
# This script will install and configure the entire application stack by
# asking for necessary details.
# ==============================================================================

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Helper Functions for User Input ---
# Function to print a formatted header
print_header() {
    echo ""
    echo "======================================================================"
    echo "=> $1"
    echo "======================================================================"
}

# Function to prompt the user for input with validation
prompt_for_input() {
    local prompt_message=$1
    local variable_name=$2
    while [ -z "${!variable_name}" ]; do
        read -p "$prompt_message: " $variable_name
        if [ -z "${!variable_name}" ]; then
            echo "Input cannot be empty. Please try again."
        fi
    done
}

print_header "Starting Bot Hosting Platform Deployment"

# --- Step 1: Pre-flight Checks ---
# Check for Root privileges
if [ "$EUID" -ne 0 ]; then
  echo "❌ This script must be run as root. Please use 'sudo ./setup.sh'."
  exit 1
fi

# --- Step 2: Gather User Configuration ---
print_header "Gathering Configuration Details"

prompt_for_input "Enter the domain name (e.g., mybot.com or your server IP)" DOMAIN_NAME
prompt_for_input "Enter the MySQL database name to be created" DB_NAME
prompt_for_input "Enter the MySQL username to be created" DB_USER
prompt_for_input "Enter a secure password for the new MySQL user" DB_PASSWORD
prompt_for_input "Enter the SECRET_KEY for the Flask application (a long random string)" FLASK_SECRET_KEY
prompt_for_input "Enter your Google OAuth Client ID" GOOGLE_CLIENT_ID
prompt_for_input "Enter your Google OAuth Client Secret" GOOGLE_CLIENT_SECRET

# --- Step 3: System Update and Dependency Installation ---
print_header "Updating System and Installing Dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3-pip python3-venv python3-dev build-essential libssl-dev libffi-dev nginx curl git mysql-server

# --- Step 4: MySQL Secure Installation & Database Setup ---
print_header "Configuring MySQL and Setting Up Database"
# Start and wait for MySQL service
systemctl start mysql.service
systemctl enable mysql.service
echo "Waiting for MySQL to become available..."
while ! mysqladmin ping -hlocalhost --silent; do
    sleep 1
done
echo "MySQL is up and running."

# Create database and user with the provided details
echo "Creating database '$DB_NAME' and user '$DB_USER'..."
mysql -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -e "CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';"
mysql -e "GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';"
mysql -e "FLUSH PRIVILEGES;"
echo "✅ Database and user created successfully."

# --- Step 5: Application Setup ---
print_header "Setting Up Application Files"
PROJECT_DIR="/var/www/bothost"
REPO_URL="https://github.com/TEAM-AKIRU/BOT-HOSTING-WEB.git"

if [ -d "$PROJECT_DIR" ]; then
    echo "Existing project directory found. Pulling latest changes..."
    cd $PROJECT_DIR
    git pull
else
    echo "Cloning repository into $PROJECT_DIR..."
    git clone $REPO_URL $PROJECT_DIR
    cd $PROJECT_DIR
fi

# Create the .env file from user input
echo "Creating .env configuration file..."
cat > .env << EOF
# Flask App Configuration
SECRET_KEY='$FLASK_SECRET_KEY'

# Database Configuration
DB_USER='$DB_USER'
DB_PASSWORD='$DB_PASSWORD'
DB_HOST='localhost'
DB_NAME='$DB_NAME'

# Google OAuth Credentials
GOOGLE_CLIENT_ID='$GOOGLE_CLIENT_ID'
GOOGLE_CLIENT_SECRET='$GOOGLE_CLIENT_SECRET'
EOF
echo "✅ .env file created."

# --- Step 6: Python Environment and Database Migration ---
print_header "Setting Up Python Environment and Database"
python3 -m venv venv
source venv/bin/activate
echo "Installing Python dependencies..."
pip install -r requirements.txt
echo "✅ Dependencies installed."

# Run database migrations
echo "Running database migrations..."
export $(grep -v '^#' .env | xargs) # Load .env for flask command
flask db upgrade
echo "✅ Database schema is up to date."
deactivate

# --- Step 7: Nginx Configuration (Reverse Proxy) ---
print_header "Configuring Nginx"
# Update the template with the user's domain
sed -i "s/YOUR_DOMAIN_OR_IP/$DOMAIN_NAME/g" nginx.conf
cp nginx.conf /etc/nginx/sites-available/bothost
ln -sfn /etc/nginx/sites-available/bothost /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t # Test configuration syntax
systemctl restart nginx
echo "✅ Nginx configured to proxy requests to the application."

# --- Step 8: Systemd Service (Process Management) ---
print_header "Configuring Systemd Service for Autostart"
# Update the service file with the correct paths
sed -i "s|WorkingDirectory=/var/www/bothost|WorkingDirectory=$PROJECT_DIR|g" app.service
sed -i "s|ExecStart=/var/www/bothost/venv/bin/gunicorn|ExecStart=$PROJECT_DIR/venv/bin/gunicorn|g" app.service
cp app.service /etc/systemd/system/app.service
systemctl daemon-reload
systemctl start app
systemctl enable app # Enable service to start on boot
echo "✅ Application is now running as a system service."

# --- Final Step: Deployment Summary ---
print_header "✅ Deployment Complete! ✅"
echo "Your bot hosting platform is now running and configured."
echo ""
echo "You can access it at: http://$DOMAIN_NAME"
echo ""
echo "To check the application status, run: systemctl status app"
echo "To view live logs, run: journalctl -u app -f"
echo "----------------------------------------------------------------------"
