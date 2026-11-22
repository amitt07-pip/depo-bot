#!/usr/bin/env python3
"""Find the private key of an old/replaced bot wallet so its funds can be moved.

Searches the bot database, its retired_wallets archive, backups (wallets.db.bak*, *.db),
SQLite journal/WAL files and any extra files you pass, for encrypted keys. Every key it can
decrypt with ENCRYPTION_KEY is turned back into addresses and matched against --address.

  python3 scripts/recover_wallet.py --address <OLD_ADDRESS> --service <bot-service>
  python3 scripts/recover_wallet.py --address <OLD_ADDRESS> --service <bot-service> --show-key

The key is only printed with --show-key. Import it into a wallet app (MetaMask/Trust for EVM,
Phantom for Solana, TronLink for Tron, Tonkeeper for TON) and send the funds to your new address.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOB = re.compile(rb'\{"iv":\s*"([A-Za-z0-9+/=]{16,})",\s*"ciphertext":\s*"([A-Za-z0-9+/=]{16,})"\}')


def service_env(name: str) -> dict:
    """Environment of a systemd service (Environment= and EnvironmentFile=)."""
    env = {}
    try:
        out = subprocess.run(["systemctl", "show", name, "-p", "Environment", "-p", "EnvironmentFiles"],
                             capture_output=True, text=True, check=True).stdout
    except Exception as e:
        print(f"Could not read service {name}: {e}")
        return env
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key == "Environment":
            for item in re.findall(r'(?:[^\s"]+|"[^"]*")+', value):
                k, _, v = item.strip('"').partition("=")
                env[k] = v
        elif key == "EnvironmentFiles" and value:
            env.update(read_env_file(value.split()[0].lstrip("-")))
    return env


def read_env_file(path: str) -> dict:
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip().removeprefix("export ")] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def candidate_files(extra) -> list:
    pats = ["wallets.db", "wallets.db-journal", "wallets.db-wal", "wallets.db.bak*", "*.db", "*.db.*", "*.bak"]
    files = []
    for p in pats:
        files += glob.glob(os.path.join(ROOT, p))
    files += list(extra)
    seen, out = set(), []
    for f in files:
        f = os.path.abspath(f)
        if os.path.isfile(f) and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def blobs_in(path: str) -> set:
    found = set()
    with open(path, "rb") as f:
        data = f.read()
    for iv, ct in BLOB.findall(data):
        found.add(json.dumps({"iv": iv.decode(), "ciphertext": ct.decode()}))
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for table in ("wallets", "retired_wallets"):
            try:
                for (blob,) in conn.execute(f"SELECT encrypted_private_key FROM {table}"):
                    found.add(blob)
            except sqlite3.Error:
                pass
        conn.close()
    except sqlite3.Error:
        pass
    return found


def addresses_for(key: str) -> dict:
    """Every address this key controls, by network type."""
    import depobot as d
    out = {}
    k = key.strip()
    hexkey = k[2:] if k.lower().startswith("0x") else k
    if k.startswith(getattr(d, "TON_SEED_PREFIX", "ed25519:")):
        try:
            w = d.ton_wallet_from_mnemonic(k)
            out["TON"] = w.address.to_string(True, True, False)
            out["TON (raw)"] = w.address.to_string(False)
        except Exception:
            pass
    elif re.fullmatch(r"[0-9a-fA-F]{64}", hexkey):
        try:
            out["EVM"] = d.Account.from_key("0x" + hexkey).address
            out["TRON"] = d.TronPrivateKey(bytes.fromhex(hexkey)).public_key.to_base58check_address()
            out["BTC"] = d.WalletGenerator.btc_address_from_private_key(hexkey)
            out["LTC"] = d.WalletGenerator.ltc_address_from_private_key(hexkey)
        except Exception:
            pass
    elif len(k.split()) in (12, 24):
        try:
            w = d.ton_wallet_from_mnemonic(k)
            out["TON"] = w.address.to_string(True, True, False)
            out["TON (raw)"] = w.address.to_string(False)
        except Exception:
            pass
    else:
        try:
            import base64
            out["SOLANA"] = str(d.Keypair.from_bytes(base64.b64decode(k)).pubkey())
        except Exception:
            pass
    return out


def same(a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    return a.lower() == b.lower() if a.startswith("0x") or b.startswith("0x") else a == b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", help="old wallet address that received the funds")
    ap.add_argument("--service", help="systemd service of the bot, to read its ENCRYPTION_KEY")
    ap.add_argument("--show-key", action="store_true", help="print the private key of the match")
    ap.add_argument("files", nargs="*", help="extra files to search (backups, copies of the db)")
    args = ap.parse_args()

    env = read_env_file(os.path.join(ROOT, ".env"))
    if args.service:
        env.update(service_env(args.service))
    for k, v in env.items():
        os.environ.setdefault(k, v)
    if not os.getenv("ENCRYPTION_KEY"):
        print("WARNING: ENCRYPTION_KEY not found; trying the bot's default key. "
              "Pass --service <bot-service> or export ENCRYPTION_KEY.")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:recovery")
    sys.path.insert(0, ROOT)
    import logging
    logging.disable(logging.CRITICAL)
    import depobot as d

    files = candidate_files(args.files)
    print("Searching:", *[f"  {f}" for f in files], sep="\n")
    blobs = set()
    for f in files:
        blobs |= blobs_in(f)
    keys = set()
    for b in blobs:
        try:
            keys.add(d.CryptoUtils.decrypt_private_key(b))
        except Exception:
            pass
    print(f"\nEncrypted keys found: {len(blobs)}, decrypted: {len(keys)}")
    if blobs and not keys:
        print("None could be decrypted: ENCRYPTION_KEY differs from the one used by the bot.")

    current = set()
    try:
        conn = sqlite3.connect(os.path.join(ROOT, "wallets.db"))
        current = {a.lower() for (a,) in conn.execute("SELECT address FROM wallets")}
        conn.close()
    except sqlite3.Error:
        pass

    match = None
    for key in keys:
        addrs = addresses_for(key)
        if args.address and any(same(a, args.address) for a in addrs.values()):
            match = (key, addrs)
            break
    if args.address:
        if not match:
            print(f"\nNOT FOUND: no recoverable key for {args.address} in these files.")
            print("Try again with any other copy of wallets.db (VPS snapshot, old backup) as an extra argument.")
            return 1
        key, addrs = match
        print(f"\nFOUND the key for {args.address}")
        for net, a in addrs.items():
            print(f"  {net:10} {a}")
        if args.show_key:
            print(f"\nPRIVATE KEY (keep secret, never share):\n{key}")
        else:
            print("\nRe-run with --show-key to print the private key.")
        return 0

    orphans = [addresses_for(k) for k in keys]
    orphans = [a for a in orphans if a and not any(x.lower() in current for x in a.values())]
    print(f"\nKeys whose address is no longer in the bot's wallets: {len(orphans)}")
    for a in orphans:
        print("  " + "  ".join(f"{n}: {x}" for n, x in a.items() if "raw" not in n))
    print("\nRe-run with --address <one of these> to recover it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
