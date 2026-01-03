import os
import json
import sqlite3
import logging
import hashlib
import base64
from typing import Optional
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from eth_account import Account
from web3 import Web3
from solders.keypair import Keypair
from tronpy.keys import PrivateKey as TronPrivateKey

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = 7338429782
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "default_key_change_me_32bytes!")

NETWORKS = {
    "ETH": {
        "name": "Ethereum",
        "rpc": "https://eth.llamarpc.com",
        "chain_id": 1,
        "symbol": "ETH",
        "explorer": "https://etherscan.io",
        "type": "evm"
    },
    "BSC": {
        "name": "Binance Smart Chain",
        "rpc": "https://bsc-dataseed.binance.org",
        "chain_id": 56,
        "symbol": "BNB",
        "explorer": "https://bscscan.com",
        "type": "evm"
    },
    "POLYGON": {
        "name": "Polygon",
        "rpc": "https://polygon-rpc.com",
        "chain_id": 137,
        "symbol": "MATIC",
        "explorer": "https://polygonscan.com",
        "type": "evm"
    },
    "SOLANA": {
        "name": "Solana",
        "rpc": "https://api.mainnet-beta.solana.com",
        "symbol": "SOL",
        "explorer": "https://solscan.io",
        "type": "solana"
    },
    "TRON": {
        "name": "Tron",
        "rpc": "https://api.trongrid.io",
        "symbol": "TRX",
        "explorer": "https://tronscan.org",
        "type": "tron"
    }
}

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    }
]


class WalletDatabase:
    def __init__(self, db_path: str = "wallets.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                network TEXT NOT NULL,
                address TEXT NOT NULL,
                encrypted_private_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, network)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                network TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                amount TEXT NOT NULL,
                tx_hash TEXT,
                destination TEXT,
                token_address TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def get_wallet(self, user_id: int, network: str) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT address, encrypted_private_key FROM wallets "
            "WHERE user_id = ? AND network = ?",
            (user_id, network)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"address": row[0], "encrypted_private_key": row[1]}
        return None

    def save_wallet(
        self,
        user_id: int,
        network: str,
        address: str,
        encrypted_private_key: str
    ):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO wallets "
            "(user_id, network, address, encrypted_private_key) "
            "VALUES (?, ?, ?, ?)",
            (user_id, network, address, encrypted_private_key)
        )
        conn.commit()
        conn.close()

    def get_all_wallets(self, user_id: int) -> list:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT network, address FROM wallets WHERE user_id = ?",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"network": row[0], "address": row[1]} for row in rows]

    def log_transaction(
        self,
        user_id: int,
        network: str,
        tx_type: str,
        amount: str,
        tx_hash: str = None,
        destination: str = None,
        token_address: str = None,
        status: str = "pending"
    ):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions "
            "(user_id, network, tx_type, amount, tx_hash, destination, "
            "token_address, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, network, tx_type, amount, tx_hash, destination,
             token_address, status)
        )
        conn.commit()
        conn.close()


class CryptoUtils:
    @staticmethod
    def get_encryption_key() -> bytes:
        key = ENCRYPTION_KEY.encode()
        return hashlib.sha256(key).digest()

    @staticmethod
    def encrypt_private_key(private_key: str) -> str:
        key = CryptoUtils.get_encryption_key()
        cipher = AES.new(key, AES.MODE_CBC)
        ct_bytes = cipher.encrypt(pad(private_key.encode(), AES.block_size))
        iv = base64.b64encode(cipher.iv).decode()
        ct = base64.b64encode(ct_bytes).decode()
        return json.dumps({"iv": iv, "ciphertext": ct})

    @staticmethod
    def decrypt_private_key(encrypted_data: str) -> str:
        key = CryptoUtils.get_encryption_key()
        data = json.loads(encrypted_data)
        iv = base64.b64decode(data["iv"])
        ct = base64.b64decode(data["ciphertext"])
        cipher = AES.new(key, AES.MODE_CBC, iv)
        pt = unpad(cipher.decrypt(ct), AES.block_size)
        return pt.decode()


class WalletGenerator:
    @staticmethod
    def generate_evm_wallet() -> tuple:
        Account.enable_unaudited_hdwallet_features()
        account = Account.create()
        return account.address, account.key.hex()

    @staticmethod
    def generate_solana_wallet() -> tuple:
        keypair = Keypair()
        address = str(keypair.pubkey())
        private_key = base64.b64encode(bytes(keypair)).decode()
        return address, private_key

    @staticmethod
    def generate_tron_wallet() -> tuple:
        priv_key = TronPrivateKey.random()
        address = priv_key.public_key.to_base58check_address()
        return address, priv_key.hex()

    @staticmethod
    def generate_wallet(network: str) -> tuple:
        network_info = NETWORKS.get(network.upper())
        if not network_info:
            raise ValueError(f"Unsupported network: {network}")

        if network_info["type"] == "evm":
            return WalletGenerator.generate_evm_wallet()
        elif network_info["type"] == "solana":
            return WalletGenerator.generate_solana_wallet()
        elif network_info["type"] == "tron":
            return WalletGenerator.generate_tron_wallet()
        else:
            raise ValueError(f"Unknown network type: {network_info['type']}")


class BalanceChecker:
    @staticmethod
    async def get_evm_balance(
        network: str,
        address: str,
        token_address: str = None
    ) -> dict:
        network_info = NETWORKS[network]
        w3 = Web3(Web3.HTTPProvider(network_info["rpc"]))

        if token_address:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            try:
                balance = contract.functions.balanceOf(
                    Web3.to_checksum_address(address)
                ).call()
                decimals = contract.functions.decimals().call()
                symbol = contract.functions.symbol().call()
                return {
                    "balance": str(Decimal(balance) / Decimal(10 ** decimals)),
                    "symbol": symbol,
                    "raw_balance": balance
                }
            except Exception as e:
                logger.error(f"Error getting token balance: {e}")
                return {"balance": "0", "symbol": "TOKEN", "error": str(e)}
        else:
            balance = w3.eth.get_balance(Web3.to_checksum_address(address))
            return {
                "balance": str(Web3.from_wei(balance, "ether")),
                "symbol": network_info["symbol"],
                "raw_balance": balance
            }

    @staticmethod
    async def get_solana_balance(address: str) -> dict:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [address]
            }
            async with session.post(
                NETWORKS["SOLANA"]["rpc"],
                json=payload
            ) as resp:
                data = await resp.json()
                if "result" in data:
                    lamports = data["result"]["value"]
                    return {
                        "balance": str(Decimal(lamports) / Decimal(10 ** 9)),
                        "symbol": "SOL",
                        "raw_balance": lamports
                    }
                return {"balance": "0", "symbol": "SOL", "error": data}

    @staticmethod
    async def get_tron_balance(address: str) -> dict:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = f"{NETWORKS['TRON']['rpc']}/v1/accounts/{address}"
            async with session.get(url) as resp:
                data = await resp.json()
                if "data" in data and len(data["data"]) > 0:
                    balance = data["data"][0].get("balance", 0)
                    return {
                        "balance": str(Decimal(balance) / Decimal(10 ** 6)),
                        "symbol": "TRX",
                        "raw_balance": balance
                    }
                return {"balance": "0", "symbol": "TRX"}

    @staticmethod
    async def get_balance(
        network: str,
        address: str,
        token_address: str = None
    ) -> dict:
        network = network.upper()
        network_info = NETWORKS.get(network)
        if not network_info:
            return {"error": f"Unsupported network: {network}"}

        try:
            if network_info["type"] == "evm":
                return await BalanceChecker.get_evm_balance(
                    network, address, token_address
                )
            elif network_info["type"] == "solana":
                return await BalanceChecker.get_solana_balance(address)
            elif network_info["type"] == "tron":
                return await BalanceChecker.get_tron_balance(address)
        except Exception as e:
            logger.error(f"Error getting balance for {network}: {e}")
            return {"error": str(e)}


class WithdrawalHandler:
    @staticmethod
    async def withdraw_evm(
        network: str,
        private_key: str,
        to_address: str,
        amount: str,
        token_address: str = None
    ) -> dict:
        network_info = NETWORKS[network]
        w3 = Web3(Web3.HTTPProvider(network_info["rpc"]))
        account = Account.from_key(private_key)

        try:
            if token_address:
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(token_address),
                    abi=ERC20_ABI
                )
                decimals = contract.functions.decimals().call()
                amount_wei = int(Decimal(amount) * Decimal(10 ** decimals))

                tx = contract.functions.transfer(
                    Web3.to_checksum_address(to_address),
                    amount_wei
                ).build_transaction({
                    "from": account.address,
                    "nonce": w3.eth.get_transaction_count(account.address),
                    "gas": 100000,
                    "gasPrice": w3.eth.gas_price,
                    "chainId": network_info["chain_id"]
                })
            else:
                amount_wei = Web3.to_wei(amount, "ether")
                tx = {
                    "to": Web3.to_checksum_address(to_address),
                    "value": amount_wei,
                    "nonce": w3.eth.get_transaction_count(account.address),
                    "gas": 21000,
                    "gasPrice": w3.eth.gas_price,
                    "chainId": network_info["chain_id"]
                }

            signed_tx = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            return {
                "success": True,
                "tx_hash": tx_hash.hex(),
                "explorer_url": f"{network_info['explorer']}/tx/0x{tx_hash.hex()}"
            }
        except Exception as e:
            logger.error(f"EVM withdrawal error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def withdraw_solana(
        private_key: str,
        to_address: str,
        amount: str
    ) -> dict:
        try:
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            from solders.system_program import transfer, TransferParams
            from solders.transaction import Transaction
            from solders.message import Message
            from solders.hash import Hash
            import aiohttp

            keypair = Keypair.from_bytes(base64.b64decode(private_key))
            to_pubkey = Pubkey.from_string(to_address)
            lamports = int(Decimal(amount) * Decimal(10 ** 9))

            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getLatestBlockhash",
                    "params": []
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=payload
                ) as resp:
                    data = await resp.json()
                    blockhash = Hash.from_string(
                        data["result"]["value"]["blockhash"]
                    )

                ix = transfer(TransferParams(
                    from_pubkey=keypair.pubkey(),
                    to_pubkey=to_pubkey,
                    lamports=lamports
                ))
                msg = Message.new_with_blockhash([ix], keypair.pubkey(), blockhash)
                tx = Transaction.new_unsigned(msg)
                tx.sign([keypair], blockhash)

                tx_bytes = bytes(tx)
                tx_base64 = base64.b64encode(tx_bytes).decode()

                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [tx_base64, {"encoding": "base64"}]
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=payload
                ) as resp:
                    data = await resp.json()
                    if "result" in data:
                        return {
                            "success": True,
                            "tx_hash": data["result"],
                            "explorer_url": (
                                f"{NETWORKS['SOLANA']['explorer']}"
                                f"/tx/{data['result']}"
                            )
                        }
                    return {"success": False, "error": data.get("error", data)}
        except Exception as e:
            logger.error(f"Solana withdrawal error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def withdraw_tron(
        private_key: str,
        to_address: str,
        amount: str
    ) -> dict:
        try:
            from tronpy import Tron
            from tronpy.keys import PrivateKey

            client = Tron()
            priv_key = PrivateKey(bytes.fromhex(private_key))
            from_address = priv_key.public_key.to_base58check_address()

            amount_sun = int(Decimal(amount) * Decimal(10 ** 6))

            txn = (
                client.trx.transfer(from_address, to_address, amount_sun)
                .build()
                .sign(priv_key)
            )
            result = txn.broadcast()

            if result.get("result"):
                return {
                    "success": True,
                    "tx_hash": result["txid"],
                    "explorer_url": (
                        f"{NETWORKS['TRON']['explorer']}"
                        f"/#/transaction/{result['txid']}"
                    )
                }
            return {"success": False, "error": result}
        except Exception as e:
            logger.error(f"Tron withdrawal error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def withdraw(
        network: str,
        private_key: str,
        to_address: str,
        amount: str,
        token_address: str = None
    ) -> dict:
        network = network.upper()
        network_info = NETWORKS.get(network)
        if not network_info:
            return {"success": False, "error": f"Unsupported network: {network}"}

        if network_info["type"] == "evm":
            return await WithdrawalHandler.withdraw_evm(
                network, private_key, to_address, amount, token_address
            )
        elif network_info["type"] == "solana":
            return await WithdrawalHandler.withdraw_solana(
                private_key, to_address, amount
            )
        elif network_info["type"] == "tron":
            return await WithdrawalHandler.withdraw_tron(
                private_key, to_address, amount
            )


db = WalletDatabase()


def is_authorized(user_id: int) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return user_id == ALLOWED_USER_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    welcome_text = """
Welcome to Depo Bot - Your Virtual Wallet

Available Commands:
/generate <network> - Generate a new wallet for a network
/balance [network] - Check your wallet balances
/deposit - Show your deposit addresses
/withdraw <network> <amount> <address> [token] - Withdraw funds

Supported Networks:
- ETH (Ethereum)
- BSC (Binance Smart Chain)
- POLYGON (Polygon)
- SOLANA (Solana)
- TRON (Tron)

Example:
/generate ETH
/balance ETH
/withdraw ETH 0.1 0x123...abc
"""
    await update.message.reply_text(welcome_text)


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    if not context.args:
        networks_list = ", ".join(NETWORKS.keys())
        await update.message.reply_text(
            f"Please specify a network.\nUsage: /generate <network>\n"
            f"Available networks: {networks_list}"
        )
        return

    network = context.args[0].upper()
    if network not in NETWORKS:
        networks_list = ", ".join(NETWORKS.keys())
        await update.message.reply_text(
            f"Unsupported network: {network}\n"
            f"Available networks: {networks_list}"
        )
        return

    user_id = update.effective_user.id
    existing = db.get_wallet(user_id, network)
    if existing:
        await update.message.reply_text(
            f"You already have a {NETWORKS[network]['name']} wallet:\n"
            f"Address: `{existing['address']}`\n\n"
            f"Use /deposit to see all your addresses.",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"Generating {NETWORKS[network]['name']} wallet...")

    try:
        address, private_key = WalletGenerator.generate_wallet(network)
        encrypted_key = CryptoUtils.encrypt_private_key(private_key)
        db.save_wallet(user_id, network, address, encrypted_key)

        explorer = NETWORKS[network]["explorer"]
        if network == "SOLANA":
            explorer_url = f"{explorer}/account/{address}"
        elif network == "TRON":
            explorer_url = f"{explorer}/#/address/{address}"
        else:
            explorer_url = f"{explorer}/address/{address}"

        await update.message.reply_text(
            f"Wallet generated successfully!\n\n"
            f"Network: {NETWORKS[network]['name']}\n"
            f"Address: `{address}`\n\n"
            f"Explorer: {explorer_url}\n\n"
            f"Your private key has been securely encrypted and stored.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error generating wallet: {e}")
        await update.message.reply_text(f"Error generating wallet: {str(e)}")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    user_id = update.effective_user.id
    token_address = None

    if context.args:
        network = context.args[0].upper()
        if len(context.args) > 1:
            token_address = context.args[1]

        if network not in NETWORKS:
            networks_list = ", ".join(NETWORKS.keys())
            await update.message.reply_text(
                f"Unsupported network: {network}\n"
                f"Available networks: {networks_list}"
            )
            return

        wallet = db.get_wallet(user_id, network)
        if not wallet:
            await update.message.reply_text(
                f"No wallet found for {NETWORKS[network]['name']}.\n"
                f"Use /generate {network} to create one."
            )
            return

        await update.message.reply_text(f"Checking {NETWORKS[network]['name']} balance...")

        balance_info = await BalanceChecker.get_balance(
            network, wallet["address"], token_address
        )

        if "error" in balance_info:
            await update.message.reply_text(
                f"Error checking balance: {balance_info['error']}"
            )
            return

        await update.message.reply_text(
            f"Network: {NETWORKS[network]['name']}\n"
            f"Address: `{wallet['address']}`\n"
            f"Balance: {balance_info['balance']} {balance_info['symbol']}",
            parse_mode="Markdown"
        )
    else:
        wallets = db.get_all_wallets(user_id)
        if not wallets:
            await update.message.reply_text(
                "No wallets found. Use /generate <network> to create one."
            )
            return

        await update.message.reply_text("Checking all balances...")

        response = "Your Wallet Balances:\n\n"
        for wallet in wallets:
            balance_info = await BalanceChecker.get_balance(
                wallet["network"], wallet["address"]
            )
            balance_str = balance_info.get("balance", "Error")
            symbol = balance_info.get("symbol", "")
            response += (
                f"{NETWORKS[wallet['network']]['name']}:\n"
                f"  Address: `{wallet['address']}`\n"
                f"  Balance: {balance_str} {symbol}\n\n"
            )

        await update.message.reply_text(response, parse_mode="Markdown")


async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    user_id = update.effective_user.id
    wallets = db.get_all_wallets(user_id)

    if not wallets:
        await update.message.reply_text(
            "No wallets found. Use /generate <network> to create one.\n\n"
            "Available networks: " + ", ".join(NETWORKS.keys())
        )
        return

    response = "Your Deposit Addresses:\n\n"
    for wallet in wallets:
        network_info = NETWORKS[wallet["network"]]
        if wallet["network"] == "SOLANA":
            explorer_url = f"{network_info['explorer']}/account/{wallet['address']}"
        elif wallet["network"] == "TRON":
            explorer_url = f"{network_info['explorer']}/#/address/{wallet['address']}"
        else:
            explorer_url = f"{network_info['explorer']}/address/{wallet['address']}"

        response += (
            f"{network_info['name']} ({network_info['symbol']}):\n"
            f"`{wallet['address']}`\n"
            f"[View on Explorer]({explorer_url})\n\n"
        )

    await update.message.reply_text(response, parse_mode="Markdown")


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: /withdraw <network> <amount> <destination_address> [token_address]\n\n"
            "Examples:\n"
            "/withdraw ETH 0.1 0x123...abc\n"
            "/withdraw BSC 10 0x123...abc 0xtoken..."
        )
        return

    network = context.args[0].upper()
    amount = context.args[1]
    destination = context.args[2]
    token_address = context.args[3] if len(context.args) > 3 else None

    if network not in NETWORKS:
        networks_list = ", ".join(NETWORKS.keys())
        await update.message.reply_text(
            f"Unsupported network: {network}\n"
            f"Available networks: {networks_list}"
        )
        return

    try:
        Decimal(amount)
    except Exception:
        await update.message.reply_text("Invalid amount. Please enter a valid number.")
        return

    user_id = update.effective_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        await update.message.reply_text(
            f"No wallet found for {NETWORKS[network]['name']}.\n"
            f"Use /generate {network} to create one."
        )
        return

    token_val = token_address or 'native'
    callback_data = f"confirm_withdraw:{network}:{amount}:{destination}:{token_val}"
    keyboard = [
        [
            InlineKeyboardButton("Confirm", callback_data=callback_data),
            InlineKeyboardButton("Cancel", callback_data="cancel_withdraw")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    token_info = f"\nToken: {token_address}" if token_address else ""
    await update.message.reply_text(
        f"Withdrawal Confirmation\n\n"
        f"Network: {NETWORKS[network]['name']}\n"
        f"Amount: {amount} {NETWORKS[network]['symbol'] if not token_address else 'tokens'}\n"
        f"Destination: {destination}{token_info}\n\n"
        f"Please confirm this withdrawal:",
        reply_markup=reply_markup
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_authorized(query.from_user.id):
        await query.edit_message_text("You are not authorized to use this bot.")
        return

    if query.data == "cancel_withdraw":
        await query.edit_message_text("Withdrawal cancelled.")
        return

    if query.data.startswith("confirm_withdraw:"):
        parts = query.data.split(":")
        network = parts[1]
        amount = parts[2]
        destination = parts[3]
        token_address = parts[4] if parts[4] != "native" else None

        user_id = query.from_user.id
        wallet = db.get_wallet(user_id, network)

        if not wallet:
            await query.edit_message_text("Wallet not found.")
            return

        await query.edit_message_text("Processing withdrawal...")

        try:
            private_key = CryptoUtils.decrypt_private_key(
                wallet["encrypted_private_key"]
            )
            result = await WithdrawalHandler.withdraw(
                network, private_key, destination, amount, token_address
            )

            if result.get("success"):
                db.log_transaction(
                    user_id, network, "withdraw", amount,
                    result.get("tx_hash"), destination, token_address, "completed"
                )
                await query.edit_message_text(
                    f"Withdrawal successful!\n\n"
                    f"Amount: {amount}\n"
                    f"Destination: {destination}\n"
                    f"TX Hash: `{result.get('tx_hash')}`\n\n"
                    f"[View on Explorer]({result.get('explorer_url')})",
                    parse_mode="Markdown"
                )
            else:
                db.log_transaction(
                    user_id, network, "withdraw", amount,
                    None, destination, token_address, "failed"
                )
                await query.edit_message_text(
                    f"Withdrawal failed: {result.get('error')}"
                )
        except Exception as e:
            logger.error(f"Withdrawal error: {e}")
            await query.edit_message_text(f"Withdrawal error: {str(e)}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return

    help_text = """
Depo Bot - Help

Commands:
/start - Show welcome message
/help - Show this help message
/generate <network> - Generate a new wallet
/balance [network] [token_address] - Check balances
/deposit - Show deposit addresses
/withdraw <network> <amount> <address> [token] - Withdraw funds

Networks:
- ETH: Ethereum Mainnet
- BSC: Binance Smart Chain
- POLYGON: Polygon (Matic)
- SOLANA: Solana
- TRON: Tron

Examples:
/generate ETH - Create Ethereum wallet
/balance - Check all balances
/balance ETH - Check ETH balance
/balance BSC 0xtoken... - Check token balance on BSC
/withdraw ETH 0.1 0x123... - Withdraw 0.1 ETH
/withdraw BSC 100 0x123... 0xtoken... - Withdraw tokens

Note: All private keys are encrypted and stored securely.
"""
    await update.message.reply_text(help_text)


def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("deposit", deposit))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Starting Depo Bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
