import re
import logging

from avocado.utils import process
from avocado.core import exceptions

from virttest import virsh
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test aarch64 SVE feature

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm", "no")
    check_sve = params.get("check_sve", "")
    check_sve_config = params.get("check_sve_config", "")
    get_maxium_sve_length = params.get("get_maxium_sve_length", "")

    cpu_xml_mode = params.get("cpu_xml_mode", "host-passthrough")
    cpu_xml_policy = params.get("cpu_xml_policy", "require")

    status_error = "yes" == params.get("status_error", "no")
    expect_sve = "yes" == params.get("expect_sve", "yes")
    expect_msg = params.get("expect_msg", "")
    vector_length = params.get("vector_length", "sve")

    def _prepare_env(vm):
        """
        Prepare test env

        :param vm: The virtual machine
        """
        try:
            if not vm.is_alive():
                vm.start()
            session = vm.wait_for_login(timeout=120)
            current_boot = session.cmd('uname -r').strip()

            # Install lscpu tool that check whether CPU has SVE
            if (not utils_package.package_install("util-linux")
                    or not utils_package.package_install("util-linux", session)):
                test.error("Failed to install util-linux")
            # Cancel test if host doesn't support SVE
            if not process.run(check_sve,
                               ignore_status=True, shell=True).exit_status:
                test.cancel("Host doesn't support SVE")
            # To enable SVE: Hardware support && enable kconfig
            # CONFIG_ARM64_SVE
            if session.cmd_status(check_sve_config % current_boot):
                test.cancel("Guest kernel doesn't enable CONFIG_ARM64_SVE")
        except (exceptions.TestCancel, exceptions.TestError):
            raise
        except Exception as e:
            test.error("Failed to prepare test env: %s" % e)
        finally:
            if session:
                session.close()

    def _get_maxium_sve_length(vm):
        """
        Get the maxium supported sve length of guest

        : return maxium vector length. Format: e.g sve512
        """
        try:
            session = None
            if not vm.is_alive():
                vm.start()
            session = vm.wait_for_login(timeout=120)
            ret = session.cmd(get_maxium_sve_length).strip()
            # dmesg record maxium sve length in bytes
            sve_length_byte = re.search("length (\d+) bytes", ret).groups()[0]
            # Change max_length into sve + length(bit) E.g. sve512
            sve_length_bit = "sve" + str(int(sve_length_byte) * 8)
            logging.debug("guest sve_length_bit is %s" % sve_length_bit)
        except Exception as e:
            test.fail("Failed to get guest SVE Vector length: %s" % e)
        finally:
            if session:
                session.close()
        return sve_length_bit

    def _guest_has_sve(vm):
        """
        Check whether guest has SVE

        :param vm: The virtual machine

        :return True if guest has sve
        """
        try:
            ret = False
            session = None
            if not vm.is_alive():
                vm.start()
            session = vm.wait_for_login(timeout=120)
            if not session.cmd_status(check_sve):
                ret = True
        except Exception as e:
            test.error("Failed to check guest SVE: %s" % e)
        finally:
            if session:
                session.close()
        return ret

    # Close guest and edit guest xml
    if vm.is_alive() and start_vm == "no":
        vm.destroy(gracefully=False)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    original_vm_xml = vmxml.copy()

    try:
        # Install lscpu && Check host sve support &&
        # Check guest guest kernel support
        _prepare_env(vm)

        # Create cpu xml and modify SVE
        cpu_xml = vm_xml.VMCPUXML()
        cpu_xml.mode = cpu_xml_mode
        # For sve, each SVE vector_length is a feature name
        # E.g. sve128 sve256 sve512
        cpu_xml.add_feature(vector_length, cpu_xml_policy)
        logging.debug("cpu_xml is %s" % cpu_xml)

        # Updae vm's cpu
        vmxml.cpu = cpu_xml
        vmxml.sync()
        logging.debug("vmxml is %s" % vmxml)

        result = virsh.start(vm_name)
        libvirt.check_exit_status(result, status_error)

        if status_error:
            # Test boot failed
            if result.exit_status:
                if not re.search(expect_msg, result.stderr.strip()):
                    logging.debug(result.stderr.strip())
                    test.fail("Failed to get expect err msg: %s" % expect_msg)
                else:
                    logging.info("Get expected err msg %s" % expect_msg)
        else:
            # Test boot successfully
            if expect_sve:
                # Enable SVE in domain xml
                if not _guest_has_sve(vm):
                    test.fail("Expect guest cpu enable SVE")

                # SVE available with only the selected vector
                expect_vector_length = vector_length
                if vector_length == "sve":
                    expect_vector_length = "sve512"
                if expect_vector_length != _get_maxium_sve_length(vm):
                    test.fail("Expect guest support %s" % vector_length)
            else:
                # Disable SVE in domain xml
                if _guest_has_sve(vm):
                    test.fail("Expect guest cpu disable SVE")

    finally:
        # Restore guest
        if vm.is_alive():
            vm.destroy(gracefully=False)
        original_vm_xml.sync()
