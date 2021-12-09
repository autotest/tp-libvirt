import logging
import threading
import re
try:
    import queue as Queue
except ImportError:
    import Queue

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_libvirtd

msg_queue = Queue.Queue()


def start_journal():
    """
    Track system journal
    """

    ret = process.run("journalctl -f", shell=True, verbose=True, ignore_status=True)
    msg_queue.put(ret.stdout_text)


def test_check_journal(libvirtd, test):
    """
    Test restart libvirtd with running guest.
    1) Start a guest;
    2) Start journal;
    3) Restart libvirtd;
    4) Check the output of `journalctl -f`;

    :param libvirtd: libvirtd object
    :param test: test object
    """

    # Start journal
    monitor_journal = threading.Thread(target=start_journal, args=())
    monitor_journal.start()

    # Restart libvirtd
    libvirtd.restart()

    monitor_journal.join(2)

    # Stop journalctl command
    utils_misc.kill_process_by_pattern("journalctl")
    output = msg_queue.get()
    if re.search("error", output):
        test.fail("Found error message during libvirtd restarting: %s" % output)
    else:
        logging.info("Not found error message during libvirtd restarting.")


def run(test, params, env):
    """
    Test libvirtd.
    1) Test restart libvirtd with running guest.
    """

    check_journal = "yes" == params.get("check_journal", "no")

    libvirtd = utils_libvirtd.Libvirtd()

    if check_journal:
        test_check_journal(libvirtd, test)
