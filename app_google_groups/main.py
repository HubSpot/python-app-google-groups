#!/usr/bin/env python3
# Imports in this file should be absolute for
# module and file runtime compatibility
import logging
import sys
from argparse import ArgumentParser, FileType
from asyncio import ensure_future, get_event_loop
from typing import List

from aiohttp import web
from aiomysql import Pool, create_pool
from bonsai import LDAPClient, set_connect_async
from bonsai.asyncio import AIOConnectionPool
from slack import WebClient

from app_google_groups.config import ConfigSchema
from app_google_groups.controllers import (
    GoogleGroupsController,
    LDAPController,
    RequestController,
    ScheduleController,
    SlackActionController,
    SlackEventController,
    SlackVerifyController,
)
from app_google_groups.integrations import (
    GoogleAPIIntegration,
    GoogleGroupsDatabaseIntegration,
    RequestsDatabaseIntegration,
    ScheduleDatabaseIntegration,
)
from app_google_groups.metadata import version
from app_google_groups.migrations import ggroups_v1, requests_v3, schedule_v1
from app_google_groups.routes import setup_routes
from app_google_groups.scheduler import TaskScheduler
from app_google_groups.tasks import LDAPLoadTask


def setup_argparse() -> ArgumentParser:
    parser = ArgumentParser(
        prog="app_google_groups", description="Google Groups App version " + version,
    )

    parser.add_argument("command", type=str, choices=["run", "migrate"], help="Command to run")

    parser.add_argument(
        "--config-file",
        "-c",
        type=FileType("r"),
        help="JSON config file path",
        nargs="?",
        required=True,
    )

    return parser


async def migrate(db_conn_pool: Pool) -> None:
    print("Beginning database migrations")
    async with db_conn_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await schedule_v1.upgrade(cur)
            await requests_v3.upgrade(cur)
            await ggroups_v1.upgrade(cur)
            await conn.commit()


def main(args: List[str]) -> int:
    parser = setup_argparse()
    parsed_args = parser.parse_args(args)
    event_loop = get_event_loop()
    logging.basicConfig(level=logging.INFO)

    # Initialize singletons
    try:
        config = ConfigSchema.from_json_file(file_handle=parsed_args.config_file)
    except KeyError as err:
        print("Missing section in config:", err)
        return 1

    # Set up DB pool
    db_conn_pool: Pool = event_loop.run_until_complete(
        create_pool(
            host=config.database.host,
            port=config.database.port,
            user=config.database.user,
            password=config.database.password,
            db=config.database.dbname,
            loop=event_loop,
            charset="utf8mb4",
            use_unicode=True,
        )
    )

    # Nothing else is needed for migrations - check the command
    if parsed_args.command == "migrate":
        event_loop.run_until_complete(migrate(db_conn_pool))
        return 0

    # Async connect broke when TLS is enabled
    # See https://github.com/noirello/bonsai/issues/25
    if config.ldap.use_tls:
        set_connect_async(False)
    ldap_client = LDAPClient(url=config.ldap.url, tls=config.ldap.use_tls)
    ldap_connection_pool = AIOConnectionPool(client=ldap_client)
    slack_client = WebClient(token=config.slack.api_token, run_async=True)

    # Setup LDAP pool
    ldap_client.set_credentials(
        mechanism="SIMPLE", user=config.ldap.bind_user, password=config.ldap.bind_password,
    )
    event_loop.run_until_complete(ldap_connection_pool.open())

    # Integrations
    google_api = GoogleAPIIntegration(config=config)
    ggroups_db = GoogleGroupsDatabaseIntegration(db_conn_pool=db_conn_pool)
    requests_db = RequestsDatabaseIntegration(db_conn_pool=db_conn_pool)
    schedule_db = ScheduleDatabaseIntegration(db_conn_pool=db_conn_pool)

    # Tasks
    ldap_load_task = LDAPLoadTask(ldap_conn_pool=ldap_connection_pool, ldap_config=config.ldap)

    # Controllers
    ldap_controller = LDAPController(load_task=ldap_load_task)
    ggroups_controller = GoogleGroupsController(ggroups_db=ggroups_db, google_api=google_api)
    request_controller = RequestController(
        requests_db=requests_db, ldap=ldap_controller, config=config,
    )
    schedule_controller = ScheduleController(schedule_db=schedule_db)
    slack_action_controller = SlackActionController(
        client=slack_client,
        ldap=ldap_controller,
        ggroups=ggroups_controller,
        request=request_controller,
        schedule=schedule_controller,
        config=config,
    )
    slack_event_controller = SlackEventController(
        client=slack_client, ggroups=ggroups_controller, request=request_controller
    )
    verify_controller = SlackVerifyController(slack_config=config.slack, path="/slack")

    # Add singletons to the app context
    # This allows views to read them in on initalisation
    app = web.Application(middlewares=[verify_controller.verify_request])
    app["ConfigSchema"] = config
    app["GoogleGroupsController"] = ggroups_controller
    app["LDAPController"] = ldap_controller
    app["RequestController"] = request_controller
    app["SlackActionController"] = slack_action_controller
    app["SlackEventController"] = slack_event_controller
    app["ScheduleController"] = schedule_controller

    # Setup scheduled tasks
    scheduler = TaskScheduler()

    scheduler.add_task("Refresh LDAP Data", 2, ldap_controller.sync)
    scheduler.add_task("Refresh Google Groups Data", 2, ggroups_controller.sync)
    scheduler.add_task("Load schedule from DB", 1, schedule_controller.sync)

    # Do one initial run of all scheduled tasks
    print("Performing initial run of tasks")
    event_loop.run_until_complete(scheduler.run_all())

    # Setup routes
    setup_routes(app)

    # Go go go!
    ensure_future(scheduler.run_scheduler(), loop=event_loop)
    web.run_app(app, path=config.sockfile, host=config.host, port=config.port)

    return 0


def main_with_args() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main_with_args())
