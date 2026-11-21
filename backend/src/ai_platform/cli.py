import argparse
import json
from pathlib import Path
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import conninfo_to_dict

from ai_platform.api.main import app
from ai_platform.db import connection
from ai_platform.services import metering
from ai_platform.services.identity import bootstrap_admin, reset_admin_mfa
from ai_platform.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-openapi")
    export.add_argument("--output", type=Path, required=True)
    commands.add_parser("bootstrap-admin")
    reset = commands.add_parser("reset-admin-mfa")
    reset.add_argument("--username", required=True)
    commands.add_parser("init-checkpoints")
    commands.add_parser("init-archive")
    commands.add_parser("replay-deletions")
    grant = commands.add_parser("grant-runtime")
    grant.add_argument("--role", required=True)
    reconciliation = commands.add_parser("reconcile-attempt")
    reconciliation.add_argument("--attempt-id", type=UUID, required=True)
    reconciliation.add_argument("--input-tokens", type=int, required=True)
    reconciliation.add_argument("--output-tokens", type=int, required=True)
    reconciliation.add_argument("--reason", choices=["provider_usage_confirmed", "provider_confirmed_no_consumption"],
                                required=True)
    args = parser.parse_args()
    if args.command == "init-checkpoints":
        config = settings()
        if not config.database_url or not config.checkpoint_database_url:
            parser.exit(1, "Both PostgreSQL connection URLs are required\n")
        fields = ("host", "hostaddr", "port", "dbname")
        business = conninfo_to_dict(config.database_url)
        graph = conninfo_to_dict(config.checkpoint_database_url)
        if not business.get("dbname") or any(business.get(key) != graph.get(key) for key in fields):
            parser.exit(1, "Checkpoint and business connections must target the same database\n")
        with PostgresSaver.from_conn_string(config.checkpoint_database_url) as saver:
            from ai_platform.services.chat_graph import setup_checkpoints
            setup_checkpoints(saver)
        print("Checkpoint schema initialized")
        return
    if args.command == "grant-runtime":
        from psycopg import sql

        statements = [
            "GRANT USAGE ON SCHEMA app, graph TO {role}",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app, graph TO {role}",
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app, graph TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA app, graph GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA app, graph GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        ]
        with connection() as conn:
            for statement in statements:
                conn.execute(sql.SQL(statement).format(role=sql.Identifier(args.role)))
        print(f"Runtime privileges granted to {args.role}")
        return
    if args.command in ("init-archive", "replay-deletions"):
        import sqlite3

        from ai_platform.services.deletion import init_archive, replay_archive
        try:
            if args.command == "init-archive":
                init_archive()
                print("Deletion archive initialized")
                return
            records, applied, purged = replay_archive()
        except (OSError, sqlite3.Error):
            parser.exit(2, "Independent deletion archive unavailable; keep the restored instance isolated\n")
        print(f"Deletion archive replayed: records={records} applied={applied} purged={purged}")
        return
    if args.command == "reconcile-attempt":
        usage = args.input_tokens, args.output_tokens
        if not all(0 <= amount <= 10**12 for amount in usage) or (
            args.reason == "provider_confirmed_no_consumption" and any(usage)
        ):
            parser.error("Usage does not match the reconciliation evidence")
        with connection() as conn:
            changed = metering.reconcile(conn, args.attempt_id, usage, args.reason)
        if not changed:
            parser.exit(1, "Attempt has no unresolved reservation\n")
        print("Attempt reconciled")
        return
    if args.command == "bootstrap-admin":
        with connection() as conn:
            created = bootstrap_admin(conn)
        if not created:
            parser.exit(1, "Administrator already initialized or name reserved\n")
        print("Administrator initialized")
        return
    if args.command == "reset-admin-mfa":
        with connection() as conn:
            reset = reset_admin_mfa(conn, args.username.lower())
        if not reset:
            parser.exit(1, "Administrator unavailable\n")
        print("Administrator MFA reset")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(payload, encoding="utf-8", newline="\n")
