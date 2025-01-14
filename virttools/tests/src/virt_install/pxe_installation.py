import logging as log

from virttest.utils_misc import cmd_status_output
from provider.virtual_network import tftpboot


logging = log.getLogger("avocado." + __name__)

cleanup_actions = []


def run(test, params, env):
    """
    Install VM with s390x specific pxe configuration
    """

    vm_name = 'pxe_installation'
    vm = None

    try:
        install_tree_url = params.get("install_tree_url")
        kickstart_url = params.get("kickstart_url")
        tftpboot.create_tftp_content(install_tree_url, kickstart_url, arch="s390x")
        tftpboot.create_tftp_network()

        cmd = ("virt-install --pxe --name %s"
               " --disk size=10"
               " --vcpus 2 --memory 2048"
               " --osinfo detect=on,require=off"
               " --nographics"
               " --wait 10"
               " --noreboot"
               " --network network=%s" %
               (vm_name, tftpboot.net_name))

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
        tftpboot.cleanup()
        for action in cleanup_actions:
            try:
                action()
            except:
                logging.debug("There were errors during cleanup. Please check the log.")
