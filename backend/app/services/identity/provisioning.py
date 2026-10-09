"""Local CLI for issuing provisioning secrets and revoking installations."""

import argparse
import asyncio

from app.services.identity.auth import SQLiteCredentialStore
from app.core.config import settings


async def _run() -> int:
    parser = argparse.ArgumentParser(description="Manage local Aurora installation credentials.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("issue", help="Issue a one-time installation provisioning secret.")
    revoke = commands.add_parser("revoke", help="Revoke bearer credentials for a server-owned device ID.")
    revoke.add_argument("device_id")
    args = parser.parse_args()

    store = SQLiteCredentialStore(settings.DATABASE_PATH)
    if args.command == "issue":
        secret = await store.issue_provisioning()
        print("Provisioning secret (valid for 10 minutes; shown once):")
        print(secret)
        return 0

    count = await store.revoke_device(args.device_id)
    print(f"Revoked credentials: {count}")
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
