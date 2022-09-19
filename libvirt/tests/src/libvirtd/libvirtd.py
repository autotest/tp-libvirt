import logging as log
import threading
import re
try:
    import queue as Queue
except ImportError:
    import Queue

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_libvirtd

from virttest.utils_test import libvirt

msg_queue = Queue.Queue()


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def start_journal():
    """
    Track system journal
    """

    ret = process.run("journalctl -f", shell=True, verbose=True, ignore_status=True)
    msg_queue.put(ret.stdout_text)


def test_check_journal(libvirtd, params, test):
    """
    Test restart libvirtd with running guest.
    1) Start a guest;
    2) Start journal;
    3) Restart libvirtd;
    4) Check the output of `journalctl -f`;
    5) Check libvirtd log

    :param libvirtd: libvirtd object
    :param test: test object
    """
    libvirtd_debug_file = params.get("libvirtd_debug_file")
    error_msg_in_journal = params.get("error_msg_in_journal")
    error_msg_in_log = params.get("error_msg_in_log")

    utils_libvirtd.Libvirtd("libvirtd-tls.socket").stop()
    utils_libvirtd.Libvirtd("libvirtd-tcp.socket").stop()

    # Start journal
    monitor_journal = threading.Thread(target=start_journal, args=())
    monitor_journal.start()

    # Restart libvirtd
    libvirtd.restart()

    monitor_journal.join(2)

    # Stop journalctl command
    utils_misc.kill_process_by_pattern("journalctl")
    output = msg_queue.get()
    # Check error message in journal
    if re.search(error_msg_in_journal, output):
        test.fail("Found error message during libvirtd restarting: %s" % output)
    else:
        logging.info("Not found error message during libvirtd restarting.")

    # Check error messages in libvirtd log
    libvirt.check_logfile(error_msg_in_log, libvirtd_debug_file, False)


def run(test, params, env):
    """
    Test libvirtd.
    1) Test restart libvirtd with running guest.
    """

    case = params.get('case', '')
    run_test = eval("test_%s" % case)

    libvirtd = utils_libvirtd.Libvirtd()

    run_test(libvirtd, params, test)
