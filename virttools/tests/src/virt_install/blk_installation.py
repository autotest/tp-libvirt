# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Sebastian Mitterle<smitterl@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import logging as log

from avocado.utils import process

from virttest.utils_misc import cmd_status_output
from virttest.utils_zchannels import SubchannelPaths as paths

from provider.vfio import ccw


logging = log.getLogger("avocado." + __name__)

cleanup_actions = []


def run(test, params, env):
    """
    Install VM with s390x DASD as block device
    """

    vm_name = 'blk_installation'
    vm = None
    devid = params.get("devid")

    try:
        install_tree_url = params.get("install_tree_url")
        kickstart_url = params.get("kickstart_url")

        process.run("curl -k -o /tmp/ks.cfg %s" % kickstart_url)
        device = ccw.get_device_info(devid, True)
        devid = device[paths.HEADER["Device"]]
        disk_path = "/dev/disk/by-path/ccw-%s" % devid

        cmd = ("virt-install --name %s"
               " --disk %s"
               " --location %s"
               " --initrd-inject /tmp/ks.cfg"
               " --extra-args 'inst.ks=file:/ks.cfg'"
               " --vcpus 2 --memory 2048"
               " --osinfo detect=on,require=off"
               " --nographics"
               " --wait 10"
               " --noreboot" %
               (vm_name, disk_path, install_tree_url))

        cmd_status_output(cmd, shell=True, timeout=600)
        logging.debug("Installation finished")
        env.create_vm(vm_type='libvirt', target=None, name=vm_name, params=params, bindir=test.bindir)
        vm = env.get_vm(vm_name)
        cleanup_actions.insert(0, lambda: vm.undefine(options="--remove-all-storage"))

        if vm.is_dead():  # kickstart might shut machine down
            logging.debug("VM is dead, starting")
            vm.start()

        session = vm.wait_for_login().close()

    finally:
        if vm and vm.is_alive():
            vm.destroy()
        for action in cleanup_actions:
            try:
                action()
            except:
                logging.debug("There were errors during cleanup. Please check the log.")
