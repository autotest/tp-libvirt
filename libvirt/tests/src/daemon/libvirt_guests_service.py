import logging
import re

from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.staging.service import Factory


def run(test, params, env):
    """
    Test libvirt-guests service
    """
    def test_start_while_libvirtd_stopped():
        """
        Check the status of active libvirt-guests status while libvirtd is
        stopped.
        """
        logging.info("Stopping libvirtd and libvirt-guests services...")
        optr_dict = {libvirtd: 'stop', libvirt_guests: 'stop'}
        test_setup(optr_dict)

        logging.info("Starting libvirt-guests service...")
        libvirt_guests.start()

        logging.info("libvirtd and libvirt-guests should be running.")
        if not libvirtd.is_running():
            test.fail("libvird should be running.")
        if not libvirt_guests.status():
            test.fail("libvirt-guests should be running.")

    def test_stop_while_libvirtd_stopped():
        """
        Check the status of inactive libvirt-guests status while libvirtd is
        stopped.
        """
        logging.info("Starting libvirtd and libvirt-guests services...")
        optr_dict = {libvirtd: 'start', libvirt_guests: 'start'}
        test_setup(optr_dict)

        logging.info("Stopping libvirtd service...")
        libvirtd.stop()
        logging.info("Stopping libvirt-guests...")
        libvirt_guests.stop()
        if libvirt_guests.status():
            test.fail("libvirt-guests should be down.")

    def test_restart_libvirtd_with_running_vm():
        """
        Libvirt-guests should not be restarted automatically when libvirtd is
        restarted.
        """
        logging.info("Starting libvirtd and libvirt-guests...")
        optr_dict = {libvirtd: 'start', libvirt_guests: 'start'}
        test_setup(optr_dict)

        logging.info("Starting VM...")
        vm_id = get_non_init_dom_id(vm)
        org_guests_pid = get_libvirt_guests_pid(libvirt_guests)

        logging.info("Restarting libvirtd...")
        libvirtd.restart()
        act_guests_pid = get_libvirt_guests_pid(libvirt_guests)
        if org_guests_pid != act_guests_pid:
            test.fail("Pid of libvirt-guests changed from {} to {}."
                      .format(org_guests_pid, act_guests_pid))
        vm_id_act = vm.get_id()
        if vm_id != vm_id_act:
            test.fail("Domain id changed! Expected: {}, Acatual: {}."
                      .format(vm_id, vm_id_act))

    def test_setup(optr_dict):
        """
        Setup services based on optr_dict

        :param optr_dict: Test parameters, eg. {libvirtd_obj: 'start'}
        """
        if not isinstance(optr_dict, dict):
            test.error("Incorrect 'optr_dict'! It must be a dict!")
        for serv, optr in optr_dict.items():
            if optr:
                if optr not in ['start', 'stop']:
                    test.error("Unknown service operation - %s!" % optr)
                getattr(serv, optr)()

    def get_non_init_dom_id(vm):
        """
        Prepare a VM with domain id not equal to 1

        :param vm: The VM object
        :return: VM's id
        """
        def _get_id():
            if not vm.is_alive():
                vm.destroy()
            vm.start()
            vmid = vm.get_id()
            logging.debug("vm id: %s.", vmid)
            if vmid != '1':
                return vmid
            else:
                vm.destroy()

        vm_id = utils_misc.wait_for(_get_id, 120)
        if not vm_id:
            test.error("Unable to get the expected vm id!")
        return vm_id

    def get_libvirt_guests_pid(libvirt_guests):
        """
        Get pid of libvirt-guests

        :param libvirt_guests: The libvirt-guests object
        :return: libvirt-guests' pid
        """
        cmdRes = libvirt_guests.raw_status()
        if cmdRes.exit_status:
            test.fail("libvirt-guests is down!")
        res = re.search('Main PID: (\d+)', cmdRes.stdout)
        if not res:
            test.fail("Unable to get pid of libvirt-guests!")
        else:
            logging.debug("Pidof libvirt-guests: %s.", res[1])
        return res[1]

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    libvirt_guests = Factory.create_service("libvirt-guests")
    libvirtd = utils_libvirtd.Libvirtd('virtqemud')

    try:
        run_test()
    finally:
        logging.info("Recover test enviroment.")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        if libvirt_guests.status():
            libvirt_guests.stop()
