import os
import logging as log

from virttest import utils_misc
from virttest.utils_libvirtd import LibvirtdSession
from virttest.utils_libvirtd import Libvirtd


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Start libvirt daemon with break point inserted.
    And then kill daemon ensure no sigsegv happends.
    """
    signal_name = params.get("signal", "SIGTERM")
    send_signal_at = params.get("send_signal_at", None)

    def _signal_callback(gdb, info, params):
        """
        Callback function when a signal is received by libvirtd.
        """
        params['recieved'] = True
        logging.debug("Signal received:")
        logging.debug(info)

    def _break_callback(gdb, info, params):
        """
        Callback function when a breakpoint is reached.
        """
        for line in gdb.back_trace():
            logging.debug(line)
        gdb.send_signal(signal_name)
        gdb.cont()

    def get_service(send_signal_at):
        """
        Get the name of the service

        :param send_signal_at: The function to set breakpoint
        :return: Service name
        """
        return {
            'netcfStateInitialize': 'virtinterfaced',
            'networkStateInitialize': 'virtnetworkd',
            'nwfilterStateInitialize': 'virtnwfilterd'
        }.get(send_signal_at)

    serv_name = get_service(send_signal_at)
    bundle = {'recieved': False}

    libvirtd = LibvirtdSession(service_name=serv_name, gdb=True)
    try:
        libvirtd.set_callback('break', _break_callback)
        libvirtd.set_callback('signal', _signal_callback, bundle)

        libvirtd.insert_break(send_signal_at)

        libvirtd.start(wait_for_working=False)

        if not utils_misc.wait_for(lambda: bundle['recieved'], 60, 0.5):
            test.fail("Expect receive signal, but not.")
    finally:
        libvirtd.exit()
        # Remove pid file under /run
        if serv_name:
            default_pid_path = "/run/" + serv_name + ".pid"
            if os.path.exists(default_pid_path):
                os.remove(default_pid_path)
        # Need to restart libvirtd.socket after starting libvirtd in the foreground
        Libvirtd("libvirtd.socket").restart()
