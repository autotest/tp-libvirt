import logging
from autotest.client.shared import error
from virttest import utils_misc
from virttest.utils_libvirtd import LibvirtdSession


def run(test, params, env):
    """
    Start libvirt daemon with break point inserted.
    And then kill daemon ensure no sigsegv happends.
    """
    signal_name = params.get("signal", "SIGTERM")
    send_signal_at = params.get("send_signal_at", None)

    def _signal_callback(gdb, info, params):
        """
        Callback function when a signal is recieved by libvirtd.
        """
        params['recieved'] = True
        logging.debug("Signal recieved:")
        logging.debug(info)

    def _break_callback(gdb, info, params):
        """
        Callback function when a breakpoint is reached.
        """
        for line in gdb.back_trace():
            logging.debug(line)
        gdb.send_signal(signal_name)
        gdb.cont()

    bundle = {'recieved': False}

    libvirtd = LibvirtdSession(gdb=True)
    try:
        libvirtd.set_callback('break', _break_callback)
        libvirtd.set_callback('signal', _signal_callback, bundle)

        libvirtd.insert_break(send_signal_at)

        libvirtd.start(wait_for_working=False)

        if not utils_misc.wait_for(lambda: bundle['recieved'], 20, 0.5):
            raise error.TestFail("Expect recieve signal, but not.")
    finally:
        libvirtd.exit()
