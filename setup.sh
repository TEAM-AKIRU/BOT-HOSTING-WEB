#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "--- Bot Hosting Platform Auto-Deploy Script ---"

# --- Configuration ---
# Your GitHub repository URL
REPO_URL="https://github.com/TEAM-AKIRU/BOT-HOSTING-WEB.git" 
# The directory where the app will be installed
PROJECT_DIR="/var/www/bothost"

# --- Check for Root ---
if [ "$EUID" -ne 0 ]; then 
  echo "Please run this script as root"
  exit
fi

# --- System Update and Dependency Installation ---
echo "Updating system and installing dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv python3-dev build-essential libssl-dev libffi-dev nginx curl mysql-server

# --- MySQL Secure Installation & Database Setup ---
echo "Securing MySQL and setting up the database..."
# Run mysql_secure_installation non-interactively
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'your_root_password'; FLUSH PRIVILEGES;"
mysql -u root -pyour_root_password -e "DELETE FROM mysql.user WHERE User='';"
mysql -u root -pyour_root_password -e "DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');"
mysql -u root -pyour_root_password -e "DROP DATABASE IF EXISTS test;"
mysql -u root -pyour_root_password -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';"
mysql -u root -pyour_root_password -e "FLUSH PRIVILEGES;"

# Create database and user from .env file (assuming .env is in the repo for this script)
# IMPORTANT: It's better to create the .env file on the server manually.
# This script will assume the .env file is in the git repo for automation.
echo "Creating database and user..."
DB_NAME=$(grep DB_NAME .env | cut -d '=' -f2)
DB_USER=$(grep DB_USER .env | cut -d '=' -f2)
DB_PASSWORD=$(grep DB_PASSWORD .env | cut -d '=' -f2)
mysql -u root -pyour_root_password -e "CREATE DATABASE IF NOT EXISTS $DB_NAME;"
mysql -u root -pyour_root_password -e "CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';"
mysql -u root -pyour_root_password -e "GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'localhost';"
mysql -u root -pyour_root_password -e "FLUSH PRIVILEGES;"

# --- Cloning Repository & Setting up Application ---
echo "Cloning repository from $REPO_URL..."
rm -rf $PROJECT_DIR # Clean up previous installations
git clone $REPO_URL $PROJECT_DIR
cd $PROJECT_DIR

# --- Python Virtual Environment and Dependencies ---
echo "Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "Installing Python packages from requirements.txt..."
pip install -r requirements.txt

# --- Initialize Database Schema with Flask-Migrate ---
echo "Initializing the database schema..."
# We need to load .env variables for flask to connect to the DB
export $(grep -v '^#' .env | xargs)
flask db init || echo "Migrations folder already exists."
flask db migrate -m "Initial deployment migration" || echo "No changes to migrate."
flask db upgrade

# --- Nginx Configuration ---
echo "Configuring Nginx..."
SERVER_IP=$(curl -s http://icanhazip.com)
sed -i "s/YOUR_DOMAIN_OR_IP/$SERVER_IP/g" nginx.conf
cp nginx.conf /etc/nginx/sites-available/bothost
ln -sfn /etc/nginx/sites-available/bothost /etc/nginx/sites-enabled/
# Remove the default Nginx site
rm -f /etc/nginx/sites-enabled/default
nginx -t # Test configuration
systemctl restart nginx

# --- Systemd Service Configuration ---
echo "Configuring Systemd service..."
# Replace placeholders in the service file
sed -i "s|WorkingDirectory=/var/www/bothost|WorkingDirectory=$PROJECT_DIR|g" app.service
sed -i "s|ExecStart=/var/www/bothost/venv/bin/gunicorn|ExecStart=$PROJECT_DIR/venv/bin/gunicorn|g" app.service

cp app.service /etc/systemd/system/app.service
systemctl daemon-reload
systemctl start app
systemctl enable app # Enable service to start on boot

echo ""
echo "--- ✅ Deployment Complete! ---"
echo "Your bot hosting platform is now running."
echo "Access it at: http://$SERVER_IP"
echo "You can check the application status with: systemctl status app"
echo "You can view live logs with: journalctl -u app -f"
echo "--------------------------------"
