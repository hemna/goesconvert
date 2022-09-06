import itertools

import click
from oslo_config import cfg
from rich.console import Console

from goesconvert.cli import cli
from goesconvert import (
    cli_helper, threads, utils
)
from goesconvert.cmds import (
    monitor
)

CONF = cfg.CONF


@cli.command()
@cli_helper.add_options(cli_helper.common_options)
@click.pass_context
@cli_helper.process_standard_options
def sample_config(ctx):
    chain = [
        ('monitor',
         itertools.chain(monitor.monitor_opts)),
    ]
    console = Console()
    console.print(chain)
