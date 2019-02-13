import logging

from avocado.utils import cpu
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test whether kvm guest can start after offlining part of unrelated host cpus

    1. config a vm with cpuset='0-1'
    2. shutdown vm
    3. offline part of unrelated host cpus
    4. start the vm

    Expected results:
    vm start successfully after offlining part of unrelated host cpus
    """

    vm_name = params.get("main_vm")
    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    cpuset = params.get("cpuset", "0-1")
    cpus_list_offline = params.get("cpus_list_offline", "2,3")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        # set domain cpuset
        vmxml.cpuset = cpuset
        logging.debug(vmxml)
        vmxml.sync()

        # start vm
        logging.info("start vm with cpuset {}".format(cpuset))
        ret = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(ret, status_error)

        # shutdown vm
        logging.info("shutdown vm")
        ret = virsh.destroy(vm_name, debug=True)
        libvirt.check_exit_status(ret, status_error)

        # offline host cpus
        cpus_list = cpu.cpu_online_list()
        logging.debug("active host cpus {}".format(cpus_list))
        logging.debug("offline host cpus {}".format(cpus_list_offline))
        for x in cpus_list_offline.split(','):
            if cpu.offline(x):
                test.fail("fail to offline cpu{}".format(x))

        # check whether vm could start successfully
        logging.info("start vm")
        result = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(result, status_error)

    finally:
        logging.debug("online host cpus {}".format(cpus_list_offline))
        for x in cpus_list_offline.split(','):
            if cpu.online(x):
                test.fail("fail to online cpu{}".format(x))

        vmxml_backup.sync()
