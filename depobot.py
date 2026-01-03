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
    ConversationHandler,
    MessageHandler,
    filters,
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
ALLOWED_CHAT_ID = -1002215462357
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "default_key_change_me_32bytes!")

wallet_balances_cache = {}

NETWORKS = {
    "ETH": {
        "name": "Ethereum",
        "rpc": "https://eth.llamarpc.com",
        "chain_id": 1,
        "symbol": "ETH",
        "explorer": "https://etherscan.io",
        "type": "evm",
        "icon": "\u26aa"
    },
    "BSC": {
        "name": "BNB Chain",
        "rpc": "https://bsc-dataseed.binance.org",
        "chain_id": 56,
        "symbol": "BNB",
        "explorer": "https://bscscan.com",
        "type": "evm",
        "icon": "\U0001F7E1"
    },
    "POLYGON": {
        "name": "Polygon",
        "rpc": "https://polygon-rpc.com",
        "chain_id": 137,
        "symbol": "MATIC",
        "explorer": "https://polygonscan.com",
        "type": "evm",
        "icon": "\U0001F7E3"
    },
    "SOLANA": {
        "name": "Solana",
        "rpc": "https://api.mainnet-beta.solana.com",
        "symbol": "SOL",
        "explorer": "https://solscan.io",
        "type": "solana",
        "icon": "\U0001F7E2"
    },
    "TRON": {
        "name": "Tron",
        "rpc": "https://api.trongrid.io",
        "symbol": "TRX",
        "explorer": "https://tronscan.org",
        "type": "tron",
        "icon": "\U0001F534"
    },
    "LTC": {
        "name": "Litecoin",
        "rpc": "https://ltc.getblock.io/mainnet/",
        "symbol": "LTC",
        "explorer": "https://blockchair.com/litecoin",
        "type": "ltc",
        "icon": "\U0001F315"
    }
}

TOKENS = {
    "ETH": {
        "name": "Ethereum",
        "symbol": "ETH",
        "icon": "\u26aa",
        "native": True,
        "networks": {
            "ETH": {"native": True, "decimals": 18}
        }
    },
    "BNB": {
        "name": "BNB",
        "symbol": "BNB",
        "icon": "\U0001F7E1",
        "native": True,
        "networks": {
            "BSC": {"native": True, "decimals": 18}
        }
    },
    "MATIC": {
        "name": "Polygon",
        "symbol": "MATIC",
        "icon": "\U0001F7E3",
        "native": True,
        "networks": {
            "POLYGON": {"native": True, "decimals": 18}
        }
    },
    "SOL": {
        "name": "Solana",
        "symbol": "SOL",
        "icon": "\U0001F7E2",
        "native": True,
        "networks": {
            "SOLANA": {"native": True, "decimals": 9}
        }
    },
    "TRX": {
        "name": "Tron",
        "symbol": "TRX",
        "icon": "\U0001F534",
        "native": True,
        "networks": {
            "TRON": {"native": True, "decimals": 6}
        }
    },
    "USDT": {
        "name": "Tether USD",
        "symbol": "USDT",
        "icon": "\U0001F4B5",
        "networks": {
            "ETH": {
                "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                "decimals": 6
            },
            "BSC": {
                "address": "0x55d398326f99059fF775485246999027B3197955",
                "decimals": 18
            },
            "POLYGON": {
                "address": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
                "decimals": 6
            },
            "TRON": {
                "address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                "decimals": 6
            }
        }
    },
    "USDC": {
        "name": "USD Coin",
        "symbol": "USDC",
        "icon": "\U0001F4B2",
        "networks": {
            "ETH": {
                "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                "decimals": 6
            },
            "BSC": {
                "address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
                "decimals": 18
            },
            "POLYGON": {
                "address": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
                "decimals": 6
            },
            "SOLANA": {
                "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "decimals": 6
            }
        }
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

WITHDRAW_AMOUNT, WITHDRAW_ADDRESS, WITHDRAW_CONFIRM = range(3)

pending_withdrawals = {}


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
    def generate_ltc_wallet() -> tuple:
        import hashlib
        import secrets
        private_key = secrets.token_bytes(32)
        from ecdsa import SigningKey, SECP256k1
        sk = SigningKey.from_string(private_key, curve=SECP256k1)
        vk = sk.get_verifying_key()
        public_key = b'\x04' + vk.to_string()
        sha256_hash = hashlib.sha256(public_key).digest()
        ripemd160 = hashlib.new('ripemd160')
        ripemd160.update(sha256_hash)
        pubkey_hash = ripemd160.digest()
        version = b'\x30'
        versioned_payload = version + pubkey_hash
        checksum = hashlib.sha256(
            hashlib.sha256(versioned_payload).digest()
        ).digest()[:4]
        address_bytes = versioned_payload + checksum
        alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
        num = int.from_bytes(address_bytes, 'big')
        address = ''
        while num:
            num, rem = divmod(num, 58)
            address = alphabet[rem] + address
        for byte in address_bytes:
            if byte == 0:
                address = '1' + address
            else:
                break
        return address, private_key.hex()

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
        elif network_info["type"] == "ltc":
            return WalletGenerator.generate_ltc_wallet()
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
    async def get_ltc_balance(address: str) -> dict:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/balance"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        balance_satoshi = data.get("balance", 0)
                        return {
                            "balance": str(Decimal(balance_satoshi) / Decimal(10 ** 8)),
                            "symbol": "LTC",
                            "raw_balance": balance_satoshi
                        }
                    return {"balance": "0", "symbol": "LTC", "error": "API error"}
        except Exception as e:
            logger.error(f"Error getting LTC balance: {e}")
            return {"balance": "0", "symbol": "LTC", "error": str(e)}

    @staticmethod
    async def get_solana_token_balance(address: str, token_mint: str) -> dict:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        address,
                        {"mint": token_mint},
                        {"encoding": "jsonParsed"}
                    ]
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=payload
                ) as resp:
                    data = await resp.json()
                    if "result" in data and data["result"]["value"]:
                        account = data["result"]["value"][0]
                        token_amount = account["account"]["data"]["parsed"]["info"]
                        amount = token_amount["tokenAmount"]["uiAmount"]
                        return {
                            "balance": str(amount) if amount else "0",
                            "symbol": "USDC",
                            "raw_balance": int(token_amount["tokenAmount"]["amount"])
                        }
                    return {"balance": "0", "symbol": "USDC", "raw_balance": 0}
        except Exception as e:
            logger.error(f"Error getting Solana token balance: {e}")
            return {"balance": "0", "symbol": "USDC", "error": str(e)}

    @staticmethod
    async def get_tron_token_balance(address: str, token_address: str) -> dict:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{NETWORKS['TRON']['rpc']}/v1/accounts/{address}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    if "data" in data and len(data["data"]) > 0:
                        trc20 = data["data"][0].get("trc20", [])
                        for token in trc20:
                            if token_address in token:
                                balance = int(token[token_address])
                                return {
                                    "balance": str(Decimal(balance) / Decimal(10 ** 6)),
                                    "symbol": "USDT",
                                    "raw_balance": balance
                                }
                    return {"balance": "0", "symbol": "USDT", "raw_balance": 0}
        except Exception as e:
            logger.error(f"Error getting Tron token balance: {e}")
            return {"balance": "0", "symbol": "USDT", "error": str(e)}

    @staticmethod
    async def get_token_balance(
        token: str,
        network: str,
        address: str
    ) -> dict:
        token = token.upper()
        network = network.upper()

        if token not in TOKENS:
            return {"error": f"Unsupported token: {token}"}

        token_info = TOKENS[token]
        if network not in token_info["networks"]:
            return {"error": f"{token} not available on {network}"}

        network_token = token_info["networks"][network]
        network_info = NETWORKS.get(network)

        try:
            if network_token.get("native"):
                return await BalanceChecker.get_balance(network, address)

            if network_info["type"] == "evm":
                return await BalanceChecker.get_evm_balance(
                    network, address, network_token["address"]
                )
            elif network_info["type"] == "solana":
                return await BalanceChecker.get_solana_token_balance(
                    address, network_token["address"]
                )
            elif network_info["type"] == "tron":
                return await BalanceChecker.get_tron_token_balance(
                    address, network_token["address"]
                )
        except Exception as e:
            logger.error(f"Error getting {token} balance on {network}: {e}")
            return {"error": str(e)}

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
            elif network_info["type"] == "ltc":
                return await BalanceChecker.get_ltc_balance(address)
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


def is_authorized(user_id: int, chat_id: int = None) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    if user_id == ALLOWED_USER_ID:
        return True
    if chat_id and chat_id == ALLOWED_CHAT_ID:
        return True
    return False


def get_friendly_error(error) -> str:
    error_str = str(error).lower() if error else ""

    if "insufficient funds" in error_str or "balance 0" in error_str:
        return (
            "Insufficient balance! Your wallet doesn't have enough funds "
            "to cover the transaction and gas fees.\n\n"
            "Please deposit more funds first."
        )
    if "transfer amount exceeds balance" in error_str or "exceeds balance" in error_str:
        return (
            "Insufficient token balance! The amount you're trying to send "
            "exceeds your available balance.\n\n"
            "Please reduce the amount or deposit more tokens."
        )
    if "nonce too low" in error_str or "replacement transaction" in error_str:
        return (
            "A previous transaction is still pending on the network.\n\n"
            "Please wait a moment and try again."
        )
    if "execution reverted" in error_str or "revert" in error_str:
        return (
            "Transaction was rejected by the network.\n\n"
            "Please double-check the token and network, then try again."
        )
    if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
        return (
            "Network is currently busy.\n\n"
            "Please try again in a minute."
        )
    if "invalid address" in error_str or "checksum" in error_str:
        return (
            "Invalid wallet address!\n\n"
            "Please check the address format and try again."
        )
    if "timeout" in error_str or "timed out" in error_str:
        return (
            "Network request timed out.\n\n"
            "Please check your connection and try again."
        )
    if "connection" in error_str or "network" in error_str:
        return (
            "Unable to connect to the network.\n\n"
            "Please try again later."
        )

    return "Something went wrong. Please try again later."


def get_main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                "\U0001F4B0 My Wallets", callback_data="menu_wallets"
            ),
            InlineKeyboardButton(
                "\U0001F4E5 Deposit", callback_data="menu_deposit"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001F4CA Balances", callback_data="menu_balance"
            ),
            InlineKeyboardButton(
                "\U0001F4B5 Tokens", callback_data="menu_tokens"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001F4E4 Withdraw", callback_data="menu_withdraw"
            ),
            InlineKeyboardButton(
                "\u2795 Generate", callback_data="menu_generate"
            )
        ],
        [
            InlineKeyboardButton(
                "\u2753 Help", callback_data="menu_help"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_token_keyboard():
    keyboard = []
    for token_key, token_info in TOKENS.items():
        keyboard.append([
            InlineKeyboardButton(
                f"{token_info['icon']} {token_info['name']} ({token_info['symbol']})",
                callback_data=f"token_{token_key}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton("\U0001F3E0 Main Menu", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_token_network_keyboard(token: str):
    token_info = TOKENS.get(token)
    if not token_info:
        return get_back_button("menu_tokens")

    keyboard = []
    for network in token_info["networks"].keys():
        network_info = NETWORKS.get(network)
        if network_info:
            keyboard.append([
                InlineKeyboardButton(
                    f"{network_info['icon']} {network_info['name']}",
                    callback_data=f"tokenbal_{token}_{network}"
                )
            ])
    keyboard.append([
        InlineKeyboardButton("\U0001F519 Back", callback_data="menu_tokens")
    ])
    keyboard.append([
        InlineKeyboardButton("\U0001F3E0 Main Menu", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_network_keyboard(action: str, include_tokens: bool = True):
    keyboard = []
    row = []
    for i, (key, info) in enumerate(NETWORKS.items()):
        btn = InlineKeyboardButton(
            f"{info['icon']} {info['name']}",
            callback_data=f"{action}_{key}"
        )
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    if include_tokens:
        keyboard.append([InlineKeyboardButton(
            "\U0001F4B5 ━━━ TOKENS ━━━ \U0001F4B5",
            callback_data="noop"
        )])

        token_row = []
        for token_key, token_info in TOKENS.items():
            btn = InlineKeyboardButton(
                f"{token_info['icon']} {token_info['symbol']}",
                callback_data=f"{action}_token_{token_key}"
            )
            token_row.append(btn)
            if len(token_row) == 2:
                keyboard.append(token_row)
                token_row = []
        if token_row:
            keyboard.append(token_row)

    keyboard.append([
        InlineKeyboardButton("\U0001F3E0 Main Menu", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_back_button(callback_data: str = "main_menu"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F519 Back", callback_data=callback_data)]
    ])


def get_wallet_card_keyboard(network: str):
    keyboard = [
        [
            InlineKeyboardButton(
                "\U0001F504 Refresh",
                callback_data=f"refresh_{network}"
            ),
            InlineKeyboardButton(
                "\U0001F4E5 Deposit",
                callback_data=f"deposit_{network}"
            )
        ],
        [
            InlineKeyboardButton(
                "\U0001F4E4 Withdraw",
                callback_data=f"withdraw_{network}"
            ),
            InlineKeyboardButton(
                "\U0001F310 Explorer",
                callback_data=f"explorer_{network}"
            )
        ],
        [
            InlineKeyboardButton("\U0001F3E0 Main Menu", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def format_address(address: str) -> str:
    if len(address) > 20:
        return f"{address[:8]}...{address[-6:]}"
    return address


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if not is_authorized(user_id, chat_id):
        await update.message.reply_text(
            "\U0001F6AB *Access Denied*\n\n"
            "You are not authorised to use this bot!",
            parse_mode="Markdown"
        )
        return

    user_name = update.effective_user.first_name or "User"
    wallets = db.get_all_wallets(user_id)
    wallet_count = len(wallets) if wallets else 0

    welcome_text = (
        "\U0001F3E6 *VM CRYPTO BOT*\n"
        + "\u2501" * 24 + "\n\n"
        + f"\U0001F44B Welcome back, *{user_name}*!\n\n"
        + "\U0001F4CA *Portfolio Overview*\n"
        + f"    \U0001F4B0 Wallets: *{wallet_count}*\n"
        + f"    \U0001F517 Networks: *{len(NETWORKS)}*\n"
        + f"    \U0001F4B5 Tokens: *{len(TOKENS)}*\n\n"
        + "\U0001F310 *Available Networks*\n"
    )

    for key, info in NETWORKS.items():
        welcome_text += f"    {info['icon']} {info['name']}\n"

    welcome_text += (
        "\n\U0001F6E1 *Security Status*\n"
        "    \U0001F7E2 Live Monitoring: Active\n"
        "    \U0001F512 Encryption: AES-256\n\n"
        + "\u2501" * 24 + "\n"
        + "\U0001F447 *Choose an option below:*"
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard()
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    menu_text = (
        "\U0001F3E6 *VM CRYPTO BOT*\n"
        "\u2501" * 24 + "\n\n"
        "\U0001F447 *Choose an option below:*"
    )

    await query.edit_message_text(
        menu_text,
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard()
    )


async def show_wallets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    wallets = db.get_all_wallets(user_id)

    if not wallets:
        text = (
            "\U0001F4B0 *My Wallets*\n"
            "\u2501" * 24 + "\n\n"
            "\U0001F4ED *No wallets found*\n\n"
            "Create your first wallet to get started!\n"
            "Tap the button below \U0001F447"
        )
        keyboard = [
            [InlineKeyboardButton(
                "\u2795 Create Wallet", callback_data="menu_generate"
            )],
            [InlineKeyboardButton(
                "\U0001F3E0 Main Menu", callback_data="main_menu"
            )]
        ]
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    text = (
        "\U0001F4B0 *My Wallets*\n"
        + "\u2501" * 24 + "\n\n"
        + f"\U0001F4CA Total: *{len(wallets)}* wallet(s)\n\n"
    )
    keyboard = []

    for wallet in wallets:
        network = wallet["network"]
        info = NETWORKS[network]
        text += (
            f"{info['icon']} *{info['name']}*\n"
            f"    `{format_address(wallet['address'])}`\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(
                f"{info['icon']} {info['name']}",
                callback_data=f"wallet_{network}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("\u2795 Add Wallet", callback_data="menu_generate")
    ])
    keyboard.append([
        InlineKeyboardButton("\U0001F3E0 Main Menu", callback_data="main_menu")
    ])

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_wallet_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        await query.edit_message_text(
            f"\U0001F6AB *Wallet Not Found*\n\n"
            f"No wallet exists for {NETWORKS[network]['name']}",
            parse_mode="Markdown",
            reply_markup=get_back_button("menu_wallets")
        )
        return

    info = NETWORKS[network]
    await query.edit_message_text(
        f"{info['icon']} *{info['name']} Wallet*\n"
        f"\u2501" * 24 + "\n\n"
        f"\U0001F4CD *Address*\n"
        f"    `{wallet['address']}`\n\n"
        f"\U0001F4B0 *Balance*\n"
        f"    Tap refresh to check\n\n"
        f"\U0001F517 *Network*: {info['symbol']}",
        parse_mode="Markdown",
        reply_markup=get_wallet_card_keyboard(network)
    )


async def refresh_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Fetching balance...")

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        await query.edit_message_text(
            f"\u26a0\ufe0f No wallet found for {NETWORKS[network]['name']}",
            reply_markup=get_back_button("menu_wallets")
        )
        return

    info = NETWORKS[network]

    await query.edit_message_text(
        f"{info['icon']} *{info['name']} Wallet*\n\n"
        f"\U0001F4CD *Address:*\n`{wallet['address']}`\n\n"
        f"\u23f3 *Fetching balance...*",
        parse_mode="Markdown"
    )

    balance_info = await BalanceChecker.get_balance(network, wallet["address"])
    balance_str = balance_info.get("balance", "Error")
    symbol = balance_info.get("symbol", info["symbol"])

    await query.edit_message_text(
        f"{info['icon']} *{info['name']} Wallet*\n\n"
        f"\U0001F4CD *Address:*\n`{wallet['address']}`\n\n"
        f"\U0001F4B0 *Balance:* `{balance_str} {symbol}`\n\n"
        f"\U0001F517 *Network:* {info['name']}",
        parse_mode="Markdown",
        reply_markup=get_wallet_card_keyboard(network)
    )


async def show_generate_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    text = (
        "\u2795 *Generate New Wallet*\n\n"
        "Select a network to generate a new wallet:\n\n"
        "\u26a0\ufe0f *Note:* If you already have a wallet for a network, "
        "generating a new one will replace it."
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_network_keyboard("gen", include_tokens=False)
    )


async def generate_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    info = NETWORKS[network]

    existing = db.get_wallet(user_id, network)
    if existing:
        keyboard = [
            [
                InlineKeyboardButton(
                    "\u2705 Yes, Replace",
                    callback_data=f"confirmgen_{network}"
                ),
                InlineKeyboardButton(
                    "\u274c Cancel",
                    callback_data="menu_generate"
                )
            ]
        ]
        await query.edit_message_text(
            f"\u26a0\ufe0f *Warning*\n\n"
            f"You already have a {info['name']} wallet:\n"
            f"`{existing['address']}`\n\n"
            f"Generating a new wallet will *replace* this one.\n"
            f"Make sure you have withdrawn all funds first!\n\n"
            f"Do you want to continue?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    await do_generate_wallet(query, network, user_id)


async def confirm_generate_wallet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id

    await do_generate_wallet(query, network, user_id)


async def do_generate_wallet(query, network: str, user_id: int):
    info = NETWORKS[network]

    await query.edit_message_text(
        f"\u23f3 *Generating {info['name']} wallet...*",
        parse_mode="Markdown"
    )

    try:
        address, private_key = WalletGenerator.generate_wallet(network)
        encrypted_key = CryptoUtils.encrypt_private_key(private_key)
        db.save_wallet(user_id, network, address, encrypted_key)

        keyboard = [
            [
                InlineKeyboardButton(
                    "\U0001F4CA Check Balance",
                    callback_data=f"refresh_{network}"
                ),
                InlineKeyboardButton(
                    "\U0001F4E5 Deposit",
                    callback_data=f"deposit_{network}"
                )
            ],
            [
                InlineKeyboardButton(
                    "\u2795 Generate Another",
                    callback_data="menu_generate"
                )
            ],
            [
                InlineKeyboardButton(
                    "\U0001F3E0 Main Menu",
                    callback_data="main_menu"
                )
            ]
        ]

        await query.edit_message_text(
            f"\u2705 *Wallet Generated Successfully!*\n\n"
            f"{info['icon']} *Network:* {info['name']}\n\n"
            f"\U0001F4CD *Your Address:*\n`{address}`\n\n"
            f"\U0001F512 Your private key has been securely encrypted.\n\n"
            f"\U0001F447 *What would you like to do next?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error generating wallet: {e}")
        await query.edit_message_text(
            f"\u274c *Error generating wallet*\n\n{str(e)}",
            parse_mode="Markdown",
            reply_markup=get_back_button("menu_generate")
        )


async def show_deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "\U0001F4E5 *Deposit Funds*\n\n"
        "Select a network to view your deposit address:"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_network_keyboard("deposit")
    )


async def show_deposit_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)
    info = NETWORKS[network]

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"\u2795 Generate {info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton(
                "\U0001F519 Back",
                callback_data="menu_deposit"
            )]
        ]
        await query.edit_message_text(
            f"\u26a0\ufe0f *No Wallet Found*\n\n"
            f"You don't have a {info['name']} wallet yet.\n"
            f"Generate one first to get a deposit address.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if network == "SOLANA":
        explorer_url = f"{info['explorer']}/account/{wallet['address']}"
    elif network == "TRON":
        explorer_url = f"{info['explorer']}/#/address/{wallet['address']}"
    else:
        explorer_url = f"{info['explorer']}/address/{wallet['address']}"

    keyboard = [
        [InlineKeyboardButton(
            "\U0001F310 View on Explorer",
            url=explorer_url
        )],
        [
            InlineKeyboardButton(
                "\U0001F504 Refresh Balance",
                callback_data=f"refresh_{network}"
            ),
            InlineKeyboardButton(
                "\U0001F4E4 Withdraw",
                callback_data=f"withdraw_{network}"
            )
        ],
        [InlineKeyboardButton(
            "\U0001F519 Back",
            callback_data="menu_deposit"
        )],
        [InlineKeyboardButton(
            "\U0001F3E0 Main Menu",
            callback_data="main_menu"
        )]
    ]

    await query.edit_message_text(
        f"\U0001F4E5 *Deposit {info['symbol']}*\n\n"
        f"{info['icon']} *Network:* {info['name']}\n\n"
        f"\U0001F4CD *Your Deposit Address:*\n"
        f"`{wallet['address']}`\n\n"
        f"\u2139\ufe0f Tap the address to copy it.\n\n"
        f"\u26a0\ufe0f *Important:* Only send {info['symbol']} "
        f"and {info['name']} tokens to this address!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_token_deposit_networks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    token = query.data.split("_")[2]
    token_info = TOKENS.get(token)

    if not token_info:
        await query.edit_message_text(
            "\u26a0\ufe0f Token not found.",
            reply_markup=get_back_button("menu_deposit")
        )
        return

    keyboard = []
    for network_key in token_info["networks"].keys():
        network_info = NETWORKS.get(network_key, {})
        btn = InlineKeyboardButton(
            f"{network_info.get('icon', '')} {network_info.get('name', network_key)}",
            callback_data=f"tokendep_{token}_{network_key}"
        )
        keyboard.append([btn])

    keyboard.append([InlineKeyboardButton(
        "\U0001F519 Back",
        callback_data="menu_deposit"
    )])

    await query.edit_message_text(
        f"{token_info['icon']} *Deposit {token_info['symbol']}*\n\n"
        f"Select the network to deposit {token_info['symbol']}:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_token_deposit_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    token = parts[1]
    network = parts[2]

    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"\u2795 Generate {network_info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton(
                "\U0001F519 Back",
                callback_data=f"deposit_token_{token}"
            )]
        ]
        await query.edit_message_text(
            f"\u26a0\ufe0f *No Wallet Found*\n\n"
            f"You need a {network_info['name']} wallet to receive {token_info['symbol']}.\n"
            f"Generate one first.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if network == "SOLANA":
        explorer_url = f"{network_info['explorer']}/account/{wallet['address']}"
    elif network == "TRON":
        explorer_url = f"{network_info['explorer']}/#/address/{wallet['address']}"
    else:
        explorer_url = f"{network_info['explorer']}/address/{wallet['address']}"

    keyboard = [
        [InlineKeyboardButton(
            "\U0001F310 View on Explorer",
            url=explorer_url
        )],
        [InlineKeyboardButton(
            "\U0001F519 Back",
            callback_data=f"deposit_token_{token}"
        )],
        [InlineKeyboardButton(
            "\U0001F3E0 Main Menu",
            callback_data="main_menu"
        )]
    ]

    await query.edit_message_text(
        f"{token_info['icon']} *Deposit {token_info['symbol']}*\n\n"
        f"{network_info['icon']} *Network:* {network_info['name']}\n\n"
        f"\U0001F4CD *Your Deposit Address:*\n"
        f"`{wallet['address']}`\n\n"
        f"\u2139\ufe0f Tap the address to copy it.\n\n"
        f"\u26a0\ufe0f *Important:* Only send {token_info['symbol']} "
        f"({network_info['name']} network) to this address!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_token_withdraw_networks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    token = query.data.split("_")[2]
    token_info = TOKENS.get(token)

    if not token_info:
        await query.edit_message_text(
            "\u26a0\ufe0f Token not found.",
            reply_markup=get_back_button("menu_withdraw")
        )
        return

    keyboard = []
    for network_key in token_info["networks"].keys():
        network_info = NETWORKS.get(network_key, {})
        btn = InlineKeyboardButton(
            f"{network_info.get('icon', '')} {network_info.get('name', network_key)}",
            callback_data=f"tokenwd_{token}_{network_key}"
        )
        keyboard.append([btn])

    keyboard.append([InlineKeyboardButton(
        "\U0001F519 Back",
        callback_data="menu_withdraw"
    )])

    await query.edit_message_text(
        f"{token_info['icon']} *Withdraw {token_info['symbol']}*\n\n"
        f"Select the network to withdraw {token_info['symbol']}:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_token_withdraw_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer("Checking token balance...")

    parts = query.data.split("_")
    token = parts[1]
    network = parts[2]

    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"\u2795 Generate {network_info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton(
                "\U0001F519 Back",
                callback_data=f"withdraw_token_{token}"
            )]
        ]
        await query.edit_message_text(
            f"\u26a0\ufe0f *No Wallet Found*\n\n"
            f"You need a {network_info['name']} wallet to withdraw {token_info['symbol']}.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    balance_info = await BalanceChecker.get_token_balance(
        token, network, wallet["address"]
    )
    balance_str = balance_info.get("balance", "0")

    network_token = token_info["networks"][network]
    is_native = network_token.get("native", False)

    context.user_data["withdraw_network"] = network
    context.user_data["withdraw_balance"] = balance_str
    context.user_data["withdraw_token"] = token
    context.user_data["withdraw_token_address"] = None if is_native else network_token["address"]

    keyboard = [
        [InlineKeyboardButton(
            "\u274c Cancel",
            callback_data="cancel_withdraw"
        )]
    ]

    await query.edit_message_text(
        f"{token_info['icon']} *Withdraw {token_info['symbol']}*\n\n"
        f"{network_info['icon']} *Network:* {network_info['name']}\n"
        f"\U0001F4B0 *Available:* `{balance_str} {token_info['symbol']}`\n\n"
        f"\U0001F4DD *Step 1/3:* Enter the amount to withdraw:\n\n"
        f"_Reply with the amount (e.g., 10)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return WITHDRAW_AMOUNT


async def show_balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton(
            "\U0001F4CA Check All Balances",
            callback_data="balance_all"
        )]
    ]

    for key, info in NETWORKS.items():
        keyboard.append([
            InlineKeyboardButton(
                f"{info['icon']} {info['name']}",
                callback_data=f"balance_{key}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("\U0001F3E0 Main Menu", callback_data="main_menu")
    ])

    await query.edit_message_text(
        "\U0001F4CA *Check Balances*\n\n"
        "Select a network or check all balances:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Fetching balances...")

    network = query.data.split("_")[1]
    user_id = query.from_user.id

    if network == "all":
        wallets = db.get_all_wallets(user_id)
        if not wallets:
            await query.edit_message_text(
                "\u26a0\ufe0f *No Wallets Found*\n\n"
                "Generate a wallet first to check balances.",
                parse_mode="Markdown",
                reply_markup=get_back_button("menu_balance")
            )
            return

        await query.edit_message_text(
            "\u23f3 *Fetching all balances...*",
            parse_mode="Markdown"
        )

        text = "\U0001F4CA *Your Balances*\n\n"
        for wallet in wallets:
            net = wallet["network"]
            info = NETWORKS[net]
            balance_info = await BalanceChecker.get_balance(
                net, wallet["address"]
            )
            balance_str = balance_info.get("balance", "Error")
            symbol = balance_info.get("symbol", info["symbol"])
            text += (
                f"{info['icon']} *{info['name']}*\n"
                f"   `{balance_str} {symbol}`\n"
                f"   `{format_address(wallet['address'])}`\n\n"
            )

        keyboard = [
            [InlineKeyboardButton(
                "\U0001F504 Refresh",
                callback_data="balance_all"
            )],
            [InlineKeyboardButton(
                "\U0001F3E0 Main Menu",
                callback_data="main_menu"
            )]
        ]

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        wallet = db.get_wallet(user_id, network)
        info = NETWORKS[network]

        if not wallet:
            keyboard = [
                [InlineKeyboardButton(
                    f"\u2795 Generate {info['name']} Wallet",
                    callback_data=f"gen_{network}"
                )],
                [InlineKeyboardButton(
                    "\U0001F519 Back",
                    callback_data="menu_balance"
                )]
            ]
            await query.edit_message_text(
                f"\u26a0\ufe0f *No {info['name']} Wallet*\n\n"
                f"Generate a wallet first to check balance.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        await query.edit_message_text(
            f"\u23f3 *Fetching {info['name']} balance...*",
            parse_mode="Markdown"
        )

        balance_info = await BalanceChecker.get_balance(
            network, wallet["address"]
        )
        balance_str = balance_info.get("balance", "Error")
        symbol = balance_info.get("symbol", info["symbol"])

        keyboard = [
            [
                InlineKeyboardButton(
                    "\U0001F504 Refresh",
                    callback_data=f"balance_{network}"
                ),
                InlineKeyboardButton(
                    "\U0001F4E4 Withdraw",
                    callback_data=f"withdraw_{network}"
                )
            ],
            [InlineKeyboardButton(
                "\U0001F519 Back",
                callback_data="menu_balance"
            )],
            [InlineKeyboardButton(
                "\U0001F3E0 Main Menu",
                callback_data="main_menu"
            )]
        ]

        await query.edit_message_text(
            f"{info['icon']} *{info['name']} Balance*\n\n"
            f"\U0001F4B0 *Balance:* `{balance_str} {symbol}`\n\n"
            f"\U0001F4CD *Address:*\n`{wallet['address']}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_withdraw_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    text = (
        "\U0001F4E4 *Withdraw Funds*\n\n"
        "Select a network to withdraw from:"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_network_keyboard("withdraw")
    )


async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)
    info = NETWORKS[network]

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"\u2795 Generate {info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton(
                "\U0001F519 Back",
                callback_data="menu_withdraw"
            )]
        ]
        await query.edit_message_text(
            f"\u26a0\ufe0f *No {info['name']} Wallet*\n\n"
            f"Generate a wallet first to withdraw.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    balance_info = await BalanceChecker.get_balance(network, wallet["address"])
    balance_str = balance_info.get("balance", "0")

    context.user_data["withdraw_network"] = network
    context.user_data["withdraw_balance"] = balance_str

    keyboard = [
        [InlineKeyboardButton(
            "\u274c Cancel",
            callback_data="cancel_withdraw"
        )]
    ]

    await query.edit_message_text(
        f"\U0001F4E4 *Withdraw {info['symbol']}*\n\n"
        f"{info['icon']} *Network:* {info['name']}\n"
        f"\U0001F4B0 *Available:* `{balance_str} {info['symbol']}`\n\n"
        f"\U0001F4DD *Step 1/3:* Enter the amount to withdraw:\n\n"
        f"_Reply with the amount (e.g., 0.1)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return WITHDRAW_AMOUNT


async def receive_withdraw_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    amount = update.message.text.strip()

    try:
        amount_decimal = Decimal(amount)
        if amount_decimal <= 0:
            raise ValueError("Amount must be positive")
    except Exception:
        await update.message.reply_text(
            "\u274c *Invalid Amount*\n\n"
            "Please enter a valid positive number.\n"
            "Example: `0.1` or `100`",
            parse_mode="Markdown"
        )
        return WITHDRAW_AMOUNT

    context.user_data["withdraw_amount"] = amount
    network = context.user_data.get("withdraw_network")
    token = context.user_data.get("withdraw_token")
    info = NETWORKS[network]

    if token:
        token_info = TOKENS[token]
        symbol = token_info["symbol"]
        icon = token_info["icon"]
    else:
        symbol = info["symbol"]
        icon = info["icon"]

    keyboard = [
        [InlineKeyboardButton(
            "\u274c Cancel",
            callback_data="cancel_withdraw"
        )]
    ]

    await update.message.reply_text(
        f"{icon} *Withdraw {symbol}*\n\n"
        f"\U0001F4B0 *Amount:* `{amount} {symbol}`\n\n"
        f"\U0001F4DD *Step 2/3:* Enter the destination address:\n\n"
        f"_Reply with the {info['name']} address_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return WITHDRAW_ADDRESS


async def receive_withdraw_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    address = update.message.text.strip()
    network = context.user_data.get("withdraw_network")
    amount = context.user_data.get("withdraw_amount")
    token = context.user_data.get("withdraw_token")
    token_address = context.user_data.get("withdraw_token_address")
    info = NETWORKS[network]

    if info["type"] == "evm":
        if not address.startswith("0x") or len(address) != 42:
            await update.message.reply_text(
                "\u274c *Invalid Address*\n\n"
                "Please enter a valid EVM address starting with `0x`",
                parse_mode="Markdown"
            )
            return WITHDRAW_ADDRESS
    elif info["type"] == "solana":
        if len(address) < 32 or len(address) > 44:
            await update.message.reply_text(
                "\u274c *Invalid Address*\n\n"
                "Please enter a valid Solana address",
                parse_mode="Markdown"
            )
            return WITHDRAW_ADDRESS
    elif info["type"] == "tron":
        if not address.startswith("T") or len(address) != 34:
            await update.message.reply_text(
                "\u274c *Invalid Address*\n\n"
                "Please enter a valid Tron address starting with `T`",
                parse_mode="Markdown"
            )
            return WITHDRAW_ADDRESS

    context.user_data["withdraw_address"] = address

    user_id = update.effective_user.id
    pending_withdrawals[user_id] = {
        "network": network,
        "amount": amount,
        "address": address,
        "token": token,
        "token_address": token_address
    }

    if token:
        token_info = TOKENS[token]
        symbol = token_info["symbol"]
        icon = token_info["icon"]
    else:
        symbol = info["symbol"]
        icon = info["icon"]

    keyboard = [
        [
            InlineKeyboardButton(
                "\u2705 Confirm Withdrawal",
                callback_data="exec_withdraw"
            )
        ],
        [
            InlineKeyboardButton(
                "\u274c Cancel",
                callback_data="cancel_withdraw"
            )
        ]
    ]

    await update.message.reply_text(
        f"\U0001F4E4 *Withdrawal Confirmation*\n\n"
        f"\U0001F4DD *Step 3/3:* Please review and confirm:\n\n"
        f"{'='*30}\n"
        f"{info['icon']} *Network:* {info['name']}\n"
        f"{icon} *Asset:* {symbol}\n"
        f"\U0001F4B0 *Amount:* `{amount} {symbol}`\n"
        f"\U0001F4CD *To:*\n`{address}`\n"
        f"{'='*30}\n\n"
        f"\u26a0\ufe0f *Warning:* This action cannot be undone!\n"
        f"Please verify the address carefully.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return WITHDRAW_CONFIRM


async def execute_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    withdrawal = pending_withdrawals.get(user_id)

    if not withdrawal:
        await query.edit_message_text(
            "\u274c *Withdrawal Expired*\n\n"
            "Please start a new withdrawal.",
            parse_mode="Markdown",
            reply_markup=get_back_button("menu_withdraw")
        )
        return ConversationHandler.END

    network = withdrawal["network"]
    amount = withdrawal["amount"]
    address = withdrawal["address"]
    token = withdrawal.get("token")
    token_address = withdrawal.get("token_address")
    info = NETWORKS[network]

    if token:
        token_info = TOKENS[token]
        symbol = token_info["symbol"]
        icon = token_info["icon"]
    else:
        symbol = info["symbol"]
        icon = info["icon"]

    wallet = db.get_wallet(user_id, network)
    if not wallet:
        await query.edit_message_text(
            "\u274c *Wallet Not Found*",
            parse_mode="Markdown",
            reply_markup=get_back_button("main_menu")
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "\u23f3 *Processing Withdrawal...*\n\n"
        "Please wait while we process your transaction.",
        parse_mode="Markdown"
    )

    try:
        private_key = CryptoUtils.decrypt_private_key(
            wallet["encrypted_private_key"]
        )

        if token_address and info["type"] == "evm":
            result = await WithdrawalHandler.withdraw_evm(
                network, private_key, address, amount, token_address
            )
        else:
            result = await WithdrawalHandler.withdraw(
                network, private_key, address, amount
            )

        if result.get("success"):
            db.log_transaction(
                user_id, network, "withdraw", amount,
                result.get("tx_hash"), address, None, "completed"
            )

            keyboard = [
                [InlineKeyboardButton(
                    "\U0001F310 View Transaction",
                    url=result.get("explorer_url")
                )],
                [InlineKeyboardButton(
                    "\U0001F4CA Check Balance",
                    callback_data=f"balance_{network}"
                )],
                [InlineKeyboardButton(
                    "\U0001F3E0 Main Menu",
                    callback_data="main_menu"
                )]
            ]

            await query.edit_message_text(
                f"\u2705 *Withdrawal Successful!*\n\n"
                f"{info['icon']} *Network:* {info['name']}\n"
                f"{icon} *Asset:* {symbol}\n"
                f"\U0001F4B0 *Amount:* `{amount} {symbol}`\n"
                f"\U0001F4CD *To:* `{format_address(address)}`\n\n"
                f"\U0001F4DD *TX Hash:*\n`{result.get('tx_hash')}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            db.log_transaction(
                user_id, network, "withdraw", amount,
                None, address, None, "failed"
            )

            raw_error = result.get('error', 'Unknown error')
            logger.error(f"Withdrawal failed: {raw_error}")
            friendly_msg = get_friendly_error(raw_error)
            await query.edit_message_text(
                f"\u274c *Withdrawal Failed*\n\n{friendly_msg}",
                parse_mode="Markdown",
                reply_markup=get_back_button("menu_withdraw")
            )
    except Exception as e:
        logger.error(f"Withdrawal error: {e}")
        friendly_msg = get_friendly_error(e)
        await query.edit_message_text(
            f"\u274c *Withdrawal Error*\n\n{friendly_msg}",
            parse_mode="Markdown",
            reply_markup=get_back_button("menu_withdraw")
        )

    if user_id in pending_withdrawals:
        del pending_withdrawals[user_id]
    return ConversationHandler.END


async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id in pending_withdrawals:
        del pending_withdrawals[user_id]

    context.user_data.clear()

    await query.edit_message_text(
        "\u274c *Withdrawal Cancelled*",
        parse_mode="Markdown",
        reply_markup=get_back_button("main_menu")
    )

    return ConversationHandler.END


async def show_explorer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)
    info = NETWORKS[network]

    if not wallet:
        await query.edit_message_text(
            f"\u26a0\ufe0f No wallet found for {info['name']}",
            reply_markup=get_back_button("menu_wallets")
        )
        return

    if network == "SOLANA":
        explorer_url = f"{info['explorer']}/account/{wallet['address']}"
    elif network == "TRON":
        explorer_url = f"{info['explorer']}/#/address/{wallet['address']}"
    else:
        explorer_url = f"{info['explorer']}/address/{wallet['address']}"

    keyboard = [
        [InlineKeyboardButton(
            "\U0001F310 Open Explorer",
            url=explorer_url
        )],
        [InlineKeyboardButton(
            "\U0001F519 Back",
            callback_data=f"wallet_{network}"
        )]
    ]

    await query.edit_message_text(
        f"{info['icon']} *{info['name']} Explorer*\n\n"
        f"\U0001F4CD *Address:*\n`{wallet['address']}`\n\n"
        f"Tap the button below to view on explorer:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    help_text = (
        "\U0001F4D6 *Help & Guide*\n"
        "\u2501" * 24 + "\n\n"
        "\U0001F4B0 *My Wallets*\n"
        "    View and manage your wallets\n\n"
        "\U0001F4E5 *Deposit*\n"
        "    Get deposit addresses\n\n"
        "\U0001F4CA *Balances*\n"
        "    Check wallet balances\n\n"
        "\U0001F4E4 *Withdraw*\n"
        "    Send funds externally\n\n"
        "\u2795 *Generate*\n"
        "    Create new wallets\n\n"
        "\u2501" * 24 + "\n"
        "\U0001F310 *Networks*\n"
    )

    for key, info in NETWORKS.items():
        help_text += f"    {info['icon']} {info['name']} ({info['symbol']})\n"

    help_text += (
        "\n\u2501" * 24 + "\n"
        "\U0001F512 *Security*\n"
        "    \U0001F7E2 AES-256 Encryption\n"
        "    \U0001F7E2 Secure Key Storage\n"
        "    \U0001F7E2 Live Monitoring"
    )

    await query.edit_message_text(
        help_text,
        parse_mode="Markdown",
        reply_markup=get_back_button("main_menu")
    )


async def show_tokens_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "\U0001F4B5 *Token Balances*\n\n"
        "Select a token to check your balance:\n\n"
        "*Available Tokens:*\n"
    )

    for token_key, token_info in TOKENS.items():
        networks = ", ".join(token_info["networks"].keys())
        text += f"{token_info['icon']} *{token_info['symbol']}* - {networks}\n"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_token_keyboard()
    )


async def show_token_networks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    token = query.data.split("_")[1]
    token_info = TOKENS.get(token)

    if not token_info:
        await query.edit_message_text(
            "\u26a0\ufe0f Token not found",
            reply_markup=get_back_button("menu_tokens")
        )
        return

    text = (
        f"{token_info['icon']} *{token_info['name']}*\n\n"
        f"Select a network to check your {token_info['symbol']} balance:"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_token_network_keyboard(token)
    )


async def check_token_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Fetching token balance...")

    parts = query.data.split("_")
    token = parts[1]
    network = parts[2]

    user_id = query.from_user.id
    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)

    if not token_info or not network_info:
        await query.edit_message_text(
            "\u26a0\ufe0f Invalid token or network",
            reply_markup=get_back_button("menu_tokens")
        )
        return

    wallet = db.get_wallet(user_id, network)

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"\u2795 Generate {network_info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton(
                "\U0001F519 Back",
                callback_data=f"token_{token}"
            )]
        ]
        await query.edit_message_text(
            f"\u26a0\ufe0f *No {network_info['name']} Wallet*\n\n"
            f"You need a {network_info['name']} wallet to check "
            f"{token_info['symbol']} balance.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    await query.edit_message_text(
        f"\u23f3 *Fetching {token_info['symbol']} balance on "
        f"{network_info['name']}...*",
        parse_mode="Markdown"
    )

    balance_info = await BalanceChecker.get_token_balance(
        token, network, wallet["address"]
    )
    balance_str = balance_info.get("balance", "0")

    keyboard = [
        [InlineKeyboardButton(
            "\U0001F504 Refresh",
            callback_data=f"tokenbal_{token}_{network}"
        )],
        [InlineKeyboardButton(
            "\U0001F519 Back",
            callback_data=f"token_{token}"
        )],
        [InlineKeyboardButton(
            "\U0001F3E0 Main Menu",
            callback_data="main_menu"
        )]
    ]

    await query.edit_message_text(
        f"{token_info['icon']} *{token_info['symbol']} Balance*\n\n"
        f"{network_info['icon']} *Network:* {network_info['name']}\n"
        f"\U0001F4B0 *Balance:* `{balance_str} {token_info['symbol']}`\n\n"
        f"\U0001F4CD *Wallet:*\n`{wallet['address']}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_text_commands(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if not is_authorized(user_id, chat_id):
        return

    text = update.message.text.lower().strip()

    if text in ["/menu", "/start", "menu", "home"]:
        await start(update, context)


async def check_wallet_transactions(application):
    global wallet_balances_cache

    wallets = db.get_all_wallets(ALLOWED_USER_ID)
    if not wallets:
        return

    for wallet in wallets:
        network = wallet["network"]
        address = wallet["address"]
        cache_key = f"{network}_{address}"

        try:
            balance_info = await BalanceChecker.get_balance(network, address)
            current_balance = balance_info.get("balance", "0")
            symbol = balance_info.get("symbol", network)

            if cache_key in wallet_balances_cache:
                old_balance = wallet_balances_cache[cache_key]
                old_val = Decimal(old_balance) if old_balance else Decimal("0")
                new_val = Decimal(current_balance) if current_balance else Decimal("0")

                if new_val != old_val:
                    diff = new_val - old_val
                    network_info = NETWORKS.get(network, {})
                    icon = network_info.get("icon", "")

                    if diff > 0:
                        msg = (
                            f"\U0001F4E5 *Incoming Transaction Detected!*\n\n"
                            f"{icon} *Network:* {network_info.get('name', network)}\n"
                            f"\U0001F4B0 *Amount:* `+{diff} {symbol}`\n"
                            f"\U0001F4CA *New Balance:* `{current_balance} {symbol}`\n\n"
                            f"\U0001F4CD *Wallet:*\n`{address}`"
                        )
                    else:
                        msg = (
                            f"\U0001F4E4 *Outgoing Transaction Detected!*\n\n"
                            f"{icon} *Network:* {network_info.get('name', network)}\n"
                            f"\U0001F4B8 *Amount:* `{diff} {symbol}`\n"
                            f"\U0001F4CA *New Balance:* `{current_balance} {symbol}`\n\n"
                            f"\U0001F4CD *Wallet:*\n`{address}`"
                        )

                    keyboard = [[
                        InlineKeyboardButton(
                            "\U0001F50D View Details",
                            callback_data=f"wallet_{network}"
                        ),
                        InlineKeyboardButton(
                            "\U0001F517 Explorer",
                            url=f"{network_info.get('explorer', '')}/address/{address}"
                        )
                    ]]

                    try:
                        await application.bot.send_message(
                            chat_id=ALLOWED_USER_ID,
                            text=msg,
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        if ALLOWED_CHAT_ID:
                            await application.bot.send_message(
                                chat_id=ALLOWED_CHAT_ID,
                                text=msg,
                                parse_mode="Markdown",
                                reply_markup=InlineKeyboardMarkup(keyboard)
                            )
                    except Exception as e:
                        logger.error(f"Error sending transaction notification: {e}")

            wallet_balances_cache[cache_key] = current_balance

        except Exception as e:
            logger.error(f"Error checking balance for {network}: {e}")


async def transaction_monitor_loop(application):
    import asyncio
    logger.info("Transaction monitor started - checking every 60 seconds")
    await asyncio.sleep(10)
    while True:
        try:
            await check_wallet_transactions(application)
        except Exception as e:
            logger.error(f"Transaction monitor error: {e}")
        await asyncio.sleep(60)


async def start_transaction_monitor(application):
    import asyncio
    asyncio.create_task(transaction_monitor_loop(application))
    logger.info("Transaction monitor background task created")


def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(start_transaction_monitor)
        .build()
    )

    withdraw_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                start_withdraw,
                pattern=r"^withdraw_[A-Z]+$"
            ),
            CallbackQueryHandler(
                show_token_withdraw_info,
                pattern=r"^tokenwd_[A-Z]+_[A-Z]+$"
            )
        ],
        states={
            WITHDRAW_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_withdraw_amount
                )
            ],
            WITHDRAW_ADDRESS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_withdraw_address
                )
            ],
            WITHDRAW_CONFIRM: [
                CallbackQueryHandler(execute_withdraw, pattern="^exec_withdraw$")
            ]
        },
        fallbacks=[
            CallbackQueryHandler(cancel_withdraw, pattern="^cancel_withdraw$"),
            CallbackQueryHandler(show_main_menu, pattern="^main_menu$"),
            CallbackQueryHandler(show_withdraw_menu, pattern="^menu_withdraw$")
        ],
        per_message=False
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))

    application.add_handler(withdraw_handler)

    application.add_handler(
        CallbackQueryHandler(show_main_menu, pattern="^main_menu$")
    )
    application.add_handler(
        CallbackQueryHandler(show_wallets_menu, pattern="^menu_wallets$")
    )
    application.add_handler(
        CallbackQueryHandler(show_wallet_details, pattern=r"^wallet_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(refresh_balance, pattern=r"^refresh_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(show_generate_menu, pattern="^menu_generate$")
    )
    application.add_handler(
        CallbackQueryHandler(generate_wallet, pattern=r"^gen_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(
            confirm_generate_wallet,
            pattern=r"^confirmgen_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(show_deposit_menu, pattern="^menu_deposit$")
    )
    application.add_handler(
        CallbackQueryHandler(show_deposit_address, pattern=r"^deposit_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(
            show_token_deposit_networks,
            pattern=r"^deposit_token_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            show_token_deposit_address,
            pattern=r"^tokendep_[A-Z]+_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            show_token_withdraw_networks,
            pattern=r"^withdraw_token_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^noop$")
    )
    application.add_handler(
        CallbackQueryHandler(show_balance_menu, pattern="^menu_balance$")
    )
    application.add_handler(
        CallbackQueryHandler(check_balance, pattern=r"^balance_")
    )
    application.add_handler(
        CallbackQueryHandler(show_withdraw_menu, pattern="^menu_withdraw$")
    )
    application.add_handler(
        CallbackQueryHandler(show_explorer, pattern=r"^explorer_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(show_help, pattern="^menu_help$")
    )
    application.add_handler(
        CallbackQueryHandler(show_tokens_menu, pattern="^menu_tokens$")
    )
    application.add_handler(
        CallbackQueryHandler(show_token_networks, pattern=r"^token_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(check_token_balance, pattern=r"^tokenbal_[A-Z]+_[A-Z]+$")
    )

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_commands)
    )

    logger.info("Starting Depo Bot with enhanced UI...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
