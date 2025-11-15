import http.client
import json
import os
import time
import logging
import base64
from solana.rpc.api import Client
from solders.transaction import Transaction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from web3 import Web3
from web3.exceptions import TransactionNotFound
from eth_account import Account
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

PRINTR_API_URL = os.getenv("PRINTR_API_URL",
                           "https://api-preview.printr.money")
PRINTR_BEARER_TOKEN = os.getenv(
    "PRINTR_BEARER_TOKEN",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJtZW1ldGljIn0.TWYWtkfA2TAgCW7q-b5Esn04nJEp2Z6ew9QLkj1GMYU"
)

# CAIP-2 chain mappings
CHAIN_MAPPINGS = {
    "ethereum": os.getenv("CHAIN_ETHEREUM", "eip155:1"),
    "arbitrum": os.getenv("CHAIN_ARBITRUM", "eip155:42161"),
    "avalanche": os.getenv("CHAIN_AVALANCHE", "eip155:43114"),
    "base": os.getenv("CHAIN_BASE", "eip155:8453"),
    "bnb": os.getenv("CHAIN_BNB", "eip155:56"),
    "mantle": os.getenv("CHAIN_MANTLE", "eip155:5000"),
    "solana": os.getenv("CHAIN_SOLANA",
                        "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"),
}

# RPC endpoints
RPC_ENDPOINTS = {
    "ethereum":
    os.getenv("RPC_ETHEREUM", "https://mainnet.infura.io/v3/YOUR_INFURA_KEY"),
    "arbitrum":
    os.getenv("RPC_ARBITRUM", "https://arb1.arbitrum.io/rpc"),
    "avalanche":
    os.getenv("RPC_AVALANCHE", "https://api.avax.network/ext/bc/C/rpc"),
    "base":
    os.getenv("RPC_BASE", "https://mainnet.base.org"),
    "bnb":
    os.getenv("RPC_BNB", "https://bsc-dataseed.binance.org/"),
    "mantle":
    os.getenv("RPC_MANTLE", "https://rpc.mantle.xyz"),
    "solana":
    os.getenv("RPC_SOLANA", "https://api.mainnet-beta.solana.com"),
}

# Logging setup
logging.basicConfig(
    filename="printr_client.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_api_url():
	"""Parse the API URL to extract host and base path."""
	if not PRINTR_API_URL:
		raise ValueError("PRINTR_API_URL not set in .env")
	if PRINTR_API_URL.startswith("https://"):
		host = PRINTR_API_URL[8:]
		base_path = ""
	else:
		host = PRINTR_API_URL
		base_path = ""
	if "/" in host:
		host, base_path = host.split("/", 1)
		base_path = f"/{base_path}"
	return host, base_path


def make_api_request(method,
                     endpoint,
                     payload=None,
                     retries=3,
                     backoff_factor=2):
	"""Make an API request to Printr with retry logic for rate limits and transient errors."""
	host, base_path = parse_api_url()
	headers = {
	    "Content-Type": "application/json",
	    "Authorization": f"Bearer {PRINTR_BEARER_TOKEN}",
	}
	conn = http.client.HTTPSConnection(host)
	try:
		for attempt in range(retries):
			try:
				if payload:
					conn.request(method, f"{base_path}{endpoint}", json.dumps(payload),
					             headers)
				else:
					conn.request(method, f"{base_path}{endpoint}", headers=headers)
				response = conn.getresponse()
				data = response.read().decode("utf-8")
				if response.status in (200, 201):
					return response.status, json.loads(data)
				elif response.status == 429:
					retry_after = int(response.getheader("X-RateLimit-Reset", 60))
					logger.warning(
					    f"Rate limit exceeded, retrying after {retry_after} seconds")
					time.sleep(retry_after)
					continue
				elif response.status in (400, 401, 404, 500):
					logger.error(f"API error {response.status}: {data}")
					return response.status, json.loads(data)
				else:
					logger.error(f"Unexpected status {response.status}: {data}")
					return response.status, {"error": {"code": "UNKNOWN", "message": data}}
			except Exception as e:
				logger.error(f"Request attempt {attempt + 1} failed: {str(e)}")
				if attempt == retries - 1:
					return 500, {"error": {"code": "REQUEST_FAILED", "message": str(e)}}
				time.sleep(backoff_factor**attempt)
	finally:
		conn.close()


def get_token_quote(chains, initial_buy_percent=5, graduation_threshold=69000):
	"""Get a quote for token creation from /print/quote."""
	caip_chains = [CHAIN_MAPPINGS.get(chain, chain) for chain in chains]
	payload = {
	    "chains": caip_chains,
	    "initial_buy": {
	        "supply_percent": initial_buy_percent
	    },
	    "graduation_threshold_per_chain_usd": graduation_threshold,
	}
	logger.info(f"Requesting quote for chains: {caip_chains}")
	status, response = make_api_request("POST", "/print/quote", payload)
	return status, response


def create_token(name,
                 symbol,
                 description,
                 image_b64,
                 chains,
                 initial_buy_percent=5,
                 graduation_threshold=69000,
                 external_links=None,
                 creator_account=None):
	"""Create a token using /print endpoint."""
	caip_chains = [CHAIN_MAPPINGS.get(chain, chain) for chain in chains]
	home_chain = caip_chains[0].split(":")[0]  # e.g., 'solana' or 'eip155'
	if not creator_account:
		logger.error(f"No creator account provided for home chain: {home_chain}")
		return 400, {
		    "error": {
		        "code": "NO_CREATOR_ACCOUNT",
		        "message": f"No creator account for {home_chain}"
		    }
		}
	payload = {
	    "creator_accounts": [creator_account],
	    "name": name,
	    "symbol": symbol,
	    "description": description,
	    "image": image_b64,
	    "chains": caip_chains,
	    "initial_buy": {
	        "supply_percent": initial_buy_percent
	    },
	    "graduation_threshold_per_chain_usd": graduation_threshold,
	}
	if external_links:
		payload["external_links"] = external_links
	logger.info(f"Creating token {name} ({symbol}) on chains: {caip_chains}")
	status, response = make_api_request("POST", "/print", payload)
	return status, response


def sign_and_submit_transaction(home_chain, payload, private_key, timeout=30):
	"""Sign and submit the transaction based on the home chain."""
	chain_key = home_chain.split(":")[0]  # e.g., 'solana' or 'eip155'
	rpc_endpoint = RPC_ENDPOINTS.get(chain_key)
	if not rpc_endpoint or not private_key:
		logger.error(
		    f"Missing RPC endpoint or private key for {chain_key}: rpc={rpc_endpoint}, private_key=****"
		)
		return False, f"Missing RPC endpoint or private key for {chain_key}"

	logger.debug(
	    f"Starting transaction for {chain_key} with payload: {json.dumps(payload)}"
	)
	try:
		if chain_key == "solana":
			client = Client(rpc_endpoint)
			logger.debug(f"Creating Keypair from private key for {chain_key}")
			keypair = Keypair.from_base58(private_key)
			tx = Transaction()
			ixs = payload.get("ixs", [])
			if not ixs:
				logger.error(f"No instructions found in payload for {chain_key}")
				return False, "No instructions in payload"
			for ix in ixs:
				accounts = [
				    AccountMeta(Pubkey.from_string(acc["pubkey"]), acc["is_signer"],
				                acc["is_writable"]) for acc in ix.get("accounts", [])
				]
				if not accounts:
					logger.error(f"No accounts found in instruction for {chain_key}")
					continue
				instruction = Instruction(program_id=Pubkey.from_string(ix["program_id"]),
				                          accounts=accounts,
				                          data=base64.b64decode(ix.get("data", "")))
				tx.add(instruction)
			logger.debug(f"Sending Solana transaction with {len(ixs)} instructions")
			response = client.send_transaction(tx,
			                                   keypair,
			                                   opts={
			                                       "skip_preflight": True,
			                                       "preflight_commitment": "confirmed"
			                                   },
			                                   timeout=timeout)
			if response.value:
				tx_id = str(response.value)
				logger.info(f"Solana transaction submitted: {tx_id}")
				return True, tx_id
			else:
				logger.error(f"Solana transaction failed: {response}")
				return False, str(response)

		else:  # EVM chains (ethereum, arbitrum, avalanche, base, bnb, mantle)
			w3 = Web3(
			    Web3.HTTPProvider(rpc_endpoint, request_kwargs={"timeout": timeout}))
			logger.debug(f"Creating account from private key for {chain_key}")
			account = Account.from_key(private_key)
			to_address = payload.get("to")
			calldata = payload.get("calldata", "0x")
			value = w3.to_wei(payload.get("value", 0),
			                  "wei") if payload.get("value") else 0
			gas_price = w3.eth.gas_price
			nonce = w3.eth.get_transaction_count(account.address)

			logger.debug(
			    f"Estimating gas for tx: to={to_address}, calldata={calldata[:50]}..., value={value}"
			)
			tx = {
			    "to": w3.to_checksum_address(to_address) if to_address else None,
			    "data": calldata,
			    "value": value,
			    "gasPrice": gas_price,
			    "nonce": nonce,
			    "chainId": int(home_chain.split(":")[1]) if ":" in home_chain else 1,
			}
			gas_limit = w3.eth.estimate_gas(tx) if to_address else 1000000
			tx["gas"] = gas_limit

			logger.debug(f"Signing transaction with gas={gas_limit}")
			signed_tx = w3.eth.account.sign_transaction(tx, private_key)
			logger.debug(f"Sending raw transaction")
			tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction,
			                                      timeout=timeout)
			receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
			if receipt.status == 1:
				logger.info(f"EVM transaction submitted on {chain_key}: {tx_hash.hex()}")
				return True, tx_hash.hex()
			else:
				logger.error(f"EVM transaction failed on {chain_key}: {receipt}")
				return False, f"Transaction failed: {receipt}"

	except Exception as e:
		logger.error(f"Failed to sign/submit transaction on {chain_key}: {str(e)}",
		             exc_info=True)
		return False, str(e)


def get_token_status(token_id):
	"""Check deployment status using /tokens/{id}/deployments."""
	logger.info(f"Checking status for token_id: {token_id}")
	status, response = make_api_request("GET", f"/tokens/{token_id}/deployments")
	return status, response
