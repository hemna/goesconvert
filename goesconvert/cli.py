"""Console script for goesconvert."""
import os
import sys
import click
import click_completion

from oslo_config import cfg

import goesconvert
from goesconvert import cli_helper, utils


CONF = cfg.CONF
CONF([], project='goesconvert', version=goesconvert.__version__)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def custom_startswith(string, incomplete):
    """A custom completion match that supports case insensitive matching."""
    if os.environ.get("_CLICK_COMPLETION_COMMAND_CASE_INSENSITIVE_COMPLETE"):
        string = string.lower()
        incomplete = incomplete.lower()
    return string.startswith(incomplete)


click_completion.core.startswith = custom_startswith
click_completion.init()


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option()
@click.pass_context
def cli(ctx):
    pass


@cli.command()
@cli_helper.add_options(cli_helper.common_options)
@click.pass_context
@cli_helper.process_standard_options_no_config
def check_version(ctx):
    """Check this version against the latest in pypi.org."""
    level, msg = utils._check_version()
    if level:
        click.secho(msg, fg="yellow")
    else:
        click.secho(msg, fg="green")


@cli.command()
@click.pass_context
def version(ctx):
    """Show the APRSD version."""
    click.echo(click.style("GoesConvert Version : ", fg="white"), nl=False)
    click.secho(f"{goesconvert.__version__}", fg="yellow", bold=True)


def main():
    # First import all the possible commands for the CLI
    # The commands themselves live in the cmds directory
    from .cmds import (  # noqa
        monitor, sample_config
    )
    cli()


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
