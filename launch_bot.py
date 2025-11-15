import sqlite3
import json
import logging
import os
from datetime import datetime, timedelta
import pytz
import base64
import re
import telebot
from telebot import types
from apscheduler.schedulers.background import BackgroundScheduler
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from dotenv import load_dotenv
from printr_client import get_token_quote, create_token, sign_and_submit_transaction, get_token_status
from flask import Flask
from cryptography.fernet import Fernet
import threading

# Flask setup
app = Flask(__name__)


@app.route("/health")
def health():
	return "OK", 200


def run_flask():
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8443)), debug=False)


flask_thread = threading.Thread(target=run_flask)
flask_thread.daemon = True
flask_thread.start()

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)

# Validate and load environment variables
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
	logger.error("TELEGRAM_TOKEN environment variable is not set.")
	raise ValueError("TELEGRAM_TOKEN is required.")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", 0))
if ALLOWED_USER_ID == 0:
	logger.warning("ALLOWED_USER_ID not set.")
	raise ValueError("ALLOWED_USER_ID is required.")

# Initialize Telegram bot
bot = telebot.TeleBot(BOT_TOKEN)
scheduler = BackgroundScheduler(timezone="UTC")

# Supported chains (converted to CAIP-2 format)
SUPPORTED_CHAINS = {
    "arbitrum": "eip155:42161",
    "avalanche": "eip155:43114",
    "base": "eip155:8453",
    "bnb": "eip155:56",
    "ethereum": "eip155:1",
    "mantle": "eip155:5000",
    "solana": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
}

# Encryption setup
encryption_key = os.getenv("ENCRYPTION_KEY")
if not encryption_key:
	encryption_key = Fernet.generate_key().decode()
	logger.info(
	    "Generated new encryption key. Store it securely as ENCRYPTION_KEY.")
	print(f"Generated Encryption Key: {encryption_key}")
cipher_suite = Fernet(encryption_key)


def encrypt_private_key(private_key):
	return cipher_suite.encrypt(private_key.encode()).decode()


def decrypt_private_key(encrypted_key):
	return cipher_suite.decrypt(encrypted_key.encode()).decode()


# Database initialization
def init_db():
	logger.info("Initializing SQLite database")
	conn = sqlite3.connect("launches.db")
	cursor = conn.cursor()
	cursor.execute("""
        CREATE TABLE IF NOT EXISTS launches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            json_data TEXT,
            status TEXT,
            scheduled_time TEXT,
            token_id TEXT,
            payload TEXT,
            quote TEXT,
            printr_status TEXT,
            home_chain TEXT,
            transaction_id TEXT
        )
    """)
	required_columns = [
	    'token_id', 'payload', 'quote', 'printr_status', 'home_chain',
	    'transaction_id'
	]
	cursor.execute("PRAGMA table_info(launches)")
	existing_columns = [col[1] for col in cursor.fetchall()]
	for col in required_columns:
		if col not in existing_columns:
			logger.info(f"Adding missing column {col} to launches table")
			cursor.execute(f"ALTER TABLE launches ADD COLUMN {col} TEXT")
	cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER,
            chain TEXT,
            wallet_address TEXT,
            caip10_address TEXT,
            private_key_encrypted TEXT,
            PRIMARY KEY (user_id, chain)
        )
    """)
	# Populate wallets table for Base chain from .env
	base_wallet_env = os.getenv("BASE_WALLET_ADDRESS")
	base_private_key_env = os.getenv("BASE_PRIVATE_KEY")
	if base_wallet_env and base_private_key_env:
		encrypted_private_key = encrypt_private_key(base_private_key_env)
		caip10_address = f"eip155:8453:{base_wallet_env}"  # Proper CAIP-10 format
		cursor.execute(
		    """
            INSERT OR REPLACE INTO wallets (user_id, chain, wallet_address, caip10_address, private_key_encrypted)
            VALUES (?, ?, ?, ?, ?)
            """, (ALLOWED_USER_ID, "base", base_wallet_env, caip10_address,
		                encrypted_private_key))
		logger.info("Initialized wallet for base from .env")
	else:
		logger.warning("Missing wallet or private key for base in .env")
	conn.commit()
	conn.close()
	logger.info("Database initialized successfully")


# Check if wallets are configured
def are_wallets_configured(user_id):
	conn = sqlite3.connect("launches.db")
	cursor = conn.cursor()
	cursor.execute("SELECT COUNT(*) FROM wallets WHERE user_id = ?", (user_id, ))
	count = cursor.fetchone()[0]
	conn.close()
	return count > 0


# Display main menu
def display_main_menu(chat_id, message_text="Choose an option:"):
	keyboard = types.InlineKeyboardMarkup()
	keyboard.add(
	    types.InlineKeyboardButton("Upload JSON", callback_data="upload_json"))
	keyboard.add(types.InlineKeyboardButton("Schedule", callback_data="schedule"))
	keyboard.add(
	    types.InlineKeyboardButton("Batch Schedule",
	                               callback_data="batch_schedule"))
	keyboard.add(types.InlineKeyboardButton("Status", callback_data="status"))
	keyboard.add(types.InlineKeyboardButton("Logs", callback_data="logs"))
	bot.send_message(chat_id,
	                 f"```{message_text}```",
	                 parse_mode="Markdown",
	                 reply_markup=keyboard)


# Time conversion utilities
def validate_time_input(time_str):
	pattern = r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]\sWAT$"
	return bool(re.match(pattern, time_str))


def wat_to_utc(selected_date, time_str):
	try:
		slot_time = datetime.strptime(time_str, "%H:%M WAT")
		wat_tz = pytz.timezone("Africa/Lagos")
		wat_datetime = datetime.combine(selected_date, slot_time.time())
		wat_datetime = wat_tz.localize(wat_datetime)
		utc_datetime = wat_datetime.astimezone(pytz.UTC)
		return utc_datetime
	except ValueError as e:
		logger.error(f"Invalid time format: {time_str}, error: {str(e)}")
		return None
	except Exception as e:
		logger.error(f"An error occurred: {str(e)}")
		return None


# Validate JSON format
def validate_json(data, user_id):
	logger.info(f"Validating JSON data for user_id: {user_id}")
	required_fields = ["name", "symbol", "description", "chains"]
	try:
		launches = data.get("launches", [])
		if not launches:
			logger.error("No launches found in JSON")
			return False, "No launches found in JSON"
		for launch in launches:
			for field in required_fields:
				if field not in launch:
					logger.error(f"Missing required field: {field}")
					return False, f"Missing required field: {field}"
			if not isinstance(launch["chains"], list) or not launch["chains"]:
				logger.error("Invalid chains field in JSON")
				return False, "Each launch must have a non-empty 'chains' array."
			if not all(chain in SUPPORTED_CHAINS for chain in launch["chains"]):
				logger.error("Unsupported chain in JSON")
				return False, f"Chains must be one of: {', '.join(SUPPORTED_CHAINS.keys())}"
			if "image" in launch:
				try:
					image_bytes = base64.b64decode(launch["image"])
					if len(image_bytes) > 500 * 1024:
						logger.error("Image size exceeds 500KB")
						return False, "Image size must be less than 500KB."
				except base64.binascii.Error:
					logger.error("Invalid base64 image")
					return False, "Image must be valid base64-encoded string."
		logger.info("JSON validated successfully")
		return True, "Valid JSON"
	except Exception as e:
		logger.error(f"Invalid JSON: {str(e)}")
		return False, f"Invalid JSON: {str(e)}"


# Validate interval
def validate_interval(num_launches, interval_hours):
	if num_launches * interval_hours > 24:
		return False, f"Error: {num_launches} launches with {interval_hours}-hour intervals exceed 24 hours."
	return True, "Valid interval"


# User data storage
user_data = {}


# Telegram handlers
@bot.message_handler(commands=['start'])
def start(message):
	user_id = message.from_user.id
	logger.info(f"Received /start command from user_id: {user_id}")
	user_data[user_id] = {"awaiting_user_id": True}
	bot.reply_to(message,
	             "Please enter your user ID to authenticate:",
	             parse_mode="Markdown")


@bot.message_handler(content_types=['text'],
                     func=lambda message: user_data.get(
                         message.from_user.id, {}).get("awaiting_user_id"))
def handle_user_id(message):
	user_id = message.from_user.id
	logger.info(f"Received user ID input from user_id: {user_id}")
	try:
		input_id = int(message.text.strip())
		if input_id != ALLOWED_USER_ID:
			logger.warning(f"Unauthorized user ID attempt: {input_id}")
			bot.reply_to(message, "Unauthorized user ID.", parse_mode="Markdown")
			return
		user_data[user_id]["awaiting_user_id"] = False
		if not are_wallets_configured(user_id):
			logger.error(
			    f"Wallets not configured for user_id: {user_id}. Check .env file.")
			bot.reply_to(
			    message,
			    "```Wallets not configured. Please set BASE wallet address and private key in the .env file.```",
			    parse_mode="Markdown")
			return
		display_main_menu(message.chat.id, "Welcome! Choose an option:")
	except ValueError:
		logger.info(f"Invalid user ID input from user_id: {user_id}")
		bot.reply_to(message,
		             "Invalid user ID. Please enter a numeric ID.",
		             parse_mode="Markdown")
		user_data[user_id]["awaiting_user_id"] = True


@bot.callback_query_handler(func=lambda call: True)
def button_callback(call):
	user_id = call.from_user.id
	logger.info(f"Received callback from user_id: {user_id}, data: {call.data}")
	if user_id != ALLOWED_USER_ID:
		bot.answer_callback_query(call.id)
		bot.send_message(call.message.chat.id,
		                 "Unauthorized.",
		                 parse_mode="Markdown")
		return
	bot.answer_callback_query(call.id)
	if call.data == "upload_json":
		bot.send_message(call.message.chat.id,
		                 "Please upload a JSON file with launch details.",
		                 parse_mode="Markdown")
		user_data[user_id]["awaiting_json"] = True
	elif call.data == "schedule":
		calendar, step = DetailedTelegramCalendar(calendar_id="single").build()
		bot.send_message(call.message.chat.id,
		                 "```Select a date for your launch:```",
		                 parse_mode="Markdown",
		                 reply_markup=calendar)
	elif call.data == "batch_schedule":
		calendar, step = DetailedTelegramCalendar(calendar_id="batch").build()
		bot.send_message(call.message.chat.id,
		                 "```Select the start date for batch scheduling:```",
		                 parse_mode="Markdown",
		                 reply_markup=calendar)
	elif call.data == "status":
		keyboard = types.InlineKeyboardMarkup()
		keyboard.add(
		    types.InlineKeyboardButton("Specific", callback_data="status_specific"))
		keyboard.add(types.InlineKeyboardButton("All", callback_data="status_all"))
		bot.send_message(call.message.chat.id,
		                 "```Select status check type:```",
		                 parse_mode="Markdown",
		                 reply_markup=keyboard)
	elif call.data == "status_specific":
		bot.send_message(
		    call.message.chat.id,
		    "```Enter launch names or IDs (comma-separated, e.g., 'Token1,Token2' or '1,2,3'):```",
		    parse_mode="Markdown")
		user_data[user_id]["awaiting_status_specific"] = True
	elif call.data == "status_all":
		conn = sqlite3.connect("launches.db")
		cursor = conn.cursor()
		cursor.execute(
		    "SELECT id, token_id, json_data, transaction_id FROM launches WHERE user_id = ?",
		    (user_id, ))
		launches = cursor.fetchall()
		conn.close()
		if not launches:
			bot.send_message(call.message.chat.id,
			                 "```No launches found.```",
			                 parse_mode="Markdown")
			display_main_menu(call.message.chat.id)
			return
		response = "```All Launches Status:\n"
		for launch_id, token_id, json_data, transaction_id in launches:
			name = json.loads(json_data).get('name', 'Unknown')
			response += f"ID: {launch_id}, Name: {name}\n"
			if not token_id:
				response += "Status: Not deployed yet\n"
			else:
				status, status_response = get_token_status(token_id)
				if status == 200:
					deployments = status_response.get("deployments", [])
					response += f"Token ID: {token_id}\n"
					if transaction_id:
						response += f"Transaction ID: {transaction_id}\n"
					for dep in deployments:
						chain = dep.get("chain_id", "Unknown")
						dep_status = dep.get("status", "Unknown")
						response += f"Chain: {chain}, Status: {dep_status}\n"
						if dep_status == "FAILED":
							response += f"Error: {dep.get('x_chain_transaction', {}).get('message_id', 'Unknown')}\n"
				else:
					response += f"Error checking status: {status_response.get('error', {}).get('message', 'Unknown error')}\n"
			response += "-" * 20 + "\n"
		response += "```"
		bot.send_message(call.message.chat.id, response, parse_mode="Markdown")
		display_main_menu(call.message.chat.id)
	elif call.data == "logs":
		try:
			with open("bot.log", "r") as f:
				lines = f.readlines()[-10:]
				response = "```Recent Logs:\n" + "".join(lines) + "```"
			bot.send_message(call.message.chat.id, response, parse_mode="Markdown")
			logger.info(f"Sent logs to user_id: {user_id}")
		except Exception as e:
			logger.error(f"Error reading logs for user_id: {user_id}: {str(e)}")
			bot.send_message(call.message.chat.id,
			                 f"```Error reading logs: {str(e)}```",
			                 parse_mode="Markdown")
		display_main_menu(call.message.chat.id)
	elif call.data.startswith("batch_interval_") or call.data.startswith(
	    "batch_specific_"):
		parts = call.data.split("_")
		method = parts[1]
		selected_date = datetime.strptime(parts[2], "%Y-%m-%d").date()
		num_launches = int(parts[3])
		logger.info(
		    f"Batch method selected: {method}, date: {selected_date}, launches: {num_launches}"
		)
		if method == "interval":
			bot.send_message(
			    call.message.chat.id,
			    f"```Selected start date: {selected_date.strftime('%Y-%m-%d')}\nEnter the interval between launches in hours (e.g., 2.5):```",
			    parse_mode="Markdown")
			user_data[user_id]["awaiting_batch_interval"] = True
			user_data[user_id]["batch_date"] = selected_date
			user_data[user_id]["batch_count"] = num_launches
		else:
			bot.send_message(
			    call.message.chat.id,
			    f"```Selected start date: {selected_date.strftime('%Y-%m-%d')}\nEnter the time for launch 1 of {num_launches} per day (e.g., 14:30 WAT):```",
			    parse_mode="Markdown")
			user_data[user_id]["awaiting_batch_specific_times"] = True
			user_data[user_id]["batch_date"] = selected_date
			user_data[user_id]["batch_count"] = num_launches
			user_data[user_id]["batch_times"] = []
	else:
		result, key, step = DetailedTelegramCalendar(
		    calendar_id="batch" if "batch" in call.data else "single").process(
		        call.data)
		if not result and key:
			bot.edit_message_text(
			    f"```Select {LSTEP[step]}{' for batch scheduling' if 'batch' in call.data else ''}:```",
			    chat_id=call.message.chat.id,
			    message_id=call.message.message_id,
			    parse_mode="Markdown",
			    reply_markup=key)
		elif result:
			selected_date = result
			logger.info(
			    f"User selected {'batch ' if 'batch' in call.data else ''}date: {selected_date}"
			)
			conn = sqlite3.connect("launches.db")
			cursor = conn.cursor()
			if "batch" in call.data:
				cursor.execute(
				    "SELECT COUNT(*) FROM launches WHERE user_id = ? AND status = 'pending'",
				    (user_id, ))
				pending_count = cursor.fetchone()[0]
				conn.close()
				if pending_count == 0:
					logger.info(
					    f"No pending launches for batch scheduling for user_id: {user_id}")
					bot.send_message(call.message.chat.id,
					                 "```No pending launches. Upload a JSON file first.```",
					                 parse_mode="Markdown")
					display_main_menu(call.message.chat.id)
					return
				bot.send_message(
				    call.message.chat.id,
				    f"```Selected start date: {selected_date.strftime('%Y-%m-%d')}\nEnter the number of launches per day (1-10):```",
				    parse_mode="Markdown")
				user_data[user_id]["awaiting_batch_count"] = True
				user_data[user_id]["batch_date"] = selected_date
			else:
				cursor.execute(
				    "SELECT id, json_data FROM launches WHERE user_id = ? AND status = 'pending'",
				    (user_id, ))
				launches = cursor.fetchall()
				conn.close()
				if not launches:
					logger.info(
					    f"No pending launches for single scheduling for user_id: {user_id}")
					bot.send_message(call.message.chat.id,
					                 "```No pending launches. Upload a JSON file first.```",
					                 parse_mode="Markdown")
					display_main_menu(call.message.chat.id)
					return
				response = "```Pending Launches:\n"
				for launch_id, json_data in launches:
					data = json.loads(json_data)
					response += f"ID: {launch_id}, Name: {data.get('name', 'Unknown')}, Symbol: {data.get('symbol', 'N/A')}\n"
				response += "Enter the ID of the launch to schedule:```"
				bot.send_message(call.message.chat.id, response, parse_mode="Markdown")
				user_data[user_id]["awaiting_single_launch_id"] = True
				user_data[user_id]["single_date"] = selected_date


@bot.message_handler(content_types=['document'],
                     func=lambda message: user_data.get(
                         message.from_user.id, {}).get("awaiting_json"))
def process_json_file(message):
	user_id = message.from_user.id
	logger.info(f"Processing JSON file from user_id: {user_id}")
	user_data[user_id]["awaiting_json"] = False
	if user_id != ALLOWED_USER_ID:
		bot.reply_to(message, "Unauthorized.", parse_mode="Markdown")
		return
	document = message.document
	if not document or not document.file_name.endswith(".json"):
		logger.error(f"Invalid file uploaded by user_id: {user_id}")
		bot.reply_to(message,
		             "Please upload a valid JSON file.",
		             parse_mode="Markdown")
		display_main_menu(message.chat.id)
		return
	file = bot.get_file(document.file_id)
	logger.info(f"Downloading JSON file: {document.file_name}")
	file_content = bot.download_file(file.file_path)
	try:
		json_data = json.loads(file_content.decode("utf-8"))
		logger.info(f"JSON file downloaded and parsed: {json_data}")
		is_valid, error_message = validate_json(json_data, user_id)
		if not is_valid:
			bot.reply_to(message,
			             f"```Error: {error_message}```",
			             parse_mode="Markdown")
			display_main_menu(message.chat.id)
			return
		conn = sqlite3.connect("launches.db")
		cursor = conn.cursor()
		for launch in json_data["launches"]:
			cursor.execute(
			    "INSERT INTO launches (user_id, json_data, status, home_chain) VALUES (?, ?, ?, ?)",
			    (user_id, json.dumps(launch), "pending", launch["chains"][0]))
		conn.commit()
		count = len(json_data["launches"])
		conn.close()
		logger.info(f"Stored {count} launches in database for user_id: {user_id}")
		keyboard = types.InlineKeyboardMarkup()
		keyboard.add(
		    types.InlineKeyboardButton("Schedule Now", callback_data="schedule"))
		keyboard.add(
		    types.InlineKeyboardButton("Batch Schedule",
		                               callback_data="batch_schedule"))
		bot.reply_to(
		    message,
		    f"```JSON uploaded successfully! {count} launches stored.\nUse /schedule, /batch_schedule, or buttons to queue launches.```",
		    parse_mode="Markdown",
		    reply_markup=keyboard)
	except json.JSONDecodeError:
		logger.error(f"Invalid JSON file from user_id: {user_id}")
		bot.reply_to(message, "Invalid JSON format.", parse_mode="Markdown")
		display_main_menu(message.chat.id)
	except Exception as e:
		logger.error(f"Error processing JSON for user_id: {user_id}: {str(e)}")
		bot.reply_to(message,
		             f"```Error processing file: {str(e)}```",
		             parse_mode="Markdown")
		display_main_menu(message.chat.id)


@bot.message_handler(
    content_types=['text'],
    func=lambda message: user_data.get(message.from_user.id, {}).get(
        "awaiting_single_launch_id"))
def process_single_launch_id(message):
	user_id = message.from_user.id
	logger.info(f"Processing single launch ID input from user_id: {user_id}")
	if user_id != ALLOWED_USER_ID:
		bot.reply_to(message, "Unauthorized.", parse_mode="Markdown")
		return
	user_data[user_id]["awaiting_single_launch_id"] = False
	try:
		launch_id = int(message.text)
		conn = sqlite3.connect("launches.db")
		cursor = conn.cursor()
		cursor.execute(
		    "SELECT id FROM launches WHERE user_id = ? AND status = 'pending' AND id = ?",
		    (user_id, launch_id))
		if not cursor.fetchone():
			conn.close()
			logger.info(
			    f"Invalid or unavailable launch ID {launch_id} for user_id: {user_id}")
			bot.reply_to(
			    message,
			    "```Invalid or unavailable launch ID. Please select a valid ID.```",
			    parse_mode="Markdown")
			user_data[user_id]["awaiting_single_launch_id"] = True
			return
		conn.close()
		user_data[user_id]["single_launch_id"] = launch_id
		bot.reply_to(
		    message,
		    f"```Selected date: {user_data[user_id]['single_date'].strftime('%Y-%m-%d')}\nEnter the time for launch ID {launch_id} (e.g., 14:30 WAT):```",
		    parse_mode="Markdown")
		user_data[user_id]["awaiting_single_time"] = True
	except ValueError:
		logger.info(f"Invalid launch ID input from user_id: {user_id}")
		bot.reply_to(message,
		             "```Invalid input. Please enter a valid launch ID.```",
		             parse_mode="Markdown")
		user_data[user_id]["awaiting_single_launch_id"] = True


@bot.message_handler(content_types=['text'],
                     func=lambda message: user_data.get(
                         message.from_user.id, {}).get("awaiting_single_time"))
def process_single_time(message):
	user_id = message.from_user.id
	logger.info(f"Processing single time input from user_id: {user_id}")
	if user_id != ALLOWED_USER_ID:
		bot.reply_to(message, "Unauthorized.", parse_mode="Markdown")
		return
	user_data[user_id]["awaiting_single_time"] = False
	time_str = message.text.strip()
	selected_date = user_data[user_id]["single_date"]
	launch_id = user_data[user_id]["single_launch_id"]
	if not validate_time_input(time_str):
		logger.info(f"Invalid time format input from user_id: {user_id}: {time_str}")
		bot.reply_to(
		    message,
		    "```Invalid time format. Please enter time as HH:MM WAT (e.g., 14:30 WAT).```",
		    parse_mode="Markdown")
		user_data[user_id]["awaiting_single_time"] = True
		return
	utc_time = wat_to_utc(selected_date, time_str)
	if not utc_time:
		logger.info(
		    f"Invalid time conversion for input from user_id: {user_id}: {time_str}")
		bot.reply_to(
		    message,
		    "```Invalid time format. Please enter time as HH:MM WAT (e.g., 14:30 WAT).```",
		    parse_mode="Markdown")
		user_data[user_id]["awaiting_single_time"] = True
		return
	conn = sqlite3.connect("launches.db")
	cursor = conn.cursor()
	cursor.execute(
	    "SELECT COUNT(*) FROM launches WHERE user_id = ? AND status = 'scheduled' AND scheduled_time = ?",
	    (user_id, utc_time.isoformat()))
	if cursor.fetchone()[0] > 0:
		logger.info(
		    f"Time slot conflict for {time_str} on {selected_date} for user_id: {user_id}"
		)
		bot.reply_to(
		    message,
		    f"```Time slot {time_str} on {selected_date.strftime('%Y-%m-%d')} is already taken. Choose another time.```",
		    parse_mode="Markdown")
		user_data[user_id]["awaiting_single_time"] = True
		conn.close()
		return
	cursor.execute(
	    "UPDATE launches SET status = ?, scheduled_time = ?, printr_status = ? WHERE id = ?",
	    ("scheduled", utc_time.isoformat(), "PENDING", launch_id))
	cursor.execute("SELECT json_data FROM launches WHERE id = ?", (launch_id, ))
	json_data = cursor.fetchone()[0]
	name = json.loads(json_data).get('name', 'Unknown')
	conn.commit()
	conn.close()
	response = f"```Scheduled Launch:\nID: {launch_id}, Name: {name}, Scheduled: {utc_time.strftime('%Y-%m-%d %H:%M')} UTC ({(utc_time + timedelta(hours=1)).strftime('%H:%M')} WAT)```"
	bot.reply_to(message, response, parse_mode="Markdown")
	logger.info(
	    f"Scheduled launch ID {launch_id} for user_id: {user_id} on {selected_date}"
	)
	display_main_menu(message.chat.id)


@bot.message_handler(content_types=['text'],
                     func=lambda message: user_data.get(
                         message.from_user.id, {}).get("awaiting_batch_count"))
def process_batch_count(message):
	user_id = message.from_user.id
	logger.info(f"Processing batch launch count input from user_id: {user_id}")
	if user_id != ALLOWED_USER_ID:
		bot.reply_to(message, "Unauthorized.", parse_mode="Markdown")
		return
	user_data[user_id]["awaiting_batch_count"] = False
	try:
		count = int(message.text)
		if count < 1 or count > 10:
			raise ValueError("Count out of range")
		conn = sqlite3.connect("launches.db")
		cursor = conn.cursor()
		cursor.execute(
		    "SELECT COUNT(*) FROM launches WHERE user_id = ? AND status = 'pending'",
		    (user_id, ))
		pending_count = cursor.fetchone()[0]
		conn.close()
		if count > pending_count:
			logger.info(
			    f"Not enough pending launches for user_id: {user_id}. Requested: {count}, Available: {pending_count}"
			)
			bot.reply_to(
			    message,
			    f"```Not enough pending launches. Requested: {count}, Available: {pending_count}.```",
			    parse_mode="Markdown")
			user_data[user_id]["awaiting_batch_count"] = True
			return
		user_data[user_id]["batch_count"] = count
		keyboard = types.InlineKeyboardMarkup()
		keyboard.add(
		    types.InlineKeyboardButton(
		        "Fixed Interval",
		        callback_data=
		        f"batch_interval_{user_data[user_id]['batch_date'].strftime('%Y-%m-%d')}_{count}"
		    ))
		keyboard.add(
		    types.InlineKeyboardButton(
		        "Specific Times",
		        callback_data=
		        f"batch_specific_{user_data[user_id]['batch_date'].strftime('%Y-%m-%d')}_{count}"
		    ))
		bot.reply_to(
		    message,
		    f"```Selected start date: {user_data[user_id]['batch_date'].strftime('%Y-%m-%d')}\nChoose scheduling method:```",
		    parse_mode="Markdown",
		    reply_markup=keyboard)
	except ValueError:
		logger.info(f"Invalid batch count input from user_id: {user_id}")
		bot.reply_to(message,
		             "```Please enter a number between 1 and 10.```",
		             parse_mode="Markdown")
		user_data[user_id]["awaiting_batch_count"] = True


@bot.message_handler(
    content_types=['text'],
    func=lambda message: user_data.get(message.from_user.id, {}).get(
        "awaiting_batch_interval"))
def process_batch_interval(message):
	user_id = message.from_user.id
	logger.info(f"Processing batch interval input from user_id: {user_id}")
	if user_id != ALLOWED_USER_ID:
		bot.reply_to(message, "Unauthorized.", parse_mode="Markdown")
		return
	user_data[user_id]["awaiting_batch_interval"] = False
	try:
		interval = float(message.text)
		if interval <= 0:
			raise ValueError("Interval must be positive")
		is_valid, error_message = validate_interval(
		    user_data[user_id]["batch_count"], interval)
		if not is_valid:
			logger.info(f"Invalid interval for user_id: {user_id}: {error_message}")
			bot.reply_to(message, f"```{error_message}```", parse_mode="Markdown")
			user_data[user_id]["awaiting_batch_interval"] = True
			return
		user_data[user_id]["batch_interval"] = interval
		bot.reply_to(
		    message,
		    f"```Selected start date: {user_data[user_id]['batch_date'].strftime('%Y-%m-%d')}\nEnter start time for the first launch (e.g., 08:00 WAT):```",
		    parse_mode="Markdown")
		user_data[user_id]["awaiting_batch_start_time"] = True
	except ValueError:
		logger.info(f"Invalid interval input from user_id: {user_id}")
		bot.reply_to(message,
		             "```Please enter a valid number of hours (e.g., 2.5).```",
		             parse_mode="Markdown")
		user_data[user_id]["awaiting_batch_interval"] = True


@bot.message_handler(
    content_types=['text'],
    func=lambda message: user_data.get(message.from_user.id, {}).get(
        "awaiting_batch_start_time"))
def process_batch_interval_start_time(message):
	user_id = message.from_user.id
	logger.info(
	    f"Processing batch interval start time input from user_id: {user_id}")
	if user_id != ALLOWED_USER_ID:
		bot.reply_to(message, "Unauthorized.", parse_mode="Markdown")
		return
	user_data[user_id]["awaiting_batch_start_time"] = False
	time_str = message.text.strip()
	selected_date = user_data[user_id]["batch_date"]
	num_launches = user_data[user_id]["batch_count"]
	interval_hours = user_data[user_id]["batch_interval"]
	if not validate_time_input(time_str):
		logger.info(f"Invalid start time input from user_id: {user_id}: {time_str}")
		bot.reply_to(
		    message,
		    "```Invalid time format. Please enter time as HH:MM WAT (e.g., 08:00 WAT).```",
		    parse_mode="Markdown")
		user_data[user_id]["awaiting_batch_start_time"] = True
		return
	start_time = wat_to_utc(selected_date, time_str)
	if not start_time:
		logger.info(
		    f"Invalid time conversion for input from user_id: {user_id}: {time_str}")
		bot.reply_to(
		    message,
		    "```Invalid time format. Please enter time as HH:MM WAT (e.g., 08:00 WAT).```",
		    parse_mode="Markdown")
		user_data[user_id]["awaiting_batch_start_time"] = True
		return
	conn = sqlite3.connect("launches.db")
	cursor = conn.cursor()
	cursor.execute(
	    "SELECT id, json_data, home_chain FROM launches WHERE user_id = ? AND status = 'pending'",
	    (user_id, ))
	launches = cursor.fetchall()
	if not launches:
		logger.info(
		    f"No pending launches for batch scheduling for user_id: {user_id}")
		bot.reply_to(message,
		             "```No pending launches. Upload a JSON file first.```",
		             parse_mode="Markdown")
		conn.close()
		display_main_menu(message.chat.id)
		return
	response = "```Batch Scheduling Results:\n"
	scheduled_count = 0
	current_date = selected_date
	current_time = start_time
	i = 0
	while i < len(launches):
		daily_count = 0
		daily_times = []
		while daily_count < num_launches and i < len(launches):
			cursor.execute(
			    "SELECT COUNT(*) FROM launches WHERE user_id = ? AND status = 'scheduled' AND scheduled_time = ?",
			    (user_id, current_time.isoformat()))
			if cursor.fetchone()[0] > 0:
				logger.info(f"Time slot conflict at {current_time} for user_id: {user_id}")
				current_time += timedelta(hours=interval_hours)
				continue
			daily_times.append(current_time)
			daily_count += 1
			i += 1
			current_time += timedelta(hours=interval_hours)
		for j, time in enumerate(daily_times):
			launch_id, json_data, home_chain = launches[i - daily_count + j]
			cursor.execute(
			    "UPDATE launches SET status = ?, scheduled_time = ?, printr_status = ? WHERE id = ?",
			    ("scheduled", time.isoformat(), "PENDING", launch_id))
			name = json.loads(json_data).get('name', 'Unknown')
			response += f"ID: {launch_id}, Name: {name}, Scheduled: {time.strftime('%Y-%m-%d %H:%M')} UTC ({(time + timedelta(hours=1)).strftime('%H:%M')} WAT)\n"
			scheduled_count += 1
		if i >= len(launches):
			break
		current_date += timedelta(days=1)
		current_time = datetime.combine(current_date,
		                                start_time.time()).astimezone(pytz.UTC)
	conn.commit()
	conn.close()
	response += f"Scheduled {scheduled_count} launches.```"
	bot.reply_to(message, response, parse_mode="Markdown")
	logger.info(
	    f"Batch scheduled {scheduled_count} launches for user_id: {user_id}")
	display_main_menu(message.chat.id)


@bot.message_handler(
    content_types=['text'],
    func=lambda message: user_data.get(message.from_user.id, {}).get(
        "awaiting_batch_specific_times"))
def process_batch_specific_times(message):
	user_id = message.from_user.id
	logger.info(f"Processing batch specific times input from user_id: {user_id}")
	if user_id != ALLOWED_USER_ID:
		bot.reply_to(message, "Unauthorized.", parse_mode="Markdown")
		return
	time_str = message.text.strip()
	selected_date = user_data[user_id]["batch_date"]
	num_launches = user_data[user_id]["batch_count"]
	times = user_data[user_id]["batch_times"]
	if not validate_time_input(time_str):
		logger.info(f"Invalid time format input from user_id: {user_id}: {time_str}")
		bot.reply_to(
		    message,
		    "```Invalid time format. Please enter time as HH:MM WAT (e.g., 14:30 WAT).```",
		    parse_mode="Markdown")
		return
	utc_time = wat_to_utc(selected_date, time_str)
	if not utc_time:
		logger.info(
		    f"Invalid time conversion for input from user_id: {user_id}: {time_str}")
		bot.reply_to(
		    message,
		    "```Invalid time format. Please enter time as HH:MM WAT (e.g., 14:30 WAT).```",
		    parse_mode="Markdown")
		return
	if utc_time in times:
		logger.info(f"Duplicate time input from user_id: {user_id}: {time_str}")
		bot.reply_to(
		    message,
		    f"```Time {time_str} is already selected for this day. Choose a different time.```",
		    parse_mode="Markdown")
		return
	times.append(utc_time)
	current_launch = len(times)
	if current_launch < num_launches:
		bot.reply_to(
		    message,
		    f"```Enter the time for launch {current_launch + 1} of {num_launches} per day (e.g., 14:30 WAT):```",
		    parse_mode="Markdown")
		return
	user_data[user_id]["awaiting_batch_specific_times"] = False
	conn = sqlite3.connect("launches.db")
	cursor = conn.cursor()
	cursor.execute(
	    "SELECT id, json_data, home_chain FROM launches WHERE user_id = ? AND status = 'pending'",
	    (user_id, ))
	launches = cursor.fetchall()
	if not launches:
		logger.info(
		    f"No pending launches for batch scheduling for user_id: {user_id}")
		bot.reply_to(message,
		             "```No pending launches. Upload a JSON file first.```",
		             parse_mode="Markdown")
		conn.close()
		display_main_menu(message.chat.id)
		return
	response = "```Batch Scheduling Results:\n"
	scheduled_count = 0
	current_date = selected_date
	i = 0
	while i < len(launches):
		daily_count = 0
		for j in range(num_launches):
			if i >= len(launches):
				break
			utc_time = times[daily_count % len(times)]
			slot_time = utc_time.time()
			utc_time = datetime.combine(current_date, slot_time).astimezone(pytz.UTC)
			cursor.execute(
			    "SELECT COUNT(*) FROM launches WHERE user_id = ? AND status = 'scheduled' AND scheduled_time = ?",
			    (user_id, utc_time.isoformat()))
			if cursor.fetchone()[0] > 0:
				logger.info(f"Time slot conflict at {utc_time} for user_id: {user_id}")
				bot.reply_to(
				    message,
				    f"```Time slot {(utc_time + timedelta(hours=1)).strftime('%H:%M')} WAT on {current_date.strftime('%Y-%m-%d')} is already taken. Please restart batch scheduling.```",
				    parse_mode="Markdown")
				conn.close()
				return
			launch_id, json_data, home_chain = launches[i]
			cursor.execute(
			    "UPDATE launches SET status = ?, scheduled_time = ?, printr_status = ? WHERE id = ?",
			    ("scheduled", utc_time.isoformat(), "PENDING", launch_id))
			name = json.loads(json_data).get('name', 'Unknown')
			response += f"ID: {launch_id}, Name: {name}, Scheduled: {utc_time.strftime('%Y-%m-%d %H:%M')} UTC ({(utc_time + timedelta(hours=1)).strftime('%H:%M')} WAT)\n"
			scheduled_count += 1
			daily_count += 1
			i += 1
		current_date += timedelta(days=1)
	conn.commit()
	conn.close()
	response += f"Scheduled {scheduled_count} launches.```"
	bot.reply_to(message, response, parse_mode="Markdown")
	logger.info(
	    f"Batch scheduled {scheduled_count} launches for user_id: {user_id}")
	user_data[user_id]["batch_times"] = []
	display_main_menu(message.chat.id)


@bot.message_handler(
    content_types=['text'],
    func=lambda message: user_data.get(message.from_user.id, {}).get(
        "awaiting_status_specific"))
def process_status_specific(message):
	user_id = message.from_user.id
	logger.info(f"Processing specific status check for user_id: {user_id}")
	if user_id != ALLOWED_USER_ID:
		bot.reply_to(message, "Unauthorized.", parse_mode="Markdown")
		return
	user_data[user_id]["awaiting_status_specific"] = False
	input_text = message.text.strip()
	identifiers = [x.strip() for x in input_text.split(",") if x.strip()]
	if not identifiers:
		logger.info(
		    f"No identifiers provided for specific status check by user_id: {user_id}"
		)
		bot.reply_to(message,
		             "```Please provide at least one launch name or ID.```",
		             parse_mode="Markdown")
		display_main_menu(message.chat.id)
		return
	conn = sqlite3.connect("launches.db")
	cursor = conn.cursor()
	response = "```Specific Launches Status:\n"
	found = False
	for identifier in identifiers:
		try:
			launch_id = int(identifier)
			cursor.execute(
			    "SELECT id, token_id, json_data, transaction_id FROM launches WHERE user_id = ? AND id = ?",
			    (user_id, launch_id))
		except ValueError:
			cursor.execute(
			    "SELECT id, token_id, json_data, transaction_id FROM launches WHERE user_id = ? AND json_data LIKE ?",
			    (user_id, f'%\"name\": \"{identifier}\"'))
		result = cursor.fetchone()
		if not result:
			response += f"Identifier: {identifier}, Status: Not found\n"
			continue
		found = True
		launch_id, token_id, json_data, transaction_id = result
		name = json.loads(json_data).get('name', 'Unknown')
		response += f"ID: {launch_id}, Name: {name}\n"
		if not token_id:
			response += "Status: Not deployed yet\n"
		else:
			status, status_response = get_token_status(token_id)
			if status == 200:
				response += f"Token ID: {token_id}\n"
				if transaction_id:
					response += f"Transaction ID: {transaction_id}\n"
				deployments = status_response.get("deployments", [])
				for dep in deployments:
					chain = dep.get("chain_id", "Unknown")
					dep_status = dep.get("status", "Unknown")
					response += f"Chain: {chain}, Status: {dep_status}\n"
					if dep_status == "FAILED":
						response += f"Error: {dep.get('x_chain_transaction', {}).get('message_id', 'Unknown')}\n"
			else:
				response += f"Error checking status: {status_response.get('error', {}).get('message', 'Unknown error')}\n"
		response += "-" * 20 + "\n"
	conn.close()
	if not found:
		response += "No matching launches found.```"
	else:
		response += "```"
	bot.reply_to(message, response, parse_mode="Markdown")
	display_main_menu(message.chat.id)


def run_scheduled_launch():
	logger.info("Running scheduled launch job")
	conn = sqlite3.connect("launches.db")
	cursor = conn.cursor()
	now = datetime.now(pytz.UTC)
	logger.debug(f"Current UTC time: {now.isoformat()}")
	cursor.execute(
	    """
        SELECT id, json_data, scheduled_time, home_chain
        FROM launches
        WHERE status = 'scheduled' AND scheduled_time <= ? AND printr_status = 'PENDING'
        """, (now.isoformat(), ))
	launches = cursor.fetchall()
	if not launches:
		logger.info(
		    "No scheduled launches found eligible for processing at this time.")
	else:
		logger.info(f"Found {len(launches)} eligible launches to process.")
	for launch_id, json_data, scheduled_time, home_chain in launches:
		logger.info(
		    f"Processing launch ID {launch_id} scheduled for {scheduled_time}")
		launch_data = json.loads(json_data)
		name = launch_data.get("name", "Unnamed Token")
		symbol = launch_data.get("symbol")
		description = launch_data.get("description", f"{name} launched via Printr")
		image_b64 = launch_data.get("image")  # Extract image from JSON
		chains = [
		    SUPPORTED_CHAINS.get(chain, chain)
		    for chain in launch_data.get("chains", [])
		]  # Convert to CAIP-2
		external_links = launch_data.get("external_links", None)

		# Retry logic for get_token_quote
		max_retries = 3
		quote_response = None
		for attempt in range(max_retries):
			try:
				logger.debug(
				    f"Attempt {attempt + 1}/{max_retries} to get quote for {name}")
				status, quote_response = get_token_quote(chains)
				if status == 200:
					logger.debug(f"Quote received for {name}: {json.dumps(quote_response)}")
					break
			except Exception as e:
				logger.error(
				    f"Quote failed for launch_id {launch_id} (attempt {attempt + 1}/{max_retries}): {str(e)}"
				)
			if attempt < max_retries - 1:
				import time
				time.sleep(5)  # Wait before retry
			else:
				error_message = str(e) if quote_response is None else quote_response.get(
				    'error', {}).get('message', 'Unknown error')
				cursor.execute(
				    "UPDATE launches SET printr_status = ?, quote = ? WHERE id = ?",
				    ("FAILED", json.dumps({"error": {
				        "message": error_message
				    }}), launch_id))
				conn.commit()
				try:
					bot.send_message(
					    ALLOWED_USER_ID,
					    f"```Quote failed for {name} (ID: {launch_id}): {error_message}```",
					    parse_mode="Markdown")
				except Exception as e:
					logger.error(f"Error sending quote failure message: {str(e)}")
				continue
		if status != 200:
			continue

		# Get creator account from wallets table
		cursor.execute(
		    "SELECT caip10_address FROM wallets WHERE user_id = ? AND chain = ?",
		    (ALLOWED_USER_ID, home_chain))
		result = cursor.fetchone()
		if not result:
			logger.error(
			    f"No creator account found for {home_chain} for launch_id {launch_id}")
			cursor.execute("UPDATE launches SET printr_status = ? WHERE id = ?",
			               ("FAILED", launch_id))
			conn.commit()
			try:
				bot.send_message(
				    ALLOWED_USER_ID,
				    f"```No creator account configured for {home_chain} for {name} (ID: {launch_id}). Check wallets table.```",
				    parse_mode="Markdown")
			except Exception as e:
				logger.error(f"Error sending creator account missing message: {str(e)}")
			continue
		creator_account = result[0]

		# Retry logic for create_token with correct parameters
		max_retries = 3
		response = None
		for attempt in range(max_retries):
			try:
				logger.debug(f"Attempt {attempt + 1}/{max_retries} to create token {name}")
				status, response = create_token(name=name,
				                                symbol=symbol,
				                                description=description,
				                                image_b64=image_b64,
				                                chains=chains,
				                                initial_buy_percent=5,
				                                graduation_threshold=69000,
				                                external_links=external_links,
				                                creator_account=creator_account)
				if status == 201:
					logger.debug(f"Token created for {name}: {json.dumps(response)}")
					break
			except Exception as e:
				logger.error(
				    f"Token creation failed for launch_id {launch_id} (attempt {attempt + 1}/{max_retries}): {str(e)}"
				)
			if attempt < max_retries - 1:
				import time
				time.sleep(5)  # Wait before retry
			else:
				error_message = str(e) if response is None else response.get(
				    'error', {}).get('message', 'Unknown error')
				cursor.execute(
				    "UPDATE launches SET printr_status = ?, quote = ? WHERE id = ?",
				    ("FAILED", json.dumps({"error": {
				        "message": error_message
				    }}), launch_id))
				conn.commit()
				try:
					bot.send_message(
					    ALLOWED_USER_ID,
					    f"```Token creation failed for {name} (ID: {launch_id}): {error_message}```",
					    parse_mode="Markdown")
				except Exception as e:
					logger.error(f"Error sending failure message: {str(e)}")
				continue
		if status != 201:
			continue

		token_id = response.get("token_id")
		payload = response.get("payload")
		quote = response.get("quote")
		cursor.execute(
		    "UPDATE launches SET token_id = ?, payload = ?, quote = ?, printr_status = ? WHERE id = ?",
		    (token_id, json.dumps(payload), json.dumps(quote), "DEPLOYING",
		     launch_id))
		conn.commit()
		logger.info(
		    f"Token creation initiated for launch_id {launch_id}, token_id: {token_id}"
		)

		# Fetch encrypted private key for the home chain
		cursor.execute(
		    "SELECT private_key_encrypted FROM wallets WHERE user_id = ? AND chain = ?",
		    (ALLOWED_USER_ID, home_chain))
		result = cursor.fetchone()
		if not result:
			logger.error(
			    f"No private key found for {home_chain} for launch_id {launch_id}")
			cursor.execute("UPDATE launches SET printr_status = ? WHERE id = ?",
			               ("FAILED", launch_id))
			conn.commit()
			try:
				bot.send_message(
				    ALLOWED_USER_ID,
				    f"```No private key configured for {home_chain} for {name} (ID: {launch_id}). Check .env file.```",
				    parse_mode="Markdown")
			except Exception as e:
				logger.error(f"Error sending private key missing message: {str(e)}")
			continue
		private_key_encrypted = result[0]
		private_key = decrypt_private_key(private_key_encrypted)

		# Prepare payload for transaction submission
		payload_dict = payload.copy(
		)  # Create a copy to avoid modifying the original
		# Extract raw hex address from CAIP-10 format
		to_address = payload_dict["to"].split(":")[
		    -1]  # Extracts '0x576CbcdE3fe704B2166E8fAC54D7a664b0325C10'
		payload_dict["to"] = to_address  # Update with raw hex address
		# Convert base64 calldata to hex with validation
		try:
			calldata_bytes = base64.b64decode(payload_dict["calldata"])
			payload_dict["calldata"] = "0x" + calldata_bytes.hex(
			)  # Ensure hex string starts with '0x'
			logger.debug(
			    f"Converted calldata for launch_id {launch_id}: {payload_dict['calldata']}"
			)
		except base64.binascii.Error as e:
			logger.error(f"Invalid base64 calldata for launch_id {launch_id}: {str(e)}")
			cursor.execute("UPDATE launches SET printr_status = ? WHERE id = ?",
			               ("FAILED", launch_id))
			conn.commit()
			try:
				bot.send_message(
				    ALLOWED_USER_ID,
				    f"```Transaction failed for {name} (ID: {launch_id}): Invalid calldata encoding - {str(e)}```",
				    parse_mode="Markdown")
			except Exception as e:
				logger.error(f"Error sending calldata failure message: {str(e)}")
			continue

		# Transaction submission with increased timeout and detailed logging
		try:
			logger.debug(
			    f"Attempting to sign and submit transaction for {name} on {home_chain} with payload: {json.dumps(payload_dict)}"
			)
			success, tx_result = sign_and_submit_transaction(
			    home_chain, payload_dict, private_key, timeout=60)  # Removed chain_id
			logger.debug(
			    f"Transaction result for {name} (ID: {launch_id}): success={success}, tx_result={tx_result}"
			)
			if success:
				cursor.execute("UPDATE launches SET transaction_id = ? WHERE id = ?",
				               (tx_result, launch_id))
				conn.commit()
				try:
					bot.send_message(
					    ALLOWED_USER_ID,
					    f"```Token creation initiated for {name} (ID: {launch_id}, Token ID: {token_id})\nTransaction submitted on {home_chain}: {tx_result}\nUse /status {launch_id} to track deployment.```",
					    parse_mode="Markdown")
				except Exception as e:
					logger.error(f"Error sending transaction success message: {str(e)}")
			else:
				cursor.execute("UPDATE launches SET printr_status = ? WHERE id = ?",
				               ("FAILED", launch_id))
				conn.commit()
				try:
					bot.send_message(
					    ALLOWED_USER_ID,
					    f"```Token creation failed for {name} (ID: {launch_id}): Transaction submission failed - {tx_result}```",
					    parse_mode="Markdown")
				except Exception as e:
					logger.error(f"Error sending transaction failure message: {str(e)}")
		except Exception as e:
			logger.error(f"Transaction failed for launch_id {launch_id}: {str(e)}",
			             exc_info=True)
			cursor.execute("UPDATE launches SET printr_status = ? WHERE id = ?",
			               ("FAILED", launch_id))
			conn.commit()
			try:
				bot.send_message(
				    ALLOWED_USER_ID,
				    f"```Transaction failed for {name} (ID: {launch_id}): {str(e)}```",
				    parse_mode="Markdown")
			except Exception as e:
				logger.error(f"Error sending transaction failure message: {str(e)}")
	conn.close()
	logger.info("Scheduled launch job finished")


def main():
	init_db()
	logger.info("Starting APScheduler")
	scheduler.add_job(run_scheduled_launch, "interval", minutes=1)
	scheduler.start()
	logger.info("APScheduler started")
	logger.info("Starting bot polling...")
	bot.polling(none_stop=True, long_polling_timeout=60)


if __name__ == "__main__":
	main()
