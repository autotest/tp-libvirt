import logging as log
from avocado.core import exceptions

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
