import logging

from virttest import virsh
from virttest import remote
from virttest import utils_package
from virttest import utils_split_daemons
from virttest.staging import service

LOGGER = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check libvirt daemons are removed after removing libvirt pkgs.
    """
    daemons = params.get('daemons', "").split()
    require_modular_daemon = params.get('require_modular_daemon', "no") == "yes"

    utils_split_daemons.daemon_mode_check(require_modular_daemon)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    try:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)
        if not vm.is_alive():
            vm.start()

        session = vm.wait_for_login()
        if not utils_package.package_install("libvirt*", session):
            test.error("Failed to install libvirt package on guest")

        virsh.reboot(vm)
        if session is None:
            session = vm.wait_for_login()

        #Destroy default network, otherwise network daemon will not be removed after removed libvirt pkgs
        cmd = "virsh net-destroy default"
        session.cmd(cmd, ignore_all_errors=True)

        runner = remote.RemoteRunner(session=session).run
        service.Factory.create_service('virtlogd', run=runner).start()

        if not utils_package.package_remove("libvirt*", session):
            test.error("Failed to remove libvirt packages on guest")

        for daemon in daemons:
            _, out = session.cmd_status_output("systemctl -a| grep %s" % daemon)
            LOGGER.debug(out)
            if daemon in out and "not-found" not in out:
                test.fail("%s still exists after removing libvirt pkgs" % daemon)

    finally:
        if session is not None:
            session.close()
        if vm.is_alive():
            vm.destroy()
