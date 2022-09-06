from datetime import datetime
import concurrent.futures
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

import click
from oslo_config import cfg
from oslo_context import context
from rich.console import Console
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

from goesconvert import (
    cli_helper, threads, utils
)
from goesconvert.utils.timezone import (
    GMT, EST, PST
)
from goesconvert.utils import trace

from goesconvert.cli import cli

monitor_group = cfg.OptGroup(name='monitor',
                             title='Monitor options')

monitor_opts = [
    cfg.StrOpt('font_path',
               default="./Verdana_Bold.ttf",
               help="Full file path to font you want for overlay in images"),
    cfg.StrOpt('satellite',
               choices=['goeseast', 'goeswest'],
               help="Which supported satellite to process."),
    cfg.StrOpt('watch_dir',
               help="The directory to look for new files from goestools."),
    cfg.StrOpt('process_dir',
               help="The directory to write processed files to."),
    cfg.StrOpt('crop_usa',
               default="2424x1424+720+280",
               help="Crop area for USA"),
    cfg.StrOpt('crop_ca',
               default="1024x768+600+600",
               help="Crop area for California"),
    cfg.StrOpt('crop_va',
               default="1024x768+2100+600",
               help="Crop area for Virginia")
]


CONF = cfg.CONF
CONF.register_group(monitor_group)
CONF.register_opts(monitor_opts, group=monitor_group)
LOG = logging.getLogger("goesconvert")


SCRIPT_DIR = "/home/goes/bin"
FONT = f"{SCRIPT_DIR}/Verdana_Bold.ttf"


def signal_handler(sig, frame):
    click.echo("signal_handler: called")
    num_threads = len(threads.WaltThreadList())
    LOG.info(f"CTRL+C Stopping {num_threads}")
    threads.WaltThreadList().stop_all()
    click.echo("signal_handler: Done")


class ProcessSatelliteFile(threads.WaltThread):

    def __init__(self, new_file, satellite):
        self.fh = FileHandler(new_file=new_file, satellite=satellite)
        thread_name = f"{self.fh.model}/{self.fh.chan}"
        self.new_file = new_file
        self.satellite = satellite
        LOG.debug(f"Thread name {thread_name}")
        super().__init__(thread_name)

    def loop(self):
        pass

    def run(self):
        if self.fh.model == 'fd':
            # We want to crop for both CA and VA
            if not self.thread_stop:
                self.fh.crop(region='va')
            if not self.thread_stop:
                self.fh.crop(region='ca')
            if not self.thread_stop:
                self.fh.crop(region='usa')
            if not self.thread_stop:
                self.fh.copy(subdest="animate", overlay=False, resize=True)

            if not self.thread_stop:
                self.fh.animate(region='va')
            if not self.thread_stop:
                self.fh.animate(region='ca')
            if not self.thread_stop:
                self.fh.animate(region='usa')
            if not self.thread_stop:
                self.fh.animate_fd()
        else:
            # This is an m1 or m2 file
            # We copy and animate
            if not self.thread_stop:
                self.fh.copy()
            if not self.thread_stop:
                self.fh.animate()

        threads.WaltThreadList().remove(self)
        LOG.debug("Exiting")
        return False


class FileHandler(object):
    source = None
    gmt_time = None

    def __init__(self, new_file, satellite):
        satellite_name = satellite.get('satellite')
        context.RequestContext(request_id=uuid.uuid4())
        # LOG.info(f"FH for : {new_file} from {satellite_name}")
        self.source = new_file
        self.satellite = satellite
        self.satellite_dir = satellite.get('watch_dir')
        self.process_dir = satellite.get('process_dir')
        self._commands = {
            'convert': shutil.which('convert')
        }
        self._collect_info()

    def _collect_info(self):
        context.RequestContext(request_id=uuid.uuid4())
        #LOG.info(f"Process {self.source}")
        basename = os.path.basename(self.source)
        self.dirname = os.path.dirname(self.source)
        base_path = self.dirname.replace(self.satellite_dir, "")
        components = base_path.split('/')
        self.model = components[1]
        self.chan = components[3]

        time_str = basename.replace(".png","")
        dto = datetime.strptime(time_str, '%Y-%m-%dT-%H-%M-%SZ')
        self.file_time = dto.replace(tzinfo=GMT)
        self.va_date = self.file_time.astimezone(EST)
        self.ca_date = self.file_time.astimezone(PST)
        self.gmt_date = self.file_time.astimezone(GMT)

    def _destination(self, region=None):
        date_str = "%Y-%m-%d"
        if region is not None:
            if region == 'va':
                date = self.va_date.strftime(date_str)
            elif region == 'ca':
                date = self.ca_date.strftime(date_str)
            else:
                date = self.va_date.strftime(date_str)

            destination = ("%s/%s/%s/%s/%s" % (self.process_dir,
                                               self.model,
                                               date,
                                               self.chan, region))
        else:
            date = self.file_time.strftime(date_str)
            destination = ("%s/%s/%s/%s" % (self.process_dir,
                                            self.model,
                                            date,
                                            self.chan))

        return destination

    def _ensure_src(self):
        LOG.debug(f"make sure '{self.source}' exists")
        # make sure the source file exists and is written to the fs
        while not os.path.exists(self.source):
            LOG.debug(f"'{self.source}' isn't ready yet")
            time.sleep(1)

    def _ensure_dir(self, destination):
        LOG.debug(f"make sure '{destination}' exists")
        os.makedirs(destination, exist_ok=True)

    def file_exists(self, destination):
        if os.path.exists(destination):
            return True
        else:
            return False

    @utils.timeit
    def _execute(self, cmd):
        command = ' '.join(cmd)
        # LOG.debug(f"EXEC '{command}'")
        try:
            out = subprocess.run(command, shell=True, check=False,
                                 encoding="utf-8",
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            if len(out.stdout):
                LOG.debug(f"OUT = '{out.stdout.encode('utf-8')}'")
            if len(out.stderr):
                LOG.warning(f"ERR = '{out.stderr.encode('utf-8')}'")

        except Exception as ex:
            LOG.exception(f"FAIL {ex}")

    def crop(self, region):
        """ Crop a Full Disc image to cover a specific region. """
        LOG.info(f"Crop fd image for '{region}'")
        dest = self._destination(region)
        self._ensure_src()
        self._ensure_dir(dest)
        newfile_fmt = "%H-%M-%S"
        if region == "va":
            resolution = self.satellite.get('crop_va')
            newfile_name = "%s.png" % self.va_date.strftime(newfile_fmt)
        elif region == "ca":
            resolution = self.satellite.get('crop_ca')
            newfile_name = "%s.png" % self.ca_date.strftime(newfile_fmt)
        elif region == 'usa':
            resolution = self.satellite.get('crop_usa')
            newfile_name = "%s.png" % self.va_date.strftime(newfile_fmt)

        newfile = f"{dest}/{newfile_name}"
        if not self.file_exists(newfile):
            crop_cmd = [
                self._commands['convert'],
                "%s" % self.source,
                "-crop", '"%s"' % resolution,
                "+repage", "%s" % newfile
            ]
            self._execute(crop_cmd)
            self.overlay(newfile, region)

    def copy(self, subdest=None, overlay=True, resize=False):
        """Copy a full disc image to destination. """
        if subdest:
            dest = "%s/%s" % (self._destination(region=None), subdest)
        else:
            dest = self._destination(region=None)

        newfile_fmt = "%H-%M-%S"
        newfile_name = self.file_time.strftime(newfile_fmt)
        dest_file = "%s/%s.png" % (dest, newfile_name)
        LOG.debug("copy image to destination '%s'", dest_file)

        self._ensure_src()
        self._ensure_dir(dest)
        if not self.file_exists(dest_file):
            shutil.copyfile(self.source, dest_file)
            if resize:
                self.resize(dest_file)

            if overlay:
                self.overlay(dest_file)

    def resize(self, dest_file):
        # rescale the file down to something manageable in size
        # the raw fd images are 5240x5240
        cmd = [self._commands['convert'],
                dest_file,
                "-resize", "25%",
                dest_file]
        self._execute(cmd)

    def animate(self, region=None):
        dest = self._destination(region=region)
        LOG.info(f"animate directory '{dest}'")
        dest_file = "%s/animate.gif" % dest
        self._animated_gif("%s/*.png" % dest,
                           "%s" % dest_file)

    def _animated_gif(self, source, destination):
        cmd = [
            self._commands['convert'],
            "-loop",
            "0",
            "-delay",
            "15",
            source,
            destination]
        self._execute(cmd)

    def animate_fd(self):
        dest = "%s/animate" % self._destination(region=None)
        file_webm = "%s/earth.webm" % dest
        file_gif = "%s/earth.gif" % dest

        self._animated_gif("%s/*.png" % dest,
                           file_gif)
        #cmd = ["ffmpeg", "-y",
        #       "-framerate", "10",
        #       "-pattern_type", "glob",
        #       "-i", "'%s/*.png'" % dest,
        #       "-c:v", "libvpx-vp9",
        #       #"-b:v", "3M",
        #       "-b:v", "0",
        #       "-crf", "15",
        #       "-c:a", "libvorbis",
        #       file_webm]
        #self._execute(cmd)

        #cmd = ["ffmpeg", "-y",
        #       "-i", file_webm,
        #       "-loop", "0",
        #       file_gif]
        #self._execute(cmd)

    def overlay(self, image_file, region=None):
        human_date_fmt = "%A %b %e, %Y  %T  %Z"
        if region:
            font_size = "24"
            if region == "va":
                human_date = self.va_date.strftime(human_date_fmt)
            elif region == "ca":
                human_date = self.ca_date.strftime(human_date_fmt)
            elif region == "usa":
                human_date = self.va_date.strftime(human_date_fmt)
        else:
            font_size = "12"
            human_date = self.file_time.strftime(human_date_fmt)

        cmd = [self._commands['convert'],
               image_file,
               "-quality", "90",
               "-font", CONF['monitor'].get('font_path'),
               "-fill", '"#0004"', "-draw", "'rectangle 0,2000,2560,1820'",
               "-pointsize", font_size, "-gravity", "southwest",
               "-fill", "white", "-gravity", "southwest", "-annotate", "+2+10", '"%s"' % human_date,
               "-fill", "white", "-gravity", "southeast", "-annotate", "+2+10", '"wx.hemna.com"',
               image_file]
        self._execute(cmd)

    def process(self, animate=True):
        self._collect_info()
        if self.model == 'fd':
            # We want to crop for both CA and VA
            self.crop(region='va')
            self.crop(region='ca')
            self.crop(region='usa')
            self.copy(subdest="animate", overlay=False, resize=True)

            if animate:
                self.animate(region='va')
                self.animate(region='ca')
                self.animate(region='usa')
                self.animate_fd()
        else:
            # This is an m1 or m2 file
            # We copy and animate
            self.copy()
            if animate:
              self.animate()


class SatelliteHandler(object):
    satellite_dir = ''

    def __init__(self, satellite):
        self.satellite = satellite

    def handle_event(self, event):
        if event.is_directory:
            return None

        elif event.event_type == 'created':
            # Take any action here when a file is first created.
            LOG.debug(f"Got create event for '{event.src_path}'")
            try:
                LOG.debug("Start thread to process it.")
                thread = ProcessSatelliteFile(new_file=event.src_path,
                                              satellite=self.satellite)
                thread.start()
            except Exception as ex:
                LOG.exception("Failed to create FileHandler ", ex)


class GoesEastHandler(FileSystemEventHandler):

    @staticmethod
    def on_any_event(event):
        ret = None
        try:
            h = SatelliteHandler(CONF["monitor"])
            ret = h.handle_event(event)
        except Exception as ex:
            print(ex)

        return ret


class Watcher(threads.WaltThread):

    def __init__(self, satellite_name):
        super().__init__("Watcher")
        self.satellite_name = satellite_name
        if CONF['monitor'].get('watch_dir', None):
            self.satellite_dir = CONF['monitor']['watch_dir']
        else:
            self.satellite_dir = None
        LOG.info(f"Setting up directory observer for '{self.satellite_dir}'")
        self.observer = PollingObserver()

    def loop(self):
        LOG.info("Loop start")
        if not self.satellite_dir:
            LOG.error("Can't run as not properly configured")
            return

        event_handler = GoesEastHandler()

        self.observer.schedule(
            event_handler, self.satellite_dir, recursive=True
        )
        self.observer.start()
        try:
            while not self.thread_stop:
                time.sleep(1)
        except:
            self.observer.stop()
            LOG.error("Error")

        self.observer.stop()
        self.observer.join()
        LOG.info("Watcher: BYE")
        return False


# main() ###
@cli.command()
@cli_helper.add_options(cli_helper.common_options)
@click.option(
    "-f",
    "--flush",
    "flush",
    is_flag=True,
    show_default=True,
    default=False,
    help="Flush out all old aged messages on disk.",
)
@click.pass_context
@cli_helper.process_standard_options
def monitor(ctx, flush):
    console = ctx.obj['console']
    CONF.log_opt_values(LOG, logging.DEBUG)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not CONF['monitor'].get('satellite'):
        LOG.error("You must specify a satellite to watch")
        sys.exit(1)

    # launch the healthcheck flask first
    # threading.Thread(target=app.run).start()

    east = Watcher(satellite_name='goes-east')
    east.start()

    pass
