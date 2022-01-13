import logging as log
import time

from avocado.utils import process

from virttest import utils_libvirtd
from virttest import utils_package
from virttest import virsh


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    This case open multiple connections to libvirtd.

    """
    def get_netlink(service_obj):
        """
        Get netlink list of service

        :params service_obj: service object
        :return: netlink list of service
        """
        cmd = "lsof -p `pidof %s` | grep netlink" % service_obj.service_name
        netlink_list = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
        return netlink_list

    conn_num = params.get("conn_num", 1)

    virsh_instance = []

    # Install lsof pkg if not installed
    if not utils_package.package_install("lsof"):
        test.cancel("Failed to install lsof in host.")

    libvirtd = utils_libvirtd.Libvirtd()
    if not libvirtd.is_running():
        libvirtd.start()

    try:
        original_netlink = get_netlink(libvirtd)
        logging.info("original netlink: %s" % original_netlink)
        for _ in range(int(conn_num)):
            virsh_instance.append(virsh.VirshPersistent())

        time.sleep(5)
        after_netlink = get_netlink(libvirtd)
        logging.info("after netlink: %s" % after_netlink)
        if original_netlink != after_netlink:
            test.fail("Open netcf more than once in libvirtd.")
    finally:
        for session in virsh_instance:
            session.close_session()
