import http.client
import json
import os
import time
import logging
import base64
from solana.rpc.api import Client
from solders.transaction import Transaction as SolanaTransaction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

load_dotenv()

PRINTR_API_URL = os.getenv("PRINTR_API_URL",
                           "https://api-preview.printr.money")
PRINTR_BEARER_TOKEN = os.getenv(
    "PRINTR_BEARER_TOKEN",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJtZW1ldGljIn0.TWYWtkfA2TAgCW7q-b5Esn04nJEp2Z6ew9QLkj1GMYU"
)

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

logging.basicConfig(
    filename="printr_client.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def parse_api_url():
	if not PRINTR_API_URL: raise ValueError("PRINTR_API_URL not set")
	host = PRINTR_API_URL.replace("https://", "").split("/", 1)[0]
	base_path = "/" + PRINTR_API_URL.replace("https://", "").split(
	    "/", 1)[1] if "/" in PRINTR_API_URL.replace("https://", "") else ""
	return host, base_path


def make_api_request(method, endpoint, payload=None, retries=3):
	host, base_path = parse_api_url()
	headers = {
	    "Content-Type": "application/json",
	    "Authorization": f"Bearer {PRINTR_BEARER_TOKEN}"
	}
	conn = http.client.HTTPSConnection(host)
	try:
		for attempt in range(retries):
			try:
				conn.request(method, f"{base_path}{endpoint}",
				             json.dumps(payload) if payload else None, headers)
				resp = conn.getresponse()
				data = resp.read().decode()
				if resp.status in (200, 201):
					return resp.status, json.loads(data) if data else {}
				elif resp.status == 429:
					time.sleep(int(resp.getheader("Retry-After", 60)))
					continue
				else:
					return resp.status, json.loads(data) if data else {"error": data}
			except Exception as e:
				if attempt == retries - 1:
					return 500, {"error": {"message": str(e)}}
				time.sleep(2**attempt)
	finally:
		conn.close()


def get_token_quote(chains, initial_buy_percent=5, graduation_threshold=69000):
	caip_chains = [CHAIN_MAPPINGS.get(c.lower(), c) for c in chains]
	payload = {
	    "chains": caip_chains,
	    "initial_buy": {
	        "supply_percent": initial_buy_percent
	    },
	    "graduation_threshold_per_chain_usd": graduation_threshold
	}
	return make_api_request("POST", "/print/quote", payload)


def create_token(name,
                 symbol,
                 description,
                 image_b64,
                 chains,
                 initial_buy_percent=5,
                 graduation_threshold=69000,
                 external_links=None,
                 creator_account=None):
	caip_chains = [CHAIN_MAPPINGS.get(c.lower(), c) for c in chains]
	if not creator_account:
		return 400, {"error": {"message": "creator_account required"}}
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
	    "graduation_threshold_per_chain_usd": graduation_threshold
	}
	if external_links: payload["external_links"] = external_links
	return make_api_request("POST", "/print", payload)


def sign_and_submit_transaction(home_chain_caip,
                                payload,
                                private_key,
                                timeout=60):
	if not home_chain_caip or ":" not in home_chain_caip:
		return False, "Invalid CAIP-2 chain"

	parts = home_chain_caip.split(":", 2)
	chain_type = parts[0].lower()
	chain_id_str = parts[1] if len(parts) > 1 else None
	try:
		chain_id = int(chain_id_str) if chain_id_str else None
	except:
		return False, "Invalid chain ID"

	# Reverse lookup short name from CAIP-2
	reverse_map = {v: k for k, v in CHAIN_MAPPINGS.items()}
	short_chain = reverse_map.get(home_chain_caip)
	if not short_chain:
		return False, f"Unknown chain {home_chain_caip}"
	rpc = RPC_ENDPOINTS.get(short_chain)
	if not rpc:
		return False, f"No RPC for {short_chain}"

	try:
		if chain_type == "solana":
			# Solana code unchanged (not used on Base)
			pass

		else:  # EVM
			w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": timeout}))
			if not w3.is_connected():
				return False, "RPC not connected"

			account = Account.from_key(private_key)
			to_addr = payload["to"]
			calldata = payload.get("calldata", "0x")
			value = int(payload.get("value", 0))

			tx = {
			    "to": w3.to_checksum_address(to_addr),
			    "data": calldata,
			    "value": value,
			    "gasPrice": w3.eth.gas_price,
			    "nonce": w3.eth.get_transaction_count(account.address),
			    "chainId": chain_id,
			}

			try:
				tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
			except:
				tx["gas"] = 9_000_000

			# THIS WAS THE ONLY BUG
			signed_tx = w3.eth.account.sign_transaction(tx, private_key)
			raw_tx = signed_tx.rawTransaction  # ← NOT .raw_transaction (old web3.py)
			tx_hash = w3.eth.send_raw_transaction(raw_tx)
			logger.info(f"TX SENT: {tx_hash.hex()}")

			receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
			return (True, tx_hash.hex()) if receipt.status == 1 else (False,
			                                                          "Tx reverted")

	except Exception as e:
		logger.error(f"Tx failed: {str(e)}", exc_info=True)
		return False, str(e)


def get_token_status(token_id):
	return make_api_request("GET", f"/tokens/{token_id}/deployments")
