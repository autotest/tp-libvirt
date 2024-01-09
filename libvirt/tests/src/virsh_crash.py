from time import sleep
import logging as log

from virttest import utils_misc


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    utils_misc.cmd_status_output("virsh event --all --loop > /dev/null 2>&1 &",
                                 shell=True)
    sleep(5)
    s, o = utils_misc.cmd_status_output("ulimit -S -c", shell=True)
    logging.debug(o)
    _, pid = utils_misc.cmd_status_output("pidof virsh", shell=True)
    utils_misc.cmd_status_output("kill -11 %s" % pid, shell=True)

