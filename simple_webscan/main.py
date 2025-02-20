import asyncio
import logging
import uvicorn

from argparse import ArgumentParser

from .app import app

DEFAULT_ADDRESS = '0.0.0.0'
DEFAULT_PORT = 8000

parser = ArgumentParser("simple-webscan", description="A web service to use SANE scanners")
parser.add_argument("-d", "--host", default=DEFAULT_ADDRESS, type=str, help=f"bind server to address (default: '{DEFAULT_ADDRESS}')")
parser.add_argument("-l", "--loglevel", default="INFO", type=str, help="Loglevel (choose one of DEBUG, INFO, WARN, ERROR, FATAL, CRITICAL)")
parser.add_argument("-p", "--port", default=DEFAULT_PORT, type=int, help=f"bind server to port (default: {DEFAULT_PORT})")
args = parser.parse_args()

async def main():
    app_config = uvicorn.Config(app, host=args.host, port=args.port, log_level=getattr(logging, args.loglevel))
    server = uvicorn.Server(app_config)
    await server.serve()


def run_main():
    asyncio.run(main())


if __name__ == "__main__":
    run_main()