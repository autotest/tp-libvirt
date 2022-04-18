import logging as log
from avocado.core import exceptions

from virttest import utils_net
from virttest.libvirt_xml import vm_xml

from provider.interface import interface_base
from provider.interface import vdpa_base


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_network_accessibility(vm, **kwargs):
    """
    Check VM's network accessibility

    :param vm: VM object
    """
    if kwargs.get("recreate_vm_session", "yes") == "yes":
        logging.debug("Recreating vm session...")
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login()
    else:
        vm_session = vm.session

    dev_type = kwargs.get("dev_type")
    if dev_type == "vdpa":
        br_name = None
        config_vdpa = True
        test_target = kwargs.get("test_target")
        if test_target == "mellanox":
            if not kwargs.get("test_obj"):
                raise exceptions.TestError("test_obj must be assigned!")
            br_name = kwargs.get("test_obj").br_name
            config_vdpa = kwargs.get("config_vdpa", True)
        vdpa_base.check_vdpa_conn(
            vm_session, test_target, br_name, config_vdpa=config_vdpa)

    driver_queues = kwargs.get("driver_queues")
    if driver_queues:
        check_vm_iface_queues(vm_session, kwargs)


def check_vm_iface_queues(vm_session, params):
    """
    Check driver queues in VM's interface channel

    :param vm_session: VM session
    :param params: Dictionary with the test parameters
    :raises: TestFail if check fails
    """
    driver_queues = params.get("driver_queues")
    if not driver_queues:
        logging.warning("No need to check driver queues.")
        return
    ifname = interface_base.get_vm_iface(vm_session)
    maximums_channel, current_channel = utils_net.get_channel_info(
        vm_session, ifname)
    max_combined = maximums_channel.get("Combined", "1")
    current_combined = current_channel.get("Combined", "1")

    if max_combined != driver_queues:
        raise exceptions.TestFail("Incorrect combined number in maximums "
                                  "status! It should be %s, but got %s."
                                  % (driver_queues, max_combined))
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vmxml_cpu = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name).vcpu
    current_exp = min(vmxml_cpu, int(driver_queues))
    if int(current_combined) != current_exp:
        raise exceptions.TestFail("Incorrect combined number in current status!"
                                  "It should be %d but got %s."
                                  % (current_exp, current_combined))


def comp_interface_xml(vmxml, iface_dict, status_error=False):
    """
    Compare interface xml

    :param vmxml: VM xml
    :param iface_dict: Interface parameters dict
    :param status_error: True if expects mismatch, otherwise False
    :raise: TestFail if comparison fails
    """
    iface = vmxml.get_devices('interface')[0]
    cur_iface = iface.fetch_attrs()
    for key, val in iface_dict.items():
        if key != 'alias' and (cur_iface.get(key) == val) == status_error:
            raise exceptions.TestFail('Interface xml compare fails! The value '
                                      'of %s should be %s, but got %s.'
                                      % (key, cur_iface.get(key), val))
