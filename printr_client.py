import http.client
import json
import os
import time
import logging
import base64
from solana.rpc.api import Client
#from solana.transaction import Transaction
from solders.transaction import Transaction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
PRINTR_API_URL = os.getenv("PRINTR_API_URL")
PRINTR_BEARER_TOKEN = os.getenv("PRINTR_BEARER_TOKEN")

# CAIP-2 chain mappings
CHAIN_MAPPINGS = {
    "ethereum": os.getenv("CHAIN_ETHEREUM"),
    "arbitrum": os.getenv("CHAIN_ARBITRUM"),
    "avalanche": os.getenv("CHAIN_AVALANCHE"),
    "base": os.getenv("CHAIN_BASE"),
    "bnb": os.getenv("CHAIN_BNB"),
    "mantle": os.getenv("CHAIN_MANTLE"),
    "solana": os.getenv("CHAIN_SOLANA"),
}

# Creator accounts
CREATOR_ACCOUNTS = {
    "ethereum": os.getenv("CREATOR_ETHEREUM"),
    "arbitrum": os.getenv("CREATOR_ARBITRUM"),
    "avalanche": os.getenv("CREATOR_AVALANCHE"),
    "base": os.getenv("CREATOR_BASE"),
    "bnb": os.getenv("CREATOR_BNB"),
    "mantle": os.getenv("CREATOR_MANTLE"),
    "solana": os.getenv("CREATOR_SOLANA"),
}

# Private keys
PRIVATE_KEYS = {
    "ethereum": os.getenv("PRIVATE_KEY_ETHEREUM"),
    "arbitrum": os.getenv("PRIVATE_KEY_ARBITRUM"),
    "avalanche": os.getenv("PRIVATE_KEY_AVALANCHE"),
    "base": os.getenv("PRIVATE_KEY_BASE"),
    "bnb": os.getenv("PRIVATE_KEY_BNB"),
    "mantle": os.getenv("PRIVATE_KEY_MANTLE"),
    "solana": os.getenv("PRIVATE_KEY_SOLANA"),
}

# RPC endpoints
RPC_ENDPOINTS = {
    "ethereum": os.getenv("RPC_ETHEREUM"),
    "arbitrum": os.getenv("RPC_ARBITRUM"),
    "avalanche": os.getenv("RPC_AVALANCHE"),
    "base": os.getenv("RPC_BASE"),
    "bnb": os.getenv("RPC_BNB"),
    "mantle": os.getenv("RPC_MANTLE"),
    "solana": os.getenv("RPC_SOLANA"),
}

# Logging setup
logging.basicConfig(
    filename="printr_client.log",
    level=logging.INFO,
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

def make_api_request(method, endpoint, payload=None, retries=3, backoff_factor=2):
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
                    conn.request(method, f"{base_path}{endpoint}", json.dumps(payload), headers)
                else:
                    conn.request(method, f"{base_path}{endpoint}", headers=headers)
                response = conn.getresponse()
                data = response.read().decode("utf-8")
                if response.status in (200, 201):
                    return response.status, json.loads(data)
                elif response.status == 429:
                    retry_after = int(response.getheader("X-RateLimit-Reset", 60))
                    logger.warning(f"Rate limit exceeded, retrying after {retry_after} seconds")
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
                time.sleep(backoff_factor ** attempt)
    finally:
        conn.close()

def get_token_quote(chains, initial_buy_percent=5, graduation_threshold=69000):
    """Get a quote for token creation from /print/quote."""
    caip_chains = [CHAIN_MAPPINGS.get(chain, chain) for chain in chains]
    payload = {
        "chains": caip_chains,
        "initial_buy": {"supply_percent": initial_buy_percent},
        "graduation_threshold_per_chain_usd": graduation_threshold,
    }
    logger.info(f"Requesting quote for chains: {caip_chains}")
    status, response = make_api_request("POST", "/print/quote", payload)
    return status, response

def create_token(name, symbol, description, image_b64, chains, initial_buy_percent=5, graduation_threshold=69000, external_links=None):
    """Create a token using /print endpoint."""
    caip_chains = [CHAIN_MAPPINGS.get(chain, chain) for chain in chains]
    home_chain = caip_chains[0].split(":")[0]  # e.g., 'solana' or 'eip155'
    creator_account = CREATOR_ACCOUNTS.get(home_chain)
    if not creator_account:
        logger.error(f"No creator account for home chain: {home_chain}")
        return 400, {"error": {"code": "NO_CREATOR_ACCOUNT", "message": f"No creator account for {home_chain}"}}

    payload = {
        "creator_accounts": [creator_account],
        "name": name,
        "symbol": symbol,
        "description": description,
        "image": image_b64,
        "chains": caip_chains,
        "initial_buy": {"supply_percent": initial_buy_percent},
        "graduation_threshold_per_chain_usd": graduation_threshold,
    }
    if external_links:
        payload["external_links"] = external_links

    logger.info(f"Creating token {name} ({symbol}) on chains: {caip_chains}")
    status, response = make_api_request("POST", "/print", payload)
    return status, response

def sign_and_submit_transaction(home_chain, payload):
    """Sign and submit the transaction based on the home chain."""
    chain_key = home_chain.split(":")[0]  # e.g., 'solana' or 'eip155'
    private_key = PRIVATE_KEYS.get(chain_key)
    rpc_endpoint = RPC_ENDPOINTS.get(chain_key)
    if not private_key or not rpc_endpoint:
        logger.error(f"Missing private key or RPC endpoint for {chain_key}")
        return False, f"Missing private key or RPC endpoint for {chain_key}"

    try:
        if chain_key == "solana":
            client = Client(rpc_endpoint)
            keypair = Keypair.from_base58_string(private_key)  # Or use from_seed for seed phrase
            tx = Transaction()
            for ix in payload.get("ixs", []):
                accounts = [AccountMeta(Pubkey.from_string(acc["pubkey"]), acc["is_signer"], acc["is_writable"]) for acc in ix["accounts"]]
                instruction = Instruction(
                    program_id=Pubkey.from_string(ix["program_id"]),
                    accounts=accounts,
                    data=base64.b64decode(ix["data"])
                )
                tx.add(instruction)
            response = client.send_transaction(tx, keypair)
            tx_id = response.value
            logger.info(f"Solana transaction submitted: {tx_id}")
            return True, tx_id
        else:  # EVM chains (ethereum, arbitrum, avalanche, base, bnb, mantle)
            w3 = Web3(Web3.HTTPProvider(rpc_endpoint))
            account = Account.from_key(private_key)
            to_address = payload.get("to")
            calldata = payload.get("calldata")
            value = int(payload.get("value", "0"), 16) if payload.get("value") else 0
            gas_limit = payload.get("gas_limit", 1000000)
            tx = {
                "to": w3.to_checksum_address(to_address),
                "data": calldata,
                "value": value,
                "gas": gas_limit,
                "gasPrice": w3.eth.gas_price,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": int(home_chain.split(":")[1])
            }
            signed_tx = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            logger.info(f"EVM transaction submitted on {chain_key}: {tx_hash.hex()}")
            return True, tx_hash.hex()
    except Exception as e:
        logger.error(f"Failed to sign/submit transaction on {chain_key}: {str(e)}")
        return False, str(e)

def get_token_status(token_id):
    """Check deployment status using /tokens/{id}/deployments."""
    logger.info(f"Checking status for token_id: {token_id}")
    status, response = make_api_request("GET", f"/tokens/{token_id}/deployments")
    return status, response